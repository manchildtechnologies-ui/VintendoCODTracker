"""Real Chrome extension test against a local mock only; uses a disposable profile."""
import base64
import http.server
import json
import shutil
import threading
import time
import uuid
from pathlib import Path
from playwright.sync_api import sync_playwright

HERE=Path(__file__).resolve().parent
PROJECT=HERE.parents[1]

class MockHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self,*args,**kwargs):super().__init__(*args,directory=str(HERE),**kwargs)
    def translate_path(self,path):
        if path.startswith('/__vintendo_mock__/'):path=path[len('/__vintendo_mock__'):]
        return super().translate_path(path)
    def log_message(self,*_):pass

def run():
    work=PROJECT.parent.parent/'work'/'squad-browser-extension-tests'/('playwright-mock-'+uuid.uuid4().hex);work.mkdir(parents=True)
    extension=work/'extension';shutil.copytree(PROJECT/'Chrome-Extension',extension)
    (extension/'manifest.json').write_text((HERE/'manifest.template.json').read_text(encoding='utf-8'),encoding='utf-8')
    downloads=work/'downloads';downloads.mkdir()
    server=http.server.ThreadingHTTPServer(('127.0.0.1',8766),MockHandler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        with sync_playwright() as playwright:
            context=playwright.chromium.launch_persistent_context(str(work/'profile'),channel='chromium',headless=True,accept_downloads=True,
                args=[f'--disable-extensions-except={extension}',f'--load-extension={extension}',f'--download.default_directory={downloads}','--download.prompt_for_download=false','--no-first-run','--no-default-browser-check'])
            try:
                worker=context.service_workers[0] if context.service_workers else context.wait_for_event('serviceworker',timeout=20000)
                extension_id=worker.url.split('/')[2]
                page=context.new_page();page.goto('http://127.0.0.1:8766/__vintendo_mock__/')
                popup=context.new_page();popup.goto(f'chrome-extension://{extension_id}/popup.html')
                if popup.evaluate('chrome.runtime.id')!=extension_id:raise AssertionError('Loaded extension ID did not match the local bridge origin.')
                tab_id=popup.evaluate("""async()=>((await chrome.tabs.query({url:'http://127.0.0.1:8766/__vintendo_mock__/*'}))[0]||{}).id""")
                if tab_id is None:raise AssertionError('Extension could not locate local fixture tab.')
                popup.evaluate("""async id=>chrome.storage.local.set({metaTabs:{[String(id)]:'mock-match-1'}})""",tab_id)
                pixel='data:image/png;base64,'+base64.b64encode(bytes.fromhex('89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000b49444154789c636000020000050001a5f645400000000049454e44ae426082')).decode()
                prompt='Create a 16:9 horizontal mock poster with four squad players.'
                with page.expect_download(timeout=15000) as pending_download:
                    result=popup.evaluate("""({tabId,prompt,photos})=>new Promise(resolve=>chrome.runtime.sendMessage({type:'runMock',tabId,prompt,photos},resolve))""",
                      {'tabId':tab_id,'prompt':prompt,'photos':[{'name':'alpha.png','mime':'image/png','dataUrl':pixel},{'name':'bravo.png','mime':'image/png','dataUrl':pixel}]})
                if result.get('error'):raise AssertionError(result['error'])
                download=pending_download.value;target=work/download.suggested_filename;download.save_as(target)
                assert page.locator('#prompt').input_value()==prompt
                assert page.locator('#photos').evaluate('(el)=>el.files.length')==2
                assert page.locator('#photo-list').inner_text()=='alpha.png, bravo.png'
                assert page.locator('#completed-image').is_visible()
                assert target.is_file() and target.stat().st_size>1000
                last=None;deadline=time.monotonic()+5
                while time.monotonic()<deadline:
                    response=popup.evaluate("""()=>new Promise(resolve=>chrome.runtime.sendMessage({type:'lastDownload'},resolve))""")
                    last=response.get('value') if response else None
                    if last and last.get('state')=='complete':break
                    time.sleep(.1)
                debug=popup.evaluate("""async()=>({downloads:await new Promise(resolve=>chrome.downloads.search({limit:10},resolve)),storage:await chrome.storage.local.get(['metaTabs','trackedDownloads','lastDownload'])})""")
                assert last and last.get('state')=='complete' and last.get('jobId')=='mock-match-1',f'Download was not assigned to its source match: {json.dumps(debug)}'
                print(json.dumps({'result':'passed','target':'local mock only','extension_id':extension_id,'prompt_entered':True,
                    'photos_attached':2,'completion_detected':True,'download_assigned_to_match':last['jobId'],'downloaded_file':download.suggested_filename,'bytes':target.stat().st_size},indent=2))
            finally:context.close()
    finally:server.shutdown();server.server_close();thread.join(timeout=2)

if __name__=='__main__':run()
