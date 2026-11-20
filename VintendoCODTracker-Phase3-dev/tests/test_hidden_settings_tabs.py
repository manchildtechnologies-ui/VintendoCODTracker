"""Poster Automation and Automation Chrome tabs are hidden but their
switches and values stay live. Headless-safe: no automation jobs run;
only widget state is asserted."""
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import app


class HiddenSettingsTabsTests(unittest.TestCase):
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

    def _assert_hidden(self, title):
        w = self.window
        index = self._tab_index(title)
        try:
            visible = w.settings_tabs.isTabVisible(index)
        except (AttributeError, RuntimeError):
            visible = w.settings_tabs.tabBar().isTabVisible(index)
        self.assertFalse(visible, title)

    def test_hidden_tabs_stay_out_of_tab_bar(self):
        self._assert_hidden("Poster Automation")
        self._assert_hidden("Automation & Chrome")

    def test_all_poster_switches_stay_checked(self):
        w = self.window
        self.assertTrue(w.include_stats_checkbox.isChecked())
        self.assertTrue(w.auto_post.isChecked())
        self.assertTrue(w.full_auto.isChecked())
        self.assertTrue(w.settings.values.get("include_stats"))
        self.assertTrue(w.settings.values.get("auto_post"))
        self.assertTrue(w.settings.values.get("full_auto"))

    def test_automation_chrome_values_still_live(self):
        w = self.window
        self.assertGreaterEqual(w.poll.value(), 3)
        self.assertGreaterEqual(w.browser_timeout.value(), 10)
        self.assertGreaterEqual(w.browser_retries.value(), 0)
        self.assertIn(w.browser_mode_combo.currentIndex(), (0, 1))

    def test_codmunity_name_moved_to_account_box(self):
        from PySide6.QtWidgets import QFrame
        w = self.window
        self.assertFalse(w.player.isHidden())
        box = w.findChild(QFrame, "configBox")
        self.assertIsNotNone(box)
        self.assertTrue(box.isHidden())

    def test_stats_folder_still_settable_but_hidden(self):
        from pathlib import Path
        import tempfile
        w = self.window
        stats = tempfile.mkdtemp()
        (Path(stats) / "matches.json").write_text("{}")
        w.stats_dir.setText(stats)
        self.assertEqual(w.stats_dir.text(), stats)
        self.assertEqual(w._effective_stats_dir(), stats)

    def test_stats_frame_gets_layout_share(self):
        w = self.window
        layout = w.pages["Settings"][1]
        self.assertEqual(layout.stretch(layout.indexOf(w.settings_tabs)), 0)
        self.assertEqual(layout.stretch(layout.indexOf(w.codmunity_stats_frame)), 1)

    def test_logs_tab_index_still_valid(self):
        w = self.window
        w.settings_tabs.setCurrentIndex(w.logs_tab_index)
        self.qt.processEvents()
        self.assertEqual(w.settings_tabs.tabText(w.settings_tabs.currentIndex()), "Logs")


if __name__ == "__main__":
    unittest.main()
