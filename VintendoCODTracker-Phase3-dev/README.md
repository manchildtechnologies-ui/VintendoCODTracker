# Vintendo COD Tracker

## Phase 3 development copy

This copy includes the automatic match-to-Meta AI queue. For pairing and current behavior, see [PHASE3_AUTOMATION.md](PHASE3_AUTOMATION.md); Phase 3 verification and limitations are recorded in [PHASE3_TEST_RESULTS.md](PHASE3_TEST_RESULTS.md). The prior manual import flow described later remains available as a fallback.

The dashboard uses the existing PySide6 interface with a red-and-black tactical theme. The sidebar brand uses `assets/vintendo-cod-tracker-logo.png`; the build script includes it in packaged builds. Optional decorative backgrounds can be placed in `assets/dashboard-operator.png`, `assets/dashboard-operator.webp`, `assets/dashboard-atmosphere.png`, or `assets/dashboard-atmosphere.webp`; when none is present, the dashboard draws a lightweight gradient placeholder. Artwork is decorative and never replaces live UI controls or match data.

Double-click **VintendoCODTracker.exe**. Python, PowerShell and package installation are not required to use the executable. The one-file build includes Qt, the local OCR engine and models, and the Playwright driver. It uses an installed Microsoft Edge or Google Chrome for the optional browser handoff. No large dependencies download during normal startup.

## First run

1. Stop the original tracker and original scoreboard helper before enabling posting in this app. Both original folders are preserved. A detected original tracker EXE prevents starting the new tracker.
2. In **Discord Settings**, enter the webhook or use **Import existing tracker config**. The new application stores the credential with Windows DPAPI. It can also import the existing scoreboard helper's same-account DPAPI setting and region automatically.
3. In **Solo Ops**, choose the folder containing CODMunity's `matches.json`, or leave it blank to select the most recently updated local CODMunity stats folder. Use **Test local data**.
4. Choose **Solo Ops** or **Squad Night** on the Dashboard and click **Start Tracking**.

All mutable files are stored under `%LOCALAPPDATA%\Vintendo COD Tracker`: settings, SQLite history, logs, headshots, captures, artwork, posters and isolated browser profiles. A local `VINTENDO_DATA_DIR` environment override is available for development and offline testing.

## Solo Ops

The original tracker implementation supplies match classification, report rendering, Discord formatting, achievement processing, local feed merging, and optional periodic CODMunity career shares. The unified app manages its worker process and coordinates its match deliveries through persistent history. New startup and shutdown controls have been added to the copied source; the originals were not edited.

Solo does not start Meta AI, screen capture, OCR or an image-generation provider. Warzone is off by default to preserve the prior configuration; enable confirmed Warzone reports in Solo Ops if desired. Multiplayer/MW4 reports use the existing artwork and layout. Optional career shares use a separate Edge profile and require a normal interactive CODMunity login. The existing career-share UI integration is preserved but has not been live-tested in this build.

First use baselines existing local records instead of posting the complete old history. Import the old tracker's `config.json` to carry forward its duplicate/achievement state when available. The credential is encrypted immediately and is not embedded in the executable.

The tracker also harvests the wider CODMunity stats cache (the parent `stats` folder, not just the per-user folder): global percentile benchmarks, KD leaderboards, personal lifetime snapshots (`local.json`, `wz-modes.json`) and the full event feed. `codmunity_stats.py` stores one snapshot in history's `meta` table and baseline-imports any matches the tracker has not seen yet; nothing is reposted. While tracking, the snapshot refreshes only when the cache files change. Discord recaps and poster prompts gain clearly labelled CODMunity benchmark context (field median/P90, cached lifetime standing and kills, per-match percentile, personal bests, leaderboard rank) — benchmark numbers are marked background-only in poster prompts so they are never rendered as match statistics. Match History and the Dashboard show the captured end-game damage and eliminations, and the history summary reports best kills/damage. When a reviewed scoreboard is saved, your own CODMunity end-game row is added to the poster job (labelled `codmunity_local`) unless OCR already covers you; teammates still require reviewed OCR rows, and CODMunity data never replaces the squad scoreboard. Leaderboard identity hits add your CODMunity username/display name to your roster aliases so your rows match your consented photo; other players' leaderboard avatars are never used. Internally inconsistent cached lifetimes (for example wins exceeding games played) are flagged in the snapshot's `quality` list instead of being hidden. A one-off import can be run with `python codmunity_stats.py --stats "%LOCALAPPDATA%\CODMunity\stats"`.

## Squad Night

New local records enter the review queue rather than immediately posting the Solo report. A shared match identity, content identity and timestamp/mode identity keep later feed copies or changed source IDs from creating another job. Identified Squad Night records are reserved in the local Solo state so switching modes does not repost them. The optional additional-format setting must be enabled explicitly to post a Squad recap for a Solo-delivered match.

1. Open **Scoreboard Capture**, refresh monitors and select the complete scoreboard rectangle, including all player rows and column headings.
2. Set the scoreboard region once. At the end of a match, press **Ctrl + Shift + F8** while the final scoreboard is visible, or click **Capture & OCR**. Both paths create an OCR draft for review; continuous detection is optional and off by default.
3. Choose the correct local match in the link selector. Leave it unlinked to create a separate capture record. There is no guaranteed shared ID between a screen image and CODMunity, so association is explicit.
4. Review and correct the player table. Uncertain values should remain blank. Confirm the verification checkbox, then save. Column recognition is conservative: columns without reliable heading alignment remain unknown.
5. Saving the verified scoreboard automatically prepares actual match context, a structured verified scoreboard, and performance ranks. Use **GENERATE MATCH POSTER** to stage the full job through the authenticated Vintendo Squad AI loopback bridge. Win/loss combines both sources: the linked CODMunity match result wins when present, otherwise a Warzone placement of 1 means victory and any other Warzone placement means defeat (multiplayer never guesses). The scoreboard table's placement column backfills a missing match placement the same way. If neither source knows the result, set the Result selector to Victory or Defeat before saving; it stays on auto otherwise. CODMunity mode/placement are used where available and missing context is filled only from explicit scoreboard OCR text.
6. The app stages enabled, consented headshots only when the username match is unambiguous. It opens the job folder with `Meta-AI-Automation-Import.txt` (one prompt on one line), numbered photos, a JSON assignment manifest, and a CSV username-to-photo map. The Vintendo Squad AI popup receives the same match ID, result, scoreboard values/ranks, prompt, photo mapping decisions, and generated image association. Use the import controls in Meta AI Automation or the regular Meta AI website; the prompt, photo upload, submission, and download remain user actions unless Meta authorizes automation. The app validates and binds the returned image to that match, renders all verified rows in a 1600×900 poster, and posts automatically if **Auto-post** is enabled; otherwise, review and approve it.

## COD AI screenshots

The **COD AI** section captures an in-game moment independently of match OCR. Press **Ctrl + F8** while Call of Duty is open; the tracker identifies the visible game window, maps it to its exact DXGI monitor, and saves a lossless native-resolution PNG under `%LOCALAPPDATA%\Vintendo COD Tracker\Screenshots` (or the configured `VINTENDO_DATA_DIR`). The hotkey works while the tracker is minimized and does not change focus. **Ctrl + Shift + F8** remains reserved for scoreboard OCR. The section lists saved captures and has an **Open Screenshots Folder** button so they can be handed to the cinematic video workflow. When more than 10 screenshots pile up, the tracker offers once per run to delete the ones already used in COD AI videos. The Talent Photo panel explains that these are the people that will show up in your COD AI video (max 3 can be used): your photo plus up to 2 roster friends with headshots and Meta AI consent, attached as reference images to each queued video.

## COD Tweaker (Call of Duty config editor)

The **COD Tweaker** section is a review-first editor for local Call of Duty
config files (found under `%LOCALAPPDATA%\Activision`, with a
`Documents\Call of Duty` fallback). It only reads and rewrites plain-text
config files: no memory editing, injection, or executable patching. Files over
4MB are refused, and every Save, preset apply, pinned restore, and restore
first copies the touched file to a dated backup under
`%LOCALAPPDATA%\Vintendo COD Tracker\cod-tweaker-backups` (newest 30 kept per
file). Editing stays read-only until unlocked per file, presets only stage
changes for review before a confirm dialog writes anything, protected
`DO NOT MODIFY` settings are always skipped, and the read-only lock state is
preserved across copies. Restart the game after applying changes; while the
game runs, saves ask for confirmation. Behavior is a Python/Qt
reimplementation of SpockLSM cod-tweaker (MIT, (c) 2026 Spock); see
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## CODMunity tab preview

Run `Launch-CODMunity-Tab-Preview.vbs` to open the development-only tabbed shell. Each launch snapshots the current tracker settings, match history, roster, headshots, and saved screenshots into a separate preview data folder. Preview edits never change live data. Tracking, extension handoff, Discord posting, and global hotkeys are disabled in the preview. Closing it restores CODMunity as its own window. Native window hosting depends on Windows allowing the two app windows to share a desktop session.

**Post screenshot + OCR only** preserves the working manual capture/upload path without requiring artwork generation. Poster names and ranking come from reviewed OCR, while the local overlay supplies exact verified names and numbers. CODMunity statistics in the recap are labeled as belonging to the local player.

## Player roster and photos

Add each player's COD name, aliases separated by semicolons, and an optional reference headshot. The app removes common clan tags, normalizes case/diacritics/punctuation, prefers exact normalized matches, and declines ambiguous or low-confidence candidates. A poster job stages at most four consented headshots and maps each filename to its matched COD username; eligible additional players are marked as over the limit in the assignment manifest. The Studio and extension show whether each player has a matched, consented image or why the image was withheld. Correct ambiguous names in the scoreboard table or add an exact alias.

Record each player's consent to Meta AI use before preparing their photo. Only enabled, confidently matched, reviewed players with a photo and recorded consent are staged for the selected match. The handoff folder contains the prompt, photo files and a name-to-file assignment manifest. Original photos remain where they were; normalized copies live in AppData.

## Meta AI browser workflow and external-service boundary

**GENERATE MATCH POSTER** prepares the job for the existing Vintendo Squad AI extension, copies its complete prompt, and opens the match folder. The popup displays the unique match ID, actual mode/result/placement, every verified row and rank, and each username-to-headshot decision. Click its **Open Meta AI** control so the download listener arms the correct match. The third-party Meta AI Automation extension shown in the user's screenshot advertises prompt-file import, image inputs, and automatic download; its regular controls can import `Meta-AI-Automation-Import.txt` and the staged reference images. It is independent of Meta and of Vintendo COD Tracker; its Chrome Web Store listing is not evidence that Meta authorized its automated page actions. The tracker receives the Chrome download only when the Vintendo extension has that exact match armed; otherwise use its match-bound import flow.

**Unattended consumer-site prompt submission, photo attachment, generation submission, and result polling are not enabled.** Permission for those automated actions has not been established. The tracker prepares import-ready files and match metadata; the user chooses the third-party extension's file/image controls and starts its workflow. The app does not drive that extension or call private endpoints, extract credentials, bypass CAPTCHA or bypass usage limits. No authenticated live Meta generation was tested.

Relevant service references checked during development:

- [Meta AI image creation](https://ai.meta.com/meta-ai/media-generation/)
- [Meta AI terms](https://www.facebook.com/legal/ai-terms)
- [Meta automated data collection terms](https://www.facebook.com/legal/automated_data_collection_terms)

Meta's [Terms of Service](https://www.facebook.com/legal/terms) section 3.2 and [Automated Data Collection Terms](https://www.facebook.com/legal/automated_data_collection_terms) require permission for automated Meta product access. Meta authorization for the unrelated third-party extension has not been established. The tracker therefore provides the complete prompt/scoreboard/photo package and match-bound download tracking, while generation actions remain a user-controlled handoff. It still validates the resulting image, applies the verified overlay, and can post to Discord when auto-post is enabled.

Each generation job is tracked persistently (match id, verified stats, matched squad, reference photos, prompt, handoff state, raw download, overlay poster, delivery state) with retries, timeout reporting, stale-handoff detection, cancellation, per-job download binding, and duplicate protection, so roughly ten matches per session can be worked through the same manual handoff without losing or cross-posting results. Queue behavior is covered by `tests/test_squad_queue.py` against a controlled mock-website fixture, not the live site.

## Manual image import fallback

If the browser download event is unavailable, use **Import downloaded Meta AI image** to select the image you downloaded from the consumer website. The same validation, deterministic scoreboard overlay, duplicate protection and optional automatic Discord posting then apply. There is no API or alternate artwork generator in the app.

## Vintendo Squad AI Chrome extension

The unpacked Manifest V3 extension in [Chrome-Extension](Chrome-Extension/README.md) connects to the running app through an authenticated HTTP service bound only to `127.0.0.1:8765`. Extension pairing uses a random bearer token protected by Windows DPAPI in app settings; the extension stores its copy in Chrome's extension-local storage. Chrome may omit `Origin` on an extension's loopback preflight, so the bridge validates the extension runtime ID header and bearer token; if Chrome supplies an Origin, it must exactly match the packaged extension ID. Host and client must be loopback, request sizes are bounded, image bytes/type/dimensions are validated, and webhook settings are never returned. It is not reachable through the LAN listener.

The popup shows up to ten active jobs plus recent completions, match context, every verified squad row/rank, per-player photo match status and filename, the complete prompt, download status, retry and cancel actions. **Prepare Reference Photos** stages only unambiguous roster matches with recorded Meta AI photo consent and opens that match's folder. The adjacent import-ready `.txt` is a single prompt line for the third-party prompt-file importer; numbered images and a CSV/JSON map preserve each player's identity. **Open Meta AI** uses the current Chrome profile so normal interactive login persists. Website prompt import, photo upload, submission and image download remain user actions. Chrome's download record has no source-tab ID, so the extension associates a download only when its referrer is Meta AI and the one active Meta tab is the tab opened for the armed match. If it cannot determine the source, the user imports through the match-bound page and confirms the match ID. The app then applies the verified-stat poster overlay, duplicate protection and Discord delivery.

Install using the steps in [Chrome-Extension/README.md](Chrome-Extension/README.md). Pairing controls are in the app's **Application Settings** page. A local mock fixture and separate test-only extension manifest live under `tests/extension_mock`; the production manifest does not register a content script and has no Meta page host permission. The mock is not evidence that the consumer Meta AI site permits automated prompt entry or uploads.

## Delivery reliability and controls

SQLite stores source records, OCR references, reviewed rows and confidence, prompts, image paths, statuses, errors and delivery attempts. Discord requests suppress mentions, respect rate-limit retry responses, and use an atomic reservation to prevent concurrent duplicate sends. A network failure whose delivery cannot be established becomes **uncertain**; inspect Discord before choosing a manual retry. Successful deliveries retain Discord message IDs where returned. The app does not delete Discord messages or interact with a cleanup service.

Closing the window while tracking minimizes it to the tray. **Quit** stops tracking and closes the browser. Solo shutdown lets the current request finish; a parent-process watchdog and single-worker mutex prevent orphaned duplicate workers. If a request is interrupted, recovery requires review instead of silently reposting it.

## Verification and limitations

See `TEST_RESULTS.md` and the included offline test report for the exact build results. Automated tests cover the preserved Solo render/post/deduplication pipeline, local OCR on a generated scoreboard, Squad capture-to-poster-to-post flow, player matching, consent-filtered photo staging, 16:9 rendering, rate limits, failures, queue recovery, settings, mode changes and UI startup/shutdown. Discord transport is simulated in these tests.

Live game capture can depend on Windows display access and game presentation mode. The earlier standalone capture was confirmed working by the user; the new packaged capture requires a normal interactive desktop run. No live Discord messages or authenticated Meta AI generations were sent during verification. This executable is not code-signed.

## Source and reproducible build

The source is in this folder. `core/legacy` contains preserved tracker modules and assets; `core/capture_legacy.py` preserves the original capture implementation for reference; the unified capture adapter reuses its OCR grouping and detector rules. Pre-integration snapshots and checksums are under `backups/20261107-preintegration`.

For a development build, install `requirements.txt` in an isolated Python 3.12 environment, then run `python build.py`. The executable supports `--smoke-test` for GUI startup verification and `--verify-build` for the offline regression suite. Set `VINTENDO_DATA_DIR` to a scratch directory when testing. These flags never enable tracking or make live Discord/Meta requests.
