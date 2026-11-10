const API = 'http://127.0.0.1:8765/v1';
const META = 'https://www.meta.ai/';

async function token() {
  const {pairingToken = ''} = await chrome.storage.local.get('pairingToken');
  return pairingToken.trim();
}
async function api(path, options = {}) {
  const bearer = await token();
  if (!bearer) throw new Error('Pair this extension in Options first.');
  const headers = new Headers(options.headers || {});
  headers.set('Authorization', `Bearer ${bearer}`);
  headers.set('X-Vintendo-Extension-ID', chrome.runtime.id);
  if (options.body && !(options.body instanceof ArrayBuffer) && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
  const response = await fetch(API + path, {...options, headers, cache: 'no-store', credentials: 'omit'});
  const data = response.status === 204 ? {} : await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(`${data.error || 'Tracker bridge rejected the request'} (HTTP ${response.status})`);
  return data;
}
function safeId(value) { return encodeURIComponent(String(value)); }
async function currentMetaTabs() {
  const {metaTabs={}}=await chrome.storage.local.get('metaTabs');let changed=false;
  for(const key of Object.keys(metaTabs))try{await chrome.tabs.get(Number(key));}catch{delete metaTabs[key];changed=true;}
  if(changed){const values=Object.values(metaTabs);await chrome.storage.local.set({metaTabs,armedJobId:values.length?values[0]:''});}
  return metaTabs;
}

chrome.downloads.onCreated.addListener(async item => {
  const referrer=(()=>{try{return new URL(item.referrer||'');}catch{return null;}})();
  const siteDownload=referrer?.protocol==='https:'&&['meta.ai','www.meta.ai'].includes(referrer.hostname);
  const mockDownload=referrer?.origin==='http://127.0.0.1:8766'&&referrer.pathname==='/__vintendo_mock__/';
  if (!siteDownload&&!mockDownload) return;
  const {metaTabs = {}, armedJobId=''} = await chrome.storage.local.get(['metaTabs','armedJobId']);
  const activeTabs=await chrome.tabs.query({active:true,lastFocusedWindow:true});
  const activeJobs=[...new Set(activeTabs.map(tab=>metaTabs[String(tab.id)]).filter(Boolean))];
  const jobId=activeJobs.length===1&&activeJobs[0]===armedJobId?armedJobId:'';
  if (!jobId) return;
  const {trackedDownloads = {}} = await chrome.storage.local.get('trackedDownloads');
  trackedDownloads[String(item.id)] = {downloadId:item.id, jobId, filename:item.filename || '', state:item.state || 'in_progress', createdAt:item.startTime || new Date().toISOString()};
  for (const key of Object.keys(trackedDownloads).sort((a,b)=>String(trackedDownloads[a].createdAt).localeCompare(String(trackedDownloads[b].createdAt))).slice(0,Math.max(0,Object.keys(trackedDownloads).length-100))) delete trackedDownloads[key];
  await chrome.storage.local.set({trackedDownloads, lastDownload:trackedDownloads[String(item.id)]});
});

chrome.downloads.onChanged.addListener(async delta => {
  const id = String(delta.id);
  const {trackedDownloads = {}} = await chrome.storage.local.get('trackedDownloads');
  const record = trackedDownloads[id];
  if (!record) return;
  if (delta.filename?.current) record.filename = delta.filename.current;
  if (delta.state?.current) record.state = delta.state.current;
  if (delta.error?.current) record.error = delta.error.current;
  if (record.state === 'complete') {
    record.completedAt = new Date().toISOString();
    try {
      const job=(await api(`/jobs/${safeId(record.jobId)}`)).job;
      if (['prepared','awaiting_generation'].includes(job.state))
        await api(`/jobs/${safeId(record.jobId)}/state`,{method:'POST',body:JSON.stringify({state:'awaiting_download'})});
    } catch (_) { /* The UI still reports the download; local pairing may need retry. */ }
  }
  await chrome.storage.local.set({trackedDownloads, lastDownload:record});
});

chrome.tabs.onRemoved.addListener(async tabId => {
  const {metaTabs = {}} = await chrome.storage.local.get('metaTabs');
  if (metaTabs[String(tabId)]) {
    delete metaTabs[String(tabId)];
    const next=Object.values(metaTabs);
    await chrome.storage.local.set({metaTabs,armedJobId:next.length?next[0]:''});
  }
  const {pendingImport} = await chrome.storage.local.get('pendingImport');
  if (pendingImport?.tabId === tabId) await chrome.storage.local.remove('pendingImport');
});

chrome.runtime.onMessage.addListener((message, sender, reply) => {
  (async () => {
    switch (message?.type) {
      case 'snapshot': return await api('/status');
      case 'prepare': return await api(`/jobs/${safeId(message.jobId)}/prepare`, {method:'POST', body:'{}'});
      case 'state': return await api(`/jobs/${safeId(message.jobId)}/state`, {method:'POST', body:JSON.stringify({state:message.state})});
      case 'cancel': return await api(`/jobs/${safeId(message.jobId)}/cancel`, {method:'POST', body:'{}'});
      case 'retry': return await api(`/jobs/${safeId(message.jobId)}/retry`, {method:'POST', body:'{}'});
      case 'checkDownload': {
        if (!message.jobId) throw new Error('Select a match first.');
        const {trackedDownloads = {}} = await chrome.storage.local.get('trackedDownloads');
        const filename = String(message.filename || '').split(/[\\/]/).pop().toLowerCase();
        const matching = Object.values(trackedDownloads).filter(item => item.state === 'complete' &&
          String(item.filename || '').split(/[\\/]/).pop().toLowerCase() === filename);
        const matchedJobs = [...new Set(matching.map(item => item.jobId))];
        if (matchedJobs.length > 1) throw new Error('That filename is associated with multiple jobs; rename the downloaded image and select it again.');
        if (matchedJobs.length === 1 && matchedJobs[0] !== message.jobId)
          throw new Error(`This download belongs to match ${matchedJobs[0]}; it cannot be attached to ${message.jobId}.`);
        return {allowed:true,tracked:matchedJobs.length===1};
      }
      case 'openMeta': {
        const current=(await api(`/jobs/${safeId(message.jobId)}`)).job;
        if (current.state==='prepared') await api(`/jobs/${safeId(message.jobId)}/state`, {method:'POST', body:JSON.stringify({state:'awaiting_generation'})});
        if (!['prepared','awaiting_generation','awaiting_download'].includes(current.state)) throw new Error('Prepare this match before opening Meta AI.');
        const metaTabs=await currentMetaTabs();
        if (Object.values(metaTabs).some(id=>id!==message.jobId)) throw new Error('Finish or close the other Meta AI match tab before opening another job.');
        const tab = await chrome.tabs.create({url:META, active:true});
        metaTabs[String(tab.id)] = message.jobId;
        await chrome.storage.local.set({metaTabs,armedJobId:message.jobId});
        return {opened:true, tabId:tab.id};
      }
      case 'lastDownload': return (await chrome.storage.local.get('lastDownload')).lastDownload || null;
      case 'pendingImport': return (await chrome.storage.local.get('pendingImport')).pendingImport || null;
      case 'openImport': {
        const tab=await chrome.tabs.create({url:chrome.runtime.getURL(`import.html?job=${safeId(message.jobId)}`),active:true});
        await chrome.storage.local.set({pendingImport:{jobId:message.jobId,tabId:tab.id,filename:'',ready:false}});
        return {opened:true,tabId:tab.id};
      }
      case 'importReady': {
        const {pendingImport} = await chrome.storage.local.get('pendingImport');
        if (!sender.url?.startsWith(chrome.runtime.getURL('import.html')) || !pendingImport || pendingImport.tabId !== message.tabId || pendingImport.jobId !== message.jobId) throw new Error('Import page is not bound to the selected match.');
        pendingImport.filename=String(message.filename||'').slice(0,200);pendingImport.ready=true;
        await chrome.storage.local.set({pendingImport});return pendingImport;
      }
      case 'clearImport': {
        const {pendingImport} = await chrome.storage.local.get('pendingImport');
        if (sender.url?.startsWith(chrome.runtime.getURL('import.html')) && pendingImport?.tabId === message.tabId) await chrome.storage.local.remove('pendingImport');
        return {cleared:true};
      }
      // Only the separately loaded mock-test manifest grants this page host.
      // The production manifest has no content script or localhost mock host permission.
      case 'runMock': {
        const tabId = Number(message.tabId);
        const tab = await chrome.tabs.get(tabId);
        if (!tab.url?.startsWith('http://127.0.0.1:8766/__vintendo_mock__/')) throw new Error('Mock controls are restricted to the local test fixture.');
        const {metaTabs={}}=await chrome.storage.local.get('metaTabs');metaTabs[String(tabId)]='mock-match-1';
        await chrome.storage.local.set({metaTabs,armedJobId:'mock-match-1'});await chrome.tabs.update(tabId,{active:true});
        return await chrome.tabs.sendMessage(tabId, {type:'mockGenerate', prompt:message.prompt, photos:message.photos || []});
      }
      default: throw new Error('Unknown extension action.');
    }
  })().then(value => reply({ok:true, value})).catch(error => reply({ok:false, error:String(error.message || error)}));
  return true;
});
