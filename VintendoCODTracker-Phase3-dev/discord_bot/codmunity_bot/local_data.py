"""Optional user supplied local exports, isolated by UID subdirectory."""
import json
import os
from pathlib import Path


def load_local(uid: str):
    root = os.environ.get('CODMUNITY_LOCAL_DATA_ROOT')
    if not root:
        return None
    folder = Path(root).expanduser() / uid
    profile_path = folder / 'profile.json'
    if profile_path.is_file():
        data = json.loads(profile_path.read_text(encoding='utf-8-sig'))
        return data if isinstance(data, dict) else None
    # Preserve the export shape used by the existing desktop workflow.
    local_path, matches_path = folder / 'local.json', folder / 'matches.json'
    if local_path.is_file() or matches_path.is_file():
        local = json.loads(local_path.read_text(encoding='utf-8-sig')) if local_path.is_file() else {}
        recent = json.loads(matches_path.read_text(encoding='utf-8-sig')) if matches_path.is_file() else []
        result = dict(local) if isinstance(local, dict) else {}
        result['matches'] = recent if isinstance(recent, list) else []
        return result
    return None
