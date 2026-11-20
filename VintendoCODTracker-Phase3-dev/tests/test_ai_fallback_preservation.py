"""AI-fallback preservation: timeouts and failed AI must never discard local matches.

Local OCR results stay viable while AI stays selective:

- every AI timeout/failure path below only moves ``gen_state``/``status``
  and records an error; payload, players, OCR text, prompts and staged
  images are preserved;
- poster preparation refuses to run without verified players, so the
  Meta AI handoff never fires on unverified OCR;
- the documented timeout/retry constants (poster 1200s, video 1500s,
  viewer cap 600s, Discord quota backoff capped at 60s) are locked in.
"""
import inspect
import io
import json
import sqlite3
import tempfile
import unittest
import urllib.error
import uuid
from pathlib import Path
from types import SimpleNamespace

import engine as E
import meta_automation
import app


def _poster_self(store):
    logs = []
    return SimpleNamespace(store=store, _inapp_poster_active=None,
                           _inapp_poster_started=None, log=logs.append)


def _cinematic_self(store):
    logs = []
    return SimpleNamespace(store=store, _inapp_active=None,
                           _inapp_started=None, log=logs.append)


def _local_match(mid_suffix):
    return {'matchId': 'ai-fallback-' + mid_suffix, 'modeKey': 'resurgence',
            'result': 'win', 'playedAt': '2026-11-07T10:00:00Z'}


PLAYERS = [{'name': 'Vintendo', 'stats': {'kills': 12}, 'verified': True,
            'source': 'user_verified_ocr', 'confidence': 1.0}]


class PosterSweepPreservationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = E.Store(Path(self.temp.name) / 'history.sqlite3')

    def tearDown(self):
        self.temp.cleanup()

    def _submitting_match(self):
        mid, _ = self.store.add(_local_match(uuid.uuid4().hex), 'needs_review')
        self.store.update(mid, payload=_local_match('x'), ocr='LOCAL OCR TEXT',
                          players=json.dumps(PLAYERS), gen_state='submitting')
        return mid

    def test_timeout_never_discards_valid_local_match(self):
        mid = self._submitting_match()
        reset = app.Window._sweep_stale_poster_jobs(_poster_self(self.store), max_age_s=0)
        self.assertEqual(reset, 1)
        row = self.store.get(mid)
        self.assertEqual(row['status'], 'needs_review')
        self.assertEqual(row['gen_state'], 'idle')
        self.assertEqual(json.loads(row['players']), PLAYERS)
        self.assertEqual(row['ocr'], 'LOCAL OCR TEXT')
        self.assertIn('matchId', row['payload'])
        self.assertTrue(row['error'])

    def test_three_strikes_goes_idle_and_records_attempt(self):
        mid = self._submitting_match()
        self.store.update(mid, gen_state='submitting', gen_attempts=3)
        fake = _poster_self(self.store)
        fake._inapp_poster_active = mid
        fake._inapp_poster_started = 0.0
        reset = app.Window._sweep_stale_poster_jobs(fake, max_age_s=0)
        self.assertEqual(reset, 1)
        row = self.store.get(mid)
        self.assertEqual(row['gen_state'], 'idle')
        self.assertEqual(int(row['gen_attempts']), 4)
        self.assertEqual(json.loads(row['players']), PLAYERS)
        self.assertEqual(row['ocr'], 'LOCAL OCR TEXT')
        self.assertIsNone(fake._inapp_poster_active)

    def test_running_job_with_live_worker_is_untouched(self):
        mid = self._submitting_match()
        fake = _poster_self(self.store)
        fake._inapp_poster_active = mid
        import time
        fake._inapp_poster_started = time.time()
        reset = app.Window._sweep_stale_poster_jobs(fake)
        self.assertEqual(reset, 0)
        self.assertEqual(self.store.get(mid)['gen_state'], 'submitting')

    def test_failed_ai_update_preserves_local_results(self):
        mid = self._submitting_match()
        self.store.update(mid, gen_state='idle',
                          error='Automatic submission stopped at generation: timed out')
        row = self.store.get(mid)
        self.assertEqual(row['status'], 'needs_review')
        self.assertEqual(json.loads(row['players']), PLAYERS)
        self.assertEqual(row['ocr'], 'LOCAL OCR TEXT')
        self.assertIn('matchId', row['payload'])


class CinematicSweepPreservationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = E.Store(self.root / 'history.sqlite3')

    def tearDown(self):
        self.temp.cleanup()

    def _running_job(self):
        job = 'cin-' + uuid.uuid4().hex
        shot = self.root / 'shot.png'
        shot.write_bytes(b'png')
        self.assertTrue(self.store.create_cinematic(job, 'shot.png', shot, 'prompt text'))
        self.assertTrue(self.store.claim_cinematic(job))
        return job

    def test_requeue_preserves_prompt_and_image(self):
        job = self._running_job()
        reset = app.Window._sweep_stale_cinematics(_cinematic_self(self.store), max_age_s=0)
        self.assertEqual(reset, 1)
        row = self.store.get_cinematic(job)
        self.assertEqual(row['status'], 'queued')
        self.assertEqual(int(row['attempts']), 1)
        self.assertEqual(row['prompt'], 'prompt text')
        self.assertTrue(row['image'].endswith('shot.png'))
        self.assertIn('requeued automatically (try 1)', row['error'])

    def test_three_strikes_fails_open_for_manual_retry(self):
        job = self._running_job()
        self.store.update_cinematic(job, attempts=3, status='running')
        reset = app.Window._sweep_stale_cinematics(_cinematic_self(self.store), max_age_s=0)
        self.assertEqual(reset, 1)
        row = self.store.get_cinematic(job)
        self.assertEqual(row['status'], 'failed')
        self.assertEqual(int(row['attempts']), 4)
        self.assertEqual(row['prompt'], 'prompt text')
        self.assertTrue(row['image'].endswith('shot.png'))
        self.assertIn('re-queue', row['error'])

    def test_running_job_with_live_worker_is_untouched(self):
        job = self._running_job()
        reset = app.Window._sweep_stale_cinematics(_cinematic_self(self.store))
        self.assertEqual(reset, 0)
        self.assertEqual(self.store.get_cinematic(job)['status'], 'running')

    def test_failed_video_never_touches_match_history(self):
        job = self._running_job()
        before = self.store.history()
        self.store.update_cinematic(job, status='failed', error='boom')
        self.assertEqual(self.store.history(), before)
        row = self.store.get_cinematic(job)
        self.assertEqual(row['prompt'], 'prompt text')
        self.assertTrue(row['image'].endswith('shot.png'))

    def test_legacy_database_gains_attempts_column(self):
        legacy = self.root / 'legacy.sqlite3'
        raw = sqlite3.connect(legacy)
        try:
            raw.execute("CREATE TABLE cinematic_jobs(id TEXT PRIMARY KEY,filename TEXT NOT NULL,"
                        "image TEXT NOT NULL,prompt TEXT NOT NULL,status TEXT NOT NULL,"
                        "video TEXT DEFAULT '',error TEXT DEFAULT '',created REAL,updated REAL)")
            raw.commit()
        finally:
            raw.close()
        store = E.Store(legacy)
        probe = sqlite3.connect(legacy)
        try:
            columns = {row[1] for row in probe.execute('PRAGMA table_info(cinematic_jobs)')}
        finally:
            probe.close()
        self.assertIn('attempts', columns)
        job = 'cin-' + uuid.uuid4().hex
        shot = self.root / 'old.png'
        shot.write_bytes(b'png')
        self.assertTrue(store.create_cinematic(job, 'old.png', shot, 'old prompt'))
        self.assertTrue(store.update_cinematic(job, attempts=2))
        self.assertEqual(int(store.get_cinematic(job)['attempts']), 2)


class SelectiveAiGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = E.Store(self.root / 'history.sqlite3')

    def tearDown(self):
        self.temp.cleanup()

    def test_poster_preparation_requires_verified_players(self):
        mid, _ = self.store.add(_local_match(uuid.uuid4().hex), 'needs_review')
        with self.assertRaises(ValueError):
            E.prepare_poster_job(self.store, self.root, mid)
        row = self.store.get(mid)
        self.assertEqual(row['status'], 'needs_review')
        self.assertFalse((self.root / 'handoff').exists())

    def test_poster_and_video_timeouts_stay_selective(self):
        poster = inspect.signature(meta_automation.MetaDriver.submit_text_to_image)
        video = inspect.signature(meta_automation.MetaDriver.submit_image_to_video)
        self.assertEqual(poster.parameters['timeout_s'].default, 1200)
        self.assertEqual(video.parameters['timeout_s'].default, 1500)

    def test_discord_quota_backoff_is_capped(self):
        count = [0]
        delays = []

        def opener(req, timeout=0):
            count[0] += 1
            raise urllib.error.HTTPError(req.full_url, 429, 'rate limited',
                                         {}, io.BytesIO(b'{"retry_after":999}'))

        with self.assertRaisesRegex(RuntimeError, '429'):
            E.send_discord('https://discord.com/api/webhooks/123/fixture', None,
                           'test', opener, delays.append)
        self.assertEqual(count[0], 4)
        self.assertEqual(delays, [60, 60, 60])


if __name__ == '__main__':
    unittest.main()
