const TRACKER_BASE = 'http://127.0.0.1:8765/v1';
const TRACKER_ALARM = 'vintendo-tracker-poll';
const META_URL = 'https://www.meta.ai/create';
const TRACKER_RECOVERY_ATTEMPTS_KEY = 'trackerArtworkRecoveryAttemptsV2';
let pollRunning = false;
const trackerArtworkTransfers = new Set();

async function settings() {
  return chrome.storage.local.get(['trackerPairingToken', 'trackerAutomationEnabled', 'trackerActiveJob', 'trackerActiveCinematicJob']);
}

function setStatus(status, detail = '') {
  chrome.storage.local.set({ trackerConnectionStatus: { status, detail, updatedAt: Date.now() } }).catch(() => {});
}

const TRACKER_WORKER_STARTED_AT = Date.now();
function setDiagnostics(patch = {}) {
  chrome.storage.local.get('trackerDiagnostics').then(previous => {
    const prior = previous.trackerDiagnostics || {};
    return chrome.storage.local.set({ trackerDiagnostics: { ...prior, workerStartedAt: TRACKER_WORKER_STARTED_AT, ...patch } });
  }).catch(() => {});
}

async function trackerRequest(path, init = {}) {
  const saved = await settings();
  const token = saved.trackerPairingToken || '';
  if (!token) throw new Error('Pair Vintendo COD Automation with the tracker first.');
  const response = await fetch(`${TRACKER_BASE}${path}`, {
    ...init,
    cache: 'no-store',
    credentials: 'omit',
    headers: {
      Authorization: `Bearer ${token}`,
      'X-Vintendo-Extension-ID': chrome.runtime.id,
      ...(init.headers || {}),
    },
  });
  const contentType = response.headers.get('content-type') || '';
  if (!response.ok) {
    const body = contentType.includes('json') ? await response.json().catch(() => ({})) : {};
    throw new Error(body.error || `Tracker bridge returned HTTP ${response.status}`);
  }
  return contentType.includes('json') ? response.json() : response;
}

async function setJobState(jobId, state, error = '') {
  return trackerRequest(`/jobs/${encodeURIComponent(jobId)}/state`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ state, error }),
  });
}

async function markFailed(jobId, error) {
  try { await setJobState(jobId, 'failed', String(error || 'Automation failed.').slice(0, 280)); }
  catch (reportError) { setStatus('error', `${error}; could not report failure: ${reportError.message}`); }
  const saved = await settings();
  if (saved.trackerActiveJob?.jobId === jobId) await chrome.storage.local.remove('trackerActiveJob');
  setStatus('failed', `${jobId}: ${error}`);
}

function sendToTab(tabId, message) {
  return new Promise((resolve, reject) => chrome.tabs.sendMessage(tabId, message, response => {
    const error = chrome.runtime.lastError;
    if (error) reject(new Error(error.message));
    else resolve(response);
  }));
}

function reloadMetaTab(tabId) {
  return new Promise(async (resolve, reject) => {
    let timer;
    const onUpdated = (updatedId, change, tab) => {
      if (updatedId === tabId && change.status === 'complete') {
        clearTimeout(timer);
        chrome.tabs.onUpdated.removeListener(onUpdated);
        resolve(tab);
      }
    };
    try {
      const tab = await chrome.tabs.get(tabId);
      if (!/^https?:\/\/(?:www\.)?meta\.ai\//i.test(tab.url || '')) throw new Error('The automation tab is no longer on Meta AI.');
      chrome.tabs.onUpdated.addListener(onUpdated);
      timer = setTimeout(() => {
        chrome.tabs.onUpdated.removeListener(onUpdated);
        reject(new Error('Meta AI did not finish reloading.'));
      }, 45000);
      await chrome.tabs.reload(tabId);
    } catch (error) {
      if (timer) clearTimeout(timer);
      chrome.tabs.onUpdated.removeListener(onUpdated);
      reject(error);
    }
  });
}

function conversationMarker(candidate) {
  const player = candidate?.squad?.[0];
  if (!player) return '';
  const mapping = (candidate.referencePhotoMappings || []).find(item => item.username === player.username);
  const displayName = mapping?.posterName || player.username;
  const statistics = Object.entries(player.statistics || {});
  if (statistics.length) {
    return `${displayName}: ${statistics.map(([key, value]) => `${key}=${value}`).join(', ')}`;
  }
  return String(displayName || '');
}

async function metaTab(candidate, { requireConversation = false } = {}) {
  const tabs = await chrome.tabs.query({ url: ['*://meta.ai/*', '*://www.meta.ai/*'] });
  if (requireConversation) {
    const marker = conversationMarker(candidate);
    if (marker) {
      for (const candidateTab of tabs) {
        if (candidateTab.id === undefined) continue;
        try {
          const match = await sendToTab(candidateTab.id, { type: 'TRACKER_MATCH_CONVERSATION', marker });
          if (match?.matches) return candidateTab;
        } catch {}
      }
    }
    throw new Error('Could not find the Meta AI conversation for this match. Open its existing result and retry recovery.');
  }
  let tab = tabs.find(item => item.id !== undefined);
  if (!tab) {
    tab = await chrome.tabs.create({ url: META_URL, active: true });
  }
  let reloaded = false;
  for (let attempt = 0; attempt < 90; attempt++) {
    try {
      const response = await sendToTab(tab.id, { type: 'TRACKER_BIND_JOB', jobId: 'vintendo-handshake' });
      if (response?.success) return tab;
    } catch {}
    if (!reloaded && attempt === 5) {
      const current = await chrome.tabs.get(tab.id).catch(() => null);
      if (current?.status === 'complete' && /^https?:\/\/(?:www\.)?meta\.ai\//i.test(current.url || '')) {
        await reloadMetaTab(tab.id);
        reloaded = true;
      }
    }
    await new Promise(resolve => setTimeout(resolve, 1000));
    tab = await chrome.tabs.get(tab.id).catch(() => null);
    if (!tab) throw new Error('Meta AI tab closed before the automation was ready.');
    if (tab.status === 'complete' && !/meta\.ai\//i.test(tab.url || '')) {
      throw new Error('The Meta AI tab navigated away before generation started.');
    }
  }
  throw new Error('Vintendo COD Automation did not connect to the Meta AI page. Check that you are signed in and retry.');
}

async function imageBase64(jobId, photo) {
  const path = `/jobs/${encodeURIComponent(jobId)}/photos/${encodeURIComponent(photo.filename)}`;
  const response = await trackerRequest(path);
  const bytes = new Uint8Array(await response.arrayBuffer());
  if (!bytes.length || bytes.length > 10 * 1024 * 1024) throw new Error(`Reference image ${photo.filename} has an invalid size.`);
  let binary = '';
  const chunkSize = 0x8000;
  for (let index = 0; index < bytes.length; index += chunkSize) {
    binary += String.fromCharCode(...bytes.subarray(index, Math.min(index + chunkSize, bytes.length)));
  }
  return { name: photo.filename, base64: btoa(binary) };
}

async function cinematicState(id,state,error=''){return trackerRequest(`/cinematics/${encodeURIComponent(id)}/state`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({state,error})});}
async function processCinematicJob(candidate){const id=String(candidate.id||'');try{let job=null;try{const claim=await trackerRequest(`/cinematics/${encodeURIComponent(id)}/claim`,{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});job=claim.job;}catch(claimError){const prior=await settings();const current=await trackerRequest(`/cinematics/${encodeURIComponent(id)}`).catch(()=>null);if(prior.trackerActiveCinematicJob?.id===id&&current?.job?.status==='running')job=current.job;else throw claimError;}if(!job||job.status!=='running')throw new Error('Tracker did not acknowledge the cinematic job claim.');await chrome.storage.local.set({trackerActiveCinematicJob:{id,tabId:null,startedAt:Date.now(),downloadClaimed:false,stage:'claimed'}});const response=await trackerRequest(`/cinematics/${encodeURIComponent(id)}/image`),bytes=new Uint8Array(await response.arrayBuffer());if(!bytes.length||bytes.length>20*1024*1024)throw new Error('Cinematic screenshot is empty or exceeds 20 MB.');let binary='';for(let i=0;i<bytes.length;i+=0x8000)binary+=String.fromCharCode(...bytes.subarray(i,Math.min(i+0x8000,bytes.length)));let talentImages=[];try{try{const talentResponse=await trackerRequest(`/cinematics/talent`);const talentBytes=new Uint8Array(await talentResponse.arrayBuffer());if(talentBytes.length&&talentBytes.length<=20*1024*1024){let talentBinary='';for(let i=0;i<talentBytes.length;i+=0x8000)talentBinary+=String.fromCharCode(...talentBytes.subarray(i,Math.min(i+0x8000,talentBytes.length)));talentImages=[{name:'talent.png',base64:btoa(talentBinary)}];}}catch{}try{const talentList=await trackerRequest(`/cinematics/${encodeURIComponent(id)}/talent`);const talentJson=await talentList.json();const talentEntries=Array.isArray(talentJson&&talentJson.talent)?talentJson.talent:[];for(const entry of talentEntries.slice(0,3)){try{if(!entry||!entry.url)continue;const fileResponse=await trackerRequest(entry.url);if(!fileResponse.ok)continue;const fileBytes=new Uint8Array(await fileResponse.arrayBuffer());if(!fileBytes.length||fileBytes.length>20*1024*1024)continue;let fileBinary='';for(let i=0;i<fileBytes.length;i+=0x8000)fileBinary+=String.fromCharCode(...fileBytes.subarray(i,Math.min(i+0x8000,fileBytes.length)));talentImages.push({name:String(entry.filename||'talent.png'),base64:btoa(fileBinary)});}catch{}}}catch{}}catch{}const tab=await metaTab({});await chrome.storage.local.set({trackerActiveCinematicJob:{id,tabId:tab.id,startedAt:Date.now(),downloadClaimed:false,stage:'submitting'}});await cinematicState(id,'awaiting_generation');await cinematicState(id,'awaiting_download');const payload={mode:'imageToVideo',prompt:job.prompt,aspectRatio:'16:9',promptIndex:0,batchSize:1,outputCount:1,maxRetries:0,autoChangeFileName:true,folderName:`Vintendo-COD-Automation/Cinematics/${id}`,images:[{name:`${id}.png`,base64:btoa(binary)},...talentImages]};const ack=await sendToTab(tab.id,{type:'AUTO_FILL_META',payloads:[payload],groupId:`cinematic-${id}`,concurrentPrompts:1,promptDelaySecondsMin:0,promptDelaySecondsMax:0});if(!ack?.success)throw new Error(ack?.error||'Meta AI did not acknowledge Frame to Video.');setStatus('cinematic-generating',`${id} · Frame to Video`);}catch(error){await cinematicState(id,'failed',String(error.message||error).slice(0,300)).catch(()=>{});const saved=await settings();if(saved.trackerActiveCinematicJob?.id===id)await chrome.storage.local.remove('trackerActiveCinematicJob');setStatus('cinematic-failed',`${id}: ${error.message||String(error)}`);}}
async function uploadCinematicDownload(item){const url=String(item?.url||item?.finalUrl||''),filename=String(item?.filename||'');if(!item||item.state!=='complete'||!/\.mp4$/i.test(filename))return false;const saved=await settings(),active=saved.trackerActiveCinematicJob,started=Number(active?.startedAt||0),downloadAt=Date.parse(item.startTime||'')||0;const normalized=filename.replace(/\\/g,'/'),expected=`/Cinematics/${active?.id}/`;const recognized=/(?:meta\.ai|fbcdn)/i.test(url)||(active?.id&&normalized.includes(expected));if(!active||active.downloadClaimed||!recognized||(started&&downloadAt&&downloadAt<started-3000))return false;await chrome.storage.local.set({trackerActiveCinematicJob:{...active,downloadClaimed:true,downloadId:item.id}});try{try{const source=await fetch(url,{credentials:'include',cache:'no-store'});if(!source.ok)throw new Error(`Meta AI video fetch returned HTTP ${source.status}`);const video=await source.blob();if(!video.size||video.size<1024||video.size>250*1024*1024)throw new Error('Generated video size is invalid for tracker transfer.');const returned=await trackerRequest(`/cinematics/${encodeURIComponent(active.id)}/video`,{method:'POST',headers:{'Content-Type':'video/mp4'},body:video});if(!returned?.job||returned.job.status!=='completed')throw new Error('Tracker did not acknowledge the cinematic video.');}catch(swError){const result=await sendToTab(active.tabId,{type:'TRACKER_UPLOAD_CINEMATIC_VIDEO',jobId:active.id,url});if(!result?.success)throw new Error(result?.error||swError.message||'Meta AI video transfer failed.');}await chrome.storage.local.remove('trackerActiveCinematicJob');setStatus('cinematic-completed',active.id);return true;}catch(error){await chrome.storage.local.set({trackerActiveCinematicJob:{...active,downloadClaimed:false}});await cinematicState(active.id,'failed',String(error.message||error).slice(0,300)).catch(()=>{});setStatus('cinematic-failed',`${active.id}: ${error.message||String(error)}`);return false;}}
async function processJob(candidate) {
  const jobId = candidate.jobId;
  try {
    setStatus('preparing', jobId);
    const prepared = await trackerRequest(`/jobs/${encodeURIComponent(jobId)}/prepare`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ automatic: true }),
  });
    const job = prepared.job;
    if (!['Victory', 'Defeat'].includes(job.result)) throw new Error('Outcome is unknown. The tracker will not assume a win or loss.');
    const participants = job.squad || [];
    if (participants.length < 1 || participants.length > 4) throw new Error('The match does not contain one to four identified participants.');
    if (Number(job.playerCount) !== participants.length) throw new Error('Tracker participant count does not match the identified player list.');
    if (!job.mode || job.mode.startsWith('Not shown')) throw new Error('Game mode is not known for this match.');
    const photos = prepared.referencePhotos || [];
    if (photos.length > 4) throw new Error('The tracker supplied more than four headshots for this match.');
    const mappings = job.referencePhotoMappings || [];
    for (const participant of participants) {
      const mapping = mappings.find(item => item.username === participant.username);
      if (!mapping || mapping.status !== 'ready' || !mapping.filename) {
        throw new Error(`Missing identity-matched, consented headshot for ${participant.username}: ${mapping?.reason || 'no photo is available'}`);
      }
    }
    if (photos.length !== participants.length) throw new Error(`Tracker supplied ${photos.length} headshots for ${participants.length} identified players.`);
    const images = [];
    for (const photo of photos) images.push(await imageBase64(jobId, photo));

    const tab = await metaTab(candidate);
    await setJobState(jobId, 'awaiting_generation');
    await chrome.storage.local.set({ trackerActiveJob: { jobId, tabId: tab.id, startedAt: Date.now() } });
    const bound = await sendToTab(tab.id, { type: 'TRACKER_BIND_JOB', jobId });
    if (!bound?.success) throw new Error(bound?.error || 'Could not bind the Meta AI tab to this match.');
    await setJobState(jobId, 'awaiting_download');
    const promptJob = {
      mode: 'textToImage', prompt: job.prompt, aspectRatio: '16:9', trackerMatchId: jobId, promptIndex: 0,
      batchSize: 1, outputCount: 1, maxRetries: 0, autoChangeFileName: true,
      folderName: `Vintendo-COD-Automation/${jobId}`, images,
    };
    const acknowledgement = await sendToTab(tab.id, {
      type: 'AUTO_FILL_META', payloads: [promptJob], groupId: jobId,
      concurrentPrompts: 1, promptDelaySecondsMin: 0, promptDelaySecondsMax: 0,
    });
    if (!acknowledgement?.success) throw new Error(acknowledgement?.error || 'Meta AI did not acknowledge the match job.');
    setStatus('generating', `${jobId} · ${participants.length} player(s) · ${images.length} reference image(s)`);
  } catch (error) {
    await markFailed(jobId, error.message || String(error));
  }
}

async function pollTracker() {
  if (pollRunning) return;
  pollRunning = true;
  try {
    const saved = await settings();
    if (!saved.trackerAutomationEnabled || !saved.trackerPairingToken) {
      setStatus(saved.trackerPairingToken ? 'paused' : 'not-paired');
      return;
    }
    const snapshot = await trackerRequest('/status');
    setStatus('connected', `${snapshot.pendingCount || 0} match job(s)`);
    setDiagnostics({ lastPollAt: Date.now(), lastError: '', queuedMatch: snapshot.pendingCount || 0, activeMatchJob: saved.trackerActiveJob?.jobId || null, activeCinematicId: saved.trackerActiveCinematicJob?.id || null });
    if (saved.trackerActiveJob?.jobId) {
      // Retire an expired match job before attempting recovery. Recovery may
      // fail when its Meta tab/content script is gone; it must not block the
      // independent cinematic queue forever.
      if (Date.now() - Number(saved.trackerActiveJob.startedAt || Date.now()) > 30 * 60 * 1000) {
        await markFailed(saved.trackerActiveJob.jobId, 'Meta AI generation or download timed out after 30 minutes.');
        return;
      }
      const claimAt = Number(saved.trackerActiveJob.artworkUploadClaimedAt || 0);
      const claimStale = saved.trackerActiveJob.artworkUploadStarted && (!claimAt || Date.now() - claimAt > 90_000);
      if (!saved.trackerActiveJob.artworkUploadStarted || claimStale) {
        await recoverTrackerDownload();
      }
      const response = await trackerRequest(`/jobs/${encodeURIComponent(saved.trackerActiveJob.jobId)}`);
      if (response.job?.artworkReady) {
        await chrome.storage.local.remove('trackerActiveJob');
        await chrome.storage.local.remove(TRACKER_RECOVERY_ATTEMPTS_KEY);
        setStatus('completed', saved.trackerActiveJob.jobId);
      } else if (response.job?.state === 'failed') {
        await chrome.storage.local.remove('trackerActiveJob');
        setStatus('failed', `${saved.trackerActiveJob.jobId}: ${response.job.error || 'Meta AI handoff failed.'}`);
      } else if (response.job?.state === 'prepared' &&
                 Number(response.job.generationAttempts || 0) > 0 &&
                 Date.now() - Number(saved.trackerActiveJob.startedAt || Date.now()) > 60_000) {
        // Resume a generated-but-unclaimed result using its existing Meta
        // response. This path must never resubmit the prompt.
        const jobId = saved.trackerActiveJob.jobId;
        const tab = await metaTab();
        await sendToTab(tab.id, { type: 'TRACKER_BIND_JOB', jobId });
        await setJobState(jobId, 'awaiting_generation');
        await setJobState(jobId, 'awaiting_download');
        const redownload = await sendToTab(tab.id, { type: 'TRACKER_REDOWNLOAD_EXISTING_ARTWORK', jobId });
        if (!redownload?.success) throw new Error(redownload?.error || 'Meta AI could not redownload the existing result.');
        setStatus('recovering-artwork', `${jobId} · reconnecting to the existing Meta AI result`);
      }
      // If the match job finished during this poll, fall through so an
      // eligible cinematic job is not starved until the next alarm.
      const afterMatch = await settings();
      if (afterMatch.trackerActiveJob?.jobId) return;
    }
    const next = (snapshot.jobs || []).find(item => item.state === 'pending');
    if (next) await processJob(next);
    else {
      // A service worker can be suspended after the tracker prepared a job but
      // before the worker stored trackerActiveJob. If Meta already generated
      // the image, resume by downloading that existing result; never generate
      // it a second time.
      const stranded = (snapshot.jobs || []).find(item => {
        const updated = Number(item.lastUpdated || 0);
        const updatedMs = updated > 0 && updated < 1e12 ? updated * 1000 : updated;
        return item.state === 'prepared' && Number(item.generationAttempts || 0) > 0 &&
          Date.now() - updatedMs > 60_000;
      });
      if (stranded) {
        await recoverPreparedArtworkJob(stranded);
        return;
      }
      const missedDownload = (snapshot.jobs || []).find(item =>
        item.state === 'failed' && /(?:Meta tab content script did not reconnect for artwork transfer|Could not upload Meta AI full-resolution artwork|fetching generated image from Meta AI|uploading generated image to the local tracker|Could not recover the existing Meta AI artwork)/i.test(item.error || '')
      );
      if (missedDownload) { await recoverFailedArtworkJob(missedDownload); return; }
    }
    const savedCinematic=await settings(),activeCinematic=savedCinematic.trackerActiveCinematicJob;
    if(activeCinematic?.id){if(Date.now()-Number(activeCinematic.startedAt||Date.now())>30*60*1000){await cinematicState(activeCinematic.id,'failed','Meta AI video generation/download timed out after 30 minutes.').catch(()=>{});await chrome.storage.local.remove('trackerActiveCinematicJob');setStatus('cinematic-failed',`${activeCinematic.id}: timed out`);return;}const state=await trackerRequest(`/cinematics/${encodeURIComponent(activeCinematic.id)}`);if(state.job?.status==='completed'||state.job?.status==='failed'){await chrome.storage.local.remove('trackerActiveCinematicJob');setStatus(state.job.status==='completed'?'cinematic-completed':'cinematic-failed',activeCinematic.id);}if(state.job?.status==='running'&&activeCinematic.stage==='claimed'&&Date.now()-Number(activeCinematic.startedAt||Date.now())>90*1000){await processCinematicJob({id:activeCinematic.id});}return;}
    const cinematic=await trackerRequest('/cinematics/pending');setDiagnostics({queuedCinematic:cinematic.jobs?.length||0,activeCinematicId:activeCinematic?.id||null});if(cinematic.jobs?.length)await processCinematicJob(cinematic.jobs[0]);
  } catch (error) {
    setDiagnostics({ lastPollAt: Date.now(), lastError: error.message || String(error) });
    const saved = await settings();
    if (saved.trackerActiveJob?.jobId && /unknown|participant|game mode/i.test(error.message)) {
      await markFailed(saved.trackerActiveJob.jobId, error.message);
    } else setStatus('error', error.message || String(error));
  } finally { pollRunning = false; }
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message?.type === 'PROMPT_GROUP_STATUS') {
    settings().then(async saved => {
      const activeVideo=saved.trackerActiveCinematicJob;
      if(activeVideo && message.data?.id===`cinematic-${activeVideo.id}` && ['error','cancelled'].includes(message.data?.status)){const failure=message.data.results?.find(item=>item.error)?.error||`Meta video generation ${message.data.status}.`;await cinematicState(activeVideo.id,'failed',failure.slice(0,300)).catch(()=>{});await chrome.storage.local.remove('trackerActiveCinematicJob');setStatus('cinematic-failed',`${activeVideo.id}: ${failure}`);return;}
      const active = saved.trackerActiveJob;
      if (!active || active.jobId !== message.data?.id) return;
      if (message.data?.status === 'error' || message.data?.status === 'cancelled') {
        const failure = message.data.results?.find(item => item.error)?.error || `Meta generation ${message.data.status}.`;
        await markFailed(active.jobId, failure);
      }
    }).catch(() => {});
  } else if (message?.type === 'TRACKER_EXPECT_ARTWORK_DOWNLOAD') {
    settings().then(async saved => {
      const active = saved.trackerActiveJob;
      if (!active || active.jobId !== message.jobId) {
        sendResponse({ success: false, error: 'No matching tracker job is active.' });
        return;
      }
      await chrome.storage.local.set({ trackerActiveJob: { ...active, awaitingArtworkDownload: true } });
      sendResponse({ success: true });
    }).catch(error => sendResponse({ success: false, error: error.message || String(error) }));
    return true;
  } else if (message?.type === 'TRACKER_POLL_NOW') {
    pollTracker().then(() => sendResponse({ success: true }))
      .catch(error => sendResponse({ success: false, error: error.message || String(error) }));
    return true;
  } else if (message?.type === 'TRACKER_REPORT_FAILURE') {
    markFailed(message.jobId, message.error || 'Meta AI artwork transfer failed.').then(() => sendResponse({ success: true }));
    return true;
  }
  return false;
});

async function uploadTrackerDownload(item) {
  const url = String(item?.url || item?.finalUrl || '');
  const imageName = String(item?.filename || '');
  if (!item || item.state !== 'complete' ||
      !/\.(?:png|jpe?g|webp)$/i.test(imageName)) return false;
  const saved = await settings();
  const active = saved.trackerActiveJob;
  const downloadTime = Date.parse(item.startTime || '') || 0;
  const startedAt = Number(active?.startedAt || 0);
  const claimAt = Number(active?.artworkUploadClaimedAt || 0);
  const claimStale = active?.artworkUploadStarted && (!claimAt || Date.now() - claimAt > 90_000);
  const knownMetaImage = /create-image/i.test(imageName) || /(?:fbcdn|meta\.ai)/i.test(url);
  if (!active || (active.artworkUploadStarted && !claimStale) || !knownMetaImage ||
      (!active.allowHistoricalDownload && downloadTime && startedAt && downloadTime < startedAt - 3000)) return false;
  if (trackerArtworkTransfers.has(active.jobId)) return false;
  trackerArtworkTransfers.add(active.jobId);
  await chrome.storage.local.set({ trackerActiveJob: {
    ...active, artworkUploadStarted: true, artworkUploadClaimedAt: Date.now(), artworkDownloadId: item.id,
  } });
  try {
    const source = await fetch(url, { credentials: 'include', cache: 'no-store' });
    if (!source.ok) throw new Error(`Meta AI image fetch returned HTTP ${source.status}`);
    const image = await source.blob();
    const type = image.type.toLowerCase().split(';')[0];
    if (!['image/png', 'image/jpeg', 'image/webp'].includes(type)) {
      throw new Error(`Meta AI returned unsupported artwork type: ${type || 'unknown'}`);
    }
    if (!image.size || image.size > 20 * 1024 * 1024) throw new Error('Meta AI artwork exceeds the tracker 20 MB limit.');
    const result = await trackerRequest(`/jobs/${encodeURIComponent(active.jobId)}/artwork`, {
      method: 'POST', headers: { 'Content-Type': type }, body: image,
    });
    if (!result.job?.artworkReady) throw new Error('Tracker did not acknowledge the artwork upload.');
    await chrome.storage.local.remove('trackerActiveJob');
    await chrome.storage.local.remove(TRACKER_RECOVERY_ATTEMPTS_KEY);
    setStatus('completed', active.jobId);
  return true;
  } catch (error) {
    await markFailed(active.jobId, `Meta AI artwork transfer failed: ${error.message || String(error)}`);
    return false;
  } finally {
    trackerArtworkTransfers.delete(active.jobId);
  }
}

async function recoverTrackerDownload() {
  const saved = await settings();
  const active = saved.trackerActiveJob;
  const claimAt = Number(active?.artworkUploadClaimedAt || 0);
  const claimStale = active?.artworkUploadStarted && (!claimAt || Date.now() - claimAt > 90_000);
  if (!active?.jobId || (active.artworkUploadStarted && !claimStale) || !Number.isInteger(active.tabId)) return;
  const items = await chrome.downloads.search({ orderBy: ['-startTime'], limit: 100 });
  for (const item of items) {
    if (await uploadTrackerDownload(item)) return true;
  }
  return false;
}

async function recoverFailedArtworkJob(candidate) {
  const jobId = candidate.jobId;
  try {
    const recoveryState = await chrome.storage.local.get(TRACKER_RECOVERY_ATTEMPTS_KEY);
    const attempts = recoveryState[TRACKER_RECOVERY_ATTEMPTS_KEY] || {};
    const count = Number(attempts[jobId] || 0);
    if (count >= 5) { setStatus('failed', `${jobId}: artwork transfer recovery limit reached.`); return; }
    await chrome.storage.local.set({ [TRACKER_RECOVERY_ATTEMPTS_KEY]: { ...attempts, [jobId]: count + 1 } });
    setStatus('recovering-artwork', `${jobId} · downloading the existing Meta AI result again`);
    await trackerRequest(`/jobs/${encodeURIComponent(jobId)}/retry`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({}),
    });
    await trackerRequest(`/jobs/${encodeURIComponent(jobId)}/prepare`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ automatic: true }),
    });
    const tab = await metaTab(candidate, { requireConversation: true });
    await setJobState(jobId, 'awaiting_generation');
    await chrome.storage.local.set({ trackerActiveJob: {
      jobId, tabId: tab.id, startedAt: Date.now(), recoveryOnly: true,
    } });
    await setJobState(jobId, 'awaiting_download');
    const uploaded = await recoverTrackerDownload();
    if (!uploaded) {
      await sendToTab(tab.id, { type: 'TRACKER_BIND_JOB', jobId });
      const redownload = await sendToTab(tab.id, { type: 'TRACKER_REDOWNLOAD_EXISTING_ARTWORK', jobId });
      if (!redownload?.success) throw new Error(redownload?.error || 'Meta AI did not start a fresh download of the existing result.');
    }
  } catch (error) {
    await markFailed(jobId, `Could not recover the existing Meta AI artwork: ${error.message || String(error)}`);
  }
}

async function recoverPreparedArtworkJob(candidate) {
  const jobId = candidate.jobId;
  try {
    const recoveryState = await chrome.storage.local.get(TRACKER_RECOVERY_ATTEMPTS_KEY);
    const attempts = recoveryState[TRACKER_RECOVERY_ATTEMPTS_KEY] || {};
    const count = Number(attempts[jobId] || 0);
    if (count >= 5) { setStatus('failed', `${jobId}: existing artwork recovery limit reached.`); return; }
    await chrome.storage.local.set({ [TRACKER_RECOVERY_ATTEMPTS_KEY]: { ...attempts, [jobId]: count + 1 } });
    setStatus('recovering-artwork', `${jobId} · reconnecting to the existing Meta AI result`);
    const tab = await metaTab(candidate, { requireConversation: true });
    await setJobState(jobId, 'awaiting_generation');
    await chrome.storage.local.set({ trackerActiveJob: {
      jobId, tabId: tab.id, startedAt: Date.now(), recoveryOnly: true,
    } });
    await setJobState(jobId, 'awaiting_download');
    await sendToTab(tab.id, { type: 'TRACKER_BIND_JOB', jobId });
    const uploaded = await recoverTrackerDownload();
    if (!uploaded) {
      const redownload = await sendToTab(tab.id, { type: 'TRACKER_REDOWNLOAD_EXISTING_ARTWORK', jobId });
      if (!redownload?.success) throw new Error(redownload?.error || 'Meta AI could not download the existing result.');
    }
  } catch (error) {
    await markFailed(jobId, `Could not recover the existing Meta AI artwork: ${error.message || String(error)}`);
  }
}

chrome.downloads.onCreated.addListener(item => {
  uploadCinematicDownload(item).catch(error=>setStatus('cinematic-failed',error.message||String(error)));
  uploadTrackerDownload(item).catch(error => setStatus('error', `Meta artwork transfer: ${error.message || String(error)}`));
});
chrome.downloads.onChanged.addListener(delta => {
  if (delta.state?.current !== 'complete' && !delta.filename?.current) return;
  chrome.downloads.search({ id: delta.id }).then(items => {if(items[0]){uploadCinematicDownload(items[0]).catch(error=>setStatus('cinematic-failed',error.message||String(error)));uploadTrackerDownload(items[0]);}})
    .catch(error => setStatus('error', `Meta artwork transfer: ${error.message || String(error)}`));
});

chrome.alarms.create(TRACKER_ALARM, { periodInMinutes: 0.5 });
chrome.alarms.onAlarm.addListener(alarm => { if (alarm.name === TRACKER_ALARM) pollTracker(); });
chrome.runtime.onStartup.addListener(() => pollTracker());
chrome.runtime.onInstalled.addListener(() => pollTracker());
chrome.tabs.onUpdated.addListener((_tabId, change) => { if (change.status === 'complete') pollTracker(); });
chrome.storage.onChanged.addListener((changes, area) => {
  if (area === 'local' && (changes.trackerPairingToken || changes.trackerAutomationEnabled)) pollTracker();
});

pollTracker();

