"""Squad generation-job queue tests using a controlled mock website fixture.

These tests exercise the app's own job state machine (stage, download,
validate, overlay, deliver) with synthetic generation events. They do not
launch a browser, touch meta.ai, or perform live authenticated generation.
"""
import json
import shutil
import sqlite3
import unittest
import uuid
from pathlib import Path

import engine as E


def make_match(tag, mode='resurgence', result='win', kills=9):
    return {'matchId': f'queue-{tag}-' + uuid.uuid4().hex,
            'modeKey': mode, 'playedAt': '2026-11-07T1%d:00:00Z' % (kills % 10),
            'result': result, 'stats': {'kills': kills}}


def make_players(names):
    return [dict(name=n, stats={'kills': 9 if i == 0 else 3, 'damage': 2100},
                  verified=True, source='user_verified_ocr', confidence=1.0)
            for i, n in enumerate(names)]


class FakeDownload:
    def __init__(self, source):
        self.source = Path(source)

    def save_as(self, target):
        shutil.copyfile(self.source, target)


class MockSite:
    """Controlled stand-in for a generation website: stage then complete/fail."""

    def __init__(self, store, directory):
        self.store = store
        self.directory = Path(directory)

    def stage(self, mid, prompt, photos):
        assert self.store.record_handoff(mid, prompt, len(photos))
        folder = self.directory / 'handoff' / E.disk_id(mid)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'prompt.txt').write_text(prompt, encoding='utf-8')
        names = []
        for index, photo in enumerate(photos):
            target = folder / f'reference-{index}.jpg'
            shutil.copyfile(photo, target)
            names.append(target.name)
        return folder, names

    def complete(self, mid, image_path, on_image, on_status):
        from browser_service import save_download
        return save_download(self.directory, mid, FakeDownload(image_path),
                             on_image, on_status)

    def fail(self, mid, image_path, on_image, on_status):
        return self.complete(mid, image_path, on_image, on_status)


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.root = E.DATA / ('queue-' + uuid.uuid4().hex)
        self.root.mkdir(parents=True, exist_ok=True)
        self.store = E.Store(self.root / 'db')
        self.images = []

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def make_image(self, name='art.png', size=(640, 360), color='navy'):
        from PIL import Image
        path = self.root / name
        Image.new('RGB', size, color).save(path)
        return str(path)

    def on_image(self, mid, path):
        self.images.append((mid, path))
        self.store.note_artwork(mid, path)

    def on_status(self, message):
        pass

    def test_ten_job_session_holds_per_job_materials(self):
        site = MockSite(self.store, self.root)
        mids = []
        for index in range(10):
            match = make_match(f'session{index}', kills=index + 1)
            mid, added = self.store.add(match)
            self.assertTrue(added)
            players = make_players([f'Player{index}A', f'Player{index}B'])
            self.store.update(mid, players=json.dumps(players),
                              prompt=f'Create a 16:9 horizontal widescreen image. Session job {index}.',
                              theme=None)
            photo = self.make_image(f'face-{index}.jpg', size=(200, 200))
            site.stage(mid, self.store.get(mid)['prompt'], [photo])
            mids.append(mid)
        snapshot = self.store.queue_snapshot(limit=10)
        self.assertEqual(len(snapshot), 10)
        self.assertEqual([j['id'] for j in snapshot], mids)
        for job in snapshot:
            self.assertEqual(job['gen_state'], 'staged')
            self.assertTrue(job['prompt'])
            self.assertEqual(len(json.loads(job['players'])), 2)

    def test_out_of_order_completions_keep_correct_stats(self):
        site = MockSite(self.store, self.root)
        art_a = self.make_image('art-a.png')
        art_b = self.make_image('art-b.png', color='orange')
        mid_a, _ = self.store.add(make_match('alpha', kills=9))
        mid_b, _ = self.store.add(make_match('beta', kills=3))
        self.store.update(mid_a, players=json.dumps(make_players(['Alpha'])),
                          prompt='Create a 16:9 horizontal widescreen image. Alpha job.')
        self.store.update(mid_b, players=json.dumps(make_players(['Beta'])),
                          prompt='Create a 16:9 horizontal widescreen image. Beta job.')
        site.stage(mid_a, 'prompt a', [art_a])
        site.stage(mid_b, 'prompt b', [art_b])
        # Complete B first: binding must follow the job id, not arrival order.
        site.complete(mid_b, art_b, self.on_image, self.on_status)
        site.complete(mid_a, art_a, self.on_image, self.on_status)
        bound = dict(self.images)
        self.assertNotEqual(bound[mid_a], bound[mid_b])
        for mid, expected in ((mid_a, 'Alpha'), (mid_b, 'Beta')):
            job = self.store.get(mid)
            self.assertEqual(job['status'], 'art_ready')
            self.assertTrue(job['artwork'])
            self.assertEqual(job['image'], job['artwork'])
            players = json.loads(job['players'])
            poster = self.root / f'{mid}-poster.jpg'
            E.render_poster(job['artwork'], poster, json.loads(job['payload']), players)
            self.store.note_poster(mid, str(poster))
            content = E.recap(json.loads(job['payload']), players)
            self.assertIn(expected, content)
        content_a = E.recap(json.loads(self.store.get(mid_a)['payload']),
                            json.loads(self.store.get(mid_a)['players']))
        self.assertIn('Alpha', content_a)
        self.assertNotIn('Beta', content_a)

    def test_failed_download_retry_preserves_data(self):
        site = MockSite(self.store, self.root)
        mid, _ = self.store.add(make_match('retry', kills=7))
        players = make_players(['Retry'])
        self.store.update(mid, players=json.dumps(players),
                          prompt='Create a 16:9 horizontal widescreen image. Retry job.')
        tiny = self.make_image('tiny.png', size=(100, 100))
        site.stage(mid, 'prompt', [tiny])
        before = self.store.get(mid)
        self.assertIsNone(site.fail(mid, tiny, self.on_image, self.on_status))
        after = self.store.get(mid)
        self.assertEqual(json.loads(after['players']), players)
        self.assertEqual(after['prompt'], before['prompt'])
        self.assertNotEqual(after['status'], 'delivered')
        good = self.make_image('good.png')
        site.stage(mid, after['prompt'], [good])
        site.complete(mid, good, self.on_image, self.on_status)
        fixed = self.store.get(mid)
        self.assertEqual(fixed['status'], 'art_ready')
        self.assertGreaterEqual(fixed['gen_attempts'], 2)

    def test_cancel_preserves_match_data(self):
        mid, _ = self.store.add(make_match('cancel', kills=5))
        players = make_players(['Cancel'])
        self.store.update(mid, players=json.dumps(players),
                          prompt='Create a 16:9 horizontal widescreen image. Cancel job.')
        self.store.record_handoff(mid, 'prompt', 1)
        self.assertTrue(self.store.cancel_handoff(mid))
        job = self.store.get(mid)
        self.assertEqual(job['status'], 'needs_review')
        self.assertEqual(job['gen_state'], 'cancelled')
        self.assertEqual(json.loads(job['players']), players)
        self.assertTrue(job['prompt'])

    def test_duplicate_delivery_blocked_across_queue(self):
        mid, _ = self.store.add(make_match('dedupe', kills=9))
        self.store.update(mid, players=json.dumps(make_players(['Solo'])),
                          prompt='Create a 16:9 horizontal widescreen image.')
        art = self.make_image('dedupe.png')
        site = MockSite(self.store, self.root)
        site.stage(mid, 'prompt', [art])
        site.complete(mid, art, self.on_image, self.on_status)
        job = self.store.get(mid)
        E.render_poster(job['artwork'], self.root / 'poster.jpg',
                        json.loads(job['payload']), json.loads(job['players']))
        self.store.note_poster(mid, str(self.root / 'poster.jpg'))
        calls = []

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def read(self):
                return b'{"id":"queue-message"}'

        def opener(request, **kwargs):
            calls.append(request)
            return Response()

        job = self.store.get(mid)
        content = E.recap(json.loads(job['payload']), json.loads(job['players']))
        self.assertTrue(self.store.reserve(mid))
        E.send_discord('https://discord.com/api/webhooks/123/fixture',
                       job['poster'] or job['image'], content, opener)
        self.store.update(mid, status='delivered', message_id='queue-message')
        self.assertFalse(self.store.reserve(mid, True))
        self.assertEqual(len(calls), 1)

    def test_stale_handoff_detection(self):
        mid, _ = self.store.add(make_match('stale', kills=4))
        self.store.update(mid, players=json.dumps(make_players(['Stale'])),
                          prompt='Create a 16:9 horizontal widescreen image.')
        self.store.record_handoff(mid, 'prompt', 1)
        with self.store.connect() as connection:
            connection.execute('UPDATE matches SET updated=? WHERE id=?',
                               (0.0, mid))
        stale = self.store.stale_handoffs(timeout_secs=60)
        self.assertIn(mid, [j['id'] for j in stale])
        fresh, _ = self.store.add(make_match('fresh', kills=6))
        self.store.update(fresh, players=json.dumps(make_players(['Fresh'])),
                          prompt='Create a 16:9 horizontal widescreen image.')
        self.store.record_handoff(fresh, 'prompt', 1)
        self.assertNotIn(fresh, [j['id'] for j in self.store.stale_handoffs(timeout_secs=3600)])

    def test_schema_migration_keeps_legacy_rows(self):
        legacy_path = self.root / 'legacy-db'
        connection = sqlite3.connect(legacy_path)
        connection.execute('''CREATE TABLE matches(id TEXT PRIMARY KEY, payload TEXT NOT NULL,
            status TEXT NOT NULL, created REAL, updated REAL, capture TEXT DEFAULT '',
            ocr TEXT DEFAULT '', players TEXT DEFAULT '[]', theme TEXT DEFAULT '',
            prompt TEXT DEFAULT '', image TEXT DEFAULT '', attempts INTEGER DEFAULT 0,
            error TEXT DEFAULT '', message_id TEXT DEFAULT '')''')
        connection.execute('''CREATE TABLE roster(id TEXT PRIMARY KEY,payload TEXT NOT NULL)''')
        connection.execute('''CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT NOT NULL)''')
        connection.execute('''CREATE TABLE deliveries(id TEXT PRIMARY KEY,status TEXT,
            created REAL,message_id TEXT,error TEXT)''')
        match = make_match('legacy', kills=8)
        connection.execute('INSERT INTO matches VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                           ('legacy-row', json.dumps(match), 'needs_review', 1.0, 1.0,
                            '', '', '[]', '', '16:9 prompt', '/tmp/old.png', 0, '', ''))
        connection.commit()
        connection.close()
        store = E.Store(legacy_path)
        row = store.get('legacy-row')
        self.assertEqual(row['prompt'], '16:9 prompt')
        self.assertEqual(row['image'], '/tmp/old.png')
        self.assertEqual(row['artwork'], '')
        self.assertEqual(row['poster'], '')
        self.assertEqual(row['gen_state'], 'idle')
        self.assertEqual(row['gen_attempts'], 0)

    def test_browser_staged_metadata_binding(self):
        from browser_service import BrowserSession
        statuses = []
        session = BrowserSession(self.root, lambda *a: None, statuses.append)

        class LiveThread:
            def is_alive(self):
                return True

        session.thread = LiveThread()
        session.job_id = 'match-one'
        session.staged['match-one'] = {'photos': ['a.jpg'], 'prompt_chars': 10}
        self.assertFalse(session.open('match-two', prompt_chars=5, photos=['b.jpg']))
        self.assertEqual(session.staged_info('match-one')['photos'], ['a.jpg'])


if __name__ == '__main__':
    unittest.main()
