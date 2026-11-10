from __future__ import annotations

from typing import Any


def number(data: dict[str, Any], *keys: str):
    for key in keys:
        value = data.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return value
    return None


def profile_modes(profile: dict[str, Any]) -> dict[str, dict[str, Any]]:
    modes = {}
    lifetime = profile.get('lifetime')
    if isinstance(lifetime, dict):
        modes['Multiplayer'] = lifetime
    wz = profile.get('warzoneLifetime')
    if isinstance(wz, dict):
        # Retain nested mode keys when CODMunity supplies them.
        nested = [(str(k), v) for k, v in wz.items() if isinstance(v, dict)]
        if nested:
            for key, value in nested:
                label = 'Resurgence' if 'resurg' in key.casefold() else 'Battle Royale' if any(x in key.casefold() for x in ('battle', 'br')) else key.replace('_', ' ').title()
                modes[label] = value
        else:
            modes['Warzone'] = wz
    return modes


def mode_for_match(match: dict[str, Any]) -> str:
    raw = str(match.get('mode') or match.get('gameMode') or match.get('modeKey') or '').casefold()
    if 'resurg' in raw:
        return 'Resurgence'
    if 'battle' in raw or raw in ('br', 'warzone_br'):
        return 'Battle Royale'
    if 'warzone' in raw:
        return 'Warzone'
    return 'Multiplayer' if raw else 'Unknown'


def matches(profile: dict[str, Any]) -> list[dict[str, Any]]:
    raw = profile.get('matches')
    return [m for m in raw if isinstance(m, dict)] if isinstance(raw, list) else []


def stat_lines(stats: dict[str, Any]) -> list[str]:
    aliases = [('Kills', ('kills', 'eliminations')), ('Deaths', ('deaths',)), ('K/D', ('kd', 'kdRatio', 'kdr')),
               ('Wins', ('wins',)), ('Losses', ('losses',)), ('Matches', ('matchesPlayed', 'matches', 'gamesPlayed')),
               ('Highest kills', ('highestKillGame', 'highestKills', 'maxKills'))]
    out = []
    for label, names in aliases:
        val = number(stats, *names)
        if val is not None:
            out.append(f'**{label}:** {val:g}' if isinstance(val, float) else f'**{label}:** {val}')
    kills, deaths = number(stats, 'kills', 'eliminations'), number(stats, 'deaths')
    if number(stats, 'kd', 'kdRatio', 'kdr') is None and kills is not None and deaths:
        out.append(f'**K/D:** {kills / deaths:.2f}')
    wins, losses = number(stats, 'wins'), number(stats, 'losses')
    if wins is not None and losses is not None and losses > 0:
        out.append(f'**W/L:** {wins / losses:.2f}')
    return out


def leaderboard_rows(registrations, profiles, mode: str, category: str):
    aliases = {'kills': ('kills', 'eliminations'), 'wins': ('wins',), 'kd': ('kd', 'kdRatio', 'kdr')}.get(category)
    if not aliases:
        return []
    rows = []
    for reg in registrations:
        profile = profiles.get(reg['uid'])
        scoped = profile_modes(profile or {}).get(mode)
        value = number(scoped or {}, *aliases)
        if value is not None:
            rows.append((value, reg['username']))
    return sorted(rows, key=lambda row: row[0], reverse=True)


def match_summary(match: dict[str, Any]) -> str:
    stats = match.get('stats') if isinstance(match.get('stats'), dict) else match
    fields = []
    for label, names in (('Kills', ('kills', 'eliminations')), ('Deaths', ('deaths',)), ('Assists', ('assists',)),
                         ('Damage', ('damage',)), ('Place', ('placement', 'place'))):
        val = number(stats, *names)
        if val is None and label in ('Place',):
            val = number(match, *names)
        if val is not None:
            fields.append(f'{label} {val:g}' if isinstance(val, float) else f'{label} {val}')
    date = match.get('playedAt') or match.get('date') or match.get('timestamp') or 'date unavailable'
    result = match.get('result')
    return f"{mode_for_match(match)} · {date} · {result or 'result unavailable'}" + ((' · ' + ' · '.join(fields)) if fields else '')
