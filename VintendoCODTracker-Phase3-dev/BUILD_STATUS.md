# Delivery status — November 7, 2026

## Previous root build

The root `VintendoCODTracker.exe` remains the earlier 190,713,848-byte build with SHA-256 `14d55e34ef07086e20d3299429f0b973a77cee81b14edda4cade1d448ce7bb32`. It is preserved at `checkpoints/20261107-pre-origin-fix/VintendoCODTracker.exe`. The newer origin compatibility build is separate under `origin-fix-candidate/`; see below.

The current Squad pipeline source suite passes **97/97 tests** and the source GUI smoke passes. Coverage includes mode-aware OCR parsing, CODMunity metadata enrichment, ranking with missing stats, clan-tag normalization, ambiguity-safe roster photo matching, separate job packs for close matches, correct image-to-match binding, and verified overlays. The 45-test recovery checkpoint and prior builds remain preserved as described below.

## Squad pipeline candidate

The current candidate is isolated at `squad-pipeline-candidate/VintendoCODTracker.exe`, 190,736,074 bytes, SHA-256 `FECEEE7097F9CA2238AE93EA0FADA9D342AA4510773E0414C451BE859B81C30C`. It adds structured, unique match jobs and the import pack described below. Packaged `--verify-build` did not yield a verifiable completion artifact in this managed environment; source tests and source GUI smoke passed. The root EXE and checkpoint were not changed. See `squad-pipeline-candidate/release-manifest.json`.

## Origin compatibility candidate

The candidate is built at `origin-fix-candidate/VintendoCODTracker.exe` with SHA-256 `27f0dbdab80037326388ce52540e84baf05d87becce766523da55add61c2f6a4` (190,714,031 bytes). Its PyInstaller archive was inspected to confirm it contains the extension ID and new identity-header validation. Source tests and the real Chromium-to-mock-bridge pairing E2E pass. The packaged `--verify-build` process stalls in this managed Windows environment without producing its result file, so the candidate is built but not fully packaged-runtime verified. Close the currently running tracker, launch this candidate, then reload Vintendo Squad AI in Chrome; no token rotation is required. The candidate reads the same existing app data directory.

## Simplified poster candidate

The updated candidate is `poster-prompt-candidate/VintendoCODTracker.exe`, SHA-256 `8401c086c88448ae239a1ccd8df65906fe2185339d2d35fcde7402e16764873e` (190,713,967 bytes). It removes the theme and randomize controls, creates its single open-ended Meta prompt from reviewed OCR names and ranking, preserves consent-filtered photo staging and the verified-stat overlay/Discord pipeline, and refreshes prior prompts to creative brief v2. Source tests pass 87/87 and source GUI smoke passes. Packaged `--verify-build` and `--smoke-test` both stalled without output and were stopped; packaged-runtime verification remains incomplete. The original/root EXE and the origin-fix candidate were not changed.

## Meta AI handoff

The app creates an import pack for the third-party Meta AI Automation extension: a single-line `Meta-AI-Automation-Import.txt`, readable prompt, row-to-image CSV and JSON, and only the uniquely matched, consented reference photos. It also queues the same complete job through Vintendo Squad AI. The extension displays the verified mode/result/stats, rank and photo mapping. Website submission and image download remain manual. After the user imports the completed image, the app binds it to the unique match ID, checks it, overlays verified match information, then posts to Discord automatically by default. Auto-post can be disabled to use manual preview and approval. The extension cannot determine Meta authorization from its import features; it must be used only according to Meta's applicable permissions and terms.

No authenticated live Meta generation or Discord post was performed. The extension shown in the user's screenshot advertises text-file prompt import and image inputs in its [Chrome Web Store listing](https://chromewebstore.google.com/detail/meta-ai-automation/eoiokhgjnmagclhcpmimmigmhiglchio), and says it is independent/not affiliated with Meta. Its advertised UI is not proof of Meta authorization. This app does not automate website controls, use private endpoints, or extract credentials. Prompt import, photo attachment, generation submission, completion, and download must be completed by the user through the normal website/extension workflow; image import, verified overlay, and Discord delivery then continue in the tracker. See [Meta Terms](https://www.facebook.com/legal/terms), [Meta AI Terms](https://www.facebook.com/legal/ai-terms), and [Meta Automated Data Collection Terms](https://www.facebook.com/legal/automated_data_collection_terms).

The extension integration keeps a persistent generation job queue: each job keeps its match id, verified stats, matched squad, reference photos, creative prompt, browser handoff state, raw download path, overlay poster path, and Discord delivery state, with retries, timeout reporting, cancellation, stale-handoff detection, per-job binding (out-of-order downloads cannot cross-post stats), and the existing duplicate ledger. The extension code and queue were not changed for this prompt/UI simplification.

Solo Ops, the CODMunity tracker, the OCR path, roster/photo consent, DPAPI-protected webhook setting, match history, and duplicate ledger remain in the unified application. The CODMunity executable and original tracker/helper tools were not modified.

See [README.md](README.md), [TEST_RESULTS.md](TEST_RESULTS.md), and [test-results/release-manifest.json](test-results/release-manifest.json).
