const tokenField=document.querySelector('#token');
const statusLine=document.querySelector('#status');

chrome.storage.local.get('pairingToken').then(async({pairingToken=''})=>{
  tokenField.value=pairingToken;
  if(pairingToken) await testConnection();
});

document.querySelector('#save').addEventListener('click',async()=>{
  const pairingToken=tokenField.value.trim();
  if(pairingToken.length<32){statusLine.textContent='Token is missing or incomplete. Copy it again from Application Settings.';return;}
  await chrome.storage.local.set({pairingToken});
  statusLine.textContent='Token saved. Checking the tracker…';
  await testConnection();
});
document.querySelector('#test').addEventListener('click',testConnection);

async function testConnection(){
  try{
    const result=await chrome.runtime.sendMessage({type:'snapshot'});
    if(!result?.ok) throw new Error(result?.error||'The tracker did not respond.');
    statusLine.textContent=`Connected to ${result.value.app}. ${result.value.pendingCount} queued match(es).`;
    statusLine.style.color='#77dda5';
  }catch(error){
    const message=String(error.message||error);
    if(message.includes('Pair this extension')) statusLine.textContent='No pairing token saved. Copy it from the running tracker’s Application Settings, paste it above, then choose Save and test.';
    else if(message.includes('HTTP 401')) statusLine.textContent='Token rejected. Copy the current token from Application Settings and save it again.';
    else if(message.includes('extension identity headers required')||message.includes('extension identity header required')) statusLine.textContent='This extension copy is outdated. Open chrome://extensions, click Reload for Vintendo Squad AI, then choose Test again.';
    else if(message.includes('extension identity rejected')) statusLine.textContent='The tracker rejected this extension copy. Load the supplied Chrome-Extension folder in Chrome, then retry.';
    else if(message.includes('extension origin rejected')) statusLine.textContent='The browser supplied a different origin. Reload Vintendo Squad AI in chrome://extensions, then test again.';
    else if(message.includes('HTTP 403')) statusLine.textContent='Extension origin rejected (HTTP 403). Check the extension ID in chrome://extensions and reload the supplied Chrome-Extension folder.';
    else if(message.includes('Failed to fetch')) statusLine.textContent='Tracker not reachable. Open VintendoCODTracker.exe, wait for its window, then choose Test again.';
    else statusLine.textContent=`Not connected: ${message}`;
    statusLine.style.color='#ffaaa4';
  }
}
