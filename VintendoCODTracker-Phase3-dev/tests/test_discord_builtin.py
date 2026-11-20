"""Built-in Discord webhooks: defaults, user-override migration, routing,
failure redaction, and the hidden Discord tab. Headless-safe: the HTTP
layer is faked at the opener boundary, so no live message is ever sent."""
import io
import os
import re
import tempfile
import unittest
import urllib.error
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import app
import engine as E

WEBHOOK_PATTERN = r'https://discord(?:app)?\.com/api/webhooks/\d+/[A-Za-z0-9._-]+'


class BuiltinWebhookDefaultsTests(unittest.TestCase):
    def _fresh_settings(self):
        return E.Settings(tempfile.mkdtemp())

    def test_match_default_is_builtin(self):
        s = self._fresh_settings()
        self.assertTrue(s.webhook())
        self.assertEqual(s.webhook(), E.BUILTIN_MATCH_WEBHOOK)

    def test_cinematic_default_is_builtin(self):
        s = self._fresh_settings()
        self.assertTrue(s.cinematic_webhook())
        self.assertEqual(s.cinematic_webhook(), E.BUILTIN_CINEMATIC_WEBHOOK)

    def test_builtins_are_distinct_valid_urls(self):
        self.assertNotEqual(E.BUILTIN_MATCH_WEBHOOK, E.BUILTIN_CINEMATIC_WEBHOOK)
        for url in (E.BUILTIN_MATCH_WEBHOOK, E.BUILTIN_CINEMATIC_WEBHOOK):
            self.assertTrue(re.fullmatch(WEBHOOK_PATTERN, url), url)

    def test_manual_override_still_wins(self):
        s = self._fresh_settings()
        custom = "https://discord.com/api/webhooks/1/customtoken"
        s.set_webhook(custom)
        s.set_cinematic_webhook(custom)
        self.assertEqual(s.webhook(), custom)
        self.assertEqual(s.cinematic_webhook(), custom)

    def test_clearing_override_restores_builtin(self):
        s = self._fresh_settings()
        s.set_webhook("https://discord.com/api/webhooks/1/customtoken")
        s.set_webhook("")
        self.assertEqual(s.webhook(), E.BUILTIN_MATCH_WEBHOOK)


class WebhookRoutingTests(unittest.TestCase):
    class _FakeResponse:
        def __init__(self, payload=b""):
            self._payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return self._payload

    def _opener(self, calls, script):
        def open_request(request, timeout=None):
            calls.append(request.full_url)
            action = script.pop(0)
            if isinstance(action, Exception):
                raise action
            return action
        return open_request

    def test_match_route_posts_without_network(self):
        calls = []
        opener = self._opener(calls, [self._FakeResponse(b"")])
        self.assertEqual(E.send_discord(E.BUILTIN_MATCH_WEBHOOK, None, "hi", opener=opener), "")
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0].startswith(E.BUILTIN_MATCH_WEBHOOK))

    def test_cinematic_route_posts_without_network(self):
        calls = []
        opener = self._opener(calls, [self._FakeResponse(b"")])
        self.assertEqual(E.send_discord(E.BUILTIN_CINEMATIC_WEBHOOK, None, "hi", opener=opener), "")
        self.assertTrue(calls[0].startswith(E.BUILTIN_CINEMATIC_WEBHOOK))

    def test_rate_limit_retries_then_succeeds(self):
        fp = io.BytesIO(b'{"retry_after": 0}')
        err = urllib.error.HTTPError(E.BUILTIN_MATCH_WEBHOOK, 429, "rate limited", {}, fp)
        calls = []
        opener = self._opener(calls, [err, self._FakeResponse(b"")])
        self.assertEqual(E.send_discord(E.BUILTIN_MATCH_WEBHOOK, None, "hi",
                                       opener=opener, sleep=lambda seconds: None), "")
        self.assertEqual(len(calls), 2)

    def test_invalid_url_rejected_before_network(self):
        calls = []
        opener = self._opener(calls, [self._FakeResponse(b"")])
        with self.assertRaises(ValueError):
            E.send_discord("https://example.com/hook", None, "hi", opener=opener)
        self.assertEqual(calls, [])

    def test_post_file_rejects_bad_status_without_leaking_url(self):
        import requests

        class BadResponse:
            status_code = 404
            text = "unknown webhook"

        with mock.patch("requests.post", return_value=BadResponse()):
            with self.assertRaises(RuntimeError) as ctx:
                app.post_file_to_discord_webhook(E.BUILTIN_CINEMATIC_WEBHOOK, __file__)
        self.assertNotIn(E.BUILTIN_CINEMATIC_WEBHOOK, str(ctx.exception))


class DiscordTabHiddenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.qt = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = app.Window()
        self._saved = dict(self.window.settings.values)

    def tearDown(self):
        try:
            self.window.settings.values.clear()
            self.window.settings.values.update(self._saved)
            self.window.settings.save()
        except Exception:
            pass
        self.window.shutdown()
        self.window.deleteLater()
        self.qt.processEvents()

    def _tab_index(self, title):
        w = self.window
        for i in range(w.settings_tabs.count()):
            if w.settings_tabs.tabText(i) == title:
                return i
        self.fail(title + " tab missing")

    def test_discord_tab_hidden_but_present(self):
        w = self.window
        index = self._tab_index("Discord")
        try:
            visible = w.settings_tabs.isTabVisible(index)
        except (AttributeError, RuntimeError):
            visible = w.settings_tabs.tabBar().isTabVisible(index)
        self.assertFalse(visible)

    def test_window_webhooks_resolve_to_builtins(self):
        w = self.window
        w.settings.values.pop("protected_webhook", None)
        w.settings.values.pop("protected_cinematic_webhook", None)
        self.assertEqual(w.settings.webhook(), E.BUILTIN_MATCH_WEBHOOK)
        self.assertEqual(w.settings.cinematic_webhook(), E.BUILTIN_CINEMATIC_WEBHOOK)

    def test_discord_ready_check_passes_with_builtin(self):
        w = self.window
        w.settings.values.pop("protected_webhook", None)
        self.assertTrue(bool(w.settings.webhook().strip()))

    def test_cinematic_failure_redacts_webhook_url(self):
        w = self.window
        url = "https://discord.com/api/webhooks/123/abc.def-ghi"
        w.on_cinematic_discord_result("Discord post failed: HTTP 404: " + url)
        self.qt.processEvents()
        text = w.cinematic_status.text()
        self.assertNotIn(url, text)
        self.assertNotIn("abc.def-ghi", text)
        self.assertIn("[webhook]", text)


if __name__ == "__main__":
    unittest.main()
