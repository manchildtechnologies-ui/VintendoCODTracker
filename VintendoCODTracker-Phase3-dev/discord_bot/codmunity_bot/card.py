from __future__ import annotations

from pathlib import Path
from PIL import Image, ImageDraw, ImageFont


def make_card(username: str, mode: str, stats: dict, recent: str, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new('RGB', (1200, 675), '#101218')
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((24, 24, 1176, 651), radius=26, fill='#171b25', outline='#a82735', width=4)
    draw.text((64, 52), 'VINTENDO COD', fill='#f04a54', font=font(27, True))
    draw.text((64, 112), clean(username, 27), fill='#ffffff', font=font(54, True))
    draw.text((66, 185), clean(mode.upper(), 32), fill='#aeb6c7', font=font(24, True))
    keys = [('K/D', ('kd', 'kdRatio', 'kdr')), ('KILLS', ('kills', 'eliminations')), ('WINS', ('wins',))]
    x = 64
    for label, aliases in keys:
        value = next((stats.get(k) for k in aliases if isinstance(stats.get(k), (int, float)) and not isinstance(stats.get(k), bool)), None)
        display = '—' if value is None else f'{value:g}' if isinstance(value, float) else str(value)
        draw.rounded_rectangle((x, 255, x + 326, 408), radius=16, fill='#222837')
        draw.text((x + 24, 276), label, fill='#adb6c8', font=font(22, True))
        draw.text((x + 24, 320), display, fill='#ffffff', font=font(42, True))
        x += 352
    draw.text((64, 457), 'RECENT MATCH', fill='#f04a54', font=font(21, True))
    draw.multiline_text((64, 495), clean(recent, 88), fill='#e1e5ec', font=font(23), spacing=8)
    draw.text((64, 610), 'CODMunity statistics · values shown when available', fill='#7e879a', font=font(16))
    image.save(destination, format='PNG', optimize=True)
    return destination


def clean(text: str, limit: int) -> str:
    return ''.join(c for c in str(text) if c.isprintable())[:limit]


def font(size: int, bold: bool = False):
    paths = ([r'C:\Windows\Fonts\bahnschrift.ttf', r'C:\Windows\Fonts\arialbd.ttf'] if bold
             else [r'C:\Windows\Fonts\bahnschrift.ttf', r'C:\Windows\Fonts\arial.ttf'])
    for path in paths:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass
    return ImageFont.load_default()
