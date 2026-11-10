"""Vintendo Player Stats Service: the shared player-intelligence layer.

Every UI surface (Player Roster, Dashboard, Squad Night, AI Studio, Discord
formatters) reads through these functions instead of duplicating statistics,
distribution parsing, or ranking math. The ranking formulas themselves live in
:mod:`codmunity_player_stats` (Codex) and are reused here, never reimplemented.

Data integrity rules enforced here:
- lifetime K/D and tracked-sample K/D are never mixed; every value carries
  its source, game, mode, scope, timestamp, and verification status;
- game/mode/scope are always compared like-for-like;
- missing data yields explicit unavailable results, never invented numbers;
- persistence reuses the existing tracker stores (roster rows + store.meta),
  so no schema migration is required.
"""
import json
import unicodedata

import codmunity_player_stats as C
from datetime import datetime, timezone

SNAPSHOT_LIMIT = 90
SNAPSHOT_MIN_INTERVAL_SECONDS = 24 * 60 * 60

# Concrete public global-stats routes verified in the CODMunity site assets
# (CODMunity_Public_API_Clickable_Catalog). Each tuple is (game, mode, scope)
# for https://codmunity.gg/api/public/stats/global. The lifetime rows back
# Ladder Rank and community comparisons beyond the recent-mode sample.
VERIFIED_GLOBAL_COMBOS = (
    ("warzone", "all", "recent"),
    ("warzone", "resurgence", "lifetime"),
    ("warzone", "battle_royale", "lifetime"),
    ("mw4", "multiplayer", "lifetime"),
)


def _utcnow_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _required_text(value):
    text = str(value or "").strip()
    return text


def identity_names(player):
    """All verified-lookup names for one roster row, longest first."""
    if not isinstance(player, dict):
        return []
    username = _required_text(player.get("codmunity_username"))
    display = _required_text(player.get("codmunity_display_name"))
    names = [username, _required_text(player.get("name"))]
    aliases = player.get("aliases") or []
    names.extend(_required_text(alias) for alias in aliases if isinstance(alias, str))
    if display and display != "—":
        names.append(display)
    seen = []
    for name in names:
        if name and name not in seen:
            seen.append(name)
    return seen


def distribution_for(store, player, game, mode, scope):
    """Shared cached community distribution for one game/mode/scope."""
    key = "|".join((str(game), str(mode), str(scope)))
    saved = (store.meta("codmunity_public_distributions", {}) or {}).get(key)
    if not isinstance(saved, dict) and isinstance(player, dict):
        saved = (player.get("codmunity_distributions") or {}).get(key)
    if not isinstance(saved, dict):
        return None
    try:
        return C.CommunityDistribution.from_payload({"data": {
            "game": saved.get("game"), "mode": saved.get("mode"), "scope": saved.get("scope"),
            "sampled": saved.get("sampled"), "usersTotal": saved.get("users_total"),
            "dist": {"kd": saved.get("kd")}}})
    except (TypeError, ValueError):
        return None


def kd_for_player(store, player, game, mode):
    """Best available K/D: stored lifetime first, else tracked local sample."""
    stored = (player or {}).get("codmunity_player_stats")
    player_kd = C.player_kd_for_mode(stored, str(game or ""), str(mode or ""))
    if player_kd.kd is not None:
        return {"kd": player_kd.kd, "source": "lifetime", "sample_matches": None}
    sample = C.tracked_match_kd(store.history(), identity_names(player or {}),
                                str(game or ""), str(mode or ""))
    if sample.kd is None:
        return {"kd": None, "source": "", "sample_matches": 0}
    noun = "match" if sample.matches == 1 else "matches"
    return {"kd": sample.kd, "source": f"{sample.matches} tracked {noun}",
            "sample_matches": sample.matches}


def snapshot_for_player(store, player, game, mode, scope):
    """One JSON-safe intelligence snapshot for a roster row.

    Never raises for missing data; ``rank`` is None when no compatible
    K/D and distribution pair exists.
    """
    game, mode, scope = str(game or ""), str(mode or ""), str(scope or "")
    player = player if isinstance(player, dict) else {}
    kd_info = kd_for_player(store, player, game, mode)
    distribution = distribution_for(store, player, game, mode, scope)
    rank = None
    if kd_info["kd"] is not None and distribution is not None:
        try:
            computed = C.ladder_rank(kd_info["kd"], distribution)
        except ValueError:
            computed = None
        if computed is not None:
            rank = {"tier": computed.tier, "percentile": round(computed.percentile, 2),
                    "next_tier": computed.next_tier, "next_kd": computed.next_tier_kd,
                    "progress": round(computed.progress_percent, 1)}
    dist_info = None
    if distribution is not None:
        dist_info = {"median": distribution.kd[5] if len(distribution.kd) > 5 else None,
                     "p90": distribution.kd[9] if len(distribution.kd) > 9 else None,
                     "sampled": distribution.sampled, "users_total": distribution.users_total}
    return {
        "player_id": str(player.get("id") or ""),
        "username": _required_text(player.get("codmunity_username")) or _required_text(player.get("name")),
        "display_name": _required_text(player.get("display_name") or player.get("name")),
        "uid": _required_text(player.get("codmunity_uid")),
        "profile_url": _required_text(player.get("profile_url")),
        "game": game, "mode": mode, "scope": scope,
        "kd": kd_info["kd"], "kd_source": kd_info["source"],
        "sample_matches": kd_info["sample_matches"],
        "rank": rank, "distribution": dist_info,
        "updated_at": _utcnow_iso(),
    }


def _norm_name(value):
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return "".join(ch for ch in text if ch.isalnum())


def _row_payload(row):
    payload = row.get("payload") if isinstance(row, dict) else None
    try:
        payload = json.loads(payload) if isinstance(payload, str) else payload
    except (ValueError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


def _row_players(row):
    players = row.get("players") if isinstance(row, dict) else None
    try:
        players = json.loads(players) if isinstance(players, str) else players
    except (ValueError, TypeError):
        return []
    return players if isinstance(players, list) else []


def _finite(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def totals_for_player(store, player, game, mode):
    """Local-history aggregates for one roster row (wins, kills, best)."""
    names = {_name for _name in (_norm_name(name) for name in identity_names(player or {})) if _name}
    totals = {"matches": 0, "wins": 0, "losses": 0, "kills": 0.0, "deaths": 0.0,
              "best_kills": None, "best_kills_match": "", "kd": None, "win_rate": None}
    if not names:
        return totals
    expected_game = str(game or "").casefold()
    expected_mode = str(mode or "").casefold()
    for row in store.history() or []:
        if not isinstance(row, dict):
            continue
        match = _row_payload(row)
        if match is None:
            continue
        if str(match.get("game") or "").casefold() != expected_game:
            continue
        actual_mode = str(match.get("modeKey") or match.get("mode") or "").casefold()
        if expected_mode != "multiplayer" and expected_mode not in actual_mode:
            continue
        players = _row_players(row) or _row_players({"players": match.get("players")})
        matched = [p for p in players if isinstance(p, dict) and p.get("verified") is True
                   and _norm_name(str(p.get("name") or "")) in names
                   and isinstance(p.get("stats"), dict)]
        if len(matched) != 1:
            continue
        stats = matched[0]["stats"]
        kills = _finite(stats.get("kills", stats.get("eliminations")))
        deaths = _finite(stats.get("deaths"))
        if kills is None or deaths is None or kills < 0 or deaths < 0:
            continue
        totals["matches"] += 1
        totals["kills"] += kills
        totals["deaths"] += deaths
        if str(match.get("result") or "").casefold() in ("win", "victory", "1st", "1"):
            totals["wins"] += 1
        else:
            totals["losses"] += 1
        if totals["best_kills"] is None or kills > totals["best_kills"]:
            totals["best_kills"] = kills
            totals["best_kills_match"] = str(match.get("matchId") or row.get("id") or "")
    if totals["deaths"] > 0:
        totals["kd"] = totals["kills"] / totals["deaths"]
    if totals["matches"] > 0:
        totals["win_rate"] = totals["wins"] / totals["matches"]
    return totals


def squad_table(store, players, game, mode, scope, sort_by="percentile"):
    """Ranked comparison rows for a squad; missing stats sort last, flagged."""
    rows = []
    for player in players or []:
        snap = snapshot_for_player(store, player, game, mode, scope)
        totals = totals_for_player(store, player, game, mode)
        rows.append({"snapshot": snap, "totals": totals,
                     "available": snap["rank"] is not None})
    def _key(row):
        snap, totals = row["snapshot"], row["totals"]
        if sort_by == "kd":
            value = snap["kd"]
        elif sort_by == "kills":
            value = totals["kills"]
        elif sort_by == "wins":
            value = totals["wins"]
        elif sort_by == "win_rate":
            value = totals["win_rate"]
        elif sort_by == "matches":
            value = totals["matches"]
        else:
            value = snap["rank"]["percentile"] if snap["rank"] else None
        return (value is None, -(value or 0))
    rows.sort(key=_key)
    return rows


def _snapshots(store):
    saved = store.meta("player_stat_snapshots", {}) or {}
    return saved if isinstance(saved, dict) else {}


def snapshot_history(store, player_id, game, mode, scope):
    """Stored snapshots for one player/mode, oldest first."""
    entries = _snapshots(store).get(str(player_id)) or []
    wanted = [(str(game), str(mode), str(scope))]
    return [e for e in entries if isinstance(e, dict)
            and (str(e.get("game")), str(e.get("mode")), str(e.get("scope"))) in wanted]


def record_snapshot(store, player_id, game, mode, scope, min_interval=SNAPSHOT_MIN_INTERVAL_SECONDS):
    """Persist a snapshot point; throttled so routine refreshes add history slowly.

    Returns the stored entry, or None when throttled (same mode recorded
    recently) or when no K/D exists yet.
    """
    player = next((p for p in store.roster() if str(p.get("id")) == str(player_id)), None)
    if player is None:
        return None
    snap = snapshot_for_player(store, player, game, mode, scope)
    if snap["kd"] is None:
        return None
    saved = _snapshots(store)
    entries = [e for e in saved.get(str(player_id), []) if isinstance(e, dict)]
    same = [e for e in entries if (str(e.get("game")), str(e.get("mode")), str(e.get("scope")))
            == (str(game), str(mode), str(scope))]
    if same:
        try:
            last = datetime.fromisoformat(str(same[-1].get("ts") or ""))
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            if (datetime.now(timezone.utc) - last).total_seconds() < min_interval:
                return None
        except (ValueError, TypeError):
            pass
    entry = {"ts": _utcnow_iso(), "game": str(game), "mode": str(mode), "scope": str(scope),
             "kd": snap["kd"], "kd_source": snap["kd_source"],
             "percentile": snap["rank"]["percentile"] if snap["rank"] else None,
             "tier": snap["rank"]["tier"] if snap["rank"] else None,
             "matches": snap["sample_matches"]}
    entries.append(entry)
    saved[str(player_id)] = entries[-SNAPSHOT_LIMIT:]
    store.set_meta("player_stat_snapshots", saved)
    return entry


def _awards(store):
    saved = store.meta("player_stat_awards", {}) or {}
    return saved if isinstance(saved, dict) else {}


def awards_for_player(store, player_id):
    return list(_awards(store).get(str(player_id), []) or [])


def _grant_award(store, player_id, key):
    saved = _awards(store)
    keys = list(saved.get(str(player_id), []) or [])
    if key in keys:
        return False
    keys.append(key)
    saved[str(player_id)] = keys
    store.set_meta("player_stat_awards", saved)
    return True


def _tier_index(tier):
    for index, (_, name) in enumerate(C.LADDER_TIERS):
        if name == tier:
            return index
    return -1


def detect_stat_events(store, player_id, game, mode, scope):
    """Rank promotions and personal-best K/D from snapshot history.

    New awards persist once; repeats return no events (duplicate protection).
    """
    history = snapshot_history(store, player_id, game, mode, scope)
    if len(history) < 2:
        return []
    previous, latest = history[-2], history[-1]
    events = []
    prev_tier, new_tier = previous.get("tier"), latest.get("tier")
    if prev_tier and new_tier and _tier_index(new_tier) > _tier_index(prev_tier):
        key = f"rank_promotion:{game}:{mode}:{new_tier}:{latest.get('ts')}"
        if _grant_award(store, player_id, key):
            events.append({"type": "rank_promotion", "player_id": str(player_id),
                           "game": str(game), "mode": str(mode), "scope": str(scope),
                           "from_tier": prev_tier, "to_tier": new_tier,
                           "kd": latest.get("kd"), "ts": latest.get("ts")})
    prev_best = max((e.get("kd") or 0 for e in history[:-1]), default=0)
    if latest.get("kd") and latest["kd"] > prev_best > 0:
        key = f"pb_kd:{game}:{mode}:{latest['kd']}:{latest.get('ts')}"
        if _grant_award(store, player_id, key):
            events.append({"type": "pb_kd", "player_id": str(player_id),
                           "game": str(game), "mode": str(mode), "scope": str(scope),
                           "kd": latest.get("kd"), "previous_best": prev_best,
                           "ts": latest.get("ts")})
    return events


def _stamp(game, mode, scope):
    return f"{game} {mode} ({scope})"


def format_rank_message(snapshot):
    """Offline-safe /rank body: identity, mode, rank, K/D, next, timestamp."""
    if not isinstance(snapshot, dict) or not snapshot.get("rank"):
        name = (snapshot or {}).get("display_name") or (snapshot or {}).get("username") or "player"
        return (f"{name} — rank unavailable for "
                f"{_stamp(snapshot.get('game'), snapshot.get('mode'), snapshot.get('scope'))} "
                f"(no compatible K/D and community distribution).")
    rank = snapshot["rank"]
    lines = [f"{snapshot.get('display_name') or snapshot.get('username')} — {snapshot.get('game')} {snapshot.get('mode')} ({snapshot.get('scope')})",
             f"Rank: {rank['tier']} (top {rank['percentile']:.1f}%)",
             f"K/D: {snapshot['kd']:.2f} ({snapshot.get('kd_source') or 'tracked'})"]
    if rank.get("next_tier"):
        lines.append(f"Next: {rank['next_tier']} at {rank['next_kd']:.2f} K/D")
    else:
        lines.append("Next: MAX RANK")
    lines.append(f"Updated: {snapshot.get('updated_at') or 'unknown'}")
    return "\n".join(lines)


def format_leaderboard_message(table, game, mode, scope, metric="percentile"):
    """Offline-safe /leaderboard body; unavailable players listed, not ranked."""
    header = f"Squad leaderboard — {game} {mode} ({scope}), by {metric}"
    ranked = [f"{i}. {row['snapshot'].get('display_name') or row['snapshot'].get('username')} — "
              f"{row['snapshot']['rank']['tier']} {row['snapshot']['rank']['percentile']:.1f}% "
              f"({row['snapshot']['kd']:.2f} K/D)"
              for i, row in enumerate([r for r in table if r['available']], 1)]
    missing = [row['snapshot'].get('display_name') or row['snapshot'].get('username')
               for row in table if not row['available']]
    lines = [header] + ranked
    if missing:
        lines.append("Unavailable: " + ", ".join(missing))
    return "\n".join(lines)


def prompt_stat_block(snapshot, match_stats=None):
    """Optional AI Studio prompt lines. Match stats never borrow lifetime values."""
    if not isinstance(snapshot, dict) or not snapshot.get("rank"):
        return ""
    rank = snapshot["rank"]
    source = snapshot.get("kd_source") or "tracked"
    lines = [f"{snapshot.get('display_name') or snapshot.get('username')} · Ladder Rank: {rank['tier']} "
             f"(top {rank['percentile']:.1f}% of {snapshot.get('mode')} players)",
             f"{source.title()} K/D {snapshot['kd']:.2f}" +
             (f" across {snapshot.get('sample_matches')} tracked matches" if source == "tracked" and snapshot.get("sample_matches") else "")]
    if isinstance(match_stats, dict):
        kills = match_stats.get("kills")
        deaths = match_stats.get("deaths")
        if isinstance(kills, (int, float)) and isinstance(deaths, (int, float)) and deaths > 0:
            lines.append(f"This match: {kills:g} kills, {deaths:g} deaths ({kills / deaths:.2f} K/D)")
        elif isinstance(kills, (int, float)):
            lines.append(f"This match: {kills:g} kills")
        if match_stats.get("result"):
            lines.append(f"Result: {match_stats['result']}")
    return "\n".join(line for line in lines if line)
