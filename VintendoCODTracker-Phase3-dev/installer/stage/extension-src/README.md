# Vintendo Squad AI

Private, unpacked Manifest V3 extension for the Vintendo COD Tracker. It lists the local app's Squad Night queue, copies its prepared prompt, opens the regular Meta AI site in the user's signed-in Chrome profile, prepares consented reference photos in Explorer, and imports an explicitly selected completed download back into the matching tracker job.

## Install

1. Build and launch `VintendoCODTracker.exe`.
2. In **Application Settings**, click **Copy Chrome pairing token**.
3. In Chrome, open `chrome://extensions`, enable **Developer mode**, choose **Load unpacked**, and select this `Chrome-Extension` folder.
4. Open the extension's **Options** page, follow the three connection steps shown there, paste the pairing token, and choose **Save and test**. Confirm it reports Connected.
5. In Scoreboard Capture, link the CODMunity match, review the detected scoreboard columns, verify the player rows, then use **GENERATE MATCH POSTER**. The app stages the full metadata/prompt/photo-map job for this extension and opens its folder. Select the extension icon to review the match-bound job.

The app binds its bridge only to `127.0.0.1:8765`; no LAN listener is created. The pairing token is DPAPI-protected in Windows settings and stored in Chrome's extension-local storage. Requests carry both the token and `chrome.runtime.id`; if Chrome provides an Origin header it must match the extension ID. The bridge rejects web origins, incorrect extension IDs, non-loopback clients, and incorrect Host headers. Chrome can omit Origin from extension-to-loopback preflight requests, which is why identity and token are checked explicitly. The production extension ID is `dmkkcacnncdkdndjbjhcheijfbneklef`; the package includes its public key so Chrome assigns the matching ID.

## Squad flow

Choose the queued job and review its actual mode/result/placement, verified statistics/ranks, and per-player headshot decisions. **GENERATE MATCH POSTER** already prepares it; **Prepare Reference Photos** can refresh the staged pack. The opened folder includes `Meta-AI-Automation-Import.txt` (one complete prompt on one line), numbered consented JPEGs, `reference-photo-mapping.csv`, and `photo-assignments.json`. The prompt importer and image chooser in the third-party Meta AI Automation extension can load these prepared inputs; the tracker does not control that extension or the Meta page. Click **Open Meta AI** in Vintendo Squad AI to arm this exact job, sign in normally if Chrome asks, then use the website/extension controls manually. Chrome's download record does not include a source-tab ID, so this extension associates a download only when its referrer is Meta AI and the single active Meta tab was opened for the armed match. It prevents arming a different match while that tab is open. If the source cannot be determined, use the match-bound importer and confirm the displayed match ID. In the popup, choose **Import Completed Image**, select the downloaded file, and click **Send Completed Job to Tracker**. The app validates the upload, applies the exact verified-stat overlay, duplicate ledger, retry handling and Discord delivery.

Manual website actions remain required. This extension does not inject a script into Meta AI, read its page, enter prompts, upload photos, submit generation, or poll results. There is no published permission established for those extension actions. No private endpoints, Meta APIs, credential extraction, CAPTCHA bypass or rate-limit workaround are used. See [Meta Terms](https://www.facebook.com/legal/terms) and [Meta AI Terms](https://www.facebook.com/legal/ai-terms).

## Permissions

The extension requests `storage`, `tabs`, `downloads`, and `clipboardWrite`; host access is limited to `http://127.0.0.1:8765/*`. Opening Meta AI uses a normal browser tab and requires no host permission. No production content script is registered. `content.js` is used only by the local mock test manifest and rejects any origin/path except the controlled local fixture.

## Pairing token rotation

Choose **Rotate pairing token** in the tracker’s Application Settings. The app replaces the DPAPI-protected secret and copies the new token. Paste it into the extension Options page to reconnect; the prior token stops working immediately.

## Troubleshooting “Tracker disconnected”

The popup is disconnected until it has the pairing token. Click **Options** in the popup, leave Vintendo COD Tracker running, go to **Application Settings** in the app, click **Copy Chrome pairing token**, paste it into Options, and click **Save and test**. If Options says the tracker cannot be reached, launch `VintendoCODTracker.exe` and leave it open before choosing **Test again**. If the token is rejected, copy the current token again; rotating it is usually unnecessary. A disabled orange button is unavailable until connection and a match job are present.

If Options reports an identity or origin error, first click **Reload** for Vintendo Squad AI in `chrome://extensions`. The tracker must be updated to the matching bridge build; then click **Test again**. No token rotation is needed for this fix.
