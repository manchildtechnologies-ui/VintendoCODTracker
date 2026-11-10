"""Vintendo Match Tracker: read-only CODMunity JSON -> local image -> Discord webhook."""
import argparse
import datetime as dt
import io
import json
import os
import queue
from pathlib import Path
import sys
import threading
import time
import urllib.request
import urllib.error
import uuid
from achievements import (bootstrap as bootstrap_achievements, evaluate as evaluate_achievements,
    load_history as load_achievement_history, save_history as save_achievement_history,
    read_config as read_achievement_config, is_warzone_candidate,
    is_confirmed_warzone_match, bootstrap_warzone, evaluate_warzone, WARZONE_IMAGES)
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from graphics import graphic_path

APP = Path(os.environ.get('LOCALAPPDATA', str(Path.home() / 'AppData' / 'Local'))) / 'CODMunity'
ROOT = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent
CONFIG = ROOT / 'config.json'
STATE = ROOT / 'posted_matches.json'
OUT = ROOT / 'reports'
ACHIEVEMENT_HISTORY = ROOT / 'achievement_history.json'
ACHIEVEMENT_CONFIG = ROOT / 'achievement_config.json'
ACHIEVEMENT_IMAGES = ROOT / 'achievements' / 'images'
WARZONE_VICTORY_TEMPLATE = ROOT / 'templates' / 'warzone_victory.jpg'
WARZONE_DEFEAT_TEMPLATE = ROOT / 'templates' / 'warzone_defeat.jpg'
W, H = 1600, 900


def read_json(path, default):
    try:
        with open(path, 'r', encoding='utf-8-sig') as f:
            return json.load(f)
    except (OSError, ValueError, UnicodeError):
        return default


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(json.dumps(data, indent=2), encoding='utf-8')
    os.replace(tmp, path)


def find_stats_dir(config):
    override = config.get('stats_dir')
    if override:
        return Path(override).expanduser()
    root = APP / 'stats'
    dirs = [p for p in root.iterdir() if p.is_dir() and (p / 'matches.json').exists()] if root.exists() else []
    if not dirs:
        raise FileNotFoundError(f'No matches.json found under {root}')
    return max(dirs, key=lambda p: (p / 'matches.json').stat().st_mtime)


def font(size, bold=False):
    names = ([r'C:\Windows\Fonts\bahnschrift.ttf', r'C:\Windows\Fonts\arialbd.ttf'] if bold else [r'C:\Windows\Fonts\bahnschrift.ttf', r'C:\Windows\Fonts\arial.ttf'])
    names += ['/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf' if bold else '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf']
    for n in names:
        try:
            return ImageFont.truetype(n, size)
        except OSError:
            continue
    return ImageFont.load_default()


def label(draw, xy, text, size=24, fill='#aab0c0', bold=False, anchor=None):
    draw.text(xy, str(text), font=font(size, bold), fill=fill, anchor=anchor)


def fmt(value):
    if value is None or value == '':
        return 'N/A'
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f'{value:,}'
    return str(value)


def match_fingerprint(m):
    import hashlib
    # Ignore CODMunity delivery metadata such as sent/rejected so a saved event
    # keeps the same identity when that metadata changes.
    identity = {key: m.get(key) for key in ('game', 'mode', 'modeKey', 'playedAt',
                                            'result', 'stats', 'placement') if key in m}
    return 'wz-' + hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(',', ':')).encode()).hexdigest()[:24]


def match_keys(m):
    """Return both the source ID and content identity to survive ID changes."""
    keys = [match_fingerprint(m)]
    if m.get('matchId'):
        keys.insert(0, str(m['matchId']))
    return keys


def match_id(m):
    return match_keys(m)[0]


def timestamp(m):
    raw = m.get('playedAt')
    if not raw:
        return 'TIME UNKNOWN'
    try:
        d = dt.datetime.fromisoformat(raw.replace('Z', '+00:00'))
        return d.astimezone().strftime('%b %d, %Y  â€¢  %I:%M %p').upper()
    except ValueError:
        return str(raw)[:32]


def draw_background(img, accent):
    p = img.load()
    for y in range(H):
        for x in range(W):
            v = int(12 + 12*x/W + 10*y/H)
            p[x, y] = (v+3, v+4, v+11)
    d = ImageDraw.Draw(img, 'RGBA')
    for i in range(0, 1800, 55):
        d.line([(i-450, 0), (i+150, 900)], fill=(*accent, 12), width=2)
    d.polygon([(0,0),(700,0),(300,330),(0,490)], fill=(*accent, 22))
    d.polygon([(1180,0),(1600,0),(1600,900),(1490,900)], fill=(*accent, 14))
    for i in range(9):
        x = 950 + i*80
        d.line((x, 0, x-330, H), fill=(*accent, 12), width=2)


def panel(d, rect, fill=(19,22,31,245), outline=(85,91,107,160), r=18):
    d.rounded_rectangle(rect, radius=r, fill=fill, outline=outline, width=2)


def render(m, lifetime, target, player='vintendo'):
    stats = m.get('stats') if isinstance(m.get('stats'), dict) else {}
    life = lifetime.get('stats') if isinstance(lifetime.get('stats'), dict) else {}
    result = str(m.get('result') or '').lower()
    theme = 'victory' if result == 'win' else 'defeat'
    player = player if player in ('vintendo', 'darbo') else 'vintendo'
    img = Image.open(graphic_path(f'match_{theme}', ROOT / 'templates' / f'{player}_{theme}.png')).convert('RGB')
    w, h = img.size
    d = ImageDraw.Draw(img)
    sx, sy = w / 1672, h / 941
    green = result == 'win'
    fg = '#edffeb' if green else '#fff0f0'
    glow = '#65ff4a' if green else '#ff525c'

    def put(x, y, val, size, max_width, color=fg):
        value = fmt(val)
        size = max(12, round(size * sy))
        f = font(size, True)
        while size > 12 and d.textbbox((0, 0), value, font=f)[2] > max_width * sx:
            size -= 1
            f = font(size, True)
        xx, yy = round(x*sx), round(y*sy)
        d.text((xx+2, yy+2), value, font=f, fill='#050b0a', anchor='mt', stroke_width=2, stroke_fill='#050b0a')
        d.text((xx, yy), value, font=f, fill=color, anchor='mt', stroke_width=1, stroke_fill=glow)

    mode = str(m.get('modeKey') or 'N/A').replace('_', ' ').upper()
    put(228, 453, mode, 32, 330)
    put(640, 451, m.get('scoreOwn'), 61, 190)
    put(1050, 451, m.get('scoreOpp'), 61, 190)
    put(345, 606, stats.get('kills'), 54, 205)
    put(885, 606, stats.get('confirms'), 54, 205)
    put(1415, 606, stats.get('score'), 54, 205)
    put(520, 757, life.get('wins'), 39, 150)
    put(742, 757, life.get('losses'), 39, 150)
    put(974, 757, life.get('kills'), 39, 150)
    put(1474, 757, life.get('highest_streak'), 39, 140)
    put(1390, 868, timestamp(m), 17, 350)
    target.parent.mkdir(parents=True, exist_ok=True)
    img.save(target, 'PNG', optimize=True)
    return target


def render_career(lifetime, target, player='vintendo'):
    life = lifetime.get('stats') if isinstance(lifetime.get('stats'), dict) else {}
    player_name = 'DARBO' if player == 'darbo' else 'VINTENDO'
    img = Image.new('RGB', (1600, 900), (8, 13, 17))
    d = ImageDraw.Draw(img)
    for y in range(900):
        r = int(9 + y / 90)
        d.line((0, y, 1600, y), fill=(r, r + 7, r + 8))
    for x in range(-900, 1900, 100):
        d.line((x, 0, x + 700, 900), fill=(18, 54, 47), width=2)
    d.rounded_rectangle((45, 42, 1555, 858), radius=32, outline=(62, 220, 148), width=5, fill=(10, 19, 24))
    d.text((800, 94), player_name + '  â€¢  CAREER REPORT', anchor='mt', font=font(64, True), fill='#baffd8')
    d.text((800, 175), 'CALL OF DUTY MW4  /  CODMUNITY LIFETIME STATS', anchor='mt', font=font(27, True), fill='#7bb89e')
    entries = [
        ('WINS', 'wins'), ('LOSSES', 'losses'), ('TOTAL KILLS', 'kills'),
        ('TOTAL DEATHS', 'deaths'), ('GAMES PLAYED', 'games_played'),
        ('HIGHEST STREAK', 'highest_streak'), ('BEST KILLS / GAME', 'highest_kills_game'),
        ('AVG KILLS / GAME', 'avg_kills_game'), ('SCORE / MIN', 'score_per_min')
    ]
    for i, (label_text, key) in enumerate(entries):
        col, row = i % 3, i // 3
        x, y = 94 + col * 480, 256 + row * 185
        d.rounded_rectangle((x, y, x + 452, y + 158), radius=17, fill=(18, 35, 40), outline=(45, 108, 86), width=2)
        d.text((x + 226, y + 22), label_text, anchor='mt', font=font(23, True), fill='#92bfae')
        value = fmt(life.get(key))
        size = 56
        while size > 20 and d.textbbox((0, 0), value, font=font(size, True))[2] > 425:
            size -= 2
        d.text((x + 226, y + 68), value, anchor='mt', font=font(size, True), fill='#e7fff2')
    d.text((800, 825), 'AUTO-TRACKED BY CODMUNITY  â€¢  MISSING VALUES: N/A', anchor='mt', font=font(20, True), fill='#79a58e')
    target.parent.mkdir(parents=True, exist_ok=True)
    img.save(target, 'PNG', optimize=True)
    return target


def post_discord(url, image_path, m):
    if not url.startswith('https://discord.com/api/webhooks/') and not url.startswith('https://discordapp.com/api/webhooks/'):
        raise ValueError('Invalid Discord webhook URL')
    boundary='----Vintendo'+uuid.uuid4().hex
    result=str(m.get('result','match')).upper()
    content=f'\U0001F3AE **{result}** \u00b7 {str(m.get("modeKey",m.get("mode","match"))).replace("_"," ").title()}'
    if is_warzone_candidate(m):
        stats=m.get('stats') if isinstance(m.get('stats'),dict) else {}
        details=[]
        for key,label_text in (('kills','Kills'),('damage','Damage'),('assists','Assists'),('placement','Placement')):
            value=stats.get(key,m.get(key))
            if value is not None:
                details.append(f'{label_text}: {value}')
        if m.get('playedAt'):
            details.append(f'Played: {m["playedAt"]}')
        if details:
            content+='\n'+' \u00b7 '.join(details)
    payload=json.dumps({'username':'Vintendo Match Tracker','content':content,'allowed_mentions':{'parse':[]}}).encode('utf-8')
    data=image_path.read_bytes()
    body=(f'--{boundary}\r\nContent-Disposition: form-data; name="payload_json"\r\nContent-Type: application/json\r\n\r\n').encode()+payload+b'\r\n'
    mime = 'image/jpeg' if data[:3] == b'\xff\xd8\xff' else 'image/png'
    filename = 'career-report.jpg' if mime == 'image/jpeg' else 'match-report.png'
    body+=(f'--{boundary}\r\nContent-Disposition: form-data; name="files[0]"; filename="{filename}"\r\nContent-Type: {mime}\r\n\r\n').encode()+data+b'\r\n'+(f'--{boundary}--\r\n').encode()
    req=urllib.request.Request(url,body,{'Content-Type':f'multipart/form-data; boundary={boundary}','User-Agent':'VintendoMatchTracker/1.0'},method='POST')
    with urllib.request.urlopen(req,timeout=30) as resp:
        if resp.status not in (200,204):
            raise RuntimeError(f'Discord returned HTTP {resp.status}')


def post_achievements(url, earned):
    """Post individual achievement cards, attaching the selected matching image when available."""
    if not url.startswith('https://discord.com/api/webhooks/') and not url.startswith('https://discordapp.com/api/webhooks/'):
        raise ValueError('Invalid Discord webhook URL')
    for a in earned:
        c = a.get('match_context', {})
        s = a.get('statistics', {})
        lines = ['🏆 **VINTENDO ACHIEVEMENT UNLOCKED**', '', f'**{a["name"].upper()}**', a['description'],
                 ' · '.join(x for x in [f'Mode: {c.get("mode")}' if c.get('mode') else '',
                                        f'Kills: {s.get("kills")}' if s.get('kills') is not None else '',
                                        f'Result: {c.get("result", "").title()}' if c.get('result') else ''] if x)]
        payload = {'content': '\n'.join(lines), 'allowed_mentions': {'parse': []}}
        image = graphic_path(f"achievement_{a['achievement_id']}", ACHIEVEMENT_IMAGES / f"{a['achievement_id']}.jpg")
        if image.is_file():
            boundary = '----Vintendo' + uuid.uuid4().hex
            data = image.read_bytes()
            body = (f'--{boundary}\r\nContent-Disposition: form-data; name="payload_json"\r\nContent-Type: application/json\r\n\r\n').encode() + json.dumps(payload).encode('utf-8') + b'\r\n'
            body += (f'--{boundary}\r\nContent-Disposition: form-data; name="files[0]"; filename="{image.name}"\r\nContent-Type: image/jpeg\r\n\r\n').encode() + data + b'\r\n' + (f'--{boundary}--\r\n').encode()
            headers = {'Content-Type': f'multipart/form-data; boundary={boundary}', 'User-Agent': 'VintendoMatchTracker/1.0'}
        else:
            body = json.dumps(payload).encode('utf-8')
            headers = {'Content-Type': 'application/json', 'User-Agent': 'VintendoMatchTracker/1.0'}
        req = urllib.request.Request(url, body, headers, method='POST')
        for attempt in range(4):
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    if resp.status not in (200, 204): raise RuntimeError(f'Discord returned HTTP {resp.status}')
                    break
            except urllib.error.HTTPError as exc:
                if exc.code != 429 or attempt == 3: raise
                try: delay = float(exc.headers.get('Retry-After', '1'))
                except (TypeError, ValueError): delay = 1
                time.sleep(max(0, delay))


def classify_watch_match(match, warzone_enabled=False):
    """Route explicit Warzone candidates away from the multiplayer report/achievements."""
    if not is_warzone_candidate(match):
        return 'multiplayer'
    if not warzone_enabled:
        return 'warzone_disabled'
    # Recent CODMunity saved-match events can omit matchId. A completed record
    # with an explicit Warzone mode, outcome, and timestamp is still postable;
    # match_id() supplies a stable content fingerprint for deduplication.
    if not is_confirmed_warzone_match(match):
        game = str(match.get('game', '')).strip().casefold()
        mode = str(match.get('mode', '')).strip().casefold().replace(' ', '_')
        result = str(match.get('result', '')).strip().casefold()
        played = match.get('playedAt')
        try:
            parsed = dt.datetime.fromisoformat(played.replace('Z', '+00:00')) if isinstance(played, str) else None
            valid_time = parsed is not None
        except ValueError:
            valid_time = False
        if game != 'warzone' or mode not in ('battle_royale', 'resurgence') or result not in ('win', 'loss') or not valid_time:
            return 'warzone_unconfirmed'
    if not warzone_enabled:
        return 'warzone_disabled'
    return 'warzone'


def _match_event_key(m):
    return (str(m.get('game', '')).casefold(), str(m.get('mode', m.get('modeKey', ''))).casefold(),
            str(m.get('playedAt', '')), str(m.get('result', '')).casefold())


def watch_matches(directory, matches, feed_cutoff_ms=0):
    """Read saved individual feed events as a fallback and merge by match identity."""
    merged = {}
    event_keys = set()
    for match in matches:
        if not isinstance(match, dict):
            continue
        merged[match_id(match)] = match
        if is_warzone_candidate(match):
            event_keys.add(_match_event_key(match))
    feed = read_json(directory / 'feed.json', [])
    if isinstance(feed, list):
        for event in feed:
            if not isinstance(event, dict) or event.get('kind') != 'match' or event.get('state') != 'saved':
                continue
            if int(event.get('at', 0) or 0) <= int(feed_cutoff_ms or 0):
                continue
            data = event.get('data')
            if not isinstance(data, dict) or not is_warzone_candidate(data):
                continue
            natural_key = _match_event_key(data)
            if natural_key in event_keys:
                continue
            key = match_id(data)
            # Prefer the full matches.json copy when available (it may contain
            # additional fields), while allowing feed-only saved events through.
            merged.setdefault(key, data)
            event_keys.add(natural_key)
    return list(merged.values())


def render_warzone(match, target, player='vintendo'):
    """Render a separate Warzone report, displaying only values verified in the saved record."""
    result = str(match.get('result', '')).casefold()
    victory = result == 'win'
    accent = (211, 165, 65) if victory else (153, 47, 55)
    bg = (10, 13, 13) if victory else (12, 10, 12)
    img = Image.new('RGB', (1600, 900), bg)
    d = ImageDraw.Draw(img, 'RGBA')
    for y in range(900):
        t = y / 900
        color = tuple(int(bg[i] + (accent[i] - bg[i]) * (0.15 * (1-t))) for i in range(3))
        d.line((0, y, 1600, y), fill=color)
    for x in range(-500, 1800, 90):
        d.line((x, 0, x + 560, 900), fill=(*accent, 24), width=2)
    d.rounded_rectangle((46, 42, 1554, 858), radius=28, fill=(8, 12, 12, 235), outline=(*accent, 210), width=4)
    name = 'VINTENDO' if str(player).casefold() == 'vintendo' else str(player).upper()
    mode = str(match.get('mode', '')).replace('_', ' ').upper()
    d.text((800, 92), f'{name}  /  WARZONE {"VICTORY" if victory else "DEFEAT"}', anchor='mt', font=font(50, True), fill='#f2d27b' if victory else '#ef9292')
    d.text((800, 160), f'CALL OF DUTY WARZONE  /  {mode}', anchor='mt', font=font(27, True), fill='#c7c8bd')
    stats = match.get('stats') if isinstance(match.get('stats'), dict) else {}
    fields = [('KILLS', stats.get('kills', stats.get('eliminations'))), ('DAMAGE', stats.get('damage')),
              ('ASSISTS', stats.get('assists')), ('PLACEMENT', match.get('placement', stats.get('placement'))),
              ('DEATHS', stats.get('deaths'))]
    for i, (title, value) in enumerate(fields):
        col, row = i % 3, i // 3
        x, y = 110 + col * 470, 270 + row * 240
        d.rounded_rectangle((x, y, x + 420, y + 190), radius=20, fill=(20, 25, 24, 235), outline=(*accent, 155), width=2)
        d.text((x + 210, y + 34), title, anchor='mt', font=font(27, True), fill='#c3b67e')
        d.text((x + 210, y + 105), fmt(value), anchor='mt', font=font(52, True), fill='#f2eee3')
    d.text((800, 790), timestamp(match), anchor='mt', font=font(20, True), fill='#a8aca4')
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    img.save(target, 'PNG', optimize=True)
    return target


def render_warzone_template(match, target, player='vintendo'):
    """Fill the supplied win/loss poster with verified individual match values."""
    victory = str(match.get('result', '')).casefold() == 'win'
    template = graphic_path('warzone_victory' if victory else 'warzone_defeat', WARZONE_VICTORY_TEMPLATE if victory else WARZONE_DEFEAT_TEMPLATE)
    img = Image.open(template).convert('RGBA')
    w, h = img.size
    sx, sy = w / 1696, h / 1472
    accent = (244, 207, 119, 255) if victory else (218, 157, 157, 255)
    stats = match.get('stats') if isinstance(match.get('stats'), dict) else {}
    values = [stats.get('kills', stats.get('eliminations')), stats.get('damage'), stats.get('assists'),
              match.get('placement', stats.get('placement')), stats.get('deaths')]
    centers = [212, 529, 848, 1166, 1483]
    draw = ImageDraw.Draw(img)
    for cx, value in zip(centers, values):
        text = fmt(value)
        size = int(68 * sy)
        while size > int(36 * sy) and draw.textbbox((0, 0), text, font=font(size, True), stroke_width=2)[2] > 255 * sx:
            size -= 2
        draw.text((cx * sx, 1281 * sy), text, anchor='mm', font=font(size, True), fill=accent,
                  stroke_width=3, stroke_fill=(5, 8, 11, 245))

    mode = str(match.get('mode', '')).replace('_', ' ').upper()
    subtitle = f'CALL OF DUTY WARZONE / {mode}'
    overlay = Image.new('RGBA', img.size, (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay)
    overlay_draw.rounded_rectangle((535 * sx, 295 * sy, 1160 * sx, 334 * sy), radius=int(5 * sx),
                                   fill=(8, 12, 16, 255))
    img = Image.alpha_composite(img, overlay)
    draw = ImageDraw.Draw(img)
    subtitle_font = font(int(19 * sy), True)
    while draw.textbbox((0, 0), subtitle, font=subtitle_font)[2] > 595 * sx and subtitle_font.size > 10:
        subtitle_font = font(subtitle_font.size - 1, True)
    draw.text((848 * sx, 315 * sy), subtitle, anchor='mm', font=subtitle_font, fill=accent)

    stamp = 'TIME UNKNOWN'
    raw_time = match.get('playedAt')
    if isinstance(raw_time, str):
        try:
            parsed = dt.datetime.fromisoformat(raw_time.replace('Z', '+00:00'))
            stamp = parsed.astimezone().strftime('%b %d, %Y - %I:%M %p').upper()
        except ValueError:
            stamp = raw_time[:32].upper()
    overlay = Image.new('RGBA', img.size, (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay)
    overlay_draw.rounded_rectangle((570 * sx, 1404 * sy, 1126 * sx, 1458 * sy), radius=int(7 * sx),
                                   fill=(4, 9, 13, 255))
    img = Image.alpha_composite(img, overlay)
    ImageDraw.Draw(img).text((848 * sx, 1432 * sy), stamp, anchor='mm',
                             font=font(int(21 * sy), True), fill=accent)
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    img.convert('RGB').save(target, 'PNG', optimize=True)
    return target


def post_warzone_achievement(url, achievement, player='Vintendo', image_root=None):
    """Post one image announcement; use text-only when its mapped JPG is absent."""
    if not url.startswith('https://discord.com/api/webhooks/') and not url.startswith('https://discordapp.com/api/webhooks/'):
        raise ValueError('Invalid Discord webhook URL')
    image_path = graphic_path(f"achievement_{achievement['achievement_id']}", Path(image_root or ACHIEVEMENT_IMAGES) / WARZONE_IMAGES[achievement['achievement_id']])
    context = achievement.get('match_context', {})
    stats = achievement.get('statistics', {})
    details = [f'Player: {player}', f'Mode: Warzone {str(context.get("mode", "")).replace("_", " ").title()}']
    for key, label_text in [('eliminations', 'Eliminations'), ('deaths', 'Deaths'), ('damage', 'Damage'), ('placement', 'Placement')]:
        value = stats.get(key)
        if value is not None:
            details.append(f'{label_text}: {value}')
    if context.get('result'):
        details.append(f'Result: {str(context["result"]).title()}')
    content = f'🏆 **{player} — WARZONE ACHIEVEMENT UNLOCKED**\n\n**{achievement["name"]}**\n{achievement["description"]}\n' + '\n'.join(details)
    payload = {'content': content, 'allowed_mentions': {'parse': []}}
    if image_path.is_file():
        boundary = '----Vintendo' + uuid.uuid4().hex
        data = image_path.read_bytes()
        body = (f'--{boundary}\r\nContent-Disposition: form-data; name="payload_json"\r\nContent-Type: application/json\r\n\r\n').encode() + json.dumps(payload).encode('utf-8') + b'\r\n'
        body += (f'--{boundary}\r\nContent-Disposition: form-data; name="files[0]"; filename="{image_path.name}"\r\nContent-Type: image/jpeg\r\n\r\n').encode() + data + b'\r\n' + (f'--{boundary}--\r\n').encode()
        headers = {'Content-Type': f'multipart/form-data; boundary={boundary}', 'User-Agent': 'VintendoMatchTracker/1.0'}
    else:
        body = json.dumps(payload).encode('utf-8')
        headers = {'Content-Type': 'application/json', 'User-Agent': 'VintendoMatchTracker/1.0'}
    req = urllib.request.Request(url, body, headers, method='POST')
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                if resp.status not in (200, 204):
                    raise RuntimeError(f'Discord returned HTTP {resp.status}')
                return 'image' if image_path.is_file() else 'text'
        except urllib.error.HTTPError as exc:
            if exc.code != 429 or attempt == 3:
                raise
            try:
                delay = float(exc.headers.get('Retry-After', '1'))
            except (TypeError, ValueError):
                delay = 1
            time.sleep(max(0, delay))


def official_career(config):
    from codmunity_share import download_lifetime_png
    return download_lifetime_png(OUT/'codmunity-official-lifetime.png', config.get('profile_url','https://codmunity.gg/profile/userinvyyxdp/stats'), config.get('share_username','Vintendo'), config.get('browser_visible',True))


def career_retry_delay(attempts):
    return min(600, 30 * (2 ** min(max(0, int(attempts) - 1), 5)))


def _career_report_worker(url, config, events, requested_count):
    """Run browser and Discord career work outside the watcher thread."""
    try:
        career_path = official_career(config)
        post_discord(url, career_path, {'result': 'CAREER STATS', 'modeKey': 'Lifetime'})
        events.put({'ok': True, 'requested_count': requested_count})
    except Exception as exc:
        events.put({'ok': False, 'requested_count': requested_count,
                    'error': f'{type(exc).__name__}: {exc}'})


def start_career_report(url, config, events, requested_count):
    worker = threading.Thread(target=_career_report_worker,
                              args=(url, config, events, requested_count),
                              name='VintendoCareerShare', daemon=True)
    worker.start()
    return worker


def main(stop_event=None):
    ap=argparse.ArgumentParser(description='CODMunity -> Discord match image tracker')
    ap.add_argument('--preview',action='store_true',help='Generate a sample report, never send')
    ap.add_argument('--latest',action='store_true',help='Render latest real match locally, never send')
    ap.add_argument('--watch',action='store_true',help='Watch for new matches and post')
    ap.add_argument('--post-latest',action='store_true',help='Explicitly post latest match (testing)')
    ap.add_argument('--career-preview',action='store_true',help='Render career report locally without posting')
    ap.add_argument('--post-career',action='store_true',help='Explicitly post official career PNG to Discord (testing)')
    ap.add_argument('--test-official-career',action='store_true',help='Download official CODMunity PNG locally; do not post')
    ap.add_argument('--achievements-preview',action='store_true',help='Show potential achievements locally without posting')
    ap.add_argument('--achievements-status',action='store_true',help='Display achievement progress and unlock history locally')
    args=ap.parse_args()
    config=read_json(CONFIG,{})
    aconfig=read_achievement_config(ACHIEVEMENT_CONFIG)
    if args.preview:
        sample=read_json(ROOT/'sample_data.json',{})
        path=render(sample['matches'][0],sample['local'],OUT/'sample-preview.png',config.get('player','vintendo'))
        print(f'Preview created: {path}')
        return
    if args.test_official_career:
        from codmunity_share import download_lifetime_png
        path=download_lifetime_png(OUT/'codmunity-official-lifetime.png', config.get('profile_url','https://codmunity.gg/profile/userinvyyxdp/stats'), config.get('share_username','Vintendo'))
        print(f'Official CODMunity PNG saved: {path}')
        return
    directory=find_stats_dir(config)
    print(f'Reading CODMunity data from: {directory}')
    if args.career_preview or args.post_career:
        life=read_json(directory/'local.json',{})
        path=official_career(config)
        print(f'Career report created: {path}')
        if args.post_career:
            post_discord(config.get('webhook_url',''),path,{'result':'CAREER STATS','modeKey':'Lifetime'})
            print('Career report posted to Discord.')
        return
    if args.latest or args.post_latest:
        matches=read_json(directory/'matches.json',[])
        if not isinstance(matches,list) or not matches:
            raise RuntimeError('No match data found')
        candidates=[m for m in matches if isinstance(m,dict) and classify_watch_match(m,aconfig.get('warzone_enabled',False)) in ('multiplayer','warzone')]
        if not candidates:
            raise RuntimeError('No eligible confirmed match available for preview/posting')
        m=max(candidates,key=lambda x:x.get('playedAt',''))
        if classify_watch_match(m,aconfig.get('warzone_enabled',False)) == 'warzone':
            path=render_warzone_template(m,OUT/'latest-warzone-match.png',config.get('player','vintendo'))
            report_match={**m,'modeKey':'Warzone '+str(m.get('mode',''))}
        else:
            path=render(m,read_json(directory/'local.json',{}),OUT/'latest-match.png',config.get('player','vintendo'))
            report_match=m
        print(f'Image created: {path}')
        if args.post_latest:
            post_discord(config.get('webhook_url',''),path,report_match)
            print('Posted to Discord.')
        return
    matches=read_json(directory/'matches.json',[])
    if args.achievements_preview or args.achievements_status:
        mp_matches=[m for m in matches if isinstance(m,dict) and not is_warzone_candidate(m)]
        hist=bootstrap_achievements(ACHIEVEMENT_HISTORY, mp_matches)
        if args.achievements_status:
            print(json.dumps({'unlocks':hist.get('unlocks',[]),'streak':hist.get('streak',0),'session':hist.get('session',{}),'baseline_max_kills':hist.get('baseline_max_kills'),'warzone_enabled':aconfig.get('warzone_enabled',False),'warzone':hist.get('warzone',{})},indent=2))
        else:
            # Preview only newly observed matches; the first-run baseline is never previewed as fresh unlocks.
            temp=json.loads(json.dumps(hist))
            found=[]
            for m in sorted(mp_matches,key=lambda x:x.get('playedAt','')):
                found.extend(evaluate_achievements(temp,m,read_achievement_config(ACHIEVEMENT_CONFIG)))
            print(json.dumps(found,indent=2))
        return
    if not args.watch:
        ap.print_help()
        return
    url=config.get('webhook_url','')
    if not url:
        raise RuntimeError('Add your webhook_url to config.json before --watch')
    if config.get('player') == 'darbo':
        raise RuntimeError('Darbo individual stats are not available from local CODMunity JSON. Use player=vintendo for automatic posting until scoreboard capture is implemented.')
    state=read_json(STATE,None)
    matches=read_json(directory/'matches.json',[])
    mp_matches=[m for m in matches if isinstance(m,dict) and not is_warzone_candidate(m)] if isinstance(matches,list) else []
    ahistory=bootstrap_achievements(ACHIEVEMENT_HISTORY, mp_matches)
    if aconfig.get('warzone_enabled',False):
        wz_before=ahistory.get('warzone',{}).get('initialized',False)
        bootstrap_warzone(ahistory,matches if isinstance(matches,list) else [])
        if not wz_before:
            save_achievement_history(ACHIEVEMENT_HISTORY,ahistory)
    if state is None:
        current=read_json(directory/'matches.json',[])
        ids=[match_id(m) for m in current if isinstance(m,dict)] if isinstance(current,list) else []
        state={'posted_ids':ids, 'matches_since_career':0}
        write_json(STATE,state)
        print(f'First run: skipping {len(ids)} existing matches. Waiting for NEW matches.')
    if 'feed_cutoff_ms' not in state:
        state['feed_cutoff_ms'] = int(STATE.stat().st_mtime * 1000) if STATE.exists() else int(time.time() * 1000)
        write_json(STATE, state)
    career_retry={'attempts':0,'retry_at':0}
    career_events=queue.Queue()
    career_thread=None

    def collect_career_result():
        nonlocal career_thread
        while True:
            try:
                outcome=career_events.get_nowait()
            except queue.Empty:
                return
            career_thread=None
            if outcome['ok']:
                # Subtract the count included when the background report began; retain newer matches.
                state['matches_since_career']=max(0,int(state.get('matches_since_career',0))-int(outcome['requested_count']))
                write_json(STATE,state)
                career_retry.update(attempts=0,retry_at=0)
                print('Posted periodic career report')
            else:
                attempts=int(career_retry.get('attempts',0))+1
                delay=career_retry_delay(attempts)
                career_retry.update(attempts=attempts,retry_at=time.monotonic()+delay)
                print(f'Career share/report failed ({outcome["error"]}); match watcher remains active; retrying in {delay}s.',file=sys.stderr)

    def schedule_career_report():
        nonlocal career_thread
        if not config.get('profile_url'):
            return
        interval=max(1,int(config.get('career_summary_every_matches',10)))
        if int(state.get('matches_since_career',0)) < interval:
            return
        if time.monotonic() < career_retry.get('retry_at',0):
            return
        if career_thread is not None and career_thread.is_alive():
            return
        requested=int(state.get('matches_since_career',0))
        career_thread=start_career_report(url,config,career_events,requested)
        print('Started periodic career report in background')

    print('Watching for new matches. Ctrl+C to stop.')
    while stop_event is None or not stop_event.is_set():
        try:
            matches=read_json(directory/'matches.json',[])
            if not isinstance(matches,list):
                raise ValueError('matches.json is not an array')
            state=read_json(STATE,{'posted_ids':[]})
            seen=set(state.get('posted_ids',[]))
            collect_career_result()
            schedule_career_report()
            for m in sorted(watch_matches(directory, matches, state.get('feed_cutoff_ms', 0)),key=lambda x:x.get('playedAt','')):
                mid=match_id(m)
                route=classify_watch_match(m,aconfig.get('warzone_enabled',False))
                if route in ('warzone_disabled','warzone_unconfirmed'):
                    continue
                if str(m.get('result','')).lower() not in ('win','loss'):
                    continue
                if route == 'warzone':
                    if not any(key in seen for key in match_keys(m)):
                        safe=''.join(c for c in mid if c.isalnum() or c in '-_')[:65]
                        wz_path=render_warzone_template(m,OUT/f'warzone-match-{safe}.png',config.get('player','vintendo'))
                        post_discord(url,wz_path,{**m,'modeKey':'Warzone '+str(m.get('mode',''))})
                        seen.update(match_keys(m))
                        state['posted_ids']=sorted(seen)
                        write_json(STATE,state)
                        print(f'Posted Warzone match {mid}')
                    try:
                        earned=evaluate_warzone(ahistory,m,aconfig)
                        save_achievement_history(ACHIEVEMENT_HISTORY,ahistory)
                        for item in earned:
                            try:
                                delivery=post_warzone_achievement(url,item,config.get('share_username','Vintendo'))
                                item['delivery_status']='delivered_'+delivery
                            except (OSError,ValueError,urllib.error.URLError,RuntimeError) as exc:
                                item['delivery_status']='uncertain'
                                print(f'Warzone achievement post failed (not automatically retried to avoid duplicates): {exc}',file=sys.stderr)
                            save_achievement_history(ACHIEVEMENT_HISTORY,ahistory)
                    except Exception as exc:
                        print(f'Warzone achievement processing failed; match report remains posted: {exc}',file=sys.stderr)
                    continue
                if any(key in seen for key in match_keys(m)):
                    continue
                life=read_json(directory/'local.json',{})
                safe=''.join(c for c in mid if c.isalnum() or c in '-_')[:65]
                path=render(m,life,OUT/f'match-{safe}.png',config.get('player','vintendo'))
                post_discord(url,path,m)
                # The normal report is already delivered and recorded before achievement work begins.
                seen.update(match_keys(m))
                state['posted_ids']=sorted(seen)
                state['matches_since_career']=int(state.get('matches_since_career',0))+1
                write_json(STATE,state)
                print(f'Posted match {mid}')
                try:
                    earned=evaluate_achievements(ahistory,m,aconfig)
                    save_achievement_history(ACHIEVEMENT_HISTORY,ahistory)
                    if earned:
                        try:
                            post_achievements(url,earned)
                            for item in earned: item['delivery_status']='delivered'
                        except (OSError,ValueError,urllib.error.URLError,RuntimeError) as exc:
                            # A transport failure may have reached Discord; keep it uncertain and never auto-repost.
                            for item in earned: item['delivery_status']='uncertain'
                            print(f'Achievement post failed (not automatically retried to avoid duplicates): {exc}',file=sys.stderr)
                        save_achievement_history(ACHIEVEMENT_HISTORY,ahistory)
                except Exception as exc:
                    print(f'Achievement processing failed; normal match report remains posted: {exc}',file=sys.stderr)
                collect_career_result()
                schedule_career_report()
            collect_career_result()
            schedule_career_report()
        except (OSError,ValueError,urllib.error.URLError,RuntimeError) as e:
            print(f'Watch error (will retry): {e}',file=sys.stderr)
        delay=max(3,int(config.get('poll_seconds',5)))
        if stop_event is None:
            time.sleep(delay)
        else:
            stop_event.wait(delay)


if __name__=='__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\nStopped.')
    except Exception as exc:
        print(f'Error: {exc}',file=sys.stderr)
        sys.exit(1)




