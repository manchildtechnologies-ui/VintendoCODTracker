"""Read-only CODMunity public metadata/distribution support.

Individual player-stat retrieval deliberately has no default endpoint. Supply an
approved fetcher only after CODMunity confirms the external access method.
"""
from __future__ import annotations

import json
import math
import re
import unicodedata
from dataclasses import dataclass
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
        )


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


class ModularPlayerStatsService:
    """Adapter seam for a future CODMunity-approved player-data mechanism."""

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
