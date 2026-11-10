import fs from 'node:fs/promises';
import http from 'node:http';
import net from 'node:net';
import path from 'node:path';
import os from 'node:os';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';

const here=path.dirname(fileURLToPath(import.meta.url));
const project=path.resolve(here,'..','..');
const extensionSource=path.join(project,'Chrome-Extension');
const chrome='C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
const extensionId='dmkkcacnncdkdndjbjhcheijfbneklef';
const work=path.join(project,'..','..','work','squad-browser-extension-tests','cdp-mock-'+Date.now());
await fs.mkdir(work,{recursive:true});
const extension=path.join(work,'extension');
await fs.cp(extensionSource,extension,{recursive:true});
const template=JSON.parse(await fs.readFile(path.join(here,'manifest.template.json'),'utf8'));
await fs.writeFile(path.join(extension,'manifest.json'),JSON.stringify(template,null,2));
const downloads=path.join(work,'downloads');await fs.mkdir(downloads);
const profile=path.join(work,'profile');
const server=http.createServer(async(req,res)=>{
  let target=(req.url||'/').split('?')[0];
  if(target.startsWith('/__vintendo_mock__/'))target=target.slice('/__vintendo_mock__'.length);
  if(!target||target==='/')target='/index.html';
  const file=path.resolve(here,'.'+decodeURIComponent(target));
  if(!file.startsWith(here+path.sep)){res.writeHead(403);return res.end();}
  try{const data=await fs.readFile(file);const type=file.endsWith('.html')?'text/html':file.endsWith('.js')?'text/javascript':'application/octet-stream';res.writeHead(200,{'Content-Type':type,'Access-Control-Allow-Origin':'*'});res.end(data);}
  catch{res.writeHead(404);res.end('not found');}
});
const port=await new Promise((resolve,reject)=>{server.once('error',reject);server.listen(8766,'127.0.0.1',()=>resolve(server.address().port));});
const socket=net.createServer();const cdpPort=await new Promise((resolve,reject)=>{socket.once('error',reject);socket.listen(0,'127.0.0.1',()=>{const p=socket.address().port;socket.close(()=>resolve(p));});});
let browser;
async function getJson(url,options={}){const response=await fetch(url,options);if(!response.ok)throw new Error(`${url} HTTP ${response.status}`);return response.json();}
async function waitFor(fn,timeout=15000){const start=Date.now();let lastError='';while(Date.now()-start<timeout){try{const value=await fn();if(value)return value;}catch(error){lastError=String(error.message||error);}await new Promise(r=>setTimeout(r,150));}throw new Error(`Timed out waiting for Chrome endpoint ${cdpPort} (${lastError}); pid=${browser?.pid}; exitCode=${browser?.exitCode}; signal=${browser?.signalCode}`);}
class CDP {
  constructor(url){this.ws=new WebSocket(url);this.next=0;this.waiters=new Map();this.ready=new Promise((resolve,reject)=>{this.ws.onopen=resolve;this.ws.onerror=reject;});this.ws.onmessage=event=>{const msg=JSON.parse(event.data);if(msg.id&&this.waiters.has(msg.id)){const w=this.waiters.get(msg.id);this.waiters.delete(msg.id);msg.error?w.reject(new Error(msg.error.message)):w.resolve(msg.result);}};}
  async call(method,params={}){await this.ready;const id=++this.next;const pending=new Promise((resolve,reject)=>this.waiters.set(id,{resolve,reject}));this.ws.send(JSON.stringify({id,method,params}));return pending;}
  close(){this.ws.close();}
}
try{
  if(!(await fs.stat(chrome).catch(()=>null)))throw new Error('Chrome is not installed at the expected path.');
  browser=spawn(chrome,[`--remote-debugging-port=${cdpPort}`,`--user-data-dir=${profile}`,`--load-extension=${extension}`,`--disable-extensions-except=${extension}`,`--download.default_directory=${downloads}`,'--download.prompt_for_download=false','--disable-background-networking','--no-first-run','--no-default-browser-check','--headless=new','about:blank'],{stdio:'ignore',windowsHide:true});
  const version=await waitFor(()=>getJson(`http://127.0.0.1:${cdpPort}/json/version`));
  const browserCdp=new CDP(version.webSocketDebuggerUrl);await browserCdp.call('Browser.setDownloadBehavior',{behavior:'allow',downloadPath:downloads});
  const mock=await getJson(`http://127.0.0.1:${cdpPort}/json/new?${encodeURIComponent(`http://127.0.0.1:8766/__vintendo_mock__/`)}`,{method:'PUT'});
  const mockCdp=new CDP(mock.webSocketDebuggerUrl);await mockCdp.call('Page.enable');
  const popup=await getJson(`http://127.0.0.1:${cdpPort}/json/new?${encodeURIComponent(`chrome-extension://${extensionId}/popup.html`)}`,{method:'PUT'});
  const popupCdp=new CDP(popup.webSocketDebuggerUrl);await popupCdp.call('Runtime.enable');
  const prompt='Create a 16:9 horizontal comedy poster with four teammates.';
  const pixel='data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII=';
  const expression=`(async()=>{const tabs=await chrome.tabs.query({url:'http://127.0.0.1:8766/__vintendo_mock__/*'});if(!tabs.length)throw new Error('No mock page tab');await chrome.storage.local.set({metaTabs:{[String(tabs[0].id)]:'mock-match-1'}});return await new Promise(resolve=>chrome.runtime.sendMessage({type:'runMock',tabId:tabs[0].id,prompt:${JSON.stringify(prompt)},photos:[{name:'alpha.png',mime:'image/png',dataUrl:${JSON.stringify(pixel)}},{name:'bravo.png',mime:'image/png',dataUrl:${JSON.stringify(pixel)}}]},resolve));})()`;
  const action=await popupCdp.call('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});
  const result=action.result?.value;if(!result||result.error)throw new Error(`Mock interaction failed: ${JSON.stringify(result)}`);
  await waitFor(async()=>{const items=await fs.readdir(downloads);return items.find(n=>n.endsWith('.png'));},15000);
  const last=await popupCdp.call('Runtime.evaluate',{expression:`new Promise(resolve=>chrome.runtime.sendMessage({type:'lastDownload'},resolve))`,awaitPromise:true,returnByValue:true});
  const saved=path.join(downloads,(await fs.readdir(downloads)).find(n=>n.endsWith('.png')));
  const bytes=await fs.readFile(saved);
  const check=await mockCdp.call('Runtime.evaluate',{expression:`JSON.stringify({prompt:document.querySelector('#prompt').value,photos:document.querySelector('#photos').files.length,names:document.querySelector('#photo-list').textContent,complete:!!document.querySelector('#completed-image')})`,returnByValue:true});
  const pageResult=JSON.parse(check.result.value);
  if(pageResult.prompt!==prompt||pageResult.photos!==2||!pageResult.complete)throw new Error(`Mock page assertions failed: ${JSON.stringify(pageResult)}`);
  if(bytes.length<1000||bytes[0]!==0x89||bytes[1]!==0x50)throw new Error('Downloaded mock artwork is not a valid PNG.');
  console.log(JSON.stringify({result:'passed',target:'local mock only',promptEntered:pageResult.prompt===prompt,photosAttached:pageResult.photos,completionDetected:pageResult.complete,downloadedBytes:bytes.length,downloadEvent:last.result?.value||null,extensionId},null,2));
  mockCdp.close();popupCdp.close();await browserCdp.call('Browser.close');browserCdp.close();
}finally{
  server.close();
  if(browser&&!browser.killed)browser.kill();
}
