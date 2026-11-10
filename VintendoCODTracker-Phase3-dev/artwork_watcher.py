"""Attach completed Meta AI downloads to their waiting match through the local bridge."""
from __future__ import annotations

import logging
import mimetypes
import json
import os
import socket
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import engine as E
from local_bridge import BRIDGE_HOST, BRIDGE_PORT, EXTENSION_ID

POLL_SECONDS = 2
MIN_BYTES = 100_000
MIN_WIDTH, MIN_HEIGHT = 1024, 576
IMAGE_SUFFIXES = {'.png', '.jpg', '.jpeg', '.webp'}
DATA_DIR = Path(E.DATA)
LOG_PATH = DATA_DIR / 'artwork-watcher.log'
RECOVERY_PATH = DATA_DIR / 'artwork-recovery.json'

DATA_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(filename=LOG_PATH, level=logging.INFO,
                    format='%(asctime)s %(levelname)s %(message)s')


def single_instance() -> socket.socket | None:
    lock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        lock.bind(('127.0.0.1', 18765))
        lock.listen(1)
        return lock
    except OSError:
        lock.close()
        return None


def connect_db():
    path = DATA_DIR / 'history.sqlite3'
    return sqlite3.connect(f'file:{path.as_posix()}?mode=ro', uri=True, timeout=5)


def waiting_matches():
    try:
        with connect_db() as db:
            db.row_factory = sqlite3.Row
            return [dict(row) for row in db.execute(
                "SELECT id, status, gen_state, updated, created, error FROM matches "
                "WHERE COALESCE(artwork,'')='' AND ("
                "(status='awaiting_artwork' AND gen_state='awaiting_download') OR "
                "(status='failed' AND (lower(error) LIKE '%failed to fetch%' OR "
                "lower(error) LIKE '%artwork transfer%' OR "
                "lower(error) LIKE '%content script did not reconnect%' OR "
                "lower(error) LIKE '%could not upload meta ai full-resolution artwork%' OR "
                "lower(error) LIKE '%could not recover the existing meta ai artwork%'))"
                ") ORDER BY created ASC LIMIT 10"
            )]
    except Exception as exc:
        logging.debug('Could not inspect waiting matches: %s', exc)
        return []


def candidates(match_id: str, updated: float, *, allow_generic: bool = False, anchor: float = 0):
    downloads = Path.home() / 'Downloads'
    roots = [downloads, downloads / 'Vintendo-COD-Automation' / match_id]
    seen = set()
    for folder in roots:
        if not folder.is_dir():
            continue
        try:
            paths = folder.iterdir() if folder == downloads else folder.rglob('*')
            for path in paths:
                if path in seen or not path.is_file() or path.suffix.lower() not in IMAGE_SUFFIXES:
                    continue
                seen.add(path)
                try:
                    stat = path.stat()
                    if stat.st_size < MIN_BYTES or stat.st_mtime < float(updated or 0) - 3:
                        continue
                    if time.time() - stat.st_mtime < 2:
                        continue
                    exact_folder = match_id.lower() in {part.lower() for part in path.parts}
                    known_name = 'create-image' in path.name.lower()
                    # Chrome's fallback filename is often image_YYYYMMDD_HHMMSS.jpg.
                    # Only consider it when one failed handoff is waiting, and tightly
                    # correlate its timestamp with that job's failure time.
                    generic_recent = (allow_generic and path.name.lower().startswith('image_')
                                      and abs(stat.st_mtime - float(anchor or updated or 0)) <= 180)
                    if not exact_folder and not known_name and not generic_recent:
                        continue
                    yield path, stat.st_mtime
                except OSError:
                    continue
        except OSError:
            continue


def image_mime_and_size(path: Path):
    from PIL import Image, UnidentifiedImageError
    try:
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            width, height = image.size
            fmt = image.format
            if fmt not in ('PNG', 'JPEG', 'WEBP'):
                return None
            if width < MIN_WIDTH or height < MIN_HEIGHT or width > 8192 or height > 8192:
                return None
            if abs((width / height) - (16 / 9)) > 0.04:
                return None
            return {'PNG': 'image/png', 'JPEG': 'image/jpeg', 'WEBP': 'image/webp'}[fmt], width, height
    except (UnidentifiedImageError, OSError, ValueError):
        return None


def upload(match_id: str, path: Path, token: str, mime: str):
    endpoint = f'http://{BRIDGE_HOST}:{BRIDGE_PORT}/v1/jobs/{urllib.parse.quote(match_id, safe="")}/artwork'
    headers = {
        'Authorization': f'Bearer {token}',
        'X-Vintendo-Extension-ID': EXTENSION_ID,
        'Origin': 'https://www.meta.ai',
        'Content-Type': mime,
    }
    request = urllib.request.Request(endpoint, data=path.read_bytes(), headers=headers, method='POST')
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def bridge_json(match_id: str, action: str, body: dict, token: str):
    endpoint = f'http://{BRIDGE_HOST}:{BRIDGE_PORT}/v1/jobs/{urllib.parse.quote(match_id, safe="")}/{action}'
    request = urllib.request.Request(endpoint, data=json.dumps(body).encode(), headers={
        'Authorization': f'Bearer {token}',
        'X-Vintendo-Extension-ID': EXTENSION_ID,
        'Content-Type': 'application/json',
    }, method='POST')
    with urllib.request.urlopen(request, timeout=20) as response:
        return response.status, json.loads(response.read())


def current_job(match_id: str, token: str):
    endpoint = f'http://{BRIDGE_HOST}:{BRIDGE_PORT}/v1/jobs/{urllib.parse.quote(match_id, safe="")}'
    request = urllib.request.Request(endpoint, headers={
        'Authorization': f'Bearer {token}', 'X-Vintendo-Extension-ID': EXTENSION_ID,
    })
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read()).get('job', {})


def prepare_failed_job(match_id: str, token: str):
    job = current_job(match_id, token)
    if job.get('artworkReady'):
        return True
    if job.get('state') == 'failed':
        bridge_json(match_id, 'retry', {}, token)
        job = current_job(match_id, token)
    if job.get('state') in ('pending', 'prepared'):
        if job.get('state') == 'pending':
            bridge_json(match_id, 'prepare', {'automatic': True}, token)
            job = current_job(match_id, token)
        if job.get('state') == 'prepared':
            bridge_json(match_id, 'state', {'state': 'awaiting_generation'}, token)
            job = current_job(match_id, token)
    if job.get('state') == 'awaiting_generation':
        bridge_json(match_id, 'state', {'state': 'awaiting_download'}, token)
    return current_job(match_id, token).get('state') == 'awaiting_download'


def recover_file(match_id: str, path: Path, token: str, mime: str, width: int, height: int):
    try:
        if not prepare_failed_job(match_id, token):
            return False
        status, body = upload(match_id, path, token, mime)
        if status == 201:
            logging.info('Recovered failed transfer for %s from %s (%dx%d)', match_id, path.name, width, height)
            return True
        if status == 409 and current_job(match_id, token).get('artworkReady'):
            return True
        logging.warning('Bridge returned HTTP %s while recovering %s: %s', status, match_id,
                        body.decode('utf-8', 'replace')[:180])
    except urllib.error.HTTPError as exc:
        logging.warning('Recovery request for %s returned HTTP %s: %s', match_id, exc.code,
                        exc.read().decode('utf-8', 'replace')[:180])
    except Exception as exc:
        logging.debug('Recovery retry for %s: %s', match_id, exc)
    return False


def load_recoveries():
    try:
        value = json.loads(RECOVERY_PATH.read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def save_recoveries(value):
    E.atomic_json(RECOVERY_PATH, value)


def run():
    lock = single_instance()
    if lock is None:
        return
    try:
        token = E.Settings(directory=DATA_DIR).extension_token()
        recoveries = load_recoveries()
        logging.info('Artwork download watcher started for %s', DATA_DIR)
        while True:
            for match_id, saved_path in list(recoveries.items()):
                path = Path(saved_path)
                validated = image_mime_and_size(path) if path.is_file() else None
                if not validated:
                    continue
                mime, width, height = validated
                if recover_file(match_id, path, token, mime, width, height):
                    recoveries.pop(match_id, None)
                    save_recoveries(recoveries)
            rows = waiting_matches()
            failed_rows = [row for row in rows if row.get('status') == 'failed']
            for row in rows:
                match_id = str(row['id'])
                failed_transfer = row.get('status') == 'failed'
                handoff_dir = DATA_DIR / 'handoff' / E.disk_id(match_id)
                handoff_time = handoff_dir.stat().st_mtime if handoff_dir.exists() else float(row.get('created') or 0)
                if failed_transfer:
                    threshold = max(float(row.get('created') or 0), handoff_time - 1800)
                else:
                    threshold = float(row.get('updated') or 0)
                options = sorted(candidates(
                    match_id, threshold,
                    allow_generic=failed_transfer and len(failed_rows) == 1,
                    anchor=float(row.get('updated') or 0),
                ), key=lambda pair: pair[1], reverse=True)
                for path, _ in options:
                    validated = image_mime_and_size(path)
                    if not validated:
                        continue
                    mime, width, height = validated
                    if failed_transfer:
                        recoveries[match_id] = str(path)
                        save_recoveries(recoveries)
                        if recover_file(match_id, path, token, mime, width, height):
                            recoveries.pop(match_id, None)
                            save_recoveries(recoveries)
                        break
                    try:
                        status, body = upload(match_id, path, token, mime)
                        if status == 201:
                            logging.info('Linked %s to %s (%dx%d)', path.name, match_id, width, height)
                            break
                        if status == 409:
                            # Match state changed or another sender already claimed the artwork.
                            break
                        logging.warning('Bridge returned HTTP %s for %s: %s', status, match_id,
                                        body.decode('utf-8', 'replace')[:180])
                    except Exception as exc:
                        logging.debug('Transfer retry for %s: %s', match_id, exc)
            time.sleep(POLL_SECONDS)
    except KeyboardInterrupt:
        pass
    except Exception:
        logging.exception('Artwork download watcher stopped unexpectedly')
    finally:
        lock.close()


if __name__ == '__main__':
    run()
