"""First-time Meta AI login explainer. Headless-safe: the popup and the
login window are faked; no browser is launched."""
import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import app


class _FakeBox:
    AcceptRole = 0
    RejectRole = 1
    clicked = "go"
    last = None

    def __init__(self, *args, **kwargs):
        type(self).last = self
        self.text = ""

    def setWindowTitle(self, title):
        self.title = title

    def setText(self, text):
        self.text = text

    def addButton(self, label, _role):
        button = object()
        if label == "Continue to login":
            self._go = button
        return button

    def setDefaultButton(self, _button):
        pass

    def exec(self):
        return 0

    def clickedButton(self):
        return self._go if type(self).clicked == "go" else object()


class MetaLoginPromptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.qt = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = app.Window()
        self._saved = dict(self.window.settings.values)
        self.window.settings.values.pop("meta_login_prompt_seen", None)
        self.window._inapp_active = True

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

    def test_first_click_explains_one_time_login(self):
        w = self.window
        _FakeBox.clicked = "go"
        with mock.patch.object(app, "QMessageBox", _FakeBox):
            w.open_login_window()
            self.qt.processEvents()
        self.assertIn("only need to log into Meta AI once", _FakeBox.last.text)
        self.assertIn("AI pictures and videos", _FakeBox.last.text)
        self.assertIn("No user info is saved or stored", _FakeBox.last.text)
        self.assertTrue(w.settings.values.get("meta_login_prompt_seen"))

    def test_cancel_stays_unseen_and_aborts(self):
        w = self.window
        _FakeBox.clicked = "cancel"
        with mock.patch.object(app, "QMessageBox", _FakeBox):
            w.open_login_window()
            self.qt.processEvents()
        self.assertFalse(w.settings.values.get("meta_login_prompt_seen"))

    def test_seen_login_skips_popup(self):
        w = self.window
        w.settings.values["meta_login_prompt_seen"] = True

        class _BoomBox:
            def __init__(self, *args, **kwargs):
                raise AssertionError("popup must not reappear")

        with mock.patch.object(app, "QMessageBox", _BoomBox):
            w.open_login_window()
            self.qt.processEvents()


if __name__ == "__main__":
    unittest.main()
