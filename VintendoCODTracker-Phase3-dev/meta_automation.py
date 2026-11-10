"""In-app Meta AI video automation (Playwright). Replaces the Chrome extension.

The driver owns one headed browser pointed at the tracker's TrackerChrome
profile, so the Meta AI login is shared with the embedded Tracker Browser
tab. Generation runs in a disposable page; the finished MP4 is captured
through Playwright's download event and handed back as a file path.

Meta changes their markup regularly. Every interaction lives in SELECTORS
with fallback strategies, and every failed step saves a screenshot next to
the job so the next calibration run knows exactly what moved.
"""

import re
import time
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


def ensure_context(playwright, user_data_dir, channel='chrome', viewport=(1440, 900)):
    """Launch (or reuse the caller's) headed browser on the tracker profile."""
    folder = Path(user_data_dir)
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    try:
        context = playwright.chromium.launch_persistent_context(
            str(folder), channel=channel, headless=False,
            viewport={'width': viewport[0], 'height': viewport[1]},
            accept_downloads=True,
            args=['--disable-blink-features=AutomationControlled','--start-minimized'])
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
        last = None
        for candidate in candidates:
            try:
                locator = page.locator(candidate).first
                locator.wait_for(state='visible', timeout=timeout)
                return locator
            except Exception as exc:
                last = exc
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

    def submit_image_to_video(self, page, screenshot, faces, prompt,
                              download_dir, timeout_s=1500):
        shot = Path(screenshot)
        if not shot.is_file():
            raise MetaAutomationError('screenshot', f'Missing source image: {shot}')
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
        out_dir = Path(download_dir)
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        try:
            with page.expect_download(timeout=timeout_s * 1000) as download_info:
                generate.click(timeout=8000)
            download = download_info.value
        except MetaLoginRequired:
            raise
        except Exception as exc:
            self._shot(page, 'no-download')
            raise MetaAutomationError(
                'generation',
                'No video download started in time. The job may still be '
                f'rendering on Meta AI — check the tracker browser tab. ({exc})')
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
