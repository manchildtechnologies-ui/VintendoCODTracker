# Phase 3 Meta AI handoff

This development copy submits poster and video jobs to Meta AI through a minimized background browser. The user signs in once with **Log into Meta AI** in the sidebar and verifies the session. The Tracker Browser page is hidden; generation, reference upload, and result collection continue without showing the Meta page. Match jobs use CODMunity feed records for solo matches and verified OCR rows for squad matches.

## Sign in and background jobs

Click **Log into Meta AI** in the sidebar, sign in in the visible browser window, then click **Verify Meta AI login**. Poster and video jobs then reuse that browser profile minimized in the background. A sign-in failure is shown in the tracker and can be fixed by signing in again.

The legacy Vintendo COD Automation extension and its `127.0.0.1:8765` pairing bridge were removed. If automatic submission is unavailable, the staged prompt and reference photos remain available for manual handoff; imported results still go through the match-bound review and validation flow.

## Job rules

- Only identified participants with verified records are included. A CODMunity solo record uses the configured local player identity and only its numeric source statistics.
- The tracker refuses unknown outcomes, unknown modes, and jobs with missing, ambiguous, unconsented, or unavailable identity-matched photos. Missing-photo errors name the player. It never fills gaps with another player's photo.
- Successful jobs contain a unique match ID, exact result/mode, player count, usernames, verified statistics and identity-to-file assignments. At most four staged headshots accompany one job.
- Meta generation or transfer failures are recorded against the match. The imported image is stored against that same ID and receives a local overlay rendered from verified tracker data before the existing Discord delivery controls apply.

## Verification

`tests/test_ui.py` covers photo staging rules and the in-app cinematic queue; `tests/test_cinematic_jobs.py` covers the job store and MP4 validation. Nothing here uses a live Meta account on the test side.
