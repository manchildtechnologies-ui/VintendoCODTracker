"""CODMunity settings redesign: compact account, auto profile, renamed fields."""
import json
import os
import tempfile
import unittest
import urllib.error

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QPushButton, QLabel

import codmunity_player_stats as cps
import engine as E
import app


class _Resp:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _fake_opener(request, timeout=None):
    url = request.full_url if hasattr(request, "full_url") else request.get_full_url()
    if url.endswith("/vintendo"):
        return _Resp(_banner_payload())
    raise urllib.error.HTTPError(url, 404, "missing", {}, None)


def _banner_payload():
    return {"user": {"username": "vintendo", "uid": "uid-1",
                     "displayName": "Vin", "description": "Bio here",
                     "followers": 3,
                     "imageUrl": "https://assets.codmunity.gg/profiles/pi-x.webp",
                     "imageUrlOptimized": "https://assets.codmunity.gg/profiles/pi-y.webp"},
            "countLoadouts": 2, "countLikes": 7}


def _seed_snapshot(window):
    window.store.set_meta("codmunity_stats", {
        "identity": {"hits": [{"leaderboard": "mw4_multiplayer", "rank": 5,
                               "username": "vintendo", "displayName": "Vin"}]},
        "personal": {"multiplayer": {"stats": {"wins": 4, "games_played": 12,
                                               "kills": 60, "deaths": 30}}}})


class AccountLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = app.Window()
        self.window.notice = lambda *args, **kwargs: None
        self.window.ask = lambda *args, **kwargs: False

    def tearDown(self):
        self.window.shutdown()
        self.window.deleteLater()
        self.qt.processEvents()

    def test_test_local_data_removed(self):
        labels = [b.text() for b in self.window.findChildren(QPushButton)]
        self.assertNotIn("Test local data", labels)
        self.assertFalse(hasattr(self.window, "data_status"))

    def test_account_columns_compact(self):
        w = self.window
        self.assertLessEqual(w.codmunity_email_edit.maximumWidth(), 360)
        self.assertLessEqual(w.codmunity_password_edit.maximumWidth(), 360)
        for name in ("codmunity_signin_button", "codmunity_signout_button",
                     "codmunity_reset_button"):
            self.assertTrue(hasattr(w, name))
        self.assertEqual(w.codmunity_signin_button.text(), "Sign in")
        self.assertEqual(w.codmunity_signout_button.text(), "Sign out")
        self.assertEqual(w.codmunity_reset_button.text(), "Reset password")

    def test_config_fields_same_length(self):
        w = self.window
        for widget in (w.player, w.stats_dir):
            self.assertEqual(widget.minimumWidth(), 320)
            self.assertEqual(widget.maximumWidth(), 320)

    def test_warzone_reports_always_on(self):
        w = self.window
        self.assertFalse(hasattr(w, "warzone"))
        labels = [c.text() for c in w.solo_settings_panel.findChildren(QLabel)]
        self.assertNotIn("Enable confirmed Warzone reports in Solo Ops", labels)
        self.assertTrue(w.settings.values["warzone_enabled"])

    def test_stats_url_uses_player_name(self):
        from app import Window
        self.assertEqual(Window._codmunity_stats_url(" VintendoPower "),
                         "https://codmunity.gg/profile/VintendoPower/stats")
        self.assertEqual(Window._codmunity_stats_url("@vintendopower"),
                         "https://codmunity.gg/profile/vintendopower/stats")
        self.assertEqual(Window._codmunity_stats_url("  "), "")

    def test_stats_frame_follows_player_name(self):
        w = self.window
        self.assertTrue(hasattr(w, "codmunity_stats_frame"))
        if getattr(w, "codmunity_stats_view", None) is None:
            self.skipTest("Qt WebEngine unavailable")
        w.player.setText("vintendopower")
        self.assertEqual(w.codmunity_stats_view.url().toString(),
                         "https://codmunity.gg/profile/vintendopower/stats")
        w.player.setText("someone-else")
        self.assertEqual(w.codmunity_stats_view.url().toString(),
                         "https://codmunity.gg/profile/someone-else/stats")

    def test_fields_renamed(self):
        labels = [c.text() for c in
                  self.window.solo_settings_panel.findChildren(QLabel)]
        self.assertIn("CODMunity Name", labels)
        self.assertIn("CODMunity Profile", labels)
        self.assertNotIn("Your CODMunity / COD username", labels)
        self.assertNotIn("Optional CODMunity profile URL", labels)


class IdentityLinkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = app.Window()
        self.window.notice = lambda *args, **kwargs: None
        self.window.ask = lambda *args, **kwargs: False
        self.window.codmunity_client = cps.CodmunityPublicClient(opener=_fake_opener)

    def tearDown(self):
        self.window.shutdown()
        self.window.deleteLater()
        self.qt.processEvents()

    def test_candidates_prefer_snapshot_hits(self):
        w = self.window
        _seed_snapshot(w)
        w.player.setText("someone-else")
        self.assertEqual(w._identity_candidates()[0], "vintendo")
        self.assertIn("someone-else", w._identity_candidates())

    def test_fetch_confirms_public_identity(self):
        w = self.window
        ident = w._fetch_codmunity_identity(["vintendo", "ghost"], "me@example.com")
        self.assertEqual(ident, {"username": "vintendo", "uid": "uid-1",
                                "display_name": "Vin", "email": "me@example.com",
                                "avatar": "https://assets.codmunity.gg/profiles/pi-y.webp",
                                "bio": "Bio here", "followers": 3,
                                "loadouts": 2, "likes": 7})

    def test_fetch_rejects_everything(self):
        w = self.window
        with self.assertRaises(RuntimeError):
            w._fetch_codmunity_identity(["ghost"], "me@example.com")

    def test_full_lookup_applies_profile_and_fields(self):
        w = self.window
        _seed_snapshot(w)
        w.settings.values["codmunity_email"] = "e2e@example.com"
        w.settings.codmunity_refresh_token = lambda: "tok"
        w.async_task = lambda work, done=None, failed=None: done(work())
        seen_urls = []
        w._fetch_profile_avatar = lambda url: seen_urls.append(url)
        saved = os.environ.pop("PYTEST_CURRENT_TEST", None)
        try:
            w._start_codmunity_identity_lookup()
        finally:
            if saved is not None:
                os.environ["PYTEST_CURRENT_TEST"] = saved
        self.assertEqual(w.player.text(), "vintendo")
        self.assertEqual(w.profile.text(), "https://codmunity.gg/profile/vintendo")
        self.assertEqual(w.settings.values["player"], "vintendo")
        self.assertEqual(w.settings.values["codmunity_username"], "vintendo")
        self.assertEqual(w.settings.values["codmunity_bio"], "Bio here")
        self.assertEqual(w.settings.values["codmunity_loadouts"], 2)
        self.assertEqual(w.codmunity_profile_name.text(), "vintendo")
        self.assertIn("uid-1", w.codmunity_profile_sub.text())
        self.assertIn("rank #5", w.codmunity_profile_stats.text())
        self.assertIn("https://codmunity.gg/profile/vintendo", w.codmunity_profile_sub.text())
        self.assertEqual(w.dash_profile_name.text(), "Vin")
        self.assertEqual(w.dash_profile_handle.text(), "@vintendo")
        self.assertEqual(w.dash_profile_bio.text(), "Bio here")
        self.assertEqual(w.dash_meters["kd"]["value"].text(), "2.00")
        self.assertEqual(w.dash_meters["kd"]["sub"].text(), "60 kills · 30 deaths")
        self.assertEqual(seen_urls, ["https://assets.codmunity.gg/profiles/pi-y.webp"])

    def test_lookup_applies_profile_and_fields(self):
        w = self.window
        _seed_snapshot(w)
        w._on_codmunity_identity({"username": "vintendo", "uid": "uid-1",
                                  "display_name": "Vin", "email": "me@example.com"})
        self.assertEqual(w.player.text(), "vintendo")
        self.assertEqual(w.profile.text(), "https://codmunity.gg/profile/vintendo")
        self.assertEqual(w.settings.values["player"], "vintendo")
        self.assertEqual(w.settings.values["codmunity_username"], "vintendo")
        self.assertEqual(w.codmunity_profile_name.text(), "vintendo")
        self.assertIn("uid-1", w.codmunity_profile_sub.text())
        self.assertIn("rank #5", w.codmunity_profile_stats.text())
        self.assertIn("https://codmunity.gg/profile/vintendo", w.codmunity_profile_sub.text())

    def test_stale_link_without_avatar_refreshes(self):
        w = self.window
        _seed_snapshot(w)
        w.settings.values.update(codmunity_username="vintendo", codmunity_uid="uid-1",
                                 codmunity_link_email="me2@example.com",
                                 codmunity_avatar="", codmunity_bio="")
        w.settings.values["codmunity_email"] = "me2@example.com"
        w.settings.codmunity_refresh_token = lambda: "tok"
        w.async_task = lambda work, done=None, failed=None: done(work())
        seen = []
        real_fetch = w._fetch_profile_avatar
        w._fetch_profile_avatar = lambda url: seen.append(url) if url else real_fetch(url)
        saved = os.environ.pop("PYTEST_CURRENT_TEST", None)
        try:
            w._start_codmunity_identity_lookup()
        finally:
            if saved is not None:
                os.environ["PYTEST_CURRENT_TEST"] = saved
        self.assertEqual(w.settings.values["codmunity_avatar"],
                         "https://assets.codmunity.gg/profiles/pi-y.webp")
        self.assertEqual(w.settings.values["codmunity_bio"], "Bio here")
        self.assertEqual(w.dash_profile_bio.text(), "Bio here")
        self.assertEqual(seen, ["https://assets.codmunity.gg/profiles/pi-y.webp"])

    def test_failure_preserves_manual_entry(self):
        w = self.window
        w.player.setText("my-manual-name")
        w.profile.setText("https://codmunity.gg/profile/my-manual-name")
        w._on_codmunity_identity_failed("boom")
        self.assertEqual(w.player.text(), "my-manual-name")
        self.assertIn("boom", w.codmunity_profile_status.text())

    def test_sign_out_clears_panel_and_link(self):
        w = self.window
        w.settings.values.update(codmunity_username="vintendo", codmunity_uid="uid-1",
                                 codmunity_link_email="me@example.com")
        w.codmunity_profile_name.setText("vintendo")
        w.codmunity_sign_out()
        self.assertEqual(w.settings.values["codmunity_username"], "")
        self.assertEqual(w.codmunity_profile_name.text(), "—")
        self.assertIn("Sign in", w.codmunity_profile_status.text())
        self.assertEqual(w.dash_profile_name.text(), "—")
        self.assertEqual(w.dash_profile_handle.text(), "")
        self.assertEqual(w.dash_meters["kd"]["value"].text(), "—")
        self.assertEqual(w.dash_meters["kd"]["cap"].text(), "")
        self.assertEqual(w.dash_profile_avatar.text(), "◉")
        # manual config fields are left alone as fallback
        self.assertTrue(hasattr(w, "player"))

    def test_avatar_download_renders_circle(self):
        import urllib.request
        from unittest.mock import patch
        from PySide6.QtCore import QBuffer, QIODevice
        from PySide6.QtGui import QImage, QColor
        img = QImage(100, 100, QImage.Format_RGB32)
        img.fill(QColor("#112233"))
        buf = QBuffer()
        buf.open(QIODevice.WriteOnly)
        self.assertTrue(img.save(buf, "WEBP"))
        payload = bytes(buf.data())
        self.assertTrue(len(payload) > 0)

        class _FakeResp:
            def __init__(self, body):
                self._body = body

            def read(self, *args):
                return self._body

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        w = self.window
        with patch.object(urllib.request, "urlopen", return_value=_FakeResp(payload)):
            w.async_task = lambda work, done=None, failed=None: done(work())
            w._fetch_profile_avatar("https://assets.codmunity.gg/profiles/pi-t.webp")
        pixmap = w.dash_profile_avatar.pixmap()
        self.assertIsNotNone(pixmap)
        self.assertFalse(pixmap.isNull())
        self.assertEqual(w.dash_profile_avatar.text(), "")


if __name__ == "__main__":
    unittest.main()
