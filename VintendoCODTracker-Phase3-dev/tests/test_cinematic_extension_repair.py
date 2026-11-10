"""Regression tests for the cinematic queue pickup failure.

Root cause covered here: Chrome was running an older extension copy whose
service worker only polled match jobs and never queried
``/v1/cinematics/pending``, so a queued cinematic job stayed ``queued``
forever while the bridge reported CONNECTED. The Z: Phase 3 sources must
keep the cinematic poll, the Meta-page video return handler, and the poll
diagnostics that make this failure visible.
"""
import os
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXT = Path(os.environ.get('VINTENDO_EXT_DIR', ROOT.parent / 'Vintendo-COD-Automation-Phase3-dev'))


@unittest.skipUnless((EXT / 'tracker.js').is_file(), 'Phase 3 extension sources not present')
class CinematicExtensionRepairTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.worker = (EXT / 'tracker.js').read_text(encoding='utf-8', errors='replace')
        cls.content = (EXT / 'assets' / 'tracker-content.js').read_text(encoding='utf-8', errors='replace')
        cls.options = (EXT / 'options.js').read_text(encoding='utf-8', errors='replace')

    def test_worker_polls_and_claims_cinematic_queue(self):
        self.assertIn('/cinematics/pending', self.worker)
        self.assertIn('/claim', self.worker)
        self.assertIn('chrome.alarms.create', self.worker)
        self.assertIn('onStartup', self.worker)

    def test_content_script_returns_generated_video_to_bridge(self):
        self.assertIn('TRACKER_UPLOAD_CINEMATIC_VIDEO', self.content)
        self.assertIn('/video', self.content)
        self.assertIn('video/mp4', self.content)

    def test_claim_is_persisted_before_meta_handoff_so_it_can_resume(self):
        # A service-worker restart mid-claim must not strand the job in
        # 'running' with no local record (the second live failure).
        self.assertIn("stage:'claimed'", self.worker)
        self.assertIn("stage:'submitting'", self.worker)

    def test_worker_returns_video_itself_with_page_fallback(self):
        # Page-context fetch of the generated video URL failed live with
        # 'Failed to fetch'; the worker must try its own fetch first.
        self.assertIn("/video`,{method:'POST',headers:{'Content-Type':'video/mp4'}", self.worker)
        self.assertIn('TRACKER_UPLOAD_CINEMATIC_VIDEO', self.worker)

    def test_worker_attaches_talent_reference_when_stored(self):
        # The worker must fetch /cinematics/talent and append it as a
        # second image; absence must be non-fatal (empty catch).
        self.assertIn('/cinematics/talent', self.worker)
        self.assertIn('talent.png', self.worker)
        self.assertIn('talentImages=[]', self.worker)
        self.assertIn('...talentImages', self.worker)
    def test_worker_attaches_per_job_friend_talent_with_legacy_fallback(self):
        self.assertIn('/cinematics/${encodeURIComponent(id)}/talent', self.worker)
        self.assertIn('talentImages.push', self.worker)
        self.assertIn('/cinematics/talent', self.worker)
        self.assertIn('talent.png', self.worker)

    def test_worker_records_poll_diagnostics(self):
        self.assertIn('trackerDiagnostics', self.worker)
        self.assertIn('lastPollAt', self.worker)
        self.assertIn('queuedCinematic', self.worker)

    def test_options_page_shows_diagnostics_and_can_poll_now(self):
        self.assertIn('trackerDiagnostics', self.options)
        self.assertIn('TRACKER_POLL_NOW', self.options)
        self.assertIn('TRACKER_POLL_NOW', self.worker)


if __name__ == '__main__':
    unittest.main()
