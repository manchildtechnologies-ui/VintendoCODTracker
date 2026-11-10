# Phase 3 automatic artwork handoff

This development copy adds a local authenticated queue between Vintendo COD Tracker and the Phase 3 Vintendo COD Automation MV3 extension. It uses the existing CODMunity feed records for solo matches and verified OCR rows for squad matches. It does not change either original executable or the pre-Phase-3 extension.

## Pair once

Run the development tracker, copy its pairing token from Application Settings, then enter it in **Vintendo COD Automation → Options** and enable automatic handoff. The bridge listens only on `127.0.0.1:8765`; every request requires the pairing token and extension ID. New eligible match jobs are queued automatically.

## Job rules

- Only identified participants with verified records are included. A CODMunity solo record uses the configured local player identity and only its numeric source statistics.
- The tracker refuses unknown outcomes, unknown modes, and jobs with missing, ambiguous, unconsented, or unavailable identity-matched photos. Missing-photo errors name the player. It never fills gaps with another player's photo.
- Successful jobs contain a unique match ID, exact result/mode, player count, usernames, verified statistics and identity-to-file assignments. The extension transfers one to four staged headshots and uses the existing Meta page upload/generation/download workflow.
- Meta generation or transfer failures are recorded against the match. The returned image is stored against that same ID and receives a local overlay rendered from verified tracker data before the existing Discord delivery controls apply.

## Verification

`tests/tracker_automation_browser_e2e.py` launches an isolated Chrome profile with the unpacked dev extension and a controlled Meta-page fixture. It verifies a four-player job's four uploaded files, one exact prompt submission, identity assignment text, and returned artwork. It does not use a live Meta account or make a live generation. The real Chrome launch is blocked in this managed environment with Windows `WinError 5`; see `PHASE3_TEST_RESULTS.md` for what did and did not run.
