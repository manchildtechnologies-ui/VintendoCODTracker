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
        return 1

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

    def goto(self, url, wait_until=''):
        self.navigated.append(url)

    def wait_for_timeout(self, _ms):
        pass

    def locator(self, selector):
        return FakeLocator(self, selector)

    def screenshot(self, path=''):
        pass

    @contextmanager
    def expect_download(self, timeout=0):
        yield FakeExpect(self)


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
            result = make_driver(self.root).submit_text_to_image(
                page, photos, 'Victory poster prompt', out, on_stage=lambda stage, detail=None: stages.append(stage))
            saved = Path(result)
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


if __name__ == '__main__':
    unittest.main()
