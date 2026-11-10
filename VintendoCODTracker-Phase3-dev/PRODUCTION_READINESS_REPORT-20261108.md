# Vintendo COD Tracker — Production Readiness Report (2026-11-08)

Audit method: 8 parallel read-only subagent audits (app/startup, ingest/solo/achievements,
squad/OCR/capture, cinematic tracker, local bridge, Chrome extension, tests/data, runtime env),
plus critic, gap follow-up, and synthesis. Every fix below was verified by execution, not by
re-reading tests. Full suite: **133 passed** (`pytest tests`, 27 s). Backup of pre-repair state:
`backups/audit-backup-20261108-1345/` (sources + phase3 settings + history snapshot).

## System status (PASS / PARTIAL / FAIL)

| Subsystem | Verdict | Basis |
|---|---|---|
| App startup / dashboard / settings / shutdown | PASS (after fix) | Bug below fixed + tested; smoke test exit 0; 20+ offscreen `Window()` inits in suite |
| Solo Ops ingest + achievements | PASS | Live probe: bootstrap → earn kd_3/kill_25/personal_best → save/load round-trip → re-ingest 0 new (dedup confirmed); unknown results never converted |
| Squad OCR / capture / roster / hotkeys | PASS (static + unit) | Guards inspected; no defect cited; OCR fixture is env-conditional (Win fonts/models) |
| Cinematic engine | PASS | Real job `cin-198e75f7…`: queued → running → completed; real Meta MP4 validated (ftyp/moov/mdat, H.264 720p + AAC, 10.0 s, 8,349,692 B, SHA-identical); posted to cinematic Discord webhook HTTP 200 |
| Talent reference photo | PASS | `Cinematics/talent.png` stored; bridge `/v1/cinematics/talent` live-verified (404→200); worker attaches as 2nd image; prompt line added; Set-talent-photo UI added |
| Local bridge | PASS | Loopback-only, token + extension-ID auth, size/format guards, claim-once, atomic MP4 write; traversal/probe-tested |
| Chrome extension | PASS (code) / needs reload (deploy) | MV3 alarms/recovery, match-first queue, claim persist + resume, worker-first video upload, diagnostics; Z: and Desktop copies now in sync — **user must press Reload on chrome://extensions** |
| Discord | PASS | Separate cinematic webhook (DPAPI, persists, verified); manual-only cinematic approval dialog; match webhook untouched |
| Tests / data integrity | PASS | 133 passed; additive-only schema; dedup at every layer; no production data touched |
| Runtime / deployment | PARTIAL | Tracker GUI not running (bridge down) — expected; artwork_watcher pair healthy; one duplicate-tracker risk noted below |

## Additional features (same session, tested)

- **Delete screenshot button** (COD AI section): removes the selected `COD_*.png` from the
  tracker's Screenshots folder after confirmation; confined to that folder (no arbitrary deletes).
- **Auto-delete posted MP4s**: after a cinematic video is accepted by Discord, the local MP4 is
  deleted and the job record cleared of the file path. Failed posts keep the file. Applies to
  cinematic videos only.

## Bugs found and fixed (this session)

1. **P0 — `AUTO_OCR_INTERVAL_SECONDS` undefined** (`app.py:887`): enabling Squad Night +
   continuous OCR raised `NameError` on every 1 s tick. Fixed with `AUTO_OCR_INTERVAL_SECONDS=30`
   plus `test_tick_with_continuous_ocr_enabled_does_not_raise` (fails without fix, passes with it).
2. **Talent route shadowed**: `/v1/cinematics/talent` was registered after the generic
   `{jobId}` route and `talent` matches the job-ID regex → always 404. Moved above it; live-probed.
3. **Deployment drift (recurring)**: Chrome loads the Desktop extension copy, not Z:. Z: had newer
   code (diagnostics, claim-resume, talent fetch) while Desktop lagged. Re-synced all 4 files
   (backups `*.audit-deploy-20261108-1400`). **Needs one user click: Reload the extension.**
4. **Discord 403 from `urllib`** (no User-Agent) + TLS break on 8 MB upload: posted via `requests`
   with `User-Agent: VintendoCODTracker/1.0`; in-app Post button patched with the same header.

## Earlier session repairs (carried, re-verified)

Stale-extension cinematic blindness (root cause: no `/cinematics/pending` poll), mid-claim worker
restart stranding, page-context video fetch failure, cinematic webhook not persisting (two tracker
copies / stale instance overwrite — saved programmatically, verified across restart).

## Unverified / needs a live session

- Continuous-OCR tick with a real camera loop (unit-tested path only).
- Fresh cinematic end-to-end with talent attached (bridge + worker + prompt verified; generation
  itself needs a Meta AI session and several minutes).
- Real game capture via Ctrl+F8 and CODMunity live ingest (no game session was active).
- No live posts were made except the one user-authorized cinematic MP4.

## Performance / safety

Idle footprint unchanged (no new polling: 30 s alarm + 3 s local list refresh pre-existing).
No game hooks/injection — external capture only. No production files overwritten; all user data,
credentials, history, artwork, videos preserved; secrets never logged (one secret-bearing scratch
script was zeroed and deleted).

## Release readiness

Dev version is safe to promote **after** the extension Reload click and one observed live cinematic
run with talent. Keep the two-tracker-copies situation in mind: close duplicate tracker windows so
a stale instance cannot overwrite `settings.json`.
