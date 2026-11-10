const $ = id => document.getElementById(id);

function fmtTime(value) {
  const n = Number(value || 0);
  return n ? new Date(n).toLocaleString() : 'never';
}

function renderDiagnostics(saved) {
  const diag = $('diag');
  if (!diag) return;
  const d = saved.trackerDiagnostics || {};
  const status = saved.trackerConnectionStatus || {};
  const lines = [
    `Bridge: ${status.status || 'unknown'}${status.detail ? ` — ${status.detail}` : ''}`,
    `Last successful poll: ${fmtTime(d.lastPollAt)}`,
    `Queued cinematic jobs: ${d.queuedCinematic ?? 'unknown'}`,
    `Active cinematic job: ${d.activeCinematicId || 'none'}`,
    `Active match job: ${d.activeMatchJob || 'none'}`,
    `Last error: ${d.lastError || 'none'}`,
    `Worker status: ${d.workerStartedAt ? `running (started ${fmtTime(d.workerStartedAt)})` : 'not recorded — reload the extension'}`,
  ];
  diag.textContent = lines.join('\n');
  diag.style.whiteSpace = 'pre-wrap';
}

async function load() {
  const saved = await chrome.storage.local.get(['trackerPairingToken', 'trackerAutomationEnabled', 'trackerConnectionStatus', 'trackerDiagnostics']);
  $('token').value = saved.trackerPairingToken || '';
  $('enabled').checked = Boolean(saved.trackerAutomationEnabled);
  const status = saved.trackerConnectionStatus;
  $('status').textContent = status ? `${status.status}${status.detail ? ` — ${status.detail}` : ''}` : 'Not paired';
  renderDiagnostics(saved);
}

$('save').addEventListener('click', async () => {
  const token = $('token').value.trim();
  if (!token) {
    $('status').textContent = 'Enter the tracker pairing token first.';
    return;
  }
  await chrome.storage.local.set({
    trackerPairingToken: token,
    trackerAutomationEnabled: $('enabled').checked,
    trackerConnectionStatus: { status: 'checking', detail: 'Checking local tracker…', updatedAt: Date.now() },
  });
  $('status').textContent = 'Pairing saved. Checking the local tracker…';
  try {
    const response = await fetch('http://127.0.0.1:8765/v1/status', {
      cache: 'no-store', credentials: 'omit',
      headers: { Authorization: `Bearer ${token}`, 'X-Vintendo-Extension-ID': chrome.runtime.id },
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.error || `Tracker bridge returned HTTP ${response.status}`);
    await chrome.storage.local.set({ trackerConnectionStatus: { status: 'connected', detail: `${body.pendingCount || 0} queued match(es)`, updatedAt: Date.now() } });
    $('status').textContent = 'Connected to Vintendo COD Tracker.';
  } catch (error) {
    await chrome.storage.local.set({ trackerConnectionStatus: { status: 'error', detail: error.message, updatedAt: Date.now() } });
    $('status').textContent = `Could not connect: ${error.message}`;
  }
});

const pollButton = $('pollNow');
if (pollButton) pollButton.addEventListener('click', async () => {
  $('diag').textContent = 'Polling tracker now…';
  try {
    const result = await chrome.runtime.sendMessage({ type: 'TRACKER_POLL_NOW' });
    if (!result?.success) throw new Error(result?.error || 'Service worker did not respond. Reload the extension on chrome://extensions.');
    await load();
  } catch (error) {
    $('diag').textContent = `Poll failed: ${error.message || String(error)}`;
  }
});

chrome.storage.onChanged.addListener((changes, area) => {
  if (area !== 'local') return;
  if (changes.trackerConnectionStatus) {
    const value = changes.trackerConnectionStatus.newValue;
    $('status').textContent = `${value?.status || 'unknown'}${value?.detail ? ` — ${value.detail}` : ''}`;
  }
  if (changes.trackerDiagnostics || changes.trackerConnectionStatus) load();
});
load();
