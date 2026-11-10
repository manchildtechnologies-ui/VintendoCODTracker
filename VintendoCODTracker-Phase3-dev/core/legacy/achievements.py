"""Local achievement evaluation and persistence for confirmed CODMunity matches."""
import datetime as dt
import hashlib
import json
import os
from pathlib import Path

DEFAULTS = {
    "kill_25": 25, "kill_50": 50, "win_3": 3, "win_5": 5,
    "kd_ratio": 3.0, "kd_min_kills": 10, "session_kills": 100,
    "session_gap_minutes": 120,
    # The source currently does not provide confirmed personal Warzone matches.
    "warzone_enabled": False,
}
NAMES = {
    "kill_25": ("25 Kill Club", "At least {threshold} kills in one multiplayer match."),
    "kill_50": ("Absolute Menace", "At least {threshold} kills in one multiplayer match."),
    "win_3": ("Hat Trick", "Three consecutive confirmed wins."),
    "win_5": ("Unstoppable", "Five consecutive confirmed wins."),
    "kd_3": ("Sharpshooter", "K/D of at least {threshold} with {minimum}+ kills."),
    "session_100": ("Killing Machine", "{threshold} confirmed kills in one session."),
    "personal_best": ("Personal Best", "New verified single-match kill record."),
}

WARZONE_NAMES = {
    "warzone_win": ("WARZONE CHAMPION", "Win a confirmed Warzone match."),
    "warzone_top5": ("TOP 5 FINISHER", "Finish in the top five."),
    "warzone_kill10": ("DOUBLE DIGITS", "10+ eliminations in one match."),
    "warzone_kill20": ("LOBBY DESTROYER", "20+ eliminations in one match."),
    "warzone_streak2": ("VICTORY STREAK", "Two consecutive confirmed Warzone wins."),
}
WARZONE_IMAGES = {
    "warzone_win": "warzone_win.jpg",
    "warzone_top5": "warzone_top5.jpg",
    "warzone_kill10": "warzone_kill10.jpg",
    "warzone_kill20": "warzone_kill20.jpg",
    "warzone_streak2": "warzone_streak2.jpg",
}

def is_warzone_candidate(match):
    """Conservatively quarantine explicitly Warzone-like records from MP handling."""
    if not isinstance(match, dict):
        return False
    game = str(match.get("game", "")).strip().casefold().replace(" ", "_")
    mode = str(match.get("mode", match.get("modeKey", ""))).strip().casefold().replace(" ", "_")
    return game in {"warzone", "call_of_duty_warzone", "wz"} or mode in {
        "battle_royale", "resurgence", "warzone_battle_royale", "warzone_resurgence"
    } or mode.startswith("warzone_")

def _warzone_timestamp(match):
    raw = match.get("playedAt")
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return None
        return parsed.timestamp()
    except (ValueError, TypeError, OverflowError):
        return None

def is_confirmed_warzone_match(match):
    """Only accept a saved-looking, explicit, uniquely identified completed match."""
    if not isinstance(match, dict):
        return False
    game = str(match.get("game", "")).strip().casefold()
    mode = str(match.get("mode", "")).strip().casefold().replace(" ", "_")
    result = str(match.get("result", "")).strip().casefold()
    return (
        game == "warzone"
        and mode in {"battle_royale", "resurgence"}
        and result in {"win", "loss"}
        and _warzone_timestamp(match) is not None
    )

def _new_warzone_history():
    return {"initialized": False, "processed_match_ids": [], "unlocks": [],
            "last_confirmed": None, "win_streak": 0}

def bootstrap_warzone(history, matches):
    """Mark existing confirmed Warzone records as baseline; never announce them retroactively."""
    wz = history.setdefault("warzone", _new_warzone_history())
    if wz.get("initialized"):
        return wz
    valid = sorted((m for m in matches if is_confirmed_warzone_match(m)), key=_warzone_timestamp)
    wz["processed_match_ids"] = [stable_id(m) for m in valid]
    streak = 0
    last = None
    for match in valid:
        seq = match.get("sourceSequence")
        seq_ok = isinstance(seq, int) and not isinstance(seq, bool) and seq > 0
        prev_seq = last.get("source_sequence") if last else None
        if not seq_ok or prev_seq is None or seq != prev_seq + 1:
            streak = 1 if match["result"].casefold() == "win" and seq_ok else 0
        elif match["result"].casefold() == "win" and last.get("result") == "win":
            streak += 1
        elif match["result"].casefold() == "win":
            streak = 1
        else:
            streak = 0
        last = {"match_id": stable_id(match), "result": match["result"].casefold(),
                "timestamp": _warzone_timestamp(match), "source_sequence": seq if seq_ok else None}
    wz["last_confirmed"] = last
    wz["win_streak"] = streak
    wz["initialized"] = True
    return wz

def evaluate_warzone(history, match, config=None):
    """Evaluate one normalized Warzone match. Exact fields only; unknown stats stay unknown."""
    if not is_confirmed_warzone_match(match):
        return []
    wz = history.setdefault("warzone", _new_warzone_history())
    if not wz.get("initialized"):
        return []
    mid = stable_id(match)
    if mid in wz.setdefault("processed_match_ids", []):
        return []
    ts = _warzone_timestamp(match)
    previous = wz.get("last_confirmed")
    if previous and ts <= previous.get("timestamp", float("-inf")):
        wz["processed_match_ids"].append(mid)
        return []

    stats = match.get("stats") if isinstance(match.get("stats"), dict) else {}
    earned = []
    now = dt.datetime.now(dt.timezone.utc).isoformat()

    def add(aid, statistic):
        if any(item.get("achievement_id") == aid for item in wz.get("unlocks", [])):
            return
        name, description = WARZONE_NAMES[aid]
        available = {key: stats[key] for key in ("eliminations", "deaths", "damage") if key in stats and isinstance(stats[key], (int, float)) and not isinstance(stats[key], bool)}
        placement = match.get("placement", stats.get("placement"))
        if isinstance(placement, int) and not isinstance(placement, bool):
            available["placement"] = placement
        available.update(statistic)
        earned.append({"achievement_id": aid, "name": name, "description": description,
                       "statistics": available, "match_id": mid,
                       "match_context": {"mode": match["mode"], "result": match["result"].casefold(),
                                         "playedAt": match["playedAt"]},
                       "unlocked_at": now, "delivery_status": "pending"})

    result = match["result"].casefold()
    if result == "win":
        add("warzone_win", {"result": "win"})

    placement = match.get("placement", stats.get("placement"))
    if isinstance(placement, int) and not isinstance(placement, bool) and placement > 0 and placement <= 5:
        add("warzone_top5", {"placement": placement})

    eliminations = stats.get("eliminations", stats.get("kills"))
    if isinstance(eliminations, int) and not isinstance(eliminations, bool) and eliminations >= 0:
        if eliminations >= 10:
            add("warzone_kill10", {"eliminations": eliminations})
        if eliminations >= 20:
            add("warzone_kill20", {"eliminations": eliminations})

    sequence = match.get("sourceSequence")
    sequence_ok = isinstance(sequence, int) and not isinstance(sequence, bool) and sequence > 0
    previous_sequence = previous.get("source_sequence") if previous else None
    if sequence_ok and previous_sequence is not None and sequence == previous_sequence + 1:
        streak = wz.get("win_streak", 0) + 1 if result == "win" and previous.get("result") == "win" else (1 if result == "win" else 0)
    else:
        # Without an explicit contiguous source sequence, a streak cannot be verified.
        streak = 1 if sequence_ok and result == "win" else 0
    if streak >= 2:
        add("warzone_streak2", {"consecutive_wins": streak, "source_sequence": sequence})

    wz["win_streak"] = streak
    wz["last_confirmed"] = {"match_id": mid, "result": result, "timestamp": ts,
                            "source_sequence": sequence if sequence_ok else None}
    wz["processed_match_ids"].append(mid)
    wz.setdefault("unlocks", []).extend(earned)
    return earned

def stable_id(match):
    if match.get("matchId"):
        return str(match["matchId"])
    return hashlib.sha256(json.dumps(match, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:24]

def _atomic(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(value, indent=2), encoding="utf-8")
    os.replace(tmp, path)

def read_config(path):
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    return {**DEFAULTS, **(raw if isinstance(raw, dict) else {})}

def new_history():
    return {"version": 1, "initialized": False, "baseline_max_kills": None,
            "processed_match_ids": [], "unlocks": [], "last_confirmed": None,
            "streak": 0, "session": {"start": None, "kills": 0, "match_ids": []}}

def load_history(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else new_history()
    except (OSError, ValueError):
        return new_history()

def _valid_result(m):
    return isinstance(m, dict) and str(m.get("result", "")).lower() in ("win", "loss")

def _kills(m):
    value = (m.get("stats") or {}).get("kills") if isinstance(m.get("stats"), dict) else None
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None

def initialize(history, matches):
    """First-run baseline: mark all present confirmed records seen and seed verified kill PB."""
    confirmed = [m for m in matches if _valid_result(m)]
    vals = [_kills(m) for m in confirmed]
    history["baseline_max_kills"] = max((v for v in vals if v is not None), default=None)
    history["processed_match_ids"] = sorted({stable_id(m) for m in confirmed})
    history["initialized"] = True
    _atomic(history["_path"], history)

def _played(m):
    try:
        raw = m.get("playedAt")
        if not raw: return None
        return dt.datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError, OverflowError):
        return None

def evaluate(history, match, config):
    """Return newly earned unlock entries; incomplete outcome records are skipped without streak impact."""
    if not _valid_result(match): return []
    mid = stable_id(match)
    if mid in history.setdefault("processed_match_ids", []): return []
    k = _kills(match)
    cfg = {**DEFAULTS, **(config or {})}
    stamp = dt.datetime.now(dt.timezone.utc).isoformat()
    earned = []
    def add(aid, stat):
        if any(x["achievement_id"] == aid for x in history.get("unlocks", [])): return
        title, description = NAMES[aid]
        earned.append({"achievement_id": aid, "name": title,
                       "description": description.format(threshold=cfg.get(aid, cfg.get("session_kills")), minimum=cfg.get("kd_min_kills")),
                       "statistics": stat, "match_id": mid, "match_context": {"mode": match.get("modeKey"), "result": match.get("result"), "playedAt": match.get("playedAt")},
                       "unlocked_at": stamp, "delivery_status": "pending"})
    if k is not None:
        if k >= int(cfg["kill_25"]): add("kill_25", {"kills": k, "threshold": cfg["kill_25"]})
        if k >= int(cfg["kill_50"]): add("kill_50", {"kills": k, "threshold": cfg["kill_50"]})
        deaths = (match.get("stats") or {}).get("deaths")
        if isinstance(deaths, (int, float)) and not isinstance(deaths, bool) and deaths > 0 and k >= int(cfg["kd_min_kills"]):
            ratio = k / deaths
            if ratio >= float(cfg["kd_ratio"]): add("kd_3", {"kills": k, "deaths": deaths, "ratio": round(ratio, 3), "threshold": cfg["kd_ratio"]})
        if history.get("baseline_max_kills") is not None and k > history["baseline_max_kills"]:
            add("personal_best", {"kills": k, "previous_record": history["baseline_max_kills"]})
        else:
            add("personal_best", {"kills": k, "previous_record": history.get("personal_best_record", history["baseline_max_kills"])}) if history.get("personal_best_record") is not None and k > history["personal_best_record"] else None
        if history.get("baseline_max_kills") is None:
            history["baseline_max_kills"] = k
        history["personal_best_record"] = max(k, history.get("personal_best_record", history.get("baseline_max_kills", k)))
        ts = _played(match)
        sess = history.setdefault("session", {"start": None, "kills": 0, "match_ids": []})
        prev_ts = history.get("last_confirmed", {}).get("timestamp") if history.get("last_confirmed") else None
        gap = (ts - prev_ts) if ts is not None and prev_ts is not None else None
        if gap is None or gap < 0 or gap > int(cfg["session_gap_minutes"]) * 60:
            sess.update(start=match.get("playedAt"), kills=0, match_ids=[])
        sess["kills"] += k
        sess["match_ids"].append(mid)
        if sess["kills"] >= int(cfg["session_kills"]): add("session_100", {"session_kills": sess["kills"], "session_start": sess["start"], "threshold": cfg["session_kills"]})
    # Incomplete records neither extend nor break the result streak.
    # A confirmed result with missing kills remains eligible to update the streak.
    previous = history.get("last_confirmed")
    history["streak"] = history.get("streak", 0) + 1 if str(match["result"]).lower() == "win" and previous and previous.get("result") == "win" else (1 if str(match["result"]).lower() == "win" else 0)
    if history["streak"] >= int(cfg["win_3"]): add("win_3", {"consecutive_wins": history["streak"], "threshold": cfg["win_3"]})
    if history["streak"] >= int(cfg["win_5"]): add("win_5", {"consecutive_wins": history["streak"], "threshold": cfg["win_5"]})
    history["last_confirmed"] = {"match_id": mid, "result": str(match["result"]).lower(), "timestamp": _played(match)}
    history.setdefault("processed_match_ids", []).append(mid)
    history.setdefault("unlocks", []).extend(earned)
    return earned

def save_history(path, history):
    _atomic(path, history)

def bootstrap(path, matches):
    history = load_history(path)
    if not history.get("initialized"):
        confirmed = [m for m in matches if _valid_result(m)]
        history["baseline_max_kills"] = max((_kills(m) for m in confirmed if _kills(m) is not None), default=None)
        history["processed_match_ids"] = sorted({stable_id(m) for m in confirmed})
        history["initialized"] = True
        save_history(path, history)
    return history
