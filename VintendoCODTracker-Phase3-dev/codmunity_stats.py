"""CODMunity stats-cache importer for the Vintendo COD Tracker.

The CODMunity desktop app caches more than per-match records under
``%LOCALAPPDATA%\\CODMunity\\stats``: global percentile benchmarks,
KD leaderboards, personal lifetime snapshots and a full event feed.
The tracker historically read only ``matches.json`` (plus saved Warzone
feed matches). This module harvests the rest into one JSON snapshot
stored in the tracker's ``meta`` table, and turns it into benchmark
context for Discord recaps and poster prompts.

Nothing here posts, sends or re-queues matches: imported match records
are added with the ``baseline`` status, which the tracker retains
without reposting. Empty cache files (``data: null``) are recorded as
empty sources instead of being treated as errors.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

SNAPSHOT_KEY = 'codmunity_stats'
SNAPSHOT_VERSION = 1

# CODMunity distribution arrays hold 12 quantile points. The tracker's
# cached files anchor the grid: index 5 always equals kdMedian and
# index 9 always equals kdP90, which fixes the grid below.
QUANTILE_GRID = (0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 95, 100)

GLOBAL_FILES = {
    'mw4_multiplayer': 'global.json',
    'warzone_recent': 'global-warzone.json',
    'warzone_battle_royale': 'global-warzone-battle_royale-lifetime.json',
    'warzone_resurgence': 'global-warzone-resurgence-lifetime.json',
}
LEADERBOARD_FILES = {
    'mw4_multiplayer_kd': 'lb-mw4-multiplayer-kd.json',
    'mw4_multiplayer_wins': 'lb-mw4-multiplayer-wins.json',
    'warzone_battle_royale': 'lb-warzone-battle_royale-kd.json',
    'warzone_resurgence': 'lb-warzone-resurgence-kd.json',
}
USER_FILES = ('matches.json', 'feed.json', 'local.json', 'sent.json',
              'wz-modes.json', 'rejected.json')

DEFAULT_ALIASES = ('vintendo', 'userinvyyxdp', 'vintendopower')


def _read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except (OSError, ValueError, UnicodeError):
        return default


def _num(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def percentile_estimate(values, value):
    """Approximate percentile of ``value`` in a CODMunity 12-point dist.

    Piecewise-linear interpolation over QUANTILE_GRID. Returns None when
    the distribution or value is unusable, so callers never invent a rank.
    """
    if not isinstance(values, (list, tuple)) or len(values) != len(QUANTILE_GRID):
        return None
    if _num(value) is None:
        return None
    points = [_num(item) for item in values]
    if any(item is None for item in points):
        return None
    if value <= points[0]:
        return 0.0
    if value >= points[-1]:
        return 100.0
    for index in range(1, len(points)):
        low, high = points[index - 1], points[index]
        if low <= value <= high:
            if high == low:
                return float(QUANTILE_GRID[index])
            share = (value - low) / (high - low)
            return QUANTILE_GRID[index - 1] + share * (QUANTILE_GRID[index] - QUANTILE_GRID[index - 1])
    return 100.0


def resolve_dirs(stats_path):
    """Accept a stats root, a user folder, or a settings stats_dir value."""
    path = Path(stats_path).expanduser() if stats_path else None
    if path is None or not path.is_dir():
        return path, None
    if (path / 'matches.json').exists() or path.name.startswith('u-'):
        return path.parent, path
    user = _newest_user_dir(path)
    return path, user


def _newest_user_dir(root):
    try:
        dirs = [item for item in Path(root).iterdir()
                if item.is_dir() and (item / 'matches.json').exists()]
    except OSError:
        return None
    if not dirs:
        return None
    return max(dirs, key=lambda item: (item / 'matches.json').stat().st_mtime)


def file_signature(stats_path):
    """Cheap (name, size, mtime) signature used to skip unchanged re-imports."""
    root, user = resolve_dirs(stats_path)
    signature = {}
    for base, names in ((root, list(GLOBAL_FILES.values()) + list(LEADERBOARD_FILES.values())),
                        (user, USER_FILES)):
        if base is None:
            continue
        for name in names:
            candidate = Path(base) / name
            try:
                stat = candidate.stat()
                signature[str(candidate)] = [stat.st_size, int(stat.st_mtime)]
            except OSError:
                signature[str(candidate)] = None
    return signature


def _mode_key(match):
    if isinstance(match, str):
        text = match.casefold()
    elif isinstance(match, dict):
        text = ' '.join(str(match.get(key) or '') for key in
                        ('game', 'mode', 'modeKey', 'modeName')).casefold()
    else:
        text = ''
    if 'resurgence' in text:
        return 'resurgence'
    if 'battle_royale' in text or 'battle royale' in text:
        return 'battle_royale'
    if 'warzone' in text:
        return 'warzone'
    return 'multiplayer'


def _quality_for_lifetime(label, stats, quality):
    """Flag internally inconsistent lifetime snapshots; never hide them."""
    if not isinstance(stats, dict):
        return
    games, wins, losses = (_num(stats.get(key)) for key in ('games_played', 'wins', 'losses'))
    top10 = _num(stats.get('top10'))
    if games is not None and wins is not None and wins > games:
        quality.append(f'{label}: cached wins ({wins:g}) exceed games_played ({games:g}); '
                       'treat wins/games in this snapshot as unreliable')
    if games is not None and top10 is not None and top10 > games:
        quality.append(f'{label}: cached top10 ({top10:g}) exceeds games_played ({games:g}); '
                       'treat top10/games in this snapshot as unreliable')
    if games is not None and wins is not None and losses is not None and games != wins + losses:
        quality.append(f'{label}: games_played ({games:g}) does not equal wins+losses '
                       f'({wins + losses:g}); snapshot is partial')


def load_snapshot(stats_path, aliases=DEFAULT_ALIASES):
    """Harvest every usable value from a CODMunity stats folder.

    Never raises for missing or corrupt cache files; problems are
    reported in the snapshot's ``quality`` list instead.
    """
    root, user = resolve_dirs(stats_path)
    quality = []
    sources = {}
    snapshot = {
        'version': SNAPSHOT_VERSION,
        'imported_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'stats_root': str(root) if root else '',
        'user_dir': str(user) if user else '',
        'sources': sources,
        'personal': {},
        'benchmarks': {},
        'leaderboards': {},
        'identity': {'aliases': [str(alias) for alias in aliases], 'hits': []},
        'quality': quality,
        'counts': {},
    }

    def load(base, name):
        if base is None:
            return None
        path = Path(base) / name
        payload = _read_json(path, None)
        try:
            sources[name] = {'path': str(path), 'bytes': path.stat().st_size,
                             'fetchedAt': payload.get('fetchedAt') if isinstance(payload, dict) else None}
        except OSError:
            sources[name] = {'path': str(path), 'bytes': None, 'fetchedAt': None}
        if payload is None and not path.exists():
            quality.append(f'{name}: not present in CODMunity cache')
        return payload

    # Global benchmark distributions.
    for key, name in GLOBAL_FILES.items():
        payload = load(root, name)
        data = payload.get('data') if isinstance(payload, dict) else None
        if isinstance(data, dict):
            snapshot['benchmarks'][key] = data
        else:
            quality.append(f'{name}: no benchmark data (empty cache entry)')

    # KD leaderboards (one cached file, mw4 wins, is legitimately empty).
    for key, name in LEADERBOARD_FILES.items():
        payload = load(root, name)
        data = payload.get('data') if isinstance(payload, dict) else None
        entries = data.get('entries') if isinstance(data, dict) else None
        snapshot['leaderboards'][key] = {
            'entries': entries if isinstance(entries, list) else None,
            'count': len(entries) if isinstance(entries, list) else 0,
            'game': data.get('game') if isinstance(data, dict) else None,
            'mode': data.get('mode') if isinstance(data, dict) else None,
            'metric': data.get('metric') if isinstance(data, dict) else None,
            'builtAt': data.get('builtAt') if isinstance(data, dict) else None,
        }
        if not isinstance(entries, list):
            quality.append(f'{name}: leaderboard empty in CODMunity cache; nothing to import')

    # Identity search across every cached leaderboard.
    wanted = [str(alias).casefold() for alias in aliases if str(alias).strip()]
    for board, info in snapshot['leaderboards'].items():
        for entry in info['entries'] or []:
            if not isinstance(entry, dict):
                continue
            haystack = f"{entry.get('username') or ''} {entry.get('displayName') or ''}".casefold()
            if any(alias in haystack for alias in wanted):
                snapshot['identity']['hits'].append({
                    'leaderboard': board, 'rank': entry.get('rank'),
                    'username': entry.get('username'), 'displayName': entry.get('displayName'),
                    'value': entry.get('value')})

    # Personal lifetime snapshots.
    local = load(user, 'local.json')
    if isinstance(local, dict) and isinstance(local.get('stats'), dict):
        stats = local['stats']
        derived = {}
        kills, deaths = _num(stats.get('kills')), _num(stats.get('deaths'))
        if kills is not None and deaths:
            derived['kd'] = round(kills / deaths, 4)
        _quality_for_lifetime('multiplayer lifetime (local.json)', stats, quality)
        snapshot['personal']['multiplayer'] = {
            'at': local.get('at'), 'game': local.get('game'), 'mode': local.get('mode'),
            'scope': local.get('scope'), 'stats': stats, 'derived': derived}
    sent = load(user, 'sent.json')
    if isinstance(sent, dict):
        snapshot['personal']['multiplayer_sent'] = sent

    wz_modes = load(user, 'wz-modes.json')
    warzone_personal = {}
    if isinstance(wz_modes, dict):
        for mode in ('battle_royale', 'resurgence'):
            entry = wz_modes.get(mode)
            if isinstance(entry, dict) and isinstance(entry.get('stats'), dict):
                warzone_personal[mode] = entry
                _quality_for_lifetime(f'warzone {mode} lifetime (wz-modes.json)',
                                      entry['stats'], quality)
    snapshot['personal']['warzone'] = warzone_personal

    # Match history plus the full event feed (progression + feed-only rows).
    matches = load(user, 'matches.json')
    matches = matches if isinstance(matches, list) else []
    feed = load(user, 'feed.json')
    feed = feed if isinstance(feed, list) else []
    snapshot['personal']['feed_only_matches'] = _feed_only_matches(matches, feed)
    snapshot['personal']['feed_progression'] = _feed_progression(feed)
    modes, results, played = {}, {}, []
    rejected = 0
    for match in matches:
        if not isinstance(match, dict):
            continue
        mode = str(match.get('mode') or match.get('modeKey') or 'unknown')
        modes[mode] = modes.get(mode, 0) + 1
        result = str(match.get('result') or 'unknown')
        results[result] = results.get(result, 0) + 1
        if match.get('playedAt'):
            played.append(str(match['playedAt']))
        if match.get('rejected'):
            rejected += 1
    snapshot['personal']['matches_summary'] = {
        'total': len(matches), 'by_mode': modes, 'by_result': results,
        'played_range': [min(played), max(played)] if played else [],
        'rejected': rejected, 'bests': personal_bests(matches),
        'bests_warzone': personal_bests([m for m in matches if isinstance(m, dict) and m.get('game') == 'warzone']),
        'bests_multiplayer': personal_bests([m for m in matches if isinstance(m, dict) and m.get('game') != 'warzone'])}
    if rejected:
        quality.append(f'matches.json: {rejected} match(es) rejected by CODMunity; kept for provenance')
    if load(user, 'rejected.json') is None:
        sources.setdefault('rejected.json', {'path': str(Path(user) / 'rejected.json') if user else '',
                                             'bytes': 4 if user else None, 'fetchedAt': None})

    snapshot['counts'] = {
        'matches': len(matches),
        'feed_events': len(feed),
        'feed_only_matches': len(snapshot['personal']['feed_only_matches']),
        'leaderboard_entries': sum(info['count'] for info in snapshot['leaderboards'].values()),
        'benchmark_modes': len(snapshot['benchmarks']),
        'identity_hits': len(snapshot['identity']['hits']),
    }
    return snapshot


def personal_bests(matches):
    """Best single-match end-game values across cached matches."""
    bests = {'matches': 0, 'wins': 0, 'losses': 0}
    for match in matches or []:
        if not isinstance(match, dict):
            continue
        bests['matches'] += 1
        result = str(match.get('result') or '').casefold()
        if result == 'win':
            bests['wins'] += 1
        elif result == 'loss':
            bests['losses'] += 1
        stats = match.get('stats') if isinstance(match.get('stats'), dict) else {}
        kills = _num(stats.get('kills', stats.get('eliminations')))
        if kills is not None and kills > bests.get('best_kills', -1):
            bests.update(best_kills=kills, best_kills_at=match.get('playedAt'),
                         best_kills_mode=match.get('mode') or match.get('modeKey'))
        damage = _num(stats.get('damage'))
        if damage is not None and damage > bests.get('best_damage', -1):
            bests['best_damage'] = damage
        placement = _num(stats.get('placement', match.get('placement')))
        if placement is not None and placement >= 1:
            if bests.get('best_placement') is None or placement < bests['best_placement']:
                bests['best_placement'] = int(placement)
    return bests


def _natural_key(match):
    return (str(match.get('game') or '').casefold(),
            str(match.get('mode') or match.get('modeKey') or '').casefold(),
            str(match.get('playedAt') or ''),
            str(match.get('result') or '').casefold())


def _feed_only_matches(matches, feed):
    known = {_natural_key(match) for match in matches if isinstance(match, dict)}
    only = []
    for event in feed:
        if not isinstance(event, dict) or event.get('kind') != 'match' or event.get('state') != 'saved':
            continue
        data = event.get('data')
        if isinstance(data, dict) and _natural_key(data) not in known:
            only.append({'feed_id': event.get('id'), 'at': event.get('at'), 'data': data})
    return only


def _feed_progression(feed):
    counts, latest = {}, {}
    for event in feed:
        if not isinstance(event, dict):
            continue
        label = f"{event.get('kind')}/{event.get('state')}"
        counts[label] = counts.get(label, 0) + 1
        if event.get('kind') == 'record' and event.get('state') == 'saved':
            data = event.get('data') if isinstance(event.get('data'), dict) else {}
            key = f"{data.get('game') or 'multiplayer'}:{data.get('mode') or 'lifetime'}"
            latest[key] = {'feed_id': event.get('id'), 'at': event.get('at'), 'data': data}
    return {'counts': counts, 'latest_saved_record': latest}


def merged_matches(user_dir):
    """matches.json plus saved Warzone feed matches, deduplicated.

    Mirrors the legacy watcher's merge: the matches.json copy wins, and
    only explicit Warzone feed events are merged in. Feed-only
    multiplayer events stay in the snapshot (see feed_only_matches)
    without entering the match table, matching tracker behaviour.
    """
    if user_dir is None:
        return []
    user_dir = Path(user_dir)
    matches = _read_json(user_dir / 'matches.json', [])
    matches = matches if isinstance(matches, list) else []
    merged, seen = [], set()
    for match in matches:
        if isinstance(match, dict):
            merged.append(match)
            seen.add(_natural_key(match))
    feed = _read_json(user_dir / 'feed.json', [])
    if isinstance(feed, list):
        for event in feed:
            if not isinstance(event, dict) or event.get('kind') != 'match' or event.get('state') != 'saved':
                continue
            data = event.get('data')
            if not isinstance(data, dict) or _mode_key(data) not in ('resurgence', 'battle_royale'):
                continue
            if _natural_key(data) in seen:
                continue
            seen.add(_natural_key(data))
            merged.append(data)
    return merged


def _benchmark_keys(mode):
    if mode == 'resurgence':
        return 'warzone_resurgence', 'warzone_resurgence', 'resurgence'
    if mode == 'battle_royale':
        return 'warzone_battle_royale', 'warzone_battle_royale', 'battle_royale'
    return 'mw4_multiplayer', 'mw4_multiplayer_kd', None


def benchmark_lines(match, snapshot):
    """Deterministic CODMunity benchmark lines for one match (may be [])."""
    if not isinstance(snapshot, dict) or not snapshot.get('benchmarks'):
        return []
    mode = _mode_key(match)
    bench_key, board_key, personal_mode = _benchmark_keys(mode)
    bench = snapshot['benchmarks'].get(bench_key) or {}
    dist = bench.get('dist') if isinstance(bench.get('dist'), dict) else {}
    label = {'resurgence': 'Resurgence', 'battle_royale': 'Battle Royale'}.get(mode, 'Multiplayer')
    lines = []
    median, p90 = _num(bench.get('kdMedian')), _num(bench.get('kdP90'))
    if median is not None and p90 is not None:
        sampled = bench.get('sampled') or bench.get('players') or '?'
        lines.append(f'CODMunity benchmark: {label} lifetime KD median {median:g} · '
                     f'P90 {p90:g} ({sampled} sampled players, cached)')

    personal_stats, personal_kd = {}, None
    if personal_mode:
        entry = (snapshot.get('personal', {}).get('warzone', {}) or {}).get(personal_mode) or {}
        personal_stats = entry.get('stats') if isinstance(entry.get('stats'), dict) else {}
        personal_kd = _num(personal_stats.get('kd'))
    elif mode == 'multiplayer':
        entry = snapshot.get('personal', {}).get('multiplayer', {}) or {}
        personal_stats = entry.get('stats') if isinstance(entry.get('stats'), dict) else {}
        personal_kd = _num((entry.get('derived') or {}).get('kd'))
    if personal_kd is not None:
        estimate = percentile_estimate(dist.get('kd'), personal_kd)
        standing = (f'~{estimate:.0f}th percentile' if estimate is not None and estimate < 100
                    else 'above the cached sampled KD range' if estimate is not None
                    else 'cached')
        kills_total = _num(personal_stats.get('kills'))
        suffix = f' · {kills_total:,.0f} lifetime kills' if kills_total else ''
        lines.append(f'Your cached {label} lifetime KD {personal_kd:.2f} ({standing} vs CODMunity field){suffix}')

    stats = match.get('stats') if isinstance(match, dict) and isinstance(match.get('stats'), dict) else {}
    kills = _num(stats.get('kills', stats.get('eliminations')))
    per_game = dist.get('avgElims') or dist.get('killsPerGame')
    if kills is not None and per_game:
        estimate = percentile_estimate(per_game, kills)
        if estimate is not None and kills > max(per_game):
            lines.append(f'This match: {kills:g} kills (above the cached per-game '
                         f'distribution, max {max(per_game):g})')
        elif estimate is not None:
            lines.append(f'This match: {kills:g} kills (~{estimate:.0f}th percentile '
                         'vs cached per-game distribution)')

    summary = snapshot.get('personal', {}).get('matches_summary', {}) or {}
    scoped = 'bests_warzone' if mode in ('resurgence', 'battle_royale', 'warzone') else 'bests_multiplayer'
    bests = summary.get(scoped) or summary.get('bests') or {}
    if bests.get('best_kills') is not None:
        parts = [f"{bests['best_kills']:g} kills"]
        if bests.get('best_damage') is not None:
            parts.append(f"{bests['best_damage']:,.0f} damage")
        if bests.get('best_placement'):
            parts.append(f"best finish #{bests['best_placement']}")
        lines.append(f"Personal bests (cached matches): {' · '.join(parts)} "
                     f"across {bests.get('matches', 0)} tracked matches")

    board = snapshot.get('leaderboards', {}).get(board_key, {}) or {}
    hit = next((item for item in snapshot.get('identity', {}).get('hits', [])
                if item.get('leaderboard') == board_key), None)
    if hit:
        lines.append(f"Leaderboard: #{hit.get('rank')} on cached {label} KD board "
                     f"({hit.get('displayName') or hit.get('username')}, value {hit.get('value')})")
    elif board.get('count'):
        lines.append(f'Leaderboard: not in cached top-{board["count"]} {label} KD board')
    return lines[:5]


def poster_context(match, snapshot):
    """Benchmark lines for a poster prompt; '' when no snapshot exists."""
    lines = benchmark_lines(match, snapshot)
    return '; '.join(lines)


def needs_refresh(store, stats_path):
    """True when the cache files changed since the stored snapshot."""
    stored = store.meta(SNAPSHOT_KEY) if store is not None else None
    if not isinstance(stored, dict):
        return True
    return stored.get('file_signature') != file_signature(stats_path)


def _sync_roster_aliases(store, snapshot, roster_player):
    """Add leaderboard identity names to the local player's roster aliases.

    Photo matching keys on roster aliases, so a CODMunity username or
    leaderboard display name found in the cache lets the player's own
    scoreboard rows match their consented photo. Consent and photos are
    never touched, and no other roster entry is modified.
    """
    if not roster_player or not hasattr(store, 'roster'):
        return 0
    hits = snapshot.get('identity', {}).get('hits', [])
    names = {str(hit.get(key)) for hit in hits for key in ('username', 'displayName')
             if hit.get(key)}
    if not names:
        return 0
    added = 0
    for player in store.roster():
        if str(player.get('name', '')).casefold() != str(roster_player).casefold():
            continue
        aliases = list(player.get('aliases') or [])
        fresh = [name for name in sorted(names)
                 if name not in aliases and name.casefold() != str(roster_player).casefold()]
        if fresh:
            store.save_player({**player, 'aliases': aliases + fresh})
            added += len(fresh)
    return added


def import_snapshot(store, stats_path, aliases=DEFAULT_ALIASES, roster_player=None):
    """Store the snapshot and baseline-import any merged matches.

    Idempotent: Store.add deduplicates by match identity, and matches
    are added as 'baseline' so nothing is ever reposted or re-queued.
    """
    snapshot = load_snapshot(stats_path, aliases=aliases)
    snapshot['file_signature'] = file_signature(stats_path)
    _, user = resolve_dirs(stats_path)
    records = merged_matches(user)
    added = 0
    for match in records:
        _, created = store.add({**match, 'integration_source': 'CODMunity'}, 'baseline')
        added += bool(created)
    store.set_meta(SNAPSHOT_KEY, snapshot)
    aliases_added = _sync_roster_aliases(store, snapshot, roster_player)
    return {
        'matches_seen': len(records), 'matches_added': added,
        'roster_aliases_added': aliases_added,
        'leaderboard_entries': snapshot['counts']['leaderboard_entries'],
        'benchmark_modes': snapshot['counts']['benchmark_modes'],
        'identity_hits': snapshot['counts']['identity_hits'],
        'quality_flags': len(snapshot['quality']),
        'user_dir': snapshot['user_dir'], 'stats_root': snapshot['stats_root'],
    }


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description='Import a CODMunity stats cache into the tracker.')
    parser.add_argument('--stats', required=True, help='CODMunity stats root or user folder')
    parser.add_argument('--alias', action='append', default=[],
                        help='COD username/display name to find on leaderboards (repeatable)')
    parser.add_argument('--player', default='vintendo',
                        help='Roster player name that receives leaderboard aliases')
    args = parser.parse_args(argv)
    import engine as E  # lazy: engine imports this module's helpers
    aliases = tuple(dict.fromkeys(list(DEFAULT_ALIASES) + list(args.alias)))
    report = import_snapshot(E.Store(), args.stats, aliases=aliases, roster_player=args.player)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())


