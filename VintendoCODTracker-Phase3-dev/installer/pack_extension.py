"""Pack Chrome-Extension into a distributable .crx for the Inno Setup installer.

One-time setup (run on the release machine only):
    python installer/pack_extension.py --new-key --key-path C:\\Maint\\vintendo-ext-release.pem

Repeat releases (same key = same extension ID = silent upgrades for friends):
    python installer/pack_extension.py --key-path C:\\Maint\\vintendo-ext-release.pem

The script copies Chrome-Extension to a staging dir, strips the dev-only
"key" pin (the release ID comes from the .crx signature instead), packs with
the installed Chrome, and prints the extension ID plus the registry fragment
to paste into installer/VintendoCODTracker.iss.

Keep the release .pem secret and backed up. Anyone with it can ship updates
your friends' browsers will accept silently. It is never committed to git.
"""
import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / 'Chrome-Extension'
STAGE_ROOT = ROOT / 'installer' / 'stage'
STAGE_EXT = STAGE_ROOT / 'extension-src'

CHROME_CANDIDATES = [
    Path(r'C:\Program Files\Google\Chrome\Application\chrome.exe'),
    Path(r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe'),
]


def find_chrome():
    for candidate in CHROME_CANDIDATES:
        if candidate.is_file():
            return candidate
    raise SystemExit('chrome.exe not found in the usual install folders.')


def extension_id_from_key(key_path):
    try:
        from cryptography.hazmat.primitives import serialization
    except ImportError:
        raise SystemExit('The "cryptography" package is required (already in the dev venv).')
    data = Path(key_path).read_bytes()
    try:
        private_key = serialization.load_pem_private_key(data, password=None)
    except Exception as exc:
        raise SystemExit('Could not read release key %s: %s' % (key_path, exc))
    spki = private_key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    digest = hashlib.sha256(spki).hexdigest()[:32]
    return ''.join(chr(ord('a') + int(ch, 16)) for ch in digest)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--key-path', required=True,
                        help='Release .pem outside the repo (created with --new-key).')
    parser.add_argument('--new-key', action='store_true',
                        help='First run: pack once without a key so Chrome generates one, then move it to --key-path.')
    args = parser.parse_args()

    if not SRC.is_dir():
        raise SystemExit('Chrome-Extension folder not found at %s' % SRC)
    key_path = Path(args.key_path)

    if STAGE_ROOT.exists():
        shutil.rmtree(STAGE_ROOT)
    shutil.copytree(SRC, STAGE_EXT, ignore=shutil.ignore_patterns('*.pem', '*.crx'))
    manifest_path = STAGE_EXT / 'manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    manifest.pop('key', None)  # Release ID comes from the .crx signature, not the dev pin.
    manifest_path.write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    version = manifest.get('version', '0.0.0')

    chrome = find_chrome()
    if args.new_key:
        subprocess.run([str(chrome), '--pack-extension=%s' % STAGE_EXT], check=True)
        generated = STAGE_EXT.with_suffix('.pem')
        if not generated.is_file():
            generated = STAGE_EXT / '.pem'
        print('Chrome generated a key next to the staging dir.')
        print('Move it to your secret key path, then re-run WITHOUT --new-key:')
        print('  move "%s" "%s"' % (generated, key_path))
        return

    if not key_path.is_file():
        raise SystemExit('Release key not found: %s (re-run with --new-key first)' % key_path)
    subprocess.run([str(chrome), '--pack-extension=%s' % STAGE_EXT,
                    '--pack-key=%s' % key_path], check=True)
    crx = STAGE_ROOT / 'vintendo-squad-ai.crx'
    staged_crx = STAGE_EXT.with_suffix('.crx')
    if staged_crx.is_file() and staged_crx != crx:
        staged_crx.rename(crx)
    if not crx.is_file():
        found = sorted(STAGE_ROOT.glob('*.crx'))
        if not found:
            raise SystemExit('Packing finished but no .crx was produced.')
        crx = found[0]
    ext_id = extension_id_from_key(key_path)
    print('Extension ID:      %s' % ext_id)
    print('Extension version: %s' % version)
    print('CRX:               %s' % crx)
    print()
    print('Paste into installer/VintendoCODTracker.iss:')
    print('  #define ExtensionID "%s"' % ext_id)
    print('  #define ExtensionVersion "%s"' % version)
    print()
    print('HKCU registry fragment (written by the installer):')
    print('  [Software\\Google\\Chrome\\Extensions\\%s] "path"="<installdir>\\extension\\vintendo-squad-ai.crx", "version"="%s"'
          % (ext_id, version))


if __name__ == '__main__':
    sys.exit(main())
