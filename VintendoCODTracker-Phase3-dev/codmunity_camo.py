"""Local-only Camo Tracker / Loadouts / Leaderboards data layer.



Redraws the Codmunity Desktop catalogue surfaces natively: camo grinds,

weapons, challenges (``camo-page.json``), loadout weapons/attachments

(``loadout-creator.json``), and leaderboard tables (tracker's stored

``codmunity_stats`` snapshot). Progress comes from the tracker's own

``camo_detections`` table plus the Codmunity ``autotrack-feed.json``

mirror. Nothing here needs network, auth, or the desktop app.

"""

from __future__ import annotations



import base64

import json

from pathlib import Path





def _read_json(path, default):

    try:

        value = json.loads(Path(path).read_text(encoding='utf-8-sig'))

    except (OSError, ValueError, UnicodeError):

        return default

    return value





def _as_list(value):

    return list(value) if isinstance(value, list) else []





def _text(value):

    return str(value or '').strip()





def default_cache_dir():

    """Codmunity's local cache root, when the desktop app data exists."""

    import os

    root = Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'CODMunity' / 'cache'

    return root if root.is_dir() else None





def load_camo_catalogue(cache_dir=None):

    """Grinds, weapons and challenges from the cached camo catalogue.



    Never raises: missing or corrupt cache yields empty lists.

    """

    cache = Path(cache_dir) if cache_dir else (default_cache_dir() or Path())

    payload = _read_json(cache / 'camo-page.json', {})

    if not isinstance(payload, dict):

        return {'grinds': [], 'weapons': [], 'challenges': []}

    grinds = []

    for grind in _as_list(payload.get('camoGrinds')):

        if not isinstance(grind, dict):

            continue

        config = grind.get('camoConfig') if isinstance(grind.get('camoConfig'), dict) else {}

        camos = []
        for camo in _as_list(config.get('camos')):
            if not isinstance(camo, dict) or not _text(camo.get('name')):
                continue
            order = camo.get('order')
            camos.append({'name': _text(camo.get('name')),
                          'order': order if isinstance(order, (int, float)) and not isinstance(order, bool) else 999,
                          'image': _text(camo.get('image')),
                          'title': _text(camo.get('title') or camo.get('name'))})
        camos.sort(key=lambda c: c['order'])

        grinds.append({'id': _text(grind.get('id') or grind.get('_id')),

                       'name': _text(grind.get('name')),

                       'title': _text(grind.get('title') or grind.get('name')),

                       'save_field': _text(grind.get('saveField')),

                       'game': _text(grind.get('game')),

                       'grindKind': _text(grind.get('grindKind')),

                       'camos': camos})

    weapons = []

    for weapon in _as_list(payload.get('weapons')):

        if not isinstance(weapon, dict):

            continue

        name = _text(weapon.get('WeaponName') or weapon.get('name'))

        if not name:

            continue

        popularity = weapon.get('popularityByGame')
        weapons.append({'name': name,

                        'WeaponName': name,

                        'category': _text(weapon.get('Category')),

                        'Category': _text(weapon.get('Category')),

                        'unlock': _text(weapon.get('Unlock')),

                        'Unlock': _text(weapon.get('Unlock')),

                        'Icon': _text(weapon.get('Icon')),

                        'popularityByGame': popularity if isinstance(popularity, dict) else {},

                        'game': _text(weapon.get('game') or weapon.get('appGame'))})

    challenges = []

    for challenge in _as_list(payload.get('challenges')):

        if not isinstance(challenge, dict):

            continue

        challenges.append({'weapon': _text(challenge.get('WeaponName')),

                           'camo': _text(challenge.get('Name')),

                           'challenge': _text(challenge.get('Challenge')),

                           'challenge_wz': _text(challenge.get('ChallengeWZ')),

                           'ChallengeWZ': _text(challenge.get('ChallengeWZ')),

                           'ChallengeDMZ': _text(challenge.get('ChallengeDMZ')),

                           'grind': _text(challenge.get('CamoGrind')),

                           'Order': challenge.get('Order'),

                           'Image': _text(challenge.get('Image')),

                           'subcategory': _text(challenge.get('SubCategory'))})

    return {'grinds': grinds, 'weapons': weapons, 'challenges': challenges}





CAMO_GAMES = (("mw4", "MW4"), ("warzone", "WARZONE"))

TRACKED_CAMO_GAMES = ("mw4", "warzone-2")



CAMO_GAME_GRINDS = {

    "mw4": {"mw4", "mw4-prestige", "mw4-mastery"},

    "warzone": {"wz-mw4", "wz-mw4-mastery", "wz-mastery-bo7", "wz-mastery"},

}





def _grind_game_map(catalogue):

    """Grind name -> catalogue game code."""

    mapping = {}

    for grind in (catalogue or {}).get('grinds', []) or []:

        if isinstance(grind, dict) and grind.get('name'):

            mapping[grind['name']] = grind.get('game') or ''

    return mapping





def game_tabs(catalogue):

    """(code, label) game tabs that actually have grinds, in catalogue order."""

    codes = ["mw4", "warzone-2", "bo7", "bo6", "mw2019", "mw3", "blackopsroyale",

             "mw2", "bo1", "bo2", "dmz"]

    labels = {"mw4": "MW4", "warzone-2": "WARZONE", "bo7": "BO7", "bo6": "BO6",

              "mw2019": "MW19", "mw3": "MW3", "blackopsroyale": "BLACK OPS ROYALE",

              "mw2": "MW2", "bo1": "BLACK OPS 1", "bo2": "BLACK OPS 2", "dmz": "DMZ"}

    present = {g.get('game') for g in (catalogue or {}).get('grinds', []) or []

               if isinstance(g, dict) and g.get('game')}

    tabs = [(code, labels[code]) for code in codes if code in present]

    for code in sorted(present - {c for c, _ in tabs}):

        tabs.append((code, code.upper()))

    return tabs


def tracked_game_tabs(catalogue):

    """Game tabs shown in the tracker: MW4 and Warzone only."""

    return [tab for tab in game_tabs(catalogue) if tab[0] in TRACKED_CAMO_GAMES]





def grinds_for_game(catalogue, game):

    """Grinds belonging to one tab; unknown games yield []."""

    wanted = set(CAMO_GAME_GRINDS.get(game, ())) | {game}

    seen, out = set(), []

    for grind in (catalogue or {}).get('grinds', []) or []:

        if not isinstance(grind, dict):

            continue

        if grind.get('game') == game or grind.get('name') in wanted:

            if grind.get('name') not in seen:

                seen.add(grind.get('name'))

                out.append(grind)

    return out





def challenges_for_game(catalogue, game):

    """Challenges for one tab, with Warzone wording preferred on Warzone."""

    wanted = set(CAMO_GAME_GRINDS.get(game, ()))

    game_map = _grind_game_map(catalogue)

    rows = []

    for entry in (catalogue or {}).get('challenges', []) or []:

        if not isinstance(entry, dict):

            continue

        if entry.get('grind') not in wanted and game_map.get(entry.get('grind')) != game:

            continue

        row = dict(entry)

        if game in ("warzone", "warzone-2") and row.get('challenge_wz'):

            row['challenge'] = row['challenge_wz']

        rows.append(row)

    return rows





def progress_map(store, autotrack_feed_path=None):

    """{(weapon, camo): {'state', 'at'}} from tracker rows + feed mirror.



    Tracker ``camo_detections`` win over the Codmunity feed file, and the

    newest row wins, so a later Undo (dismissed) overrides an earlier save.

    """

    progress = {}

    if autotrack_feed_path is not None:

        for entry in _as_list(_read_json(autotrack_feed_path, [])):

            if not isinstance(entry, dict):

                continue

            key = (_text(entry.get('weapon')).casefold(), _text(entry.get('camo')).casefold())

            if not all(key):

                continue

            progress[key] = {'state': _text(entry.get('state')) or 'saved',

                             'at': entry.get('at')}

    if store is not None:

        try:

            rows = store.camo_detections(limit=200)

        except Exception:

            rows = []

        for row in reversed(rows or []):

            if not isinstance(row, dict):

                continue

            key = (_text(row.get('weapon')).casefold(), _text(row.get('camo')).casefold())

            if all(key):

                progress[key] = {'state': _text(row.get('state')) or 'saved',

                                 'at': row.get('at')}

    return progress





def mark_progress(store, weapon, camo, state, grind='mw4'):

    """Record a manual camo tick; returns the detection id or ''."""

    if store is None or not _text(weapon) or not _text(camo):

        return ''

    if state not in ('saved', 'dismissed', 'already'):

        raise ValueError('state must be saved, dismissed or already')

    try:

        det_id = store.save_camo_detection({'weapon': _text(weapon), 'camo': _text(camo),

                                             'grind': _text(grind) or 'mw4',

                                             'state': state, 'changed': [], 'read': ['manual tick']})

        store.record_tracking_feed('camo', 'saved' if state == 'saved' else 'skipped',

                                   '', {'parser': 'manual', 'detection_id': det_id})

        return det_id

    except Exception:

        return ''





def linked_account(snapshot):

    """Local account linkage from the cached stats folder, or {}.



    The per-user cache folder is ``u-<uid>``; no login or network needed.

    """

    user_dir = (snapshot or {}).get('user_dir', '') if isinstance(snapshot, dict) else ''

    base = str(user_dir).replace('\\', '/').rstrip('/').rsplit('/', 1)[-1]

    uid = base[2:] if base.startswith('u-') and len(base) > 2 else ''

    if not uid:

        return {}

    return {'uid': uid, 'user_dir': str(user_dir)}





PAIR_URL = 'https://codmunity.gg/desktop-pair/'

SITE_URL = 'https://codmunity.gg/'





def leaderboard_tables(snapshot):

    """{board_key: {'label', 'entries', 'count'}} from the stored snapshot."""

    tables = {}

    boards = (snapshot or {}).get('leaderboards', {}) if isinstance(snapshot, dict) else {}

    labels = {'mw4_multiplayer_kd': 'MW4 Multiplayer K/D',

              'mw4_multiplayer_wins': 'MW4 Multiplayer Wins',

              'warzone_battle_royale': 'Warzone Battle Royale K/D',

              'warzone_resurgence': 'Warzone Resurgence K/D'}

    for key, info in boards.items():

        if not isinstance(info, dict):

            continue

        entries = [e for e in (info.get('entries') or []) if isinstance(e, dict)]

        tables[key] = {'label': labels.get(key, str(key)),

                       'entries': entries, 'count': len(entries)}

    return tables





BOARD_METRICS = (("kd", "K/D ratio"), ("wins", "Wins"), ("kills", "Kills"), ("time", "Time played"))



WARZONE_MODES = (("resurgence", "Resurgence"), ("battle_royale", "Battle Royale"))





def board_for(game, mode, snapshot):

    """(key, label, entries) for one game/mode from the cached snapshot."""

    tables = leaderboard_tables(snapshot)

    if game == "warzone":

        key = "warzone_resurgence" if mode == "resurgence" else "warzone_battle_royale"

        label = f"Warzone · {dict(WARZONE_MODES).get(mode, mode)}"

    else:

        key = "mw4_multiplayer_kd"

        label = "MW4 · Multiplayer"

    info = tables.get(key) or {}

    return key, label, list(info.get("entries") or [])





def metric_value(entry, metric):

    """Numeric cell value for one entry and metric, or None."""

    if not isinstance(entry, dict):

        return None

    key = {"kd": "value", "wins": "wins", "kills": "kills", "time": "timePlayedS"}.get(metric)

    if key is None:

        return None

    raw = entry.get(key)

    return raw if isinstance(raw, (int, float)) and not isinstance(raw, bool) else None





def sort_entries(entries, metric):

    """Entries sorted best-first by metric; entries without a value go last."""

    scored = [(metric_value(e, metric), e) for e in entries or []]

    scored.sort(key=lambda item: (item[0] is None, -(item[0] or 0)))

    return [entry for _, entry in scored]





def format_count(value):

    """1,208 style counts; em dash when unknown."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):

        return "—"

    return f"{int(value):,}"





def format_time(seconds):

    """1h 02m style durations; em dash when unknown."""

    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):

        return "—"

    total = max(0, int(seconds))

    hours, remainder = divmod(total, 3600)

    if hours:

        return f"{hours}h {remainder // 60:02d}m"

    return f"{remainder // 60}m {total % 60:02d}s"





def medal(rank):

    """Medal tier for rank 1-3 display, else None."""

    try:

        rank = int(rank)

    except (TypeError, ValueError):

        return None

    return {1: "gold", 2: "silver", 3: "bronze"}.get(rank)





def you_row_stats(snapshot, game, mode):

    """Local lifetime numbers for the YOU row; Nones when no snapshot."""

    personal = snapshot.get("personal", {}) if isinstance(snapshot, dict) else {}

    stats = {}

    kd = None

    if game == "warzone":

        entry = (personal.get("warzone") or {}).get(mode) or {}

        if isinstance(entry.get("stats"), dict):

            stats = entry["stats"]

            kd = stats.get("kd")

    else:

        multi = personal.get("multiplayer") or {}

        if isinstance(multi.get("stats"), dict):

            stats = multi["stats"]

        derived = multi.get("derived") if isinstance(multi.get("derived"), dict) else {}

        kd = derived.get("kd")

    kills = stats.get("kills")

    deaths = stats.get("deaths")

    if (not isinstance(kd, (int, float)) or isinstance(kd, bool)) and isinstance(kills, (int, float)) and deaths:

        kd = round(kills / deaths, 2) if deaths else None

    if isinstance(kd, bool):

        kd = None

    games = stats.get("games_played", stats.get("games"))

    return {"kd": kd, "wins": stats.get("wins"), "games": games, "kills": kills,

            "has_stats": any(v is not None for v in (kd, stats.get("wins"), games, kills))}





def tier_order(catalogue, grind_name):
    """Ordered completionist camo names for one grind (catalogue order)."""
    for grind in (catalogue or {}).get('grinds', []) or []:
        if isinstance(grind, dict) and grind.get('name') == grind_name:
            return [c['name'] for c in grind.get('camos', []) or []
                    if isinstance(c, dict) and _text(c.get('name'))]
    return []


def tier_images(catalogue, grind_name):
    """{camo name: artwork url} for one grind, for card art."""
    for grind in (catalogue or {}).get('grinds', []) or []:
        if isinstance(grind, dict) and grind.get('name') == grind_name:
            return {c['name']: c.get('image', '') for c in grind.get('camos', []) or []
                    if isinstance(c, dict) and _text(c.get('name'))}
    return {}



def _weapon_meta(catalogue, weapon_name, game):

    for weapon in (catalogue or {}).get('weapons', []) or []:

        if isinstance(weapon, dict) and _text(weapon.get('WeaponName') or weapon.get('name')).casefold() == weapon_name.casefold():

            if not game or _text(weapon.get('game')) in ('', game):

                return weapon

    for weapon in (catalogue or {}).get('weapons', []) or []:

        if isinstance(weapon, dict) and _text(weapon.get('WeaponName') or weapon.get('name')).casefold() == weapon_name.casefold():

            return weapon

    return {}





def weapon_rows(catalogue, progress, game, grind_name):

    """Per-weapon camo strips for one grind: status, locks, next camo.



    ``progress`` maps (weapon, camo) -> {'state': ...}; saved/already count

    as done. A tier locks while any earlier tier on that weapon is undone;

    Divergence additionally needs 33 Harmonic Break completions game-wide.

    """

    order = tier_order(catalogue, grind_name)

    rank = {name: index for index, name in enumerate(order)}

    by_weapon = {}

    for entry in (catalogue or {}).get('challenges', []) or []:

        if not isinstance(entry, dict) or entry.get('grind') != grind_name:

            continue

        weapon = _text(entry.get('weapon'))

        if not weapon:

            continue

        by_weapon.setdefault(weapon, []).append(entry)



    def _order(entry):

        order = entry.get('Order')

        if isinstance(order, (int, float)) and not isinstance(order, bool):

            return order

        return 9999



    rows = []

    for weapon in sorted(by_weapon):

        challenges = sorted(by_weapon[weapon],

                            key=lambda e: (_order(e), rank.get(e.get('camo'), 999)))

        camos = []

        for entry in challenges:

            camo = _text(entry.get('camo'))

            done = progress.get((weapon.casefold(), camo.casefold()), {}).get('state') in ('saved', 'already')

            earlier = [c for c in challenges if _order(c) < _order(entry)]

            locked = any(not progress.get((weapon.casefold(), _text(c.get('camo')).casefold()), {}).get('state') in ('saved', 'already')

                         for c in earlier)

            if camo == 'Divergence':

                locked = True

            camos.append({'name': camo, 'order': entry.get('Order'), 'done': done,

                          'locked': locked and not done,

                          'challenge': _text(entry.get('challenge')),

                          'challenge_dm': _text(entry.get('ChallengeDMZ')),

                          'challenge_wz': _text(entry.get('ChallengeWZ')),

                          'image': _text(entry.get('Image')),
                        'subcategory': _text(entry.get('subcategory'))})

        done_count = sum(1 for c in camos if c['done'])

        unlocked_todo = [c for c in camos if not c['done'] and not c['locked']]

        meta = _weapon_meta(catalogue, weapon, game)

        popularity = meta.get('popularityByGame') if isinstance(meta.get('popularityByGame'), dict) else {}

        rows.append({'weapon': weapon, 'category': _text(meta.get('Category')),

                     'unlock': _text(meta.get('Unlock')), 'icon': _text(meta.get('Icon')),

                     'popularity': popularity.get(game, 0) if isinstance(popularity, dict) else 0,

                     'camos': camos, 'done': done_count, 'total': len(camos),

                     'next': unlocked_todo[0] if unlocked_todo else None})

    return rows





def class_completion(rows, camo_name):

    """(weapons done, weapons total) for one camo across a weapon list."""

    total = len(rows)

    done = sum(1 for row in rows

               if any(c['name'].casefold() == camo_name.casefold() and c['done'] for c in row.get('camos', [])))

    return done, total





def mastery_progress(catalogue, progress, game):

    """(done, total) mastery-camo completions for one game."""

    mastery_grinds = {g.get('name') for g in (catalogue or {}).get('grinds', []) or []

                      if isinstance(g, dict) and g.get('game') == game

                      and _text(g.get('grindKind')) == 'account-mastery'}

    total = done = 0

    for entry in (catalogue or {}).get('challenges', []) or []:

        if not isinstance(entry, dict) or entry.get('grind') not in mastery_grinds:

            continue

        total += 1

        key = (_text(entry.get('weapon')).casefold(), _text(entry.get('camo')).casefold())

        if progress.get(key, {}).get('state') in ('saved', 'already'):

            done += 1

    return done, total





def mastery_grind_for_game(catalogue, game):

    """First account-mastery grind for one game, or {}."""

    for grind in (catalogue or {}).get('grinds', []) or []:

        if isinstance(grind, dict) and grind.get('game') == game \
                and _text(grind.get('grindKind')) == 'account-mastery':

            return grind

    return {}





def last_detection(store, feed_path=None):

    """Latest {weapon, camo, at} across tracker rows and the feed mirror."""

    best = None

    if store is not None:

        try:

            rows = store.camo_detections(limit=200)

        except Exception:

            rows = []

        for row in rows or []:

            if isinstance(row, dict) and row.get('at'):

                if best is None or row['at'] > (best.get('at') or 0):

                    best = {'weapon': row.get('weapon', ''), 'camo': row.get('camo', ''), 'at': row['at']}

    for entry in _as_list(_read_json(feed_path, [])) if feed_path else []:

        if isinstance(entry, dict) and entry.get('at'):

            at = entry['at'] / 1000.0 if entry['at'] > 1e12 else entry['at']

            if best is None or at > (best.get('at') or 0):

                best = {'weapon': entry.get('weapon', ''), 'camo': entry.get('camo', ''), 'at': at}

    return best or {}





def ago_text(timestamp):

    """'46 h ago' style relative labels; '' when unknown."""

    import time as _time

    if not isinstance(timestamp, (int, float)):

        return ''

    delta = max(0, _time.time() - float(timestamp))

    if delta < 3600:

        return f"{int(delta // 60)} m ago"

    if delta < 172800:

        return f"{int(delta // 3600)} h ago"

    return f"{int(delta // 86400)} d ago"





LIFETIME_GAMES = (("mw4", "multiplayer", "MW4 Multiplayer"),

                  ("warzone", "resurgence", "Warzone · Resurgence"),

                  ("warzone", "battle_royale", "Warzone · Battle Royale"))





def lifetime_stats(snapshot, game, mode):

    """Lifetime stats dict for one game/mode from the cached snapshot, else {}."""

    personal = snapshot.get("personal", {}) if isinstance(snapshot, dict) else {}

    if game == "warzone":

        entry = (personal.get("warzone") or {}).get(mode) or {}

        stats = entry.get("stats") if isinstance(entry.get("stats"), dict) else {}

    else:

        multi = personal.get("multiplayer") or {}

        stats = multi.get("stats") if isinstance(multi.get("stats"), dict) else {}

    return dict(stats) if stats else {}





def lifetime_kd(stats):

    """K/D from explicit kd or kills/deaths, else None."""

    kd = stats.get("kd")

    if isinstance(kd, (int, float)) and not isinstance(kd, bool):

        return round(float(kd), 2)

    kills, deaths = stats.get("kills"), stats.get("deaths")

    if isinstance(kills, (int, float)) and isinstance(deaths, (int, float)) and deaths:

        return round(kills / deaths, 2)

    return None





def format_duration(seconds):

    """33h 36m style durations; em dash when unknown."""

    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):

        return "—"

    total = max(0, int(seconds))

    hours, remainder = divmod(total, 3600)

    if hours:

        return f"{hours}h {remainder // 60:02d}m"

    minutes, secs = divmod(remainder, 60)

    if minutes:

        return f"{minutes}m {secs:02d}s"

    return f"{secs}s"





def summary_line(snapshot, game, mode):

    """One-line lifetime recap for sharing; '' when no stats."""

    stats = lifetime_stats(snapshot, game, mode)

    if not stats:

        return ""

    label = next((text for g, m, text in LIFETIME_GAMES if (g, m) == (game, mode)), f"{game} {mode}")

    kd = lifetime_kd(stats)

    parts = [f"{label}: "]

    if kd is not None:

        parts.append(f"{kd:.2f} K/D")

    wins, losses, games = stats.get("wins"), stats.get("losses"), stats.get("games_played", stats.get("games"))

    if isinstance(wins, (int, float)) and isinstance(losses, (int, float)):

        parts.append(f"{int(wins)}W/{int(losses)}L")

    elif isinstance(wins, (int, float)):

        parts.append(f"{int(wins)} wins")

    if isinstance(games, (int, float)):

        parts.append(f"{int(games)} games")

    if isinstance(stats.get("kills"), (int, float)):

        parts.append(f"{int(stats['kills']):,} kills")

    return ", ".join([parts[0].rstrip(": ") + ": " + parts[1]] + parts[2:]) if len(parts) > 1 else ""





def share_urls(profile_url, text):

    """Share targets mirrored from the desktop app; all open in the browser."""

    from urllib.parse import quote

    url, msg = quote(profile_url, safe=""), quote(text, safe="")

    return {"X": f"https://x.com/intent/post?text={msg}%20{url}",

            "Reddit": f"https://www.reddit.com/submit?url={url}&title={msg}",

            "Facebook": f"https://www.facebook.com/sharer/sharer.php?u={url}",

            "WhatsApp": f"https://wa.me/?text={msg}%20{url}"}





def cache_sizes(data_dir):

    """{folder: bytes} for the tracker's downloaded image caches."""

    sizes = {}

    for name in ("camo-images", "avatars"):

        folder = Path(data_dir) / name

        total = 0

        if folder.is_dir():

            for path in folder.rglob("*"):

                try:

                    if path.is_file():

                        total += path.stat().st_size

                except OSError:

                    continue

        sizes[name] = total

    return sizes





def clear_caches(data_dir):

    """Delete downloaded image caches; returns removed file count."""

    removed = 0

    for name in ("camo-images", "avatars"):

        folder = Path(data_dir) / name

        if not folder.is_dir():

            continue

        for path in sorted(folder.rglob("*")):

            try:

                if path.is_file():

                    path.unlink()

                    removed += 1

            except OSError:

                continue

    return removed





def identity_hits(snapshot):

    """Leaderboard identity hits for the local player, or []."""

    hits = (snapshot or {}).get('identity', {}).get('hits', []) if isinstance(snapshot, dict) else []

    return [h for h in (hits or []) if isinstance(h, dict)]












