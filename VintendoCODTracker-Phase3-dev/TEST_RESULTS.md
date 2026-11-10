# Verification — November 7, 2026

## Current Squad pipeline source/build

- **97/97 source tests passed** in the pinned Windows environment (`work/unified-build/env`), in 13.9 seconds.
- Coverage includes OCR modes/context and flexible stats, CODMunity enrichment, clan-tag normalization, ranking with unavailable metrics, safe/ambiguous photo matching, unique import packs, two near-simultaneous match jobs, reverse-order image arrivals with correct match binding, and a 16:9 poster with exact verified stats.
- `py_compile`, extension JavaScript syntax checks, and source GUI smoke passed.
- Candidate built at `squad-pipeline-candidate/VintendoCODTracker.exe` (190,736,074 bytes; SHA-256 `FECEEE7097F9CA2238AE93EA0FADA9D342AA4510773E0414C451BE859B81C30C`). Packaged `--verify-build` did not produce a verifiable completion result in this managed run; see release manifest. Root EXE remains unchanged.
- Chromium mock E2E could not start in this run: Chrome endpoint timed out and the subprocess exited with code `4294930433`. This is a failed/unavailable run, not a pass. No authenticated live Meta generation or live Discord post was performed.
- User workflow: match detection and structured job creation are automatic when the data is available from the scraper/OCR. Photo association is automatic only for unique, consented matches; uncertain or unavailable photos are explicitly withheld. The app stages the text import and image mapping for the third-party extension. Website prompt/photo submission and image generation/download require user interaction. Importing the downloaded artwork, match-bound verified overlay, and configured Discord posting use the existing pipeline.

## Previous source/build

- **87/87 source tests passed** in the pinned Windows environment (`work/unified-build/env`), in 13.3 seconds.
- Coverage includes existing OCR, CODMunity, Solo routing, scoreboard, roster/consent, overlay, Discord retry/deduplication, queue recovery, plus local bridge auth/origin, origin-less extension preflight, extension-ID validation, queue transitions, photo staging, upload validation, and duplicate rejection.
- Source GUI smoke passed. Prompt tests cover scoreboard-derived ranking, omission of unverified players/stats, and photo mapping. The extension source was unchanged. Re-running its Chromium pairing test was blocked because Playwright could not create a browser subprocess (`WinError 5: Access is denied`) in this managed run; an earlier origin-fix pairing E2E is documented above.
- The simplified-poster PyInstaller candidate built successfully (190,713,967 bytes; SHA-256 `8401c086c88448ae239a1ccd8df65906fe2185339d2d35fcde7402e16764873e`). Its packaged `--verify-build` and `--smoke-test` stalled without output and were stopped; packaged verification is incomplete. The root EXE and prior origin-fix candidate remain intact.
- Startup/shutdown: GUI exits with code 0, no duplicate Vintendo processes remain, Solo worker stop is signalled synchronously with a parent-process watchdog and single-worker mutex, and the browser close now joins its worker thread. No orphaned workers were observed.
- The existing mock browser E2E result remains from the prior build: prompt entered, two reference photos attached, completion detected, mock poster downloaded, and correctly assigned to `mock-match-1`. No live Meta generation, live Discord delivery, or authenticated website session was tested for this prompt simplification.

## Preserved recovery checkpoint

The unchanged 45-test checkpoint's packaged verifier reported 45 tests, zero failures/errors; its Windows GUI startup/shutdown smoke also passed. See `checkpoints/20261107-verified-45-tests/TEST_RESULTS.md` and the original `test-results/packaged-exe-tests.txt`. Its SHA-256 remains `5ef1738be96284a53b2a4391af8e845700ac50366064ac0e959243065af1b8a8`.

## Website workflow boundary

The new build opens a dedicated persistent browser session, prepares the generated prompt and matched, consented photos, and listens for a normal browser download event. Prompt paste, photo attachment, website submission, and pressing Download remain user actions. Once the user-triggered image download completes, the app validates it, overlays verified stats and automatically posts it once when auto-post is enabled. Failed or uncertain delivery is retained in match history for recovery; duplicates are protected by the persistent ledger.

No authenticated Meta session was supplied for a live run. The app performs no private-endpoint requests, CAPTCHA bypass, browser credential extraction, or automatic page-control workflow. See [Meta Terms](https://www.facebook.com/legal/terms) and [Meta AI Terms](https://www.facebook.com/legal/ai-terms).
