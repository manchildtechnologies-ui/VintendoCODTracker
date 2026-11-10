"""Isolated, user-controlled Meta AI browser handoff and download capture.

The consumer site is operated manually. This module does not inspect its DOM,
submit prompts, attach files, or call private service endpoints.

Authorization boundary: Meta's Terms of Service section 3.2 prohibits
accessing or collecting data from Meta Products using automated means
without Meta's prior permission, even when logged in; the Automated Data
Collection Terms separately require express written permission through a
formal authorization process. This app has no such permission, so prompt
entry, photo attachment, generation submission, and result polling on the
consumer website stay manual. This module only opens the site, listens for
the user's own download event, and validates the downloaded file.
"""
from __future__ import annotations

import hashlib
import threading
import time
import uuid
from pathlib import Path


def save_download(directory, job_id, download, on_image, on_status):
    """Validate one explicit browser download and associate it with its job."""
    from PIL import Image

    folder = Path(directory) / 'artwork'
    folder.mkdir(parents=True, exist_ok=True)
    job_key = hashlib.sha256(str(job_id).encode('utf-8')).hexdigest()[:24]
    target = folder / f'{job_key}-{uuid.uuid4().hex[:12]}.png'
    temporary = target.with_suffix('.download')
    try:
        download.save_as(str(temporary))
        with Image.open(temporary) as image:
            image.verify()
        with Image.open(temporary) as image:
            if image.width < 320 or image.height < 180:
                raise ValueError('image is below minimum dimensions')
            image.convert('RGB').save(target, format='PNG')
        on_image(str(job_id), str(target))
        on_status(f'Image download validated and linked to match {job_id}.')
        return str(target)
    except Exception as exc:
        on_status('Image download could not be validated: ' + type(exc).__name__)
        return None
    finally:
        temporary.unlink(missing_ok=True)


class BrowserSession:
    """One persistent isolated browser, restricted to manual website handoff."""
    def __init__(self, directory, on_image, on_status, *, navigation_timeout=60,
                 download_timeout=1800, retries=1):
        self.directory = Path(directory)
        self.on_image = on_image
        self.on_status = on_status
        self.navigation_timeout = max(10, min(int(navigation_timeout), 180))
        self.download_timeout = max(300, min(int(download_timeout), 7200))
        self.retries = max(0, min(int(retries), 3))
        self.thread = None
        self.stopped = threading.Event()
        self.lock = threading.Lock()
        self.job_id = None
        self.staged = {}

    def staged_info(self, job_id):
        with self.lock:
            info = self.staged.get(str(job_id))
            return dict(info) if info else None

    def open(self, job_id, *, prompt_chars=0, photos=()):
        job_id = str(job_id)
        with self.lock:
            if self.thread and self.thread.is_alive():
                if self.job_id != job_id:
                    self.on_status('The Meta window is still linked to another match. Close it before opening a different job; this prevents misattributed downloads.')
                    return False
                self.on_status('Meta AI is already open for this match. Finish the current handoff or cancel it.')
                return True
            self.stopped.clear()
            self.job_id = job_id
            self.staged[job_id] = {'photos': [str(p) for p in photos],
                                   'prompt_chars': int(prompt_chars),
                                   'staged_at': time.monotonic(),
                                   'timed_out': False}
            self.thread = threading.Thread(target=self._run, args=(job_id,), daemon=True,
                                           name='VintendoMetaBrowser')
            self.thread.start()
        return True

    def close(self, timeout=5):
        self.stopped.set()
        self.on_status('Browser close requested; match data and staged files are preserved.')
        thread = None
        with self.lock:
            thread = self.thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=max(1, min(int(timeout), 15)))

    def _launch(self, playwright, profile):
        errors = []
        for attempt in range(self.retries + 1):
            for channel in ('msedge', 'chrome'):
                if self.stopped.is_set():
                    raise RuntimeError('Browser launch cancelled.')
                try:
                    return playwright.chromium.launch_persistent_context(
                        str(profile), channel=channel, headless=False,
                        accept_downloads=True, viewport={'width': 900, 'height': 700},
                        args=['--window-size=940,780'])
                except Exception as exc:
                    errors.append(type(exc).__name__)
            if attempt < self.retries and not self.stopped.wait(min(attempt + 1, 3)):
                self.on_status(f'Chrome and Edge launch failed; retrying browser launch ({attempt + 2}/{self.retries + 1}).')
        raise RuntimeError('Could not launch Edge or Chrome. ' + ', '.join(errors[-4:]))

    def _run(self, job_id):
        context = None
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as playwright:
                profile = self.directory / 'browser-profile'
                profile.mkdir(parents=True, exist_ok=True)
                context = self._launch(playwright, profile)
                page = context.pages[0] if context.pages else context.new_page()

                def downloaded(download):
                    save_download(self.directory, job_id, download, self.on_image,
                                  self.on_status)

                for existing in context.pages:
                    existing.on('download', downloaded)
                context.on('page', lambda new_page: new_page.on('download', downloaded))

                last_error = None
                for attempt in range(self.retries + 1):
                    if self.stopped.is_set():
                        return
                    try:
                        page.goto('https://www.meta.ai/', wait_until='domcontentloaded',
                                  timeout=self.navigation_timeout * 1000)
                        last_error = None
                        break
                    except Exception as exc:
                        last_error = exc
                        if attempt < self.retries:
                            self.on_status(f'Meta AI page did not open; retrying navigation ({attempt + 2}/{self.retries + 1}).')
                if last_error:
                    raise RuntimeError('Meta AI page navigation failed: ' + type(last_error).__name__) from None

                self.on_status('Meta AI is open in its isolated persistent profile. Sign in normally once. The app has prepared the 16:9 prompt and consented photos; paste/upload/submit and choose Download on the website. The app captures and validates that download.')
                started = time.monotonic()
                timeout_reported = False
                while not self.stopped.is_set() and context.pages:
                    if (not timeout_reported and
                            time.monotonic() - started >= self.download_timeout):
                        timeout_reported = True
                        with self.lock:
                            if job_id in self.staged:
                                self.staged[job_id]['timed_out'] = True
                        self.on_status('No image download was received before the configured wait timeout. The browser stays open; finish manually or cancel and retry the handoff.')
                    # Pump Playwright events while waiting; this is not a page-state
                    # guess and does not interact with the Meta page.
                    context.pages[0].wait_for_timeout(200)
        except Exception as exc:
            self.on_status('Meta browser error: ' + str(exc)[:220])
        finally:
            if context:
                try:
                    context.close()
                except Exception:
                    pass
            self.on_status('Meta browser session ended; match data is preserved.')
            with self.lock:
                self.job_id = None
                self.staged.pop(job_id, None)
