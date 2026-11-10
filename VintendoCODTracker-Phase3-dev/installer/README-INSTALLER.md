# Sharing Vintendo COD Tracker — installer release process

Per-user installer, no admin rights needed. Friends get the app and the
desktop shortcut (skull badge icon) in one run. No Chrome extension install
or pairing is needed: Meta videos and posters are made in the in-app
Tracker Browser.

## Cutting a release

1. `python build.py` — produces the one-file `VintendoCODTracker.exe`.
2. Stage the files:
   - `installer\stage\VintendoCODTracker.exe` (copy from the repo root)
3. `iscc installer\VintendoCODTracker.iss`
   — writes `dist-installer\Setup-VintendoCODTracker-<version>.exe`.
4. Test on a clean VM or second account: install, open Tracker Chrome,
   embed, run one cinematic.

## What friends do after installing

1. Pick their Tracker Chrome profile mode under Settings → Automation & Chrome:
   - **Dedicated tracker profile (default)** — Chrome runs with its data in
     `%LOCALAPPDATA%\Vintendo COD Tracker\TrackerChrome`, fully separate
     from their own Chrome. Nothing touches their bookmarks or logins.
   - **Use my already-open Chrome** — they open `meta.ai` in their own
     Chrome and the tracker embeds that window. No second profile at all.
2. Their data (matches, settings, screenshots, videos) stays in
   `%LOCALAPPDATA%\Vintendo COD Tracker` and survives uninstall/reinstall.

## Notes

- The old hardcoded `--profile-directory=Profile 3` launch is gone. Dedicated
  mode uses `--user-data-dir=<app data>\TrackerChrome`; attach mode launches
  nothing and embeds an existing window.
- The installer never touches `HKLM`, `Program Files`, or anyone's real
  Chrome profile. Uninstall removes the app; user data is left in place.
