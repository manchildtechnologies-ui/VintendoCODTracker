"""Download the official CODMunity Lifetime PNG using the saved login."""
from pathlib import Path
import os
import sys
import time
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright, Error as PlaywrightError

BASE = Path(__file__).resolve().parent
PROFILE = Path(os.environ.get('VINTENDO_DATA_DIR', str(Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'Vintendo COD Tracker'))) / 'codmunity-browser-profile'
PROFILE_URL = "https://codmunity.gg/profile/vintendopower/stats"


def download_lifetime_png(target, profile_url=PROFILE_URL,
                          username="Vintendo", headed=True):
    target = Path(target).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    max_attempts = 2
    with sync_playwright() as p:
        last_error = None
        for attempt in range(1, max_attempts + 1):
            context = None
            page = None
            stage = "launching browser context"
            closing_intentionally = [False]
            try:
                context = p.chromium.launch_persistent_context(
                    user_data_dir=str(PROFILE),
                    channel='msedge',
                    headless=not headed,
                    viewport={"width": 1440, "height": 950},
                    accept_downloads=True
                )
                context.on("close", lambda: print("[CODMunity Share] Browser context closed.", file=sys.stderr) if not closing_intentionally[0] else None)
                page = next((pg for pg in context.pages if not pg.is_closed()), None) or context.new_page()
                page.on("close", lambda: print(f"[CODMunity Share] Page closed unexpectedly during {stage}.", file=sys.stderr) if not closing_intentionally[0] else None)
                page.on("crash", lambda: print(f"[CODMunity Share] Page crashed during {stage}.", file=sys.stderr))

                stage = "opening CODMunity stats page"
                response = page.goto(profile_url, wait_until="domcontentloaded", timeout=60000)
                if page.is_closed():
                    raise PlaywrightError("Page closed immediately after navigation")
                final_host = urlsplit(page.url).hostname
                requested_host = urlsplit(profile_url).hostname
                if final_host and requested_host and final_host != requested_host:
                    print(f"[CODMunity Share] Page redirected to host {final_host}.", file=sys.stderr)
                if response is not None and response.status >= 400:
                    raise RuntimeError(f"CODMunity stats page returned HTTP {response.status}")

                stage = "waiting for Share my stats control"
                share = page.get_by_role("button", name="Share my stats", exact=False)
                share.wait_for(state="visible", timeout=45000)
                share.click()

                stage = "selecting Lifetime share format"
                page.get_by_text("Lifetime", exact=True).last.click(timeout=10000)
                page.get_by_text("Post 4:5", exact=True).click(timeout=10000)

                dialog = page.get_by_role("dialog")
                scope = dialog if dialog.count() else page

                qr = scope.get_by_text("QR code and link", exact=True)
                if qr.count():
                    option = qr.first.locator("xpath=..")
                    checkbox = option.get_by_role("checkbox")
                    if checkbox.count():
                        if checkbox.first.is_checked():
                            checkbox.first.uncheck()
                    else:
                        selected = option.get_attribute("aria-checked")
                        if selected == "true":
                            option.click()
                        elif selected is None:
                            classes = option.get_attribute("class") or ""
                            if "border-lime" in classes or "border-green" in classes:
                                option.click()

                fields = scope.locator('input[type="text"]:visible')
                if fields.count():
                    fields.last.fill(username)

                stage = "waiting for Download control"
                download_button = page.get_by_role("button", name="Download", exact=True)
                download_button.wait_for(state="visible", timeout=15000)

                stage = "downloading lifetime share image"
                with page.expect_download(timeout=60000) as info:
                    download_button.click()
                info.value.save_as(str(target))

                signature = target.read_bytes()[:8]
                if signature.startswith(b"\x89PNG\r\n\x1a\n"):
                    pass
                elif signature.startswith(b"\xff\xd8\xff"):
                    new_target = target.with_suffix(".jpg")
                    target.replace(new_target)
                    target = new_target
                else:
                    raise RuntimeError("Downloaded file is not a recognized image")
                print("SUCCESS! CODMunity lifetime share saved:", target)
                return target
            except PlaywrightError as exc:
                last_error = exc
                print(f"[CODMunity Share] Attempt {attempt}/{max_attempts} failed during {stage}: {type(exc).__name__}: {exc}", file=sys.stderr)
                if attempt < max_attempts:
                    time.sleep(1)
            except (OSError, RuntimeError, ValueError) as exc:
                # Permanent file/response/format failures should be reported without repeating the action.
                raise RuntimeError(f"CODMunity Share failed during {stage}: {type(exc).__name__}: {exc}") from exc
            finally:
                if context is not None:
                    closing_intentionally[0] = True
                    try:
                        context.close()
                    except PlaywrightError as exc:
                        print(f"[CODMunity Share] Browser cleanup failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise RuntimeError(f"CODMunity Share failed after {max_attempts} attempts: {type(last_error).__name__}: {last_error}") from last_error



