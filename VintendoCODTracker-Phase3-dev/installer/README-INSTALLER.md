# Sharing Vintendo COD Tracker — installer release process

Per-user installer, no admin rights needed. Friends get the app, the desktop
shortcut (skull badge icon), and the Chrome extension in one run.

## One-time setup (your machine only)

1. Install [Inno Setup 6](https://jrsoftware.org/isinfo.php) (for `iscc`).
2. Generate the release extension key **outside the repo** and back it up.
   Whoever holds this key can ship extension updates your friends auto-accept:
   ```
   python installer/pack_extension.py --new-key --key-path C:\Maint\vintendo-ext-release.pem
   move installer\stage\extension-src.pem C:\Maint\vintendo-ext-release.pem
   ```
3. Paste the printed extension ID and version into
   `installer/VintendoCODTracker.iss` (`ExtensionID`, `ExtensionVersion`).

## Cutting a release

1. `python build.py` — produces the one-file `VintendoCODTracker.exe`.
2. `python installer/pack_extension.py --key-path C:\Maint\vintendo-ext-release.pem`
   — same key every time, so the extension ID never changes and friends get
   silent upgrades. Bump `ExtensionVersion` in the `.iss` if `manifest.json`
   gained a new version.
3. Stage the files:
   - `installer\stage\VintendoCODTracker.exe` (copy from the repo root)
   - `installer\stage\vintendo-squad-ai.crx` (already there from step 2)
   - `installer\stage\extension-src\` (fresh copy of `Chrome-Extension\`)
4. `iscc installer\VintendoCODTracker.iss`
   — writes `dist-installer\Setup-VintendoCODTracker-<version>.exe`.
5. Test on a clean VM or second account: install, restart Chrome, pair the
   extension (below), open Tracker Chrome, embed, run one cinematic.

## What friends do after installing

1. **Restart Chrome** (the extension registers on next launch via the
   per-user registry entry — no developer mode, no warning banner).
   Fallback if anything looks off: `chrome://extensions` → Developer mode →
   Load unpacked → `<install dir>\extension-src`.
2. Open the tracker → Settings → Automation & Chrome → **Copy Chrome pairing
   token**, paste it in the extension's Options page. Same pairing as yours.
3. Pick their Tracker Chrome profile mode in the same tab:
   - **Dedicated tracker profile (default)** — Chrome runs with its data in
     `%LOCALAPPDATA%\Vintendo COD Tracker\TrackerChrome`, fully separate
     from their own Chrome. Nothing touches their bookmarks, logins, or
     `Profile 3`.
   - **Use my already-open Chrome** — they open `meta.ai` in their own
     Chrome and the tracker embeds that window. No second profile at all.
4. Their data (matches, settings, screenshots, videos) stays in
   `%LOCALAPPDATA%\Vintendo COD Tracker` and survives uninstall/reinstall.

## Notes

- The old hardcoded `--profile-directory=Profile 3` launch is gone. Dedicated
  mode uses `--user-data-dir=<app data>\TrackerChrome`; attach mode launches
  nothing and embeds an existing window.
- The installer never touches `HKLM`, `Program Files`, or anyone's real
  Chrome profile. Uninstall removes the app, the `.crx`, and the registry
  entry; user data is left in place.
- Never commit `C:\Maint\vintendo-ext-release.pem` (or any `.pem`/`.crx`) to git.
