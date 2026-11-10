"""Start one synthetic four-player job for a real, signed-in Meta AI browser check."""
import json
import os
import secrets
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'browser-e2e-work' / 'live-handoff'
WORK.mkdir(parents=True, exist_ok=True)
os.environ['VINTENDO_DATA_DIR'] = str(WORK / 'appdata')
sys.path.insert(0, str(ROOT))

import engine as E
from local_bridge import LocalBridge

from PIL import Image, ImageDraw, ImageFont

store = E.Store(WORK / 'history.sqlite3')
match = {
    'matchId': f"phase3-live-four-player-victory-{secrets.token_hex(4)}",
    'integration_source': 'squad_ocr',
    'game': 'Warzone',
    'modeKey': 'resurgence',
    'playedAt': '2026-11-07T22:00:00Z',
    'result': 'win',
}
mid, created = store.add(match)
names = ('TestAlpha', 'TestBravo', 'TestCharlie', 'TestDelta')
players = []
for index, name in enumerate(names):
    players.append({'name': name, 'verified': True, 'stats': {'kills': 11-index, 'damage': 3200-index*350}})
    image = Image.new('RGB', (360, 420), ((35+index*42) % 255, (68+index*37) % 255, (130+index*23) % 255))
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype('C:/Windows/Fonts/arialbd.ttf', 34)
    draw.text((28, 180), name, font=font, fill='white')
    photo = WORK / f'{name}.jpg'
    image.save(photo, quality=90)
    store.save_player({'id': name.lower(), 'name': name, 'aliases': [], 'photo': str(photo),
                       'consent_meta': True, 'enabled': True})
store.update(mid, players=json.dumps(players))

def receive_artwork(job_id, path):
    raw = Path(path)
    overlay = WORK / f'{E.disk_id(job_id)}-exact-scoreboard.jpg'
    row = store.get(job_id)
    player_rows = json.loads(row.get('players') or '[]')
    E.render_poster(raw, overlay, json.loads(row.get('payload') or '{}'), player_rows)
    (WORK / 'completion.json').write_text(json.dumps({
        'matchId': job_id,
        'rawArtwork': str(raw),
        'exactScoreboardOverlay': str(overlay),
        'rawArtworkExists': raw.is_file(),
        'overlayExists': overlay.is_file(),
    }, indent=2), encoding='utf-8')

token = secrets.token_urlsafe(24)
bridge = LocalBridge(store, WORK / 'data', token, on_artwork=receive_artwork,
                     on_log=lambda message: print(message, flush=True), port=8765)
bridge.start()
print(json.dumps({'ready': True, 'created': created, 'matchId': mid, 'token': token,
                  'playerCount': len(players), 'players': names,
                  'mode': 'Warzone · Resurgence', 'outcome': 'Victory',
                  'statistics': [p['stats'] for p in players],
                  'work': str(WORK)}, indent=2), flush=True)
try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    bridge.close()
