"""Player Roster CODMUNITY tab removal: no separate tab, single refresh in
Player Info, auto-resolve on username edit. Headless-safe: no network calls
are made when no roster player is selected (every action self-guards).
"""
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import app


class RosterCodmunityTabTests(unittest.TestCase):
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

    def _tab_labels(self):
        tabs = self.window.roster_tabs
        return [tabs.tabText(i) for i in range(tabs.count())]

    def test_no_codmunity_tab_but_core_tabs_remain(self):
        labels = self._tab_labels()
        self.assertNotIn("CODMUNITY", labels)
        for expected in ("PLAYER INFO", "HEADSHOT", "PROFILE"):
            self.assertIn(expected, labels)

    def test_single_refresh_button_lives_in_player_info(self):
        w = self.window
        self.assertEqual(w.codmunity_refresh_single.text(), "Refresh CODMunity data")
        w.codmunity_refresh_single.click()
        self.qt.processEvents()
        self.assertIn("Save this roster entry", w.codmunity_status.text())

    def test_username_edit_finished_without_player_is_silent(self):
        w = self.window
        w.async_task = lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("no background work without a selected player"))
        w.codmunity_username.setText("someplayer")
        w.codmunity_username.editingFinished.emit()
        self.qt.processEvents()


if __name__ == "__main__":
    unittest.main()
