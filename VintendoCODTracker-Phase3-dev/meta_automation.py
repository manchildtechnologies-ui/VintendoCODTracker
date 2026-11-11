"""In-app Meta AI video and poster automation (Playwright). Replaces the Chrome extension.

The driver owns one headed browser pointed at the tracker's automation
profile, so one Meta AI login covers both cinematics and end-game posters.
Generation runs in a disposable page; the finished MP4 or image is captured
through Playwright's download event and handed back as a file path.

Meta changes their markup regularly. Every interaction lives in SELECTORS
with fallback strategies, and every failed step saves a screenshot next to
the job so the next calibration run knows exactly what moved.
"""

import re
import time
import urllib.request
from pathlib import Path

META_HOME = 'https://www.meta.ai/'
META_CREATE = 'https://www.meta.ai/create'

# Ordered strategies per step. Plain CSS first, accessible role second.
SELECTORS = {
    'logged_out_markers': ['a[href*="login"]', 'button:has-text("Log in")'],
    'create_entry': [
        'a[href*="/create"]',
        'button:has-text("Create")',
    ],
    'video_mode': [
        'button:has-text("Video")',
        'button:has-text("Imagine")',
        '[data-testid*="video"]',
    ],
    'image_mode': [
        'button:has-text("Imagine")',
        'button:has-text("Image")',
        '[data-testid*="image"]',
    ],
    'download': [
        'button:has-text("Download")',
        'a:has-text("Download")',
        '[aria-label*="Download"]',
        'role=button[name=/download/i]',
        'a[download]',
        '[data-testid*="download" i]',
    ],
    'file_inputs': 'input[type="file"]',
    'prompt_box': [
        'div[contenteditable="true"]',
        'textarea',
        '[role="textbox"]',
    ],
    'generate': [
        'button:has-text("Generate")',
        'button[aria-label*="Send"]',
        'button[type="submit"]',
    ],
    'result_video': 'video[src]',
}


class MetaAutomationError(Exception):
    def __init__(self, step, detail=''):
        super().__init__(f'{step}: {detail}' if detail else step)
        self.step = step
        self.detail = detail


class MetaLoginRequired(MetaAutomationError):
    pass


def valid_mp4_bytes(data):
    if not data or len(data) < 2048:
        return False
    head = bytes(data[:4096])
    return head[4:8] == b'ftyp' and b'moov' in head and b'mdat' in bytes(data[:1 << 20])


def valid_image_bytes(data):
    if not data or len(data) < 1024:
        return False
    head = bytes(data[:32])
    if head[:8] == b'\x89PNG\r\n\x1a\n':
        return True
    if head[:3] == b'\xff\xd8\xff':
        return True
    return head[:4] == b'RIFF' and head[8:12] == b'WEBP'


def ensure_context(playwright, user_data_dir, channel='chrome', viewport=(1440, 900), minimized=True):
    """Launch (or reuse the caller's) headed browser on the tracker profile."""
    folder = Path(user_data_dir)
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    try:
        args = ['--disable-blink-features=AutomationControlled']
        if minimized:
            args.append('--start-minimized')
        context = playwright.chromium.launch_persistent_context(
            str(folder), channel=channel, headless=False,
            viewport={'width': viewport[0], 'height': viewport[1]},
            accept_downloads=True,
            args=args)
    except Exception as exc:
        raise MetaAutomationError(
            'browser',
            'Could not start Chrome for automation. Install Google Chrome '
            f'or switch Meta submission back to the extension. ({exc})')
    return context


class MetaDriver:
    def __init__(self, context, diagnose_dir):
        self.context = context
        self.diagnose_dir = Path(diagnose_dir)
        try:
            self.diagnose_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

    def _shot(self, page, name):
        try:
            stamp = time.strftime('%Y%m%d-%H%M%S')
            page.screenshot(path=str(self.diagnose_dir / f'meta-{name}-{stamp}.png'))
        except Exception:
            pass

    def _first_visible(self, page, candidates, timeout=8000):
        deadline = time.time() + max(timeout, 0) / 1000.0
        while True:
            for candidate in candidates:
                try:
                    locator = page.locator(candidate).first
                    if locator.is_visible():
                        return locator
                except Exception:
                    continue
            if time.time() >= deadline:
                return None
            try:
                page.wait_for_timeout(250)
            except Exception:
                return None

    def _stage(self, on_stage, stage, detail=''):
        try:
            if callable(on_stage):
                on_stage(stage, detail)
        except Exception:
            pass

    def _submit_composer(self, page, prompt, on_stage, timeout_s, expect_button, shot_dir=None):
        """Shared prompt fill, generate click, and download capture for image and video composers."""
        prompt_box = self._first_visible(page, SELECTORS['prompt_box'], timeout=10000)
        if prompt_box is None:
            self._shot(page, 'no-prompt-box')
            raise MetaAutomationError('prompt', 'Prompt box not found on the composer.')
        try:
            prompt_box.click(timeout=5000)
            prompt_box.fill(prompt or '')
            page.wait_for_timeout(1000)
        except Exception as exc:
            self._shot(page, 'prompt-failed')
            raise MetaAutomationError('prompt', f'Could not enter the prompt. ({exc})')
        generate = self._first_visible(page, SELECTORS['generate'], timeout=10000)
        if generate is None:
            self._shot(page, 'no-generate')
            raise MetaAutomationError('submit', 'Generate button not found on the composer.')
        self._stage(on_stage, 'generating')
        try:
            if expect_button:
                result = self._image_flow(page, generate, on_stage, timeout_s, shot_dir)
                if result is None:
                    raise MetaAutomationError('generation', 'No finished file arrived in time. The job may still be rendering on Meta AI.')
                return result
            with page.expect_download(timeout=timeout_s * 1000) as download_info:
                generate.click(timeout=8000)
            download = download_info.value
        except MetaLoginRequired:
            raise
        except Exception as exc:
            self._shot(page, 'no-download')
            raise MetaAutomationError('generation', 'No finished file arrived in time. The job may still be rendering on Meta AI. (%s)' % exc)
        self._stage(on_stage, 'collecting')
        return download

    def _image_flow(self, page, generate, on_stage, timeout_s, shot_dir):
        # Snapshot the page before submission. Taking this after clicking Generate
        # marks Meta's newly-created result as old and prevents the viewer fallback
        # from selecting it when Meta does not emit a browser download event.
        known_srcs = self._known_image_srcs(page)
        try:
            generate.click(timeout=8000)
        except Exception as exc:
            raise MetaAutomationError('submit', f'Could not start generation. ({exc})')
        tried = set()
        deadline = time.time() + min(timeout_s, 600)
        while time.time() < deadline:
            try:
                img, _key = self._largest_image(page, known_srcs, tried)
                if img is not None:
                    self._hover(img)
            except Exception:
                pass
            button = self._first_visible(page, SELECTORS['download'], timeout=60000)
            if button is not None:
                self._stage(on_stage, 'collecting')
                try:
                    with page.expect_download(timeout=timeout_s * 1000) as download_info:
                        button.click(timeout=8000)
                    return download_info.value
                except Exception:
                    pass
            fetched = self._fetch_image_url(page, known_srcs, tried, shot_dir)
            if fetched is not None:
                return fetched
            got = self._acquire_viewer_shot(page, on_stage, known_srcs, tried, shot_dir)
            if got is not None:
                return ('screenshot', got)
        return None

    @staticmethod
    def _hover(locator):
        try:
            locator.hover(timeout=5000)
        except Exception:
            pass

    @staticmethod
    def _download_url(src):
        request = urllib.request.Request(src, headers={'User-Agent': 'Mozilla/5.0', 'Referer': META_HOME})
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.read(12 << 20)

    def _fetch_image_url(self, page, exclude, tried, shot_dir):
        try:
            images = page.locator('img').all()
        except Exception:
            return None
        exclude = exclude or set()
        if tried is None:
            tried = set()
        candidates = []
        for image in images or []:
            try:
                src = image.get_attribute('src')
            except Exception:
                continue
            if not src or not src.startswith(('http://', 'https://')):
                continue
            if src in exclude or src in tried:
                continue
            try:
                if not image.is_visible():
                    continue
                box = image.bounding_box()
                area = box['width'] * box['height'] if box else 0
            except Exception:
                area = 0
            candidates.append((area, src))
        candidates.sort(key=lambda item: item[0], reverse=True)
        for _area, src in candidates[:4]:
            tried.add(src)
            try:
                data = self._download_url(src)
            except Exception:
                continue
            if not valid_image_bytes(data):
                continue
            try:
                folder = Path(shot_dir or '.')
                folder.mkdir(parents=True, exist_ok=True)
                target = folder / ('meta-direct-%d.png' % int(time.time() * 1000))
                target.write_bytes(data)
                return target
            except OSError:
                return None
        return None

    @staticmethod
    def _known_image_srcs(page):
        try:
            srcs = set()
            for image in page.locator('img').all():
                try:
                    src = image.get_attribute('src')
                except Exception:
                    continue
                if src:
                    srcs.add(src)
            return srcs
        except Exception:
            return set()

    @staticmethod
    def _largest_image(page, exclude=None, tried=None):
        exclude = exclude or set()
        tried = tried or set()
        try:
            images = page.locator('img').all()
        except Exception:
            return None, None
        best = None
        best_area = 0
        best_key = None
        for image in images:
            try:
                if not image.is_visible():
                    continue
                try:
                    if image.locator('xpath=ancestor::nav | ancestor::aside').count() > 0:
                        continue
                except Exception:
                    pass
                try:
                    src = image.get_attribute('src')
                except Exception:
                    src = None
                if src and src in exclude:
                    continue
                key = src or id(image)
                if key in tried:
                    continue
                box = image.bounding_box()
                if not box:
                    continue
                area = box['width'] * box['height']
                if area > best_area:
                    best_area = area
                    best = image
                    best_key = key
            except Exception:
                continue
        if best is None or best_area < 200 * 200:
            return None, None
        return best, best_key

    def _acquire_viewer_shot(self, page, on_stage, exclude, tried, shot_dir):
        self._stage(on_stage, 'collecting')
        img, key = self._largest_image(page, exclude, tried)
        if img is None:
            return None
        tried.add(key)
        try:
            img.click(timeout=8000)
        except Exception:
            return None
        try:
            page.wait_for_timeout(1500)
        except Exception:
            pass
        self._hover(img)
        button = self._first_visible(page, SELECTORS['download'], timeout=20000)
        if button is not None:
            try:
                with page.expect_download(timeout=120000) as download_info:
                    button.click(timeout=8000)
                return download_info.value
            except Exception:
                pass
        return self._screenshot_largest(page, shot_dir)

    def _screenshot_largest(self, page, shot_dir):
        img, _key = self._largest_image(page)
        if img is None:
            return None
        try:
            data = bytes(img.screenshot(timeout=8000))
        except Exception:
            return None
        if not valid_image_bytes(data):
            return None
        try:
            folder = Path(shot_dir or '.')
            folder.mkdir(parents=True, exist_ok=True)
            target = folder / ('meta-viewer-%d.png' % int(time.time() * 1000))
            target.write_bytes(data)
            return target
        except OSError:
            return None

    def new_job_page(self):
        page = self.context.new_page()
        page.set_default_timeout(30000)
        return page

    def ensure_logged_in(self, page):
        page.goto(META_HOME, wait_until='domcontentloaded')
        page.wait_for_timeout(2500)
        for marker in SELECTORS['logged_out_markers']:
            try:
                if page.locator(marker).first.is_visible(timeout=2000):
                    self._shot(page, 'login-required')
                    raise MetaLoginRequired(
                        'login', 'Meta AI is signed out in the tracker browser. '
                                'Open Tracker Chrome, log into Meta AI once, then retry.')
            except MetaLoginRequired:
                raise
            except Exception:
                pass
        return page

    def open_image_to_video(self, page):
        self.ensure_logged_in(page)
        for url in (META_CREATE, META_HOME):
            try:
                page.goto(url, wait_until='domcontentloaded')
                page.wait_for_timeout(2000)
                entry = self._first_visible(page, SELECTORS['create_entry'], timeout=6000)
                if entry is not None:
                    try:
                        entry.click(timeout=5000)
                        page.wait_for_timeout(2000)
                    except Exception:
                        pass
                mode = self._first_visible(page, SELECTORS['video_mode'], timeout=8000)
                if mode is not None:
                    try:
                        mode.click(timeout=5000)
                        page.wait_for_timeout(2000)
                    except Exception:
                        pass
                prompt = self._first_visible(page, SELECTORS['prompt_box'], timeout=10000)
                if prompt is not None:
                    return page
            except MetaLoginRequired:
                raise
            except Exception:
                pass
        self._shot(page, 'no-create-surface')
        raise MetaAutomationError(
            'create-surface',
            'Could not reach the Meta AI video composer. The site layout may '
            'have changed — a screenshot was saved next to the job output.')

    def open_image_composer(self, page):
        self.ensure_logged_in(page)
        page.goto(META_HOME, wait_until='domcontentloaded')
        page.wait_for_timeout(2000)
        prompt = self._first_visible(page, SELECTORS['prompt_box'], timeout=10000)
        if prompt is None:
            self._shot(page, 'no-create-surface')
            raise MetaAutomationError(
                'create-surface',
                'Prompt box not found on Meta AI. Open meta.ai in Tracker Chrome once, then retry.')
        return page

    def submit_text_to_image(self, page, photos, prompt, download_dir, timeout_s=1200, on_stage=None):
        files = [str(p) for p in (photos or []) if Path(p).is_file()]
        files = [p for p in files if Path(p).stat().st_size <= 20 * 1024 * 1024][:4]
        if not files:
            raise MetaAutomationError(
                'photos', 'No staged reference photos exist for this poster job.')
        self._stage(on_stage, 'composing')
        page = self.open_image_composer(page)
        inputs = page.locator(SELECTORS['file_inputs'])
        try:
            count = inputs.count()
        except Exception:
            count = 0
        if count < 1:
            self._shot(page, 'no-file-input')
            raise MetaAutomationError(
                'upload', 'No file upload on the composer. The site layout may '
                           'have changed — a screenshot was saved next to the job output.')
        try:
            self._stage(on_stage, 'uploading')
            inputs.nth(0).set_input_files(files)
            page.wait_for_timeout(2500)
        except Exception:
            attached = False
            for index, photo in enumerate(files):
                if index >= count:
                    break
                try:
                    inputs.nth(index).set_input_files(photo)
                    attached = True
                except Exception:
                    continue
            page.wait_for_timeout(2500)
            if not attached:
                self._shot(page, 'upload-failed')
                raise MetaAutomationError('upload', 'Could not attach the reference photos.')
        out_dir = Path(download_dir)
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        result = self._submit_composer(page, prompt, on_stage, timeout_s, expect_button=True, shot_dir=str(out_dir))
        via = 'download'
        if isinstance(result, tuple):
            via, result = result
        if isinstance(result, Path):
            target = result
        else:
            download = result
            name = str(download.suggested_filename or 'poster.png')
            target = out_dir / name
            if target.suffix.lower() not in ('.png', '.jpg', '.jpeg', '.webp'):
                target = target.with_suffix('.png')
            try:
                download.save_as(str(target))
            except Exception as exc:
                raise MetaAutomationError('download', f'Could not save the image. ({exc})')
        try:
            data = target.read_bytes()
        except OSError as exc:
            raise MetaAutomationError('download', f'Saved image is unreadable. ({exc})')
        if not valid_image_bytes(data):
            try:
                target.unlink()
            except OSError:
                pass
            raise MetaAutomationError('download', 'The downloaded file is not a valid image.')
        return target, via

    def submit_image_to_video(self, page, screenshot, faces, prompt,
                              download_dir, timeout_s=1500, on_stage=None):
        shot = Path(screenshot)
        if not shot.is_file():
            raise MetaAutomationError('screenshot', f'Missing source image: {shot}')
        self._stage(on_stage, 'composing')
        page = self.open_image_to_video(page)
        inputs = page.locator(SELECTORS['file_inputs'])
        try:
            count = inputs.count()
        except Exception:
            count = 0
        if count < 1:
            self._shot(page, 'no-file-input')
            raise MetaAutomationError(
                'upload', 'No file upload on the composer. The site layout may '
                           'have changed — a screenshot was saved next to the job output.')
        try:
            self._stage(on_stage, 'uploading')
            inputs.nth(0).set_input_files(str(shot))
            page.wait_for_timeout(2500)
        except Exception as exc:
            self._shot(page, 'upload-failed')
            raise MetaAutomationError('upload', f'Could not attach the screenshot. ({exc})')
        face_files = [str(p) for p in (faces or []) if Path(p).is_file()][:3]
        if face_files:
            try:
                target = inputs.nth(1) if count > 1 else inputs.nth(0)
                target.set_input_files(face_files)
                page.wait_for_timeout(2500)
            except Exception as exc:
                self._shot(page, 'faces-failed')
                raise MetaAutomationError('faces', f'Could not attach reference faces. ({exc})')
        out_dir = Path(download_dir)
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        download = self._submit_composer(page, prompt, on_stage, timeout_s, expect_button=False)
        target = out_dir / (download.suggested_filename or 'cinematic.mp4')
        if target.suffix.lower() != '.mp4':
            target = target.with_suffix('.mp4')
        try:
            download.save_as(str(target))
        except Exception as exc:
            raise MetaAutomationError('download', f'Could not save the video. ({exc})')
        try:
            data = target.read_bytes()
        except OSError as exc:
            raise MetaAutomationError('download', f'Saved video is unreadable. ({exc})')
        if not valid_mp4_bytes(data):
            try:
                target.unlink()
            except OSError:
                pass
            raise MetaAutomationError('download', 'The downloaded file is not a valid MP4.')
        return target
