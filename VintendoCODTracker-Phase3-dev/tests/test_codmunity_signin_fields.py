"""CODMunity sign-in wiring, blank-by-default player name, readable sync buttons.

Headless-safe: fake auth client, synchronous tasks, no network.
Covers three field-reported bugs: typed credentials never reaching auth
(a misspelled widget attribute silently emptied the password), a hardcoded
'vintendo' player default on fresh installs, and unreadably small
Sync-account action buttons.
"""
import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import app
import codmunity_player_stats
import engine as E


class SignInFieldWiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.qt = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = app.Window()
        self._saved = dict(self.window.settings.values)
        self.window.async_task = lambda work, done=None, failed=None: done(work())

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

    def test_typed_credentials_reach_auth_client(self):
        seen = {}

        class FakeAuth:
            def __init__(self, *args, **kwargs):
                pass

            @property
            def refresh_token(self):
                return "tok"

            def sign_in(self, email, password):
                seen["email"] = email
                seen["password"] = password
                return {"email": email}

        w = self.window
        w.codmunity_email_edit.setText("player@example.com")
        w.codmunity_password_edit.setText("s3cret-pw")
        with mock.patch.object(codmunity_player_stats, "CodmunityAuthClient", FakeAuth):
            with mock.patch.object(
                codmunity_player_stats, "build_authorized_fetcher",
                lambda auth: None), mock.patch.object(
                    codmunity_player_stats, "ModularPlayerStatsService",
                    lambda *args, **kwargs: object()):
                w.codmunity_sign_in(False)
                self.qt.processEvents()
        self.assertEqual(seen.get("email"), "player@example.com")
        self.assertEqual(seen.get("password"), "s3cret-pw")

    def test_missing_password_never_touches_auth(self):
        def boom(*args, **kwargs):
            raise AssertionError("auth must not be called without credentials")

        w = self.window
        w.codmunity_email_edit.setText("player@example.com")
        w.codmunity_password_edit.clear()
        with mock.patch.object(codmunity_player_stats, "CodmunityAuthClient", boom):
            w.codmunity_sign_in(False)
            self.qt.processEvents()
        self.assertIn("email", w.codmunity_account_status.text().lower())


class PlayerNameDefaultTests(unittest.TestCase):
    def test_fresh_install_has_blank_player_name(self):
        self.assertIn("player", E.DEFAULTS)
        self.assertEqual(E.DEFAULTS.get("player"), "")


class SyncAccountButtonsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.qt = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = app.Window()

    def tearDown(self):
        self.window.shutdown()
        self.window.deleteLater()
        self.qt.processEvents()

    def test_sync_tab_scrolls_instead_of_squeezing(self):
        from PySide6.QtWidgets import QScrollArea
        w = self.window
        inner = w.sync_tab_page.layout().itemAt(0).widget()
        self.assertIsInstance(inner, QScrollArea)
        self.assertTrue(inner.widgetResizable())

    def test_linked_account_buttons_are_readable(self):
        w = self.window
        buttons = [w.sync_pair_button, w.sync_profile_button,
                   w.sync_site_button, w.sync_verify_button]
        parents = set()
        for button in buttons:
            self.assertFalse(button.isHidden())
            self.assertGreaterEqual(button.minimumHeight(), 30)
            parents.add(button.parentWidget())
        self.assertEqual(len(parents), 1)
        from PySide6.QtCore import Qt
        for i in range(w.nav.count()):
            if w.nav.item(i).data(Qt.UserRole) == "Settings":
                w.nav.setCurrentRow(i)
                break
        for i in range(w.settings_tabs.count()):
            if "Sync" in w.settings_tabs.tabText(i):
                w.settings_tabs.setCurrentIndex(i)
                break
        w.show()
        self.qt.processEvents()
        try:
            tops = [b.mapTo(w, b.rect().topLeft()).y() for b in buttons]
        finally:
            w.hide()
            self.qt.processEvents()
        self.assertEqual(tops, sorted(tops))
        self.assertGreater(min(b - a for a, b in zip(tops, tops[1:])), 20)


class SignInButtonBusyTests(unittest.TestCase):
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

    def test_busy_animation_starts_and_restores(self):
        from PySide6.QtWidgets import QPushButton
        w, btn = self.window, QPushButton("Sign in")
        w._set_button_busy(btn, True)
        self.assertFalse(btn.isEnabled())
        self.assertTrue(btn.text().startswith("Sign in"))
        self.assertIsNotNone(getattr(btn, "_busy_timer", None))
        w._set_button_busy(btn, False)
        self.assertTrue(btn.isEnabled())
        self.assertEqual(btn.text(), "Sign in")
        self.assertIsNone(getattr(btn, "_busy_timer", None))

    def test_busy_tick_advances_dots(self):
        from PySide6.QtWidgets import QPushButton
        w, btn = self.window, QPushButton("Go")
        w._set_button_busy(btn, True)
        btn._busy_timer.timeout.emit()
        btn._busy_timer.timeout.emit()
        self.assertEqual(btn.text(), "Go..")
        w._set_button_busy(btn, False)
        self.assertEqual(btn.text(), "Go")

    def test_none_button_is_safe(self):
        self.window._set_button_busy(None, True)
        self.window._set_button_busy(None, False)

    def test_sign_in_click_animates_then_restores_button(self):
        import codmunity_player_stats
        from unittest import mock
        from PySide6.QtWidgets import QPushButton

        class FakeAuth:
            def __init__(self, *args, **kwargs):
                pass

            @property
            def refresh_token(self):
                return "tok"

            def sign_in(self, email, password):
                return {"email": email}

        w = self.window
        w.async_task = lambda work, done=None, failed=None: done(work())
        w.codmunity_email_edit.setText("player@example.com")
        w.codmunity_password_edit.setText("s3cret-pw")
        btn = QPushButton("Sign in")
        states = []
        with mock.patch.object(codmunity_player_stats, "CodmunityAuthClient", FakeAuth):
            with mock.patch.object(codmunity_player_stats, "build_authorized_fetcher",
                                   lambda auth: None), mock.patch.object(
                codmunity_player_stats, "ModularPlayerStatsService",
                lambda *args, **kwargs: object()):
                real_busy_unbound = app.Window._set_button_busy.__get__(w, app.Window)
                w._set_button_busy = lambda button, busy, base_text=None: (
                    states.append(("busy" if busy else "idle",
                                   isinstance(button, QPushButton))),
                    real_busy_unbound(button, busy, base_text))[1]
                try:
                    btn.clicked.connect(lambda _checked=False: w.codmunity_sign_in())
                    btn.click()
                    self.qt.processEvents()
                finally:
                    del w._set_button_busy
        self.assertEqual([kind for kind, _ in states], ["busy", "idle"])
        self.assertTrue(all(same for _, same in states))
        self.assertTrue(btn.isEnabled())


class LoggedInPillTests(unittest.TestCase):
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

    def test_account_fields_share_one_width(self):
        w = self.window
        widths = {w.codmunity_email_edit.minimumWidth(),
                  w.codmunity_password_edit.minimumWidth(),
                  w.player.minimumWidth()}
        self.assertEqual(widths, {320})

    def test_password_field_masks_typed_text(self):
        from PySide6.QtWidgets import QLineEdit
        w = self.window
        self.assertEqual(w.codmunity_password_edit.echoMode(), QLineEdit.Password)
        w.codmunity_password_edit.setText("s3cret-pw")
        self.assertEqual(w.codmunity_password_edit.text(), "s3cret-pw")

    def test_pill_hidden_when_signed_out(self):
        w = self.window
        w.settings.codmunity_refresh_token = lambda: ""
        w.refresh_codmunity_account_status()
        self.qt.processEvents()
        self.assertTrue(w.codmunity_logged_pill.isHidden())
        self.assertFalse(w.codmunity_blink_timer.isActive())
        self.assertIn("Not signed in", w.codmunity_account_status.text())

    def test_pill_blinks_when_signed_in(self):
        w = self.window
        w.settings.codmunity_refresh_token = lambda: "tok"
        w.refresh_codmunity_account_status()
        self.qt.processEvents()
        self.assertFalse(w.codmunity_logged_pill.isHidden())
        self.assertIn("LOGGED IN", w.codmunity_logged_pill.text())
        self.assertTrue(w.codmunity_blink_timer.isActive())
        first = w.codmunity_logged_pill.styleSheet()
        w.codmunity_blink_timer.timeout.emit()
        self.qt.processEvents()
        second = w.codmunity_logged_pill.styleSheet()
        self.assertNotEqual(first, second)
        self.assertIn("#56e99a", first + second)
        self.assertIn("#1f6b44", first + second)

    def test_profile_panel_hidden_but_background_logic_alive(self):
        w = self.window
        panel = w.codmunity_profile_status.parentWidget()
        for attr in ("codmunity_profile_status", "codmunity_profile_name",
                     "codmunity_profile_sub", "codmunity_profile_stats"):
            self.assertEqual(getattr(w, attr).parentWidget(), panel)
        self.assertTrue(panel.isHidden())
        self.assertTrue(w.profile.isHidden())
        w.codmunity_profile_name.setText("vintendopower")
        self.assertEqual(w.codmunity_profile_name.text(), "vintendopower")
        w._refresh_codmunity_stats_frame()
        self.qt.processEvents()

    def test_sign_out_hides_pill(self):
        w = self.window
        w.settings.codmunity_refresh_token = lambda: "tok"
        w.refresh_codmunity_account_status()
        self.assertFalse(w.codmunity_logged_pill.isHidden())
        w.settings.codmunity_refresh_token = lambda: ""
        w.refresh_codmunity_account_status()
        self.qt.processEvents()
        self.assertTrue(w.codmunity_logged_pill.isHidden())
        self.assertFalse(w.codmunity_blink_timer.isActive())


if __name__ == "__main__":
    unittest.main()
