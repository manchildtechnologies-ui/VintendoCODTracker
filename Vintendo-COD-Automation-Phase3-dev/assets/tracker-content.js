(() => {
  const BASE = 'http://127.0.0.1:8765/v1';
  const extensionId = chrome.runtime.id;

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message?.type === 'TRACKER_BIND_JOB') {
      const jobId = String(message.jobId || '');
      if (!/^[A-Za-z0-9_.:-]{1,160}$/.test(jobId)) {
        sendResponse({ success: false, error: 'Invalid tracker match ID.' });
        return false;
      }
      globalThis.__vintendoTrackerJob = { jobId, artworkSent: false };
      sendResponse({ success: true });
      return false;
    }
    if (message?.type === 'TRACKER_MATCH_CONVERSATION') {
      const marker = String(message.marker || '').trim();
      const pageText = document.body?.innerText || '';
      sendResponse({ matches: Boolean(marker && pageText.includes(marker)), url: location.href });
      return false;
    }
    if (message?.type === 'TRACKER_UPLOAD_ARTWORK') {
      const job = globalThis.__vintendoTrackerJob;
      if (!job || job.jobId !== message.jobId || job.artworkSent || typeof message.url !== 'string') {
        sendResponse({ success: false, error: 'No matching active tracker job is waiting for artwork.' });
        return false;
      }
      globalThis.__vintendoUploadArtwork(message.url).then(sendResponse);
      return true;
    }
    if (message?.type === 'TRACKER_REDOWNLOAD_EXISTING_ARTWORK') {
      const job = globalThis.__vintendoTrackerJob;
      if (!job || job.jobId !== message.jobId) {
        sendResponse({ success: false, error: 'No matching tracker match is bound to this Meta AI tab.' });
        return false;
      }
      const buttons = [...document.querySelectorAll('button, [role="button"]')].filter(button =>
        [button.getAttribute('aria-label'), button.getAttribute('title'), button.getAttribute('data-tooltip-content'), button.textContent]
          .filter(Boolean).join(' ').toLowerCase().includes('download')
      ).filter(button => button.getClientRects().length && !button.disabled);
      const button = buttons.at(-1);
      if (!button) {
        sendResponse({ success: false, error: 'The existing Meta AI result has no Download button on this page.' });
        return false;
      }
      chrome.runtime.sendMessage({ type: 'TRACKER_EXPECT_ARTWORK_DOWNLOAD', jobId: job.jobId })
        .then(armed => {
          if (!armed?.success) throw new Error(armed?.error || 'Could not arm the tracker download listener.');
          for (const event of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
            button.dispatchEvent(new MouseEvent(event, { bubbles: true, cancelable: true, composed: true, view: window }));
          }
          sendResponse({ success: true });
        })
        .catch(error => sendResponse({ success: false, error: error.message || String(error) }));
      return true;
    }
    if (message?.type === 'TRACKER_UPLOAD_CINEMATIC_VIDEO') {
      const jobId = String(message.jobId || '');
      if (!/^[A-Za-z0-9_.:-]{1,160}$/.test(jobId) || typeof message.url !== 'string') {
        sendResponse({ success: false, error: 'Invalid cinematic video transfer.' });
        return false;
      }
      globalThis.__vintendoUploadCinematicVideo(jobId, message.url).then(sendResponse);
      return true;
    }
    return false;
  });

  globalThis.__vintendoUploadArtwork = async url => {
    const job = globalThis.__vintendoTrackerJob;
    if (!job || job.artworkSent) return;
    let stage = 'fetching generated image from Meta AI';
    try {
      const source = await fetch(url, { credentials: 'include', cache: 'no-store' });
      if (!source.ok) throw new Error(`Meta AI image fetch returned HTTP ${source.status}`);
      stage = 'reading generated image';
      const image = await source.blob();
      const type = image.type.toLowerCase().split(';')[0];
      if (!['image/png', 'image/jpeg', 'image/webp'].includes(type)) throw new Error(`Unsupported generated image type: ${type || 'unknown'}`);
      if (!image.size || image.size > 20 * 1024 * 1024) throw new Error('Generated image exceeds the tracker 20 MB transfer limit.');
      stage = 'uploading generated image to the local tracker';
      const pairing = await chrome.storage.local.get('trackerPairingToken');
      if (!pairing.trackerPairingToken) throw new Error('Tracker pairing token is missing.');
      const response = await fetch(`${BASE}/jobs/${encodeURIComponent(job.jobId)}/artwork`, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${pairing.trackerPairingToken}`,
          'Content-Type': type,
          'X-Vintendo-Extension-ID': extensionId,
        },
        body: image,
        cache: 'no-store',
        credentials: 'omit',
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.error || `Tracker rejected generated art with HTTP ${response.status}`);
      job.artworkSent = true;
      chrome.runtime.sendMessage({ type: 'TRACKER_ARTWORK_RECEIVED', jobId: job.jobId }).catch(() => {});
      return { success: true };
    } catch (error) {
      const detail = `${stage}: ${error.message || String(error)}`;
      chrome.runtime.sendMessage({ type: 'TRACKER_REPORT_FAILURE', jobId: job.jobId, error: detail }).catch(() => {});
      return { success: false, error: detail };
    }
  };

  globalThis.__vintendoUploadCinematicVideo = async (jobId, url) => {
    let stage = 'fetching generated video from Meta AI';
    try {
      const source = await fetch(url, { credentials: 'include', cache: 'no-store' });
      if (!source.ok) throw new Error(`Meta AI video fetch returned HTTP ${source.status}`);
      stage = 'reading generated video';
      const video = await source.blob();
      if (!video.size || video.size < 1024 || video.size > 250 * 1024 * 1024) throw new Error('Generated video is empty or exceeds the tracker 250 MB transfer limit.');
      stage = 'uploading generated video to the local tracker';
      const pairing = await chrome.storage.local.get('trackerPairingToken');
      if (!pairing.trackerPairingToken) throw new Error('Tracker pairing token is missing.');
      const response = await fetch(`${BASE}/cinematics/${encodeURIComponent(jobId)}/video`, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${pairing.trackerPairingToken}`,
          'Content-Type': 'video/mp4',
          'X-Vintendo-Extension-ID': extensionId,
        },
        body: video,
        cache: 'no-store',
        credentials: 'omit',
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.error || `Tracker rejected generated video with HTTP ${response.status}`);
      return { success: true };
    } catch (error) {
      return { success: false, error: `${stage}: ${error.message || String(error)}` };
    }
  };
})();
