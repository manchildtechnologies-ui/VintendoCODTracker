"""Capture-hotkey pill removal and first-run hotkey prompt.

Headless-safe: QMessageBox and the hotkey dialog are faked; no global
hotkeys are registered or touched.
"""
import os
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import app


class _FakeBox:
    AcceptRole = 0
    RejectRole = 1
    clicked = "set"
    last = None

    def __init__(self, *args, **kwargs):
        type(self).last = self
        self.text = ""
        self._set = object()
        self._keep = object()

    def setWindowTitle(self, title):
        self.title = title

    def setText(self, text):
        self.text = text

    def addButton(self, label, _role):
        return self._set if label == "Set hotkey" else self._keep

    def setDefaultButton(self, _button):
        pass

    def exec(self):
        return 0

    def clickedButton(self):
        return self._set if type(self).clicked == "set" else self._keep


class HotkeyPromptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.qt = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = app.Window()
        self._saved = dict(self.window.settings.values)
        self._media = tempfile.mkdtemp()
        self.window.settings.values["screenshots_dir"] = self._media
        self.window.settings.values["videos_dir"] = self._media
        self.window.settings.values.pop("capture_hotkey_prompt_seen", None)

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

    def test_hotkey_pill_not_shown_in_sidebar(self):
        w = self.window
        self.assertTrue(w.hotkey_status.isHidden())
        try:
            index = w.sidebar.layout().indexOf(w.hotkey_status)
        except (AttributeError, RuntimeError):
            index = -1
        self.assertEqual(index, -1)

    def test_first_run_prompt_states_default_and_opens_dialog(self):
        w = self.window
        _FakeBox.clicked = "set"
        with mock.patch.object(app, "QMessageBox", _FakeBox):
            with mock.patch.object(app.Window, "open_hotkey_dialog") as hotkey_dialog:
                w.run_first_boot_setup()
                self.qt.processEvents()
        from app import DEFAULT_SCOREBOARD_HOTKEY, format_hotkey
        self.assertIn(format_hotkey(*DEFAULT_SCOREBOARD_HOTKEY), _FakeBox.last.text)
        self.assertTrue(w.settings.values.get("capture_hotkey_prompt_seen"))
        hotkey_dialog.assert_called_once_with("scoreboard")

    def test_keep_default_skips_hotkey_dialog(self):
        w = self.window
        _FakeBox.clicked = "keep"
        with mock.patch.object(app, "QMessageBox", _FakeBox):
            with mock.patch.object(app.Window, "open_hotkey_dialog") as hotkey_dialog:
                w.run_first_boot_setup()
                self.qt.processEvents()
        self.assertTrue(w.settings.values.get("capture_hotkey_prompt_seen"))
        hotkey_dialog.assert_not_called()

    def test_seen_prompt_never_reappears(self):
        w = self.window
        w.settings.values["capture_hotkey_prompt_seen"] = True

        class _BoomBox:
            def __init__(self, *args, **kwargs):
                raise AssertionError("prompt must not reappear")

        with mock.patch.object(app, "QMessageBox", _BoomBox):
            w.run_first_boot_setup()
            self.qt.processEvents()


if __name__ == "__main__":
    unittest.main()
