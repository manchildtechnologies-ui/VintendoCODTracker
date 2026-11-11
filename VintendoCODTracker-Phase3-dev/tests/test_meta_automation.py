"""Poster automation tests: in-app text-to-image submission without the extension.

Uses fake Playwright page objects so no browser, network, or Meta AI login is
needed. Mirrors the retired extension flow: attach staged reference photos,
fill the prompt, submit, and collect the finished image download.
"""
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

import meta_automation
from meta_automation import MetaAutomationError, MetaDriver, MetaLoginRequired


PNG_BYTES = b'\x89PNG\r\n\x1a\n' + b'x' * 3000
JPG_BYTES = b'\xff\xd8\xff\xe0' + b'x' * 3000


class FakeDownload:
    def __init__(self, payload, name='poster.png'):
        self.payload = payload
        self.suggested_filename = name

    def save_as(self, target):
        Path(target).write_bytes(self.payload)


class FakeLocator:
    def __init__(self, page, selector):
        self.page = page
        self.selector = selector
        self.page.seen.append(selector)

    @property
    def first(self):
        return self

    def nth(self, _i):
        return self

    def count(self):
        if self.selector == meta_automation.SELECTORS['file_inputs']:
            return len(self.page.inputs)
        if self.selector.startswith('xpath='):
            return 0
        return 1

    def all(self):
        return list(self.page.images)

    def wait_for(self, state='', timeout=0):
        if self.selector in self.page.missing:
            raise RuntimeError('not visible: ' + self.selector)
        return None

    def is_visible(self, timeout=0):
        if self.selector in self.page.missing:
            return False
        if self.selector in meta_automation.SELECTORS['logged_out_markers']:
            return bool(self.page.logged_out)
        return True

    def click(self, timeout=0):
        self.page.clicks.append(self.selector)
        if self.selector in meta_automation.SELECTORS['generate']:
            self.page._generated()

    def hover(self, timeout=0):
        self.page.hovers.append(self.selector)
        reveal = self.page.revealable
        if reveal is None:
            reveal = set(meta_automation.SELECTORS['download'])
        self.page.missing.difference_update(reveal)

    def fill(self, text):
        self.page.filled[self.selector] = text

    def set_input_files(self, files):
        if isinstance(files, (str, Path)):
            files = [files]
        self.page.attached.extend(str(f) for f in files)


class FakePage:
    def __init__(self, download_payload=PNG_BYTES, download_name='poster.png'):
        self.seen = []
        self.clicks = []
        self.filled = {}
        self.attached = []
        self.inputs = ['a', 'b']
        self.missing = set()
        self.logged_out = False
        self.download_payload = download_payload
        self.download_name = download_name
        self.navigated = []
        self.broken = set()
        self.images = []
        self.waits = 0
        self.wait_limit = None
        self.hovers = []
        self.revealable = None
        self.result_image = None

    def goto(self, url, wait_until=''):
        self.navigated.append(url)

    def wait_for_timeout(self, _ms):
        self.waits += 1
        if self.wait_limit is not None and self.waits > self.wait_limit:
            raise RuntimeError('tired')

    def _generated(self):
        if self.result_image is not None:
            self.images.append(self.result_image)
            self.result_image = None

    def locator(self, selector):
        if selector in self.broken:
            raise RuntimeError('bad selector: ' + selector)
        return FakeLocator(self, selector)

    def screenshot(self, path=''):
        pass

    @contextmanager
    def expect_download(self, timeout=0):
        yield FakeExpect(self)


class FakeShot:
    def __init__(self, payload=PNG_BYTES, src=None, width=800, height=600):
        self.payload = payload
        self.page = None
        self._src = src
        self._box = {'width': width, 'height': height}

    def is_visible(self, timeout=0):
        return True

    def locator(self, _selector):
        return FakeEmpty()

    def get_attribute(self, name):
        if name == 'src':
            return self._src
        return None

    def bounding_box(self):
        return dict(self._box)

    def click(self, timeout=0):
        pass

    def hover(self, timeout=0):
        if self.page is not None:
            self.page.hovers.append('element')
            reveal = self.page.revealable
            if reveal is None:
                reveal = set(meta_automation.SELECTORS['download'])
            self.page.missing.difference_update(reveal)

    def screenshot(self, timeout=0):
        return self.payload


class FakeEmpty:
    def count(self):
        return 0


class FakeExpect:
    def __init__(self, page):
        self.value = FakeDownload(page.download_payload, page.download_name)


def make_driver(tmp):
    return MetaDriver(FakeContext(), Path(tmp) / 'diagnose')


class FakeContext:
    def new_page(self):
        raise AssertionError('driver tests supply their own page')


class PosterAutomationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def photos(self, count=2):
        files = []
        for i in range(count):
            target = self.root / f'reference-0{i + 1}-player.jpg'
            target.write_bytes(JPG_BYTES)
            files.append(str(target))
        return files

    def test_submit_attaches_photos_fills_prompt_and_collects_image(self):
        page = FakePage()
        stages = []
        photos = self.photos(2)
        with tempfile.TemporaryDirectory() as out:
            target, via = make_driver(self.root).submit_text_to_image(
                page, photos, 'Victory poster prompt', out, on_stage=lambda stage, detail=None: stages.append(stage))
            self.assertEqual(via, 'download')
            saved = Path(target)
            self.assertTrue(saved.is_file())
            self.assertEqual(saved.suffix.lower(), '.png')
            self.assertEqual(saved.read_bytes(), PNG_BYTES)
        self.assertEqual(page.attached, photos)
        self.assertEqual(stages, ['composing', 'uploading', 'generating', 'collecting'])
        self.assertIn('Victory poster prompt', ' '.join(page.filled.values()))
        self.assertTrue(page.navigated)
        self.assertTrue(any('meta.ai' in url for url in page.navigated))

    def test_submit_attaches_at_most_four_photos(self):
        page = FakePage()
        photos = self.photos(6)
        with tempfile.TemporaryDirectory() as out:
            make_driver(self.root).submit_text_to_image(
                page, photos, 'prompt', out)
        self.assertEqual(page.attached, photos[:4])

    def test_first_visible_skips_broken_candidate(self):
        page = FakePage()
        page.broken.add('role=button[name=/download/i]')
        found = make_driver(self.root)._first_visible(page, ['role=button[name=/download/i]', 'a[download]'], timeout=1000)
        self.assertIsNotNone(found)
        self.assertEqual(found.selector, 'a[download]')

    def test_image_flow_viewer_fallback_reports_screenshot(self):
        page = FakePage()
        page.missing.update(meta_automation.SELECTORS['download'])
        page.revealable = set()
        page.images.append(FakeShot())
        page.wait_limit = 400
        photos = self.photos(2)
        with tempfile.TemporaryDirectory() as out:
            target, via = make_driver(self.root).submit_text_to_image(page, photos, 'prompt', out)
            self.assertEqual(via, 'screenshot')
            self.assertTrue(Path(target).is_file())
            self.assertEqual(Path(target).read_bytes(), PNG_BYTES)

    def test_download_selectors_cover_viewer_overlay(self):
        sels = meta_automation.SELECTORS['download']
        text = ' '.join(sels)
        self.assertIn('role=button', text)
        self.assertIn('a[download]', text)

    def test_submit_rejects_invalid_image_download(self):
        page = FakePage(download_payload=b'not-an-image' + b'x' * 3000)
        with tempfile.TemporaryDirectory() as out:
            with self.assertRaises(MetaAutomationError) as raised:
                make_driver(self.root).submit_text_to_image(
                    page, self.photos(1), 'prompt', out)
            self.assertEqual(raised.exception.step, 'download')
            leftovers = [p for p in Path(out).iterdir()]
            self.assertEqual(leftovers, [])

    def test_submit_requires_an_upload_control(self):
        page = FakePage()
        page.inputs = []
        with tempfile.TemporaryDirectory() as out:
            with self.assertRaises(MetaAutomationError) as raised:
                make_driver(self.root).submit_text_to_image(
                    page, self.photos(1), 'prompt', out)
        self.assertEqual(raised.exception.step, 'upload')

    def test_submit_requires_existing_photo_files(self):
        page = FakePage()
        with tempfile.TemporaryDirectory() as out:
            with self.assertRaises(MetaAutomationError) as raised:
                make_driver(self.root).submit_text_to_image(
                    page, [str(self.root / 'ghost.jpg')], 'prompt', out)
        self.assertEqual(raised.exception.step, 'photos')

    def test_submit_requires_signed_in_session(self):
        page = FakePage()
        page.logged_out = True
        with tempfile.TemporaryDirectory() as out:
            with self.assertRaises(MetaLoginRequired):
                make_driver(self.root).submit_text_to_image(
                    page, self.photos(1), 'prompt', out)

    def test_image_bytes_validation(self):
        self.assertTrue(meta_automation.valid_image_bytes(PNG_BYTES))
        self.assertTrue(meta_automation.valid_image_bytes(JPG_BYTES))
        webp = b'RIFF' + b'\0' * 4 + b'WEBP' + b'x' * 3000
        self.assertTrue(meta_automation.valid_image_bytes(webp))
        self.assertFalse(meta_automation.valid_image_bytes(b''))
        self.assertFalse(meta_automation.valid_image_bytes(b'plain text' + b'x' * 3000))


    def test_image_flow_hovers_to_reveal_download(self):
        page = FakePage()
        page.missing.update(meta_automation.SELECTORS['download'])
        page.images.append(FakeShot())
        page.images[-1].page = page
        page.result_image = FakeShot()
        page.result_image.page = page
        page.wait_limit = 400
        with tempfile.TemporaryDirectory() as out:
            target, via = make_driver(self.root).submit_text_to_image(
                page, self.photos(1), 'prompt', out)
            self.assertEqual(via, 'download')
            self.assertTrue(page.hovers)
            self.assertEqual(Path(target).read_bytes(), PNG_BYTES)
    def test_image_flow_fetches_direct_image_url(self):
        from unittest.mock import patch
        page = FakePage()
        page.missing.update(meta_automation.SELECTORS['download'])
        page.revealable = set()
        page.images.append(FakeShot(width=100, height=100))
        page.images[-1].page = page
        page.result_image = FakeShot(src='https://cdn.meta.ai/pic.png')
        page.result_image.page = page
        page.wait_limit = 400
        fetched = []
        class FakeResp:
            def read(self, _n=-1):
                return PNG_BYTES
            def __enter__(self):
                return self
            def __exit__(self, *_args):
                return False
        def fake_urlopen(request, timeout=0):
            fetched.append(getattr(request, 'full_url', str(request)))
            return FakeResp()
        with tempfile.TemporaryDirectory() as out:
            with patch('meta_automation.urllib.request.urlopen', side_effect=fake_urlopen):
                target, via = make_driver(self.root).submit_text_to_image(
                    page, self.photos(1), 'prompt', out)
            self.assertEqual(via, 'download')
            self.assertTrue(fetched)
            self.assertTrue(any('cdn.meta.ai' in url for url in fetched))
            self.assertEqual(Path(target).read_bytes(), PNG_BYTES)
if __name__ == '__main__':
    unittest.main()
