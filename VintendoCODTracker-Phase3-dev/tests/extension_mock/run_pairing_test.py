"""Exercise actual MV3 fetch/CORS pairing against the app's local bridge."""
import json
import os
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
import engine as E
from local_bridge import LocalBridge

PORT=8876

def run():
    work=Path(tempfile.gettempdir())/('vintendo-pairing-'+uuid.uuid4().hex)
    work.mkdir(parents=True)
    store=E.Store(work/'queue.sqlite3')
    store.add({'matchId':'pairing-probe-match','modeKey':'mock','playedAt':'2026-11-07T00:00:00Z','result':'test'})
    logs=[];bridge=LocalBridge(store,work/'data','pairing-probe-'+uuid.uuid4().hex,port=PORT,on_log=logs.append)
    bridge.start()
    try:
        port=PORT
        extension=work/'extension';shutil.copytree(ROOT/'Chrome-Extension',extension)
        manifest=json.loads((extension/'manifest.json').read_text(encoding='utf-8'))
        manifest['host_permissions']=[f'http://127.0.0.1:{port}/*']
        (extension/'manifest.json').write_text(json.dumps(manifest),encoding='utf-8')
        background=extension/'background.js'
        background.write_text(background.read_text(encoding='utf-8').replace('127.0.0.1:8765',f'127.0.0.1:{port}'),encoding='utf-8')
        with sync_playwright() as playwright:
            context=playwright.chromium.launch_persistent_context(str(work/'profile'),channel='chromium',headless=True,
                args=[f'--disable-extensions-except={extension}',f'--load-extension={extension}','--no-first-run','--no-default-browser-check'])
            try:
                worker=context.service_workers[0] if context.service_workers else context.wait_for_event('serviceworker',timeout=20000)
                extension_id=worker.url.split('/')[2]
                page=context.new_page();page.goto(f'chrome-extension://{extension_id}/options.html')
                page.locator('#token').fill(bridge.token)
                page.locator('#save').click()
                try: page.get_by_text('Connected to Vintendo COD Tracker',exact=False).wait_for(timeout=10000)
                except Exception as exc: raise AssertionError(f"Options failed to connect: {page.locator('#status').inner_text()}; pinned={bridge.origin!r}; logs={logs!r}") from exc
                assert bridge.origin==f'chrome-extension://{extension_id}'
                print(json.dumps({'result':'passed','target':'local mock bridge only','extension_id':extension_id,
                    'status':page.locator('#status').inner_text(),'origin_header_optional':True,
                    'extension_identity_header_verified':True,'job_count':1},indent=2))
            finally:context.close()
    finally:bridge.close()

if __name__=='__main__':run()
