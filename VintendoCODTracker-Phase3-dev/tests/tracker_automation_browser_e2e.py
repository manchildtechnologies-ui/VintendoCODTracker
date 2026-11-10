"""Run the unpacked MV3 extension and tracker bridge in isolated Chrome against a controlled Meta AI fixture."""
import json
import os
import shutil
import sys
import time
import base64
import io
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
EXTENSION_SOURCE = ROOT.parent / 'Vintendo-COD-Automation-Phase3-dev'
CHROME = Path(r'C:\Program Files\Google\Chrome\Application\chrome.exe')

sys.path.insert(0, str(ROOT))
import engine as E
from local_bridge import LocalBridge


FIXTURE = r'''<!doctype html><meta charset="utf-8"><title>Meta AI controlled test fixture</title>
<div><button role="combobox">Image</button></div>
<div role="presentation"><div role="option">Image</div><div role="option">Video</div></div>
<input type="file" multiple accept="image/*">
<div contenteditable="true" role="textbox" aria-label="Prompt"></div>
<div id="status">ready</div><div id="attachments"></div><div id="output"></div>
<script>
window.attachedFiles=[];window.submissionCount=0;
const input=document.querySelector('input[type=file]');
input.addEventListener('change',()=>{
  for(const file of input.files){window.attachedFiles.push(file.name);const tag=document.createElement('span');tag.dataset.attachment='true';tag.textContent=file.name;document.querySelector('#attachments').append(tag)}
});
document.querySelector('[contenteditable=true]').addEventListener('keydown',event=>{
  if(event.key!=='Enter')return;event.preventDefault();window.submissionCount++;
  const prompt=document.querySelector('[contenteditable=true]').textContent;
  const item=document.createElement('div');item.dataset.messageItem='true';item.setAttribute('data-message-item','true');
  const image=document.createElement('img');image.alt='controlled generated result';image.src='data:image/png;base64,VALID_RESULT_IMAGE';
  item.append(image);document.querySelector('#output').replaceChildren(item);
  document.querySelector('#status').textContent=JSON.stringify({prompt,attachedFiles:window.attachedFiles,submissionCount:window.submissionCount});
});
</script>'''


def wait_until(predicate, timeout=45, label='condition'):
    end = time.time() + timeout
    while time.time() < end:
        value = predicate()
        if value:
            return value
        time.sleep(.25)
    raise AssertionError(f'Timed out waiting for {label}')


def run():
    if not CHROME.is_file():
        raise RuntimeError(f'Chrome executable unavailable: {CHROME}')
    work = ROOT / 'browser-e2e-work'
    work.mkdir(parents=True, exist_ok=True)
    extension = work / 'extension'
    shutil.copytree(EXTENSION_SOURCE, extension, dirs_exist_ok=True)
    os.environ['VINTENDO_DATA_DIR'] = str(work / 'appdata')
    store = E.Store(work / 'history.sqlite3')
    match = {'matchId': 'browser-four-player-win', 'integration_source': 'squad_ocr',
             'game': 'Warzone', 'modeKey': 'resurgence', 'playedAt': '2026-11-07T14:00:00Z',
             'result': 'win', 'placement': 1}
    mid, _ = store.add(match)
    players = [{'name': name, 'verified': True, 'stats': {'kills': 10-index, 'damage': 3000+index}}
                for index, name in enumerate(('Alpha', 'Bravo', 'Charlie', 'Delta'))]
    store.update(mid, players=json.dumps(players))
    for index, player in enumerate(players):
        name = player['name']
        photo = work / f'{name.lower()}.jpg'
        Image.new('RGB', (320, 240), ((35 * index) % 255, (90 + 40 * index) % 255, (170 - 30 * index) % 255)).save(photo)
        store.save_player({'id': name.lower(), 'name': name, 'aliases': [], 'photo': str(photo),
                           'consent_meta': True, 'enabled': True})

    bridge = LocalBridge(store, work / 'data', 'phase3-e2e-pairing-token', port=0)
    bridge.start()
    try:
        port = bridge.port
        manifest_path = extension / 'manifest.json'
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        manifest['host_permissions'] = [
            '*://meta.ai/*', '*://*.meta.ai/*', f'http://127.0.0.1:{port}/*', '*://*.fbcdn.net/*']
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding='utf-8')
        for relative in ('tracker.js', 'assets/tracker-content.js'):
            path = extension / relative
            text = path.read_text(encoding='utf-8').replace('http://127.0.0.1:8765', f'http://127.0.0.1:{port}')
            path.write_text(text, encoding='utf-8')
        tracker = extension / 'tracker.js'
        tracker.write_text(tracker.read_text(encoding='utf-8'), encoding='utf-8')

        image = Image.new('RGB', (640, 360), (25, 42, 58))
        buffer = io.BytesIO()
        image.save(buffer, format='PNG')
        fixture = FIXTURE.replace('VALID_RESULT_IMAGE', base64.b64encode(buffer.getvalue()).decode('ascii'))

        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                str(work / 'chrome-profile'), executable_path=str(CHROME), headless=False,
                ignore_default_args=['--disable-extensions'],
                args=[f'--disable-extensions-except={extension}', f'--load-extension={extension}',
                      '--no-first-run', '--no-default-browser-check', '--disable-background-timer-throttling'])
            try:
                context.route('https://www.meta.ai/create', lambda route: route.fulfill(
                    status=200, content_type='text/html', body=fixture))
                if context.service_workers:
                    worker = context.service_workers[0]
                else:
                    try:
                        worker = context.wait_for_event('serviceworker', timeout=8000)
                    except Exception:
                        diagnostics = context.new_page()
                        diagnostics.goto('chrome://extensions')
                        print('Extension diagnostics:', json.dumps({'url':diagnostics.url,'pages':[page.url for page in context.pages], 'body':diagnostics.locator('body').inner_text()[:4000]}))
                        raise
                extension_id = worker.url.split('/')[2]
                options = context.new_page()
                options.goto(f'chrome-extension://{extension_id}/options.html')
                options.locator('#token').fill('phase3-e2e-pairing-token')
                options.locator('#enabled').check()
                options.locator('#save').click()
                options.get_by_text('Connected to Vintendo COD Tracker', exact=False).wait_for(timeout=15000)

                def delivered():
                    row = store.get(mid)
                    return row if row and row.get('artwork') and Path(row['artwork']).is_file() else None

                completed = wait_until(delivered, timeout=75, label='four reference images, prompt submission, and tracker artwork receipt')
                meta = next((page for page in context.pages if 'meta.ai/create' in page.url), None)
                if meta is None:
                    raise AssertionError('Automation did not open the controlled Meta AI tab.')
                window_state = meta.evaluate('({attachedFiles:window.attachedFiles,submissionCount:window.submissionCount,status:document.querySelector("#status").textContent})')
                assert len(window_state['attachedFiles']) == 4, window_state
                assert window_state['submissionCount'] == 1, window_state
                prompt = json.loads(window_state['status'])['prompt']
                assert 'featuring these 4 people' in prompt and 'They WON' in prompt
                for name in ('Alpha', 'Bravo', 'Charlie', 'Delta'):
                    assert f'{name} =' in prompt
                assert completed['gen_state'] == 'done'
                print(json.dumps({'result':'passed','browser':'Chrome stable, isolated profile',
                    'target':'controlled Meta AI fixture; no live Meta account or generation',
                    'extension_id':extension_id,'match_id':mid,'identified_players':4,
                    'attached_files':window_state['attachedFiles'],'submission_count':window_state['submissionCount'],
                    'tracker_artwork_received':bool(completed['artwork']),'exact_prompt':prompt},indent=2))
            finally:
                context.close()
    finally:
        bridge.close()


if __name__ == '__main__':
    run()
