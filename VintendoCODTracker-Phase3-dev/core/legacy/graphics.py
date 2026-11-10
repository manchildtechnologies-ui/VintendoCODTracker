"""Resolve per-install Discord artwork selections."""
import json
import sys
from pathlib import Path

ROOT = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent
REGISTRY = ROOT / 'graphics.json'


def graphic_path(key, fallback):
    try:
        registry = json.loads(REGISTRY.read_text(encoding='utf-8'))
        selected = registry.get(key)
        if selected:
            candidate = Path(selected)
            if not candidate.is_absolute():
                candidate = ROOT / candidate
            if candidate.is_file():
                return candidate
    except (OSError, ValueError, TypeError):
        pass
    return Path(fallback)
