# Phase 3 Meta AI handoff

This development copy queues matches for Meta AI generation and stages each job's prompt plus consent-filtered reference photos through the in-app Tracker Browser. It uses the existing CODMunity feed records for solo matches and verified OCR rows for squad matches.

## Manual handoff (no extension)

The legacy Vintendo COD Automation extension and its `127.0.0.1:8765` pairing bridge were removed. Preparing a match copies the prompt to the clipboard and opens the staged folder; paste the prompt into Meta AI, attach the numbered reference images, generate, download the result, and link it back to the match with the tracker Import action.

## Job rules

- Only identified participants with verified records are included. A CODMunity solo record uses the configured local player identity and only its numeric source statistics.
- The tracker refuses unknown outcomes, unknown modes, and jobs with missing, ambiguous, unconsented, or unavailable identity-matched photos. Missing-photo errors name the player. It never fills gaps with another player's photo.
- Successful jobs contain a unique match ID, exact result/mode, player count, usernames, verified statistics and identity-to-file assignments. At most four staged headshots accompany one job.
- Meta generation or transfer failures are recorded against the match. The imported image is stored against that same ID and receives a local overlay rendered from verified tracker data before the existing Discord delivery controls apply.

## Verification

`tests/test_ui.py` covers photo staging rules and the in-app cinematic queue; `tests/test_cinematic_jobs.py` covers the job store and MP4 validation. Nothing here uses a live Meta account on the test side.
