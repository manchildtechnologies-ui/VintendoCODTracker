# Releasing Vintendo COD Tracker

Private flow for ~5 friends. The in-app updater polls these releases.

## One-time setup

1. Create a **private** GitHub repository (e.g. `VintendoCODTracker`).
2. In `VintendoCODTracker-Phase3-dev/version.py`, set `UPDATE_REPO` to
   `owner/name` of that repository.
3. Commit, then on this PC:

```powershell
git remote add origin https://github.com/owner/name.git
git branch -M main
git push -u origin main
```

## Shipping an update

1. Bump `APP_VERSION` in `VintendoCODTracker-Phase3-dev/version.py`.
2. Commit and tag with the same version:

```powershell
git add -A
git commit -m "Release 3.2.0"
git tag v3.2.0
git push origin main --tags
```

3. The `release` workflow builds the exe and the extension zip and
   publishes them on the tag. The workflow refuses to publish when the
   tag and `version.py` disagree.
4. Friends get an "Update available" popup in the app on next start
   (or via Settings → Check for Updates). One click downloads,
   restarts, and they are on the new build.

## Notes

- Never commit the live data folders: `.gitignore` excludes `TrackerChrome`,
  `headshots`, `browser-e2e-work`, databases, logs, and the built exe.
- Unsigned builds trigger a Windows SmartScreen warning on first run.
  For a private circle, "More info → Run anyway" once per release is fine.
- The Chrome extension still ships as a zip until the in-app automation
  replaces it; then this workflow drops the zip.
