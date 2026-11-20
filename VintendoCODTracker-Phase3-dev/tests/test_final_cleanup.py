"""Final UI cleanup: poster defaults, share removal, button rename, roster field, rank column."""
import json
import os
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QPushButton

import engine as E
import app


class PosterDefaultsTests(unittest.TestCase):
    def test_fresh_settings_default_all_on(self):
        s = E.Settings(tempfile.mkdtemp())
        self.assertTrue(s.values["auto_post"])
        self.assertTrue(s.values["full_auto"])
        self.assertTrue(s.values["include_stats"])
        self.assertTrue(s.values["warzone_enabled"])

    def test_saved_off_stays_off(self):
        folder = tempfile.mkdtemp()
        s = E.Settings(folder)
        s.values.update(auto_post=False, full_auto=False,
                        include_stats=False, squad_auto_post_version=1)
        s.save()
        reloaded = E.Settings(folder)
        self.assertFalse(reloaded.values["auto_post"])
        self.assertFalse(reloaded.values["full_auto"])
        self.assertFalse(reloaded.values["include_stats"])

    def test_ui_reflects_saved_values(self):
        qt = QApplication.instance() or QApplication([])
        window = app.Window()
        window.notice = lambda *args, **kwargs: None
        window.ask = lambda *args, **kwargs: False
        try:
            self.assertEqual(window.include_stats_checkbox.isChecked(),
                             bool(window.settings.values.get("include_stats", True)))
            self.assertEqual(window.auto_post.isChecked(),
                             bool(window.settings.values.get("auto_post", True)))
            self.assertEqual(window.full_auto.isChecked(),
                             bool(window.settings.values.get("full_auto", True)))
        finally:
            window.shutdown()
            window.deleteLater()
            qt.processEvents()


class FreshInstallDefaultsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def test_fresh_window_checks_all_three(self):
        from pathlib import Path
        data_dir = Path(os.environ.get("VINTENDO_DATA_DIR", ""))
        path = data_dir / "settings.json"
        backup = path.read_bytes() if path.is_file() else None
        if path.is_file():
            path.unlink()
        window = app.Window()
        window.notice = lambda *args, **kwargs: None
        window.ask = lambda *args, **kwargs: False
        try:
            self.assertTrue(window.include_stats_checkbox.isChecked())
            self.assertTrue(window.auto_post.isChecked())
            self.assertTrue(window.full_auto.isChecked())
        finally:
            window.shutdown()
            window.deleteLater()
            self.qt.processEvents()
            if backup is not None:
                path.write_bytes(backup)
            elif path.is_file():
                path.unlink()


class CleanupLayoutTests(unittest.TestCase):
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

    def test_share_section_removed(self):
        labels = [b.text() for b in self.window.findChildren(QPushButton)]
        self.assertFalse(any(t.startswith("Share to") for t in labels))

    def test_capture_button_renamed(self):
        self.assertEqual(self.window.capture_setup_button.text(), "Pick Game Monitor")

    def test_monitor_button_shows_picked_display(self):
        w = self.window
        w._paint_monitor_button({"label": "Display 0 · 2560x1440 · primary"})
        self.assertIn("Display 0", w.capture_setup_button.text())
        self.assertEqual(w.capture_setup_button.objectName(), "monitorReady")
        w._paint_monitor_button(None)
        self.assertEqual(w.capture_setup_button.text(), "Pick Game Monitor")
        self.assertEqual(w.capture_setup_button.objectName(), "")

    def test_roster_profile_link_hidden_but_kept(self):
        self.assertTrue(self.window.roster_profile_url.isHidden())
        self.window.roster_profile_url.setText("https://codmunity.gg/profile/someone")
        self.assertEqual(self.window.roster_profile_url.text(),
                         "https://codmunity.gg/profile/someone")

    def test_rank_column_wide_and_centered(self):
        self.assertGreaterEqual(self.window.board_table.columnWidth(0), 64)
        self.assertEqual(self.window.board_table.horizontalHeaderItem(0).textAlignment(),
                         Qt.AlignCenter)

    def test_settings_actions_sit_in_tab_corner(self):
        w = self.window
        corner = w.settings_tabs.cornerWidget()
        self.assertIsNotNone(corner)
        order = [corner.layout().itemAt(i).widget() for i in range(corner.layout().count())]
        self.assertEqual(order, [w.save_settings_button, w.check_updates_button])


if __name__ == "__main__":
    unittest.main()
