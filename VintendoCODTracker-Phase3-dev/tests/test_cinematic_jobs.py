import tempfile
import unittest
from pathlib import Path

import engine as E
from engine import valid_mp4


class CinematicJobStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.store=E.Store(Path(self.temp.name)/'history.sqlite3')
    def tearDown(self):self.temp.cleanup()
    def test_cinematic_jobs_are_separate_and_claimable_once(self):
        image=Path(self.temp.name)/'shot.png';image.write_bytes(b'png')
        job='cin-'+'a'*32
        self.assertTrue(self.store.create_cinematic(job,'shot.png',image,'prompt'))
        self.assertFalse(self.store.create_cinematic(job,'shot.png',image,'prompt'))
        self.assertEqual([row['id'] for row in self.store.pending_cinematics()],[job])
        self.assertTrue(self.store.update_cinematic(job,status='running'))
        self.assertFalse(self.store.update_cinematic('missing',status='running'))
        self.assertEqual(self.store.get_cinematic(job)['status'],'running')
        self.assertEqual(self.store.history(),[])
    def test_claim_is_exclusive_and_idempotent(self):
        image=Path(self.temp.name)/'shot.png';image.write_bytes(b'png')
        job='cin-'+'b'*32
        self.assertTrue(self.store.create_cinematic(job,'shot.png',image,'prompt'))
        self.assertTrue(self.store.claim_cinematic(job))
        self.assertFalse(self.store.claim_cinematic(job))
        self.assertEqual(self.store.get_cinematic(job)['status'],'running')
        self.assertEqual(self.store.pending_cinematics(),[])
    def test_complete_mp4_container_check(self):
        valid=b'\0\0\0\x18ftypisom'+b'\0'*12+b'moov'+b'\0'*16+b'mdat'+b'x'*1500
        self.assertTrue(valid_mp4(valid))
        self.assertFalse(valid_mp4(b'not-video'+b'x'*2000))
        self.assertFalse(valid_mp4(b'\0\0\0\x18ftypisom'+b'x'*1500))

class AutomationBrowserLaunchTests(unittest.TestCase):
    def test_automation_browser_stays_headed_but_starts_minimized(self):
        from types import SimpleNamespace
        import meta_automation
        calls={}
        class FakeChromium:
            def launch_persistent_context(self,folder,**kwargs):
                calls.update(kwargs);return object()
        with tempfile.TemporaryDirectory() as tmp:
            meta_automation.ensure_context(SimpleNamespace(chromium=FakeChromium()),Path(tmp)/'profile')
        self.assertFalse(calls.get('headless',True))
        self.assertIn('--start-minimized',calls.get('args',[]))
        self.assertIn('--disable-blink-features=AutomationControlled',calls.get('args',[]))


if __name__=='__main__':unittest.main()
