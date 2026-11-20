"""Startup requirements: missing/expired logins surface a fix-it-here popup.

Headless-safe: never execs a dialog, never touches the network. Session
validation is injected; the Meta AI section only reuses existing buttons.
"""
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialog, QLineEdit

import app


class StartupRequirementsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
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

    def _clear_codmunity_token(self):
        self.window.settings.values["protected_codmunity_refresh"] = ""
        self.window.settings.values["meta_login_needed"] = False

    def test_missing_codmunity_token_requires_sign_in(self):
        self._clear_codmunity_token()
        issues = self.window.startup_offline_requirements()
        self.assertEqual([i["id"] for i in issues], ["codmunity"])
        self.assertEqual(issues[0]["mode"], "sign_in")

    def test_valid_session_has_no_issue(self):
        self.window.settings.set_codmunity_refresh_token("startup-probe-token")
        self.window.settings.values["meta_login_needed"] = False
        offline = self.window.startup_offline_requirements()
        self.assertEqual(offline, [])
        self.assertIsNone(self.window.startup_session_issue(validate=lambda _client: "ok"))

    def test_expired_session_requests_relogin(self):
        self.window.settings.set_codmunity_refresh_token("startup-probe-token")

        def expired(_client):
            raise RuntimeError("Session refresh failed: expired")

        issue = self.window.startup_session_issue(validate=expired)
        self.assertIsNotNone(issue)
        self.assertEqual(issue["mode"], "relogin")
        self.assertIn("Sign in again", issue["detail"])

    def test_meta_flag_requests_browser_login(self):
        self.window.settings.set_codmunity_refresh_token("startup-probe-token")
        self.window.settings.values["meta_login_needed"] = True
        issues = self.window.startup_offline_requirements()
        self.assertEqual([i["id"] for i in issues], ["meta_ai"])
        self.assertEqual(issues[0]["mode"], "browser_login")

    def test_dialog_builds_codmunity_form_without_exec(self):
        self._clear_codmunity_token()
        issues = self.window.startup_offline_requirements()
        dialog = self.window.build_startup_requirements_dialog(issues, select="codmunity")
        try:
            self.assertIsInstance(dialog, QDialog)
            self.assertEqual(dialog.windowTitle(), "Startup requirements")
            edits = dialog.findChildren(QLineEdit)
            self.assertEqual(len(edits), 2)
            password = [e for e in edits if e.echoMode() == QLineEdit.Password]
            self.assertEqual(len(password), 1)
        finally:
            dialog.close()
            dialog.deleteLater()
            self.qt.processEvents()

    def test_no_popup_under_pytest(self):
        self.assertFalse(self.window.maybe_show_startup_requirements())


if __name__ == "__main__":
    unittest.main()
