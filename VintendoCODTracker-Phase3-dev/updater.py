"""Best-effort self update from GitHub Releases. Never raises to callers.

Flow: compare version.APP_VERSION against the latest release tag, download
the exe asset with progress, then swap it in with a small batch script and
restart. Every public function returns a status dict or None on failure so
the UI can simply skip the update prompt when offline.
"""

import json
import os
import subprocess
import sys
import urllib.request

from version import APP_VERSION, UPDATE_ASSET_NAME, UPDATE_REPO

API_LATEST = 'https://api.github.com/repos/{repo}/releases/latest'
USER_AGENT = 'VintendoCODTracker-updater'
TIMEOUT = 20


def parse_version(text):
    parts = []
    for chunk in str(text or '').lstrip('vV').split('.'):
        digits = ''.join(ch for ch in chunk if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def _get_json(url):
    request = urllib.request.Request(url, headers={'User-Agent': USER_AGENT,
                                                   'Accept': 'application/vnd.github+json'})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return json.loads(response.read().decode('utf-8', 'replace'))


def check_for_updates(repo=None):
    """Return a status dict, or None when the check cannot complete."""
    try:
        repo = (repo or UPDATE_REPO).strip()
        if not repo or repo.upper().startswith('OWNER/'):
            return None
        release = _get_json(API_LATEST.format(repo=repo))
        tag = str(release.get('tag_name') or '')
        if not tag:
            return None
        latest = parse_version(tag)
        current = parse_version(APP_VERSION)
        asset_url, asset_size = '', 0
        for asset in release.get('assets') or []:
            if str(asset.get('name') or '') == UPDATE_ASSET_NAME:
                asset_url = str(asset.get('browser_download_url') or '')
                asset_size = int(asset.get('size') or 0)
                break
        return {'current': APP_VERSION, 'latest': tag.lstrip('vV'),
                'update_available': latest > current and bool(asset_url),
                'download_url': asset_url, 'size': asset_size,
                'notes': str(release.get('body') or '')[:2000]}
    except Exception:
        return None


def download_update(url, dest_path, progress=None, timeout=600):
    """Stream the asset to dest_path. Returns True on success."""
    try:
        request = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            total = int(response.headers.get('Content-Length') or 0)
            received = 0
            with open(dest_path, 'wb') as handle:
                while True:
                    chunk = response.read(1024 * 256)
                    if not chunk:
                        break
                    handle.write(chunk)
                    received += len(chunk)
                    if progress:
                        try:
                            progress(received, total)
                        except Exception:
                            pass
        if total and os.path.getsize(dest_path) != total:
            try:
                os.remove(dest_path)
            except OSError:
                pass
            return False
        return os.path.getsize(dest_path) > 0
    except Exception:
        return False


def pending_path(exe_path=None):
    base = exe_path or sys.executable
    folder = os.path.dirname(os.path.abspath(base))
    return os.path.join(folder, 'VintendoCODTracker.update.exe')


def apply_and_restart(pending_exe, exe_path=None):
    """Swap in the downloaded exe and restart. Returns False on failure."""
    try:
        exe = os.path.abspath(exe_path or sys.executable)
        folder = os.path.dirname(exe)
        script = os.path.join(folder, 'apply_update.bat')
        with open(script, 'w', encoding='utf-8') as handle:
            handle.write('@echo off\r\n')
            handle.write('set "TARGET=%s"\r\n' % exe)
            handle.write('set "PENDING=%s"\r\n' % os.path.abspath(pending_exe))
            handle.write(':waitloop\r\n')
            handle.write('tasklist /FI "PID eq %d" 2>NUL | find "%d" >NUL\r\n' % (os.getpid(), os.getpid()))
            handle.write('if not errorlevel 1 (timeout /t 1 /nobreak >NUL & goto waitloop)\r\n')
            handle.write('move /Y "%PENDING%" "%TARGET%" >NUL\r\n')
            handle.write('start "" "%TARGET%"\r\n')
            handle.write('(goto) 2>NUL & del "%%~f0"\r\n')
        subprocess.Popen(['cmd.exe', '/c', script], cwd=folder,
                         creationflags=getattr(subprocess, 'DETACHED_PROCESS', 0))
        return True
    except Exception:
        return False
