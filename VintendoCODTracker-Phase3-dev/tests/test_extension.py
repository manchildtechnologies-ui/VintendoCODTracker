import json
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
EXT=ROOT/'Chrome-Extension'

class ExtensionSourceTests(unittest.TestCase):
    def test_manifest_is_mv3_and_uses_minimal_declared_permissions(self):
        manifest=json.loads((EXT/'manifest.json').read_text(encoding='utf-8'))
        self.assertEqual(manifest['manifest_version'],3)
        self.assertEqual(set(manifest['permissions']),{'storage','tabs','downloads','clipboardWrite'})
        self.assertEqual(set(manifest['host_permissions']),{'http://127.0.0.1:8765/*'})
        self.assertNotIn('content_scripts',manifest)
    def test_required_extension_files_and_icons_exist(self):
        for name in ('manifest.json','popup.html','popup.js','popup.css','background.js','content.js','options.html','options.js','README.md'):
            self.assertTrue((EXT/name).is_file(),name)
        for size in (16,48,128):self.assertTrue((EXT/'icons'/f'icon{size}.png').is_file())
    def test_pairing_instructions_and_disconnected_state_are_actionable(self):
        options=(EXT/'options.html').read_text(encoding='utf-8')
        script=(EXT/'options.js').read_text(encoding='utf-8')
        popup=(EXT/'popup.js').read_text(encoding='utf-8')
        style=(EXT/'popup.css').read_text(encoding='utf-8')
        self.assertIn('Copy Chrome pairing token',options)
        self.assertIn('Save and test',options)
        self.assertIn('HTTP 401',script)
        self.assertIn('HTTP 403',script)
        background=(EXT/'background.js').read_text(encoding='utf-8')
        self.assertIn("headers.set('X-Vintendo-Extension-ID', chrome.runtime.id)",background)
        self.assertIn('Cannot reach the tracker',popup)
        self.assertIn('.primary:disabled',style)
    def test_meta_page_controls_are_manual_in_production_sources(self):
        source='\n'.join((EXT/name).read_text(encoding='utf-8') for name in ('manifest.json','popup.js','background.js','content.js'))
        self.assertNotIn('https://www.meta.ai/*"],"js"',source)
        self.assertNotIn('set_input_files',source.lower())
        content=(EXT/'content.js').read_text(encoding='utf-8')
        self.assertIn("location.origin === 'http://127.0.0.1:8766'",content)
    def test_download_handler_remembers_tab_to_job_association(self):
        source=(EXT/'background.js').read_text(encoding='utf-8')
        self.assertIn('chrome.downloads.onCreated',source);self.assertIn('chrome.downloads.onChanged',source)
        self.assertIn('chrome.tabs.query({active:true,lastFocusedWindow:true})',source);self.assertIn('item.referrer',source);self.assertIn('matchedJobs.length',source)
    def test_popup_displays_verified_rank_and_scoreboard_to_photo_mapping(self):
        popup=(EXT/'popup.js').read_text(encoding='utf-8')
        self.assertIn('performanceRank',popup);self.assertIn('referencePhotoMappings',popup)
        self.assertIn('matchedRosterName',popup);self.assertIn('photo withheld',popup)
    def test_mock_manifest_is_scoped_to_local_fixture(self):
        manifest=json.loads((ROOT/'tests'/'extension_mock'/'manifest.template.json').read_text(encoding='utf-8'))
        self.assertEqual(manifest['host_permissions'],['http://127.0.0.1:8766/__vintendo_mock__/*'])
        self.assertEqual(manifest['content_scripts'][0]['matches'],manifest['host_permissions'])
    def test_mock_page_covers_multiple_photos_generation_and_download(self):
        page=(ROOT/'tests'/'extension_mock'/'index.html').read_text(encoding='utf-8')
        code=(ROOT/'tests'/'extension_mock'/'mock.js').read_text(encoding='utf-8')
        self.assertIn('multiple',page);self.assertIn('completed-image',code);self.assertIn('download',code)

if __name__=='__main__':unittest.main()
