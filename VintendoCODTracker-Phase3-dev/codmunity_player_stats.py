"""Read-only CODMunity public metadata/distribution support.

Individual player-stat retrieval uses the approved desktop-app endpoints as the
signed-in user; see build_authorized_fetcher. The password is never stored.
"""
from __future__ import annotations

import json
import math
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Callable, NotRequired, Protocol, TypedDict
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

PROFILE_API = "https://api.codmunity.gg/website/pages/profile/"
GLOBAL_STATS_API = "https://codmunity.gg/api/public/stats/global"
PERCENTILE_ANCHORS = (5, 10, 20, 30, 40, 50, 60, 70, 80, 90, 95, 99)
LADDER_TIERS = (
    (0, "Bot"),
    (10, "Casual"),
    (40, "Solid"),
    (70, "Sweat"),
    (85, "Demon"),
    (95, "Cracked"),
    (99, "Goat"),
)
PROVISIONAL_PLAYER_SCHEMA = True


class PlayerMatchStats(TypedDict, total=False):
    kills: int | float
    deaths: int | float
    assists: int | float
    eliminations: int | float
    score: int | float
    damage: int | float
    placement: int | float
    redeploys: int | float
    contracts: int | float
    firstBloods: int | float
    objectives: int | float
    confirms: int | float
    denies: int | float
    captures: int | float
    defends: int | float
    plants: int | float
    defuses: int | float
    rescues: int | float


class ProvisionalLifetimeStats(TypedDict, total=False):
    kills: int | float
    deaths: int | float
    kd: int | float
    wins: int
    losses: int
    games_played: int
    time_played_s: int | float
    avg_kills_game: int | float
    highest_kills_game: int
    highest_streak: int
    score_per_min: int | float
    level: int
    wins_by_mode: dict[str, int]


class ProvisionalSnapshot(TypedDict, total=False):
    capturedAt: str
    kills: int | float
    deaths: int | float


class ProvisionalMatch(TypedDict, total=False):
    playedAt: str
    game: str
    mode: str
    modeKey: str
    result: str
    flagged: bool
    endReason: str
    stats: PlayerMatchStats


class ProvisionalPlayerStats(TypedDict, total=False):
    """Provisional until checked against a permitted real player response."""
    state: str
    public: bool
    lifetime: ProvisionalLifetimeStats
    warzoneLifetime: dict[str, dict[str, Any]]
    matches: list[ProvisionalMatch]
    snapshots: list[ProvisionalSnapshot]
    sessions: list[dict[str, Any]]
    trends: dict[str, Any]
    journal: dict[str, Any]


@dataclass(frozen=True)
class PlayerIdentity:
    username: str
    uid: str
    display_name: str | None = None


@dataclass(frozen=True)
class CommunityDistribution:
    game: str
    mode: str
    scope: str
    kd: tuple[float, ...]
    sampled: int | None = None
    users_total: int | None = None
    wl: tuple[float, ...] | None = None
    win_rate: tuple[float, ...] | None = None
    extra: tuple[tuple[str, tuple[float, ...]], ...] = field(default_factory=tuple)

    @classmethod
    def from_payload(cls, payload: Any) -> "CommunityDistribution":
        """Parse the verified public global-stats JSON envelope."""
        if not isinstance(payload, dict):
            raise ValueError("Global stats response must be a JSON object")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise ValueError("Global stats response is missing object field 'data'")
        dist = data.get("dist")
        values = dist.get("kd") if isinstance(dist, dict) else None
        if not isinstance(values, list) or len(values) != len(PERCENTILE_ANCHORS):
            raise ValueError("Global stats data.dist.kd must contain 12 percentile values")
        numbers = tuple(_finite_number(value) for value in values)
        if any(value is None for value in numbers):
            raise ValueError("KD distribution values must be finite numbers")
        kd_values = tuple(float(value) for value in numbers if value is not None)
        if any(a > b for a, b in zip(kd_values, kd_values[1:])):
            raise ValueError("KD distribution must be monotonically nondecreasing")
        return cls(
            game=_required_text(data, "game"),
            mode=_required_text(data, "mode"),
            scope=_required_text(data, "scope"),
            kd=kd_values,
            sampled=_optional_int(data.get("sampled")),
            users_total=_optional_int(data.get("usersTotal")),
            wl=_optional_dist(data, "wl"),
            win_rate=_optional_dist(data, "winRate"),
            extra=_extra_distributions(data),
        )


def _extra_distributions(data: dict[str, Any]) -> tuple[tuple[str, tuple[float, ...]], ...]:
    """Every other 12-value percentile array under data.dist, sorted by key."""
    dist_map = data.get("dist") if isinstance(data, dict) and isinstance(data.get("dist"), dict) else {}
    extras = []
    for key in sorted(dist_map):
        if key in ("kd", "wl", "winRate"):
            continue
        values = _optional_dist(data, key)
        if values is not None:
            extras.append((key, values))
    return tuple(extras)


def _optional_dist(data: dict[str, Any], key: str) -> tuple[float, ...] | None:
    """Lenient percentile-array parse; None when absent or malformed."""
    dist = data.get("dist")
    values = dist.get(key) if isinstance(dist, dict) else None
    if not isinstance(values, list) or len(values) != len(PERCENTILE_ANCHORS):
        return None
    numbers = tuple(_finite_number(value) for value in values)
    if any(value is None for value in numbers):
        return None
    out = tuple(float(value) for value in numbers if value is not None)
    if any(a > b for a, b in zip(out, out[1:])):
        return None
    return out


def _validated_dist(values: Any) -> tuple[float, ...]:
    """Twelve finite percentile values, else ValueError."""
    if not isinstance(values, (list, tuple)) or len(values) != len(PERCENTILE_ANCHORS):
        raise ValueError("distribution must contain 12 percentile values")
    numbers = tuple(_finite_number(value) for value in values)
    if any(value is None for value in numbers):
        raise ValueError("distribution values must be finite numbers")
    out = tuple(float(value) for value in numbers if value is not None)
    if any(a > b for a, b in zip(out, out[1:])):
        raise ValueError("distribution must be monotonically nondecreasing")
    return out


def percentile_of(value: Any, dist_values: Any) -> float:
    """Player percentile (0-100) for one value against a distribution."""
    number = _finite_number(value)
    if number is None:
        raise ValueError("value must be a finite number")
    return _interpolate_x(_validated_dist(dist_values), PERCENTILE_ANCHORS, number)


def median_of(dist_values: Any) -> float:
    """Distribution value at the 50th percentile."""
    return _interpolate_y(PERCENTILE_ANCHORS, _validated_dist(dist_values), 50)


@dataclass(frozen=True)
class LadderRank:
    kd: float
    percentile: float
    tier: str
    next_tier: str | None
    next_tier_kd: float | None
    progress_percent: float


@dataclass(frozen=True)
class TrackedMatchKD:
    """Aggregate from explicitly verified local player rows; never lifetime stats."""

    kills: float
    deaths: float
    matches: int
    kd: float | None


@dataclass(frozen=True)
class PlayerKD:
    kd: float | None
    source: str
    updated_at: str | None = None


class PlayerStatsUnavailable(RuntimeError):
    """Raised when no approved individual-player provider has been supplied."""


class PlayerStatsFetcher(Protocol):
    def __call__(self, uid: str) -> ProvisionalPlayerStats: ...


class CodmunityPublicClient:
    """Low-volume unauthenticated GET client for verified public JSON routes."""

    def __init__(self, timeout: float = 12.0, opener: Callable[..., Any] | None = None):
        self.timeout = timeout
        self._opener = opener or urlopen

    def _get_json(self, url: str) -> Any:
        request = Request(url, headers={"Accept": "application/json",
                                          "User-Agent": "VintendoCODTracker"})
        try:
            with self._opener(request, timeout=self.timeout) as response:
                body = response.read()
        except HTTPError as exc:
            raise RuntimeError(f"CODMunity public endpoint returned HTTP {exc.code}") from exc
        except URLError as exc:
            raise RuntimeError("CODMunity public endpoint could not be reached") from exc
        try:
            return json.loads(body.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("CODMunity public endpoint returned invalid JSON") from exc

    def profile_identity(self, username: str) -> PlayerIdentity:
        if not isinstance(username, str) or not username.strip():
            raise ValueError("username is required")
        url = PROFILE_API + quote(username.strip(), safe="")
        return parse_profile_identity(self._get_json(url), requested_username=username)

    def profile_banner(self, username: str) -> dict[str, Any]:
        if not isinstance(username, str) or not username.strip():
            raise ValueError("username is required")
        url = PROFILE_API + quote(username.strip(), safe="")
        return parse_profile_banner(self._get_json(url), requested_username=username)

    def community_distribution(
        self, game: str, mode: str, scope: str
    ) -> CommunityDistribution:
        query = urlencode({"game": game, "mode": mode, "scope": scope})
        payload = self._get_json(f"{GLOBAL_STATS_API}?{query}")
        if isinstance(payload, dict) and not isinstance(payload.get("data"), dict):
            # The live envelope carries the distribution fields at the top
            # level; the parser also accepts the cached {'data': ...} shape.
            payload = {"data": payload}
        result = CommunityDistribution.from_payload(payload)
        if (result.game, result.mode, result.scope) != (game, mode, scope):
            raise ValueError("Global stats response does not match requested game/mode/scope")
        return result


FIREBASE_API_KEY = "AIzaSyD9Gr_6JFYNdtwUMXafADYX5oj9k9Z1G8Y"
SIGN_IN_API = "https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword"
OOB_API = "https://identitytoolkit.googleapis.com/v1/accounts:sendOobCode"
REFRESH_API = "https://securetoken.googleapis.com/v1/token"
APP_API = "https://api.codmunity.gg"


def request_password_reset(email: str, opener: Callable[..., Any] | None = None,
                            timeout: float = 12.0) -> None:
    """Send the Firebase password-reset email; mirrors the desktop reset flow."""
    if not isinstance(email, str) or not email.strip():
        raise ValueError("email is required")
    payload = json.dumps({"requestType": "PASSWORD_RESET",
                          "email": email.strip()}).encode("utf-8")
    request = Request(f"{OOB_API}?key={FIREBASE_API_KEY}", data=payload,
                      headers={"Content-Type": "application/json",
                               "Accept": "application/json",
                               "User-Agent": "VintendoCODTracker"})
    call = opener or urlopen
    try:
        with call(request, timeout=timeout) as response:
            response.read()
    except HTTPError as exc:
        raise AuthError(f"Password reset failed: {_firebase_error(exc.read())}") from exc
    except URLError as exc:
        raise AuthError("Password reset failed: authentication service could not be reached") from exc


class AuthError(RuntimeError):
    """Sign-in, refresh, or authorized-request failure (no tokens included)."""


def _firebase_error(body: bytes) -> str:
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeError, ValueError):
        return "authentication service returned an unreadable response"
    if isinstance(payload, dict):
        error = payload.get("error")
        message = error.get("message") if isinstance(error, dict) else error
        if isinstance(message, str) and message.strip():
            return message.strip()[:200]
    return "authentication service rejected the request"


class CodmunityAuthClient:
    """Email+password sign-in against the same Firebase project the desktop app uses.

    Only the refresh token is persisted (by the caller, DPAPI-encrypted);
    the password is never stored. Authenticated requests use the user's own
    account, matching the approved reuse of the desktop app endpoints.
    """

    def __init__(self, refresh_token: str = "", timeout: float = 12.0,
                 opener: Callable[..., Any] | None = None):
        self.timeout = timeout
        self._opener = opener or urlopen
        self._refresh_token = refresh_token or ""
        self._id_token = ""

    @property
    def signed_in(self) -> bool:
        return bool(self._refresh_token)

    def sign_in(self, email: str, password: str) -> dict[str, Any]:
        """Exchange user-supplied credentials for tokens; returns public fields only."""
        if not isinstance(email, str) or not email.strip():
            raise ValueError("email is required")
        if not isinstance(password, str) or not password:
            raise ValueError("password is required")
        payload = json.dumps({"email": email.strip(), "password": password,
                              "returnSecureToken": True}).encode("utf-8")
        request = Request(f"{SIGN_IN_API}?key={FIREBASE_API_KEY}", data=payload,
                          headers={"Content-Type": "application/json",
                                   "Accept": "application/json",
                                   "User-Agent": "VintendoCODTracker"})
        try:
            with self._opener(request, timeout=self.timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise AuthError(f"Sign-in failed: {_firebase_error(exc.read())}") from exc
        except URLError as exc:
            raise AuthError("Sign-in failed: authentication service could not be reached") from exc
        except (UnicodeError, ValueError) as exc:
            raise AuthError("Sign-in failed: authentication service returned invalid JSON") from exc
        if not isinstance(data, dict) or not data.get("idToken") or not data.get("refreshToken"):
            raise AuthError("Sign-in failed: authentication service returned an incomplete response")
        self._id_token = str(data["idToken"])
        self._refresh_token = str(data["refreshToken"])
        return {"email": str(data.get("email") or email.strip()),
                "localId": str(data.get("localId") or "")}

    def sign_out(self) -> None:
        self._id_token = ""
        self._refresh_token = ""

    @property
    def refresh_token(self) -> str:
        return self._refresh_token

    def _refresh_id_token(self) -> str:
        payload = urlencode({"grant_type": "refresh_token",
                             "refresh_token": self._refresh_token}).encode("utf-8")
        request = Request(f"{REFRESH_API}?key={FIREBASE_API_KEY}", data=payload,
                          headers={"Content-Type": "application/x-www-form-urlencoded",
                                   "Accept": "application/json",
                                   "User-Agent": "VintendoCODTracker"})
        try:
            with self._opener(request, timeout=self.timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise AuthError(f"Session refresh failed: {_firebase_error(exc.read())}") from exc
        except URLError as exc:
            raise AuthError("Session refresh failed: authentication service could not be reached") from exc
        except (UnicodeError, ValueError) as exc:
            raise AuthError("Session refresh failed: authentication service returned invalid JSON") from exc
        if not isinstance(data, dict) or not data.get("id_token"):
            raise AuthError("Session refresh failed: authentication service returned an incomplete response")
        self._id_token = str(data["id_token"])
        if data.get("refresh_token"):
            self._refresh_token = str(data["refresh_token"])
        return self._id_token

    def id_token(self) -> str:
        if self._id_token:
            return self._id_token
        if not self._refresh_token:
            raise AuthError("Not signed in")
        return self._refresh_id_token()

    def _authorized_json(self, method: str, path: str, body: Any = None) -> Any:
        token = self.id_token()
        data = None if body is None else json.dumps(body).encode("utf-8")
        headers = {"Accept": "application/json", "User-Agent": "VintendoCODTracker",
                   "Authorization": f"Bearer {token}"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = Request(APP_API + path, data=data, headers=headers, method=method)
        try:
            with self._opener(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            if exc.code == 401 and self._refresh_token:
                token = self._refresh_id_token()
                headers["Authorization"] = f"Bearer {token}"
                retry = Request(APP_API + path, data=data, headers=headers, method=method)
                try:
                    with self._opener(retry, timeout=self.timeout) as response:
                        return json.loads(response.read().decode("utf-8"))
                except HTTPError as retry_exc:
                    raise AuthError(f"CODMunity request failed (HTTP {retry_exc.code})") from retry_exc
                except URLError as retry_exc:
                    raise AuthError("CODMunity request failed: service could not be reached") from retry_exc
                except (UnicodeError, ValueError) as retry_exc:
                    raise AuthError("CODMunity request failed: invalid JSON response") from retry_exc
            raise AuthError(f"CODMunity request failed (HTTP {exc.code})") from exc
        except URLError as exc:
            raise AuthError("CODMunity request failed: service could not be reached") from exc
        except (UnicodeError, ValueError) as exc:
            raise AuthError("CODMunity request failed: invalid JSON response") from exc

    def get_json(self, path: str, query: dict[str, str] | None = None) -> Any:
        url = path
        if query:
            url += "?" + urlencode(query)
        return self._authorized_json("GET", url)

    def post_json(self, path: str, body: Any = None) -> Any:
        return self._authorized_json("POST", path, body)

    def send_email_verification(self) -> None:
        """Send the account verification email for the signed-in user."""
        token = self.id_token()
        payload = json.dumps({"requestType": "VERIFY_EMAIL",
                              "idToken": token}).encode("utf-8")
        request = Request(f"{OOB_API}?key={FIREBASE_API_KEY}", data=payload,
                          headers={"Content-Type": "application/json",
                                   "Accept": "application/json",
                                   "User-Agent": "VintendoCODTracker"})
        try:
            with self._opener(request, timeout=self.timeout) as response:
                response.read()
        except HTTPError as exc:
            raise AuthError(f"Verification email failed: {_firebase_error(exc.read())}") from exc
        except URLError as exc:
            raise AuthError("Verification email failed: authentication service could not be reached") from exc


def _read_json_file(path):
    from pathlib import Path as _Path
    try:
        import json as _json
        data = _json.loads(_Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        return None
    return data


def pending_matches(stats_dir):
    """Unsent matches.json entries for one Codmunity user folder."""
    from pathlib import Path as _Path
    matches = _read_json_file(_Path(stats_dir) / "matches.json")
    if not isinstance(matches, list):
        return []
    return [m for m in matches
            if isinstance(m, dict) and not m.get("sent") and m.get("matchId")]


def upload_pending_matches(auth: CodmunityAuthClient, stats_dir):
    """POST unsent matches.json entries as the signed-in user.

    Sends each entry minus the local ``sent`` bookkeeping flag, marks sent
    on success, and stops at the first connection failure. HTTP rejections
    are recorded per match and never marked sent.
    """
    from pathlib import Path as _Path
    import json as _json
    path = _Path(stats_dir) / "matches.json"
    matches = _read_json_file(path)
    if matches is None:
        raise ValueError("matches.json not found; import local data first")
    if not isinstance(matches, list):
        raise ValueError("matches.json is corrupt")
    summary = {"uploaded": 0, "skipped": 0, "failed": []}
    dirty = False
    for entry in matches:
        if not isinstance(entry, dict) or not entry.get("matchId"):
            continue
        if entry.get("sent"):
            summary["skipped"] += 1
            continue
        body = {k: v for k, v in entry.items() if k != "sent"}
        try:
            auth.post_json("/api/app/matches", body)
        except AuthError as exc:
            message = str(exc)
            if "could not be reached" in message or "could not be resolved" in message:
                summary["failed"].append({"matchId": entry.get("matchId"), "error": message})
                break
            summary["failed"].append({"matchId": entry.get("matchId"), "error": message})
            continue
        entry["sent"] = True
        summary["uploaded"] += 1
        dirty = True
    if dirty:
        try:
            path.write_text(_json.dumps(matches, indent=2), encoding="utf-8")
        except OSError as exc:
            raise AuthError(f"Uploads succeeded but matches.json could not be updated: {exc}") from exc
    return summary


def upload_tracked_matches(auth: CodmunityAuthClient, store):
    """POST locally tracked, user-verified matches as the signed-in user.

    Reads match rows from the tracker's own store (scoreboard captures the
    user reviewed and kept), not the desktop app's matches.json, so uploads
    work with no Codmunity Desktop data on the machine. Only rows carrying
    at least one verified player row are sent; baseline rows imported from
    the desktop file are left to the file flow. Sent match ids are recorded
    under the ``codmunity_uploaded_match_ids`` meta key; HTTP rejections are
    recorded per match and never marked sent, and the sweep stops at the
    first connection failure.
    """
    try:
        rows = store.history()
    except Exception:
        rows = []
    try:
        uploaded_ids = set(store.meta("codmunity_uploaded_match_ids", []) or [])
    except Exception:
        uploaded_ids = set()
    summary = {"uploaded": 0, "skipped": 0, "failed": []}
    dirty = False
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        if str(row.get("status") or "") == "baseline":
            continue
        payload = row.get("payload")
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                continue
        if not isinstance(payload, dict):
            continue
        match_id = payload.get("matchId")
        if not match_id:
            continue
        if match_id in uploaded_ids:
            summary["skipped"] += 1
            continue
        players = payload.get("players") or []
        verified = any(
            isinstance(p, dict) and (p.get("verified") or p.get("source") in ("user_verified_ocr",))
            for p in players
        )
        if not verified:
            continue
        try:
            auth.post_json("/api/app/matches", payload)
        except AuthError as exc:
            message = str(exc)
            if "could not be reached" in message or "could not be resolved" in message:
                summary["failed"].append({"matchId": match_id, "error": message})
                break
            summary["failed"].append({"matchId": match_id, "error": message})
            continue
        uploaded_ids.add(match_id)
        summary["uploaded"] += 1
        dirty = True
    if dirty:
        try:
            store.set_meta("codmunity_uploaded_match_ids", sorted(uploaded_ids))
        except Exception:
            pass
    return summary


def upload_lifetime_snapshot(auth: CodmunityAuthClient, stats_dir):
    """POST the local lifetime snapshot(s) as the signed-in user.

    Reads ``local.json`` (multiplayer envelope) and ``wz-modes.json``
    (per-mode entries with their own ``sent`` flags), posts each unsent
    envelope to ``POST /api/app/stats``, and mirrors the desktop app's
    bookkeeping by writing ``sent.json`` / per-mode ``sent`` flags on
    success. HTTP rejections are recorded and never marked sent.
    """
    from pathlib import Path as _Path
    import json as _json
    import datetime as _datetime
    root = _Path(stats_dir)
    summary = {"uploaded": 0, "skipped": 0, "failed": []}

    def _stamp():
        return _datetime.datetime.now(_datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    local = _read_json_file(root / "local.json")
    if isinstance(local, dict) and isinstance(local.get("stats"), dict):
        try:
            auth.post_json("/api/app/stats", local)
        except AuthError as exc:
            summary["failed"].append({"scope": "multiplayer", "error": str(exc)})
        else:
            summary["uploaded"] += 1
            try:
                (root / "sent.json").write_text(_json.dumps(
                    {"at": local.get("at") or _stamp(), "stats": local["stats"]},
                    indent=2), encoding="utf-8")
            except OSError as exc:
                raise AuthError(f"Lifetime upload succeeded but sent.json could not be updated: {exc}") from exc
    modes = _read_json_file(root / "wz-modes.json")
    if isinstance(modes, dict):
        dirty = False
        for mode, entry in modes.items():
            if not isinstance(entry, dict) or not isinstance(entry.get("stats"), dict):
                continue
            if entry.get("sent"):
                summary["skipped"] += 1
                continue
            body = {"game": "warzone", "mode": mode, "scope": "lifetime",
                    "at": entry.get("at") or _stamp(), "stats": entry["stats"]}
            try:
                auth.post_json("/api/app/stats", body)
            except AuthError as exc:
                summary["failed"].append({"scope": f"warzone/{mode}", "error": str(exc)})
                continue
            entry["sent"] = True
            summary["uploaded"] += 1
            dirty = True
        if dirty:
            try:
                (root / "wz-modes.json").write_text(_json.dumps(modes, indent=2), encoding="utf-8")
            except OSError as exc:
                raise AuthError(f"Lifetime upload succeeded but wz-modes.json could not be updated: {exc}") from exc
    return summary


def set_visibility(auth: CodmunityAuthClient, public: bool) -> Any:
    """Flip the account's leaderboard visibility; surfaces the server answer.

    Payload shape is best-effort from the approved endpoint name; any
    rejection is raised with the server message so a wrong shape is visible.
    """
    return auth.post_json("/api/app/stats/visibility", {"public": bool(public)})


def _payload_section(payload: Any) -> dict[str, Any]:
    """Accept {'stats': ...} or {'data': {'stats': ...}} or a bare stats dict."""
    if not isinstance(payload, dict):
        return {}
    for key in ("stats", "data", "lifetime"):
        section = payload.get(key)
        if isinstance(section, dict):
            if key == "data" and isinstance(section.get("stats"), dict):
                return section["stats"]
            if key != "data":
                return section
    return {k: v for k, v in payload.items() if k != "data"} if "matches" not in payload else {}


def build_authorized_fetcher(auth: CodmunityAuthClient) -> PlayerStatsFetcher:
    """Player-stats fetcher using the approved desktop-app endpoints as the user."""

    def fetch(uid: str) -> ProvisionalPlayerStats:
        if not isinstance(uid, str) or not uid.strip():
            raise ValueError("uid is required")
        stats_payload = auth.get_json("/api/app/stats/me")
        stats = _payload_section(stats_payload)
        warzone = stats_payload.get("warzoneLifetime") if isinstance(stats_payload, dict) else None
        try:
            matches_payload = auth.get_json("/api/app/matches/me")
        except AuthError:
            matches_payload = []
        if isinstance(matches_payload, dict):
            matches_payload = matches_payload.get("matches", matches_payload.get("data", []))
        result: ProvisionalPlayerStats = {"state": "live", "public": False}
        if stats:
            result["lifetime"] = {k: stats[k] for k in stats if k != "warzoneLifetime"}
        if isinstance(warzone, dict):
            result["warzoneLifetime"] = {k: v for k, v in warzone.items() if isinstance(v, dict)}
        if isinstance(matches_payload, list):
            matches = [m for m in matches_payload if isinstance(m, dict)][:200]
            if matches:
                result["matches"] = matches
        return result

    return fetch


class ModularPlayerStatsService:
    """Player-data service; inject the approved fetcher (build_authorized_fetcher)."""

    def __init__(self, fetcher: PlayerStatsFetcher | None = None):
        self._fetcher = fetcher

    @property
    def configured(self) -> bool:
        return self._fetcher is not None

    def retrieve(self, uid: str) -> ProvisionalPlayerStats:
        if not isinstance(uid, str) or not uid.strip():
            raise ValueError("uid is required")
        if self._fetcher is None:
            raise PlayerStatsUnavailable(
                "Individual-player stats are not configured; an approved CODMunity "
                "external access method is required."
            )
        value = self._fetcher(uid.strip())
        if not isinstance(value, dict):
            raise ValueError("Player stats provider must return a JSON object")
        return value


def parse_profile_banner(payload: Any, requested_username: str | None = None) -> dict[str, Any]:
    """Display fields from the verified public profile response shape.

    Identity (username/uid) is strict; decorative fields fall back to
    empty/zero rather than failing the whole card.
    """
    if not isinstance(payload, dict):
        raise ValueError("Profile response must be a JSON object")
    user = payload.get("user")
    if not isinstance(user, dict):
        raise ValueError("Profile response is missing object field 'user'")
    username = user.get("username")
    uid = user.get("uid")
    if not isinstance(username, str) or not username.strip():
        raise ValueError("Profile response is missing user.username")
    if not isinstance(uid, str) or not uid.strip():
        raise ValueError("Profile response is missing user.uid")
    if requested_username and username.casefold() != requested_username.strip().casefold():
        raise ValueError("Profile response username does not match the request")

    def _text(value: Any) -> str:
        return value.strip() if isinstance(value, str) and value.strip() else ""

    def _count(value: Any) -> int:
        return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0

    avatar = _text(user.get("imageUrlOptimized") or user.get("imageUrl"))
    if avatar and not avatar.casefold().startswith(("http://", "https://")):
        avatar = ""
    return {"username": username.strip(), "uid": uid.strip(),
            "display_name": _text(user.get("displayName")) or None,
            "avatar": avatar, "bio": _text(user.get("description")),
            "followers": _count(user.get("followers")),
            "loadouts": _count(payload.get("countLoadouts")),
            "likes": _count(payload.get("countLikes"))}


def parse_profile_identity(payload: Any, requested_username: str | None = None) -> PlayerIdentity:
    """Extract user.uid from the verified public profile response shape."""
    if not isinstance(payload, dict):
        raise ValueError("Profile response must be a JSON object")
    user = payload.get("user")
    if not isinstance(user, dict):
        raise ValueError("Profile response is missing object field 'user'")
    username = user.get("username")
    uid = user.get("uid")
    display_name = user.get("displayName")
    if not isinstance(username, str) or not username.strip():
        raise ValueError("Profile response is missing user.username")
    if not isinstance(uid, str) or not uid.strip():
        raise ValueError("Profile response is missing user.uid")
    if requested_username and username.casefold() != requested_username.strip().casefold():
        raise ValueError("Profile response username does not match the request")
    return PlayerIdentity(
        username=username.strip(),
        uid=uid.strip(),
        display_name=display_name.strip() if isinstance(display_name, str) and display_name.strip() else None,
    )


def ladder_rank(kd: float, distribution: CommunityDistribution) -> LadderRank:
    """Interpolate KD percentile and the inverse KD threshold for the next tier."""
    value = _finite_number(kd)
    if value is None or value < 0:
        raise ValueError("kd must be a finite nonnegative number")
    percentile = _interpolate_x(distribution.kd, PERCENTILE_ANCHORS, float(value))
    tier_index = max(index for index, (floor, _) in enumerate(LADDER_TIERS) if percentile >= floor)
    tier_floor, tier_name = LADDER_TIERS[tier_index]
    if tier_index + 1 >= len(LADDER_TIERS):
        return LadderRank(float(value), percentile, tier_name, None, None, 100.0)
    next_floor, next_name = LADDER_TIERS[tier_index + 1]
    next_kd = _interpolate_y(PERCENTILE_ANCHORS, distribution.kd, next_floor)
    span = next_kd - _interpolate_y(PERCENTILE_ANCHORS, distribution.kd, tier_floor)
    progress = 100.0 if span <= 0 else min(100.0, max(0.0, (float(value) - _interpolate_y(PERCENTILE_ANCHORS, distribution.kd, tier_floor)) / span * 100.0))
    return LadderRank(float(value), percentile, tier_name, next_name, next_kd, progress)


MIN_DISTRIBUTION_SAMPLE = 100


def distribution_rank(kd, distribution):
    """LadderRank, or None when the community sample is too small to trust.

    A valid-shaped but tiny distribution (a handful of players) produces
    wild percentiles; every rank display must go through this gate instead
    of calling ladder_rank directly.
    """
    try:
        sampled = int(getattr(distribution, "sampled", None) or 0)
    except (TypeError, ValueError):
        sampled = 0
    if sampled < MIN_DISTRIBUTION_SAMPLE:
        return None
    return ladder_rank(kd, distribution)


def tracked_match_kd(
    history_rows: list[dict[str, Any]],
    identity_names: list[str],
    game: str,
    mode: str,
) -> TrackedMatchKD:
    """Aggregate verified, exact-name local match rows for one game/mode.

    This result represents only saved tracker matches. It must be labeled as a
    tracked-match sample when compared with a community lifetime distribution.
    """
    wanted = {_normalize_identity(name) for name in identity_names if _normalize_identity(name)}
    if not wanted:
        return TrackedMatchKD(0.0, 0.0, 0, None)
    total_kills = total_deaths = 0.0
    count = 0
    expected_game = game.casefold()
    expected_mode = mode.casefold()
    for row in history_rows or []:
        if not isinstance(row, dict):
            continue
        match = _as_object(row.get("payload"))
        if match is None or str(match.get("game") or "").casefold() != expected_game:
            continue
        actual_mode = str(match.get("modeKey") or match.get("mode") or "").casefold()
        if expected_mode != "multiplayer" and expected_mode not in actual_mode:
            continue
        players = _as_array(row.get("players"))
        matched = [
            player for player in players
            if isinstance(player, dict)
            and player.get("verified") is True
            and _normalize_identity(str(player.get("name") or "")) in wanted
            and isinstance(player.get("stats"), dict)
        ]
        # Ambiguous duplicate rows in one match must not inflate the sample.
        if len(matched) != 1:
            continue
        stats = matched[0]["stats"]
        kills = _finite_number(stats.get("kills", stats.get("eliminations")))
        deaths = _finite_number(stats.get("deaths"))
        if kills is None or deaths is None or kills < 0 or deaths < 0:
            continue
        total_kills += kills
        total_deaths += deaths
        count += 1
    return TrackedMatchKD(
        total_kills, total_deaths, count,
        total_kills / total_deaths if total_deaths > 0 else None,
    )


def player_kd_for_mode(payload: Any, game: str, mode: str) -> PlayerKD:
    """Read K/D from an injected player payload for the requested lifetime mode."""
    if not isinstance(payload, dict):
        return PlayerKD(None, "unavailable")
    if game.casefold() == "warzone":
        if mode.casefold() == "all":
            return PlayerKD(None, "unavailable")
        section = (payload.get("warzoneLifetime") or {}).get(mode) if isinstance(payload.get("warzoneLifetime"), dict) else None
        source = f"CODMunity Warzone {mode} lifetime"
    else:
        section = payload.get("lifetime")
        source = "CODMunity multiplayer lifetime"
    if not isinstance(section, dict):
        return PlayerKD(None, "unavailable")
    stats = section.get("stats") if isinstance(section.get("stats"), dict) else section
    kd = _finite_number(stats.get("kd"))
    if kd is None:
        kills = _finite_number(stats.get("kills"))
        deaths = _finite_number(stats.get("deaths"))
        if kills is not None and deaths is not None and deaths > 0:
            kd = kills / deaths
    if kd is None or kd < 0:
        return PlayerKD(None, "unavailable", section.get("updatedAt"))
    return PlayerKD(kd, source, section.get("updatedAt") or payload.get("updatedAt"))


def _normalize_identity(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value).casefold())
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]", "", text)


def _as_object(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def _as_array(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _interpolate_x(xs: tuple[float, ...], ys: tuple[int, ...], x: float) -> float:
    if x <= xs[0]:
        return float(ys[0])
    if x >= xs[-1]:
        return float(ys[-1])
    for index in range(1, len(xs)):
        if xs[index - 1] <= x <= xs[index]:
            low, high = xs[index - 1], xs[index]
            if high == low:
                return float(ys[index])
            return ys[index - 1] + (x - low) / (high - low) * (ys[index] - ys[index - 1])
    return float(ys[-1])


def _interpolate_y(xs: tuple[int, ...], ys: tuple[float, ...], y: int) -> float:
    if y <= xs[0]:
        return float(ys[0])
    if y >= xs[-1]:
        return float(ys[-1])
    for index in range(1, len(xs)):
        if xs[index - 1] <= y <= xs[index]:
            low, high = xs[index - 1], xs[index]
            if high == low:
                return float(ys[index])
            return ys[index - 1] + (y - low) / (high - low) * (ys[index] - ys[index - 1])
    return float(ys[-1])


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _required_text(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Global stats data is missing string field {key!r}")
    return value.strip()


def _optional_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None
