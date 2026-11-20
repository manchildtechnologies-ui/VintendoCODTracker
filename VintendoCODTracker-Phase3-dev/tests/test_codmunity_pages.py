"""Native Camo/Loadout/Leaderboard pages build and run isolated."""
import os
import unittest


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLabel


import app
from tests.test_codmunity_camo import _cache as _camo_cache_fixture




class CodmunityPagesTests(unittest.TestCase):
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


    def test_camo_tabs_only_mw4_and_warzone(self):
        w = self.window
        w.refresh_camo_tracker()
        self.assertTrue(set(w.camo_game_buttons) <= {"mw4", "warzone-2"})
        self.assertIn(w._camo_game, w.camo_game_buttons)

    def test_camo_warzone_tab_selection_sticks(self):
        w = self.window
        w.refresh_camo_tracker()
        if "warzone-2" not in w.camo_game_buttons:
            self.skipTest("no warzone grinds in local catalogue cache")
        w.camo_game_buttons["warzone-2"].click()
        self.assertEqual(w._camo_game, "warzone-2")
        w.refresh_camo_tracker()
        self.assertEqual(w._camo_game, "warzone-2")
        self.assertTrue(w.camo_game_buttons["warzone-2"].isChecked())

    def test_three_pages_exist_in_nav(self):
        names = [self.window.nav.item(i).data(Qt.UserRole)
                 for i in range(self.window.nav.count())]
        for page in ("Camo Tracker", "Leaderboards", "Stats", "How it works"):
            self.assertIn(page, names)
            self.assertIn(page, self.window.pages)


    def test_detect_queue_undo_flips_state(self):
        from PySide6.QtWidgets import QPushButton, QLabel
        self.window.store.save_camo_detection({"weapon": "M4", "camo": "Gold",
                                               "grind": "mw4", "state": "saved",
                                               "changed": [], "read": []})
        self.window.refresh_detect_queue()
        target = None
        box = self.window.sync_detect_box
        for i in range(box.count()):
            wrap = box.itemAt(i).widget()
            if wrap is None:
                continue
            labels = wrap.findChildren(QLabel)
            if any("M4" in label.text() and "Gold" in label.text() for label in labels):
                undo = [b for b in wrap.findChildren(QPushButton) if b.text() == "Undo"]
                if undo:
                    target = undo[0]
                    break
        self.assertIsNotNone(target)
        target.click()
        import codmunity_camo as C
        progress = C.progress_map(self.window.store, None)
        self.assertEqual(progress[("m4", "gold")]["state"], "dismissed")


    def test_sync_tracking_guards(self):
        self.window.tracking = False
        self.window.sync_stop_tracking()
        self.window.tracking = True
        self.window.sync_start_tracking()
        self.assertTrue(self.window.tracking)


    def test_camo_cards_render_with_art(self):
        from PySide6.QtGui import QPixmap, QColor
        from PySide6.QtWidgets import QLabel
        import codmunity_camo as C
        real = C.load_camo_catalogue
        C.load_camo_catalogue = lambda _dir=None: real(_camo_cache_fixture())
        try:
            self.window.refresh_camo_tracker()
            cards = [self.window.camo_cards_row.itemAt(i).widget()
                     for i in range(self.window.camo_cards_row.count())]
            cards = [w for w in cards if w is not None]
            self.assertEqual(len(cards), 2)
            pix = QPixmap(12, 12)
            pix.fill(QColor("red"))
            self.window._camo_images["http://img/gold.webp"] = pix
            art = self.window._camo_card_art("http://img/gold.webp")
            self.assertFalse(art.pixmap().isNull())
            missing = self.window._camo_card_art("http://img/missing.webp")
            self.assertTrue(missing.pixmap() is None or missing.pixmap().isNull())
        finally:
            C.load_camo_catalogue = real

    def test_camo_page_refreshes_without_cache(self):
        self.window.refresh_camo_tracker()
        self.window.refresh_camo_weapons()
        self.window._camo_apply_filters()
        self.assertTrue(hasattr(self.window, "camo_weapon_list"))


    def _camo_fixture(self):
        return {
            "grinds": [{"name": "mw4", "title": "Weapon Camos",
                        "game": "mw4", "grindKind": "classic",
                        "camoConfig": {"camos": [{"name": "Gold", "order": 2},
                                                {"name": "Platinum", "order": 3}]}},
                       {"name": "mw4-mastery", "title": "Mastery",
                        "game": "mw4", "grindKind": "account-mastery",
                        "camoConfig": {"camos": [{"name": "Polyatomic", "order": 1}]}}],
            "weapons": [{"WeaponName": "M4", "Category": "Assault Rifle",
                         "Unlock": "Level 4", "game": "mw4",
                         "popularityByGame": {"mw4": 5.0}}],
            "challenges": [
                {"weapon": "M4", "camo": "Military Camo 1",
                 "challenge": "Reach Weapon Level 9", "challenge_wz": "",
                 "grind": "mw4", "subcategory": "Military", "Order": 1,
                 "Image": ""},
                {"weapon": "M4", "camo": "Gold", "challenge": "Get 100 kills",
                 "challenge_wz": "", "grind": "mw4", "subcategory": "Completionist",
                 "Order": 10, "Image": ""},
                {"weapon": "M4", "camo": "Platinum", "challenge": "Get 200 kills",
                 "challenge_wz": "", "grind": "mw4", "subcategory": "Completionist",
                 "Order": 11, "Image": ""}],
        }


    def test_camo_game_tabs_and_expander_and_marks(self):
        import codmunity_camo as C
        real = C.load_camo_catalogue
        C.load_camo_catalogue = lambda _c=None: self._camo_fixture()
        try:
            self.window.refresh_camo_tracker()
            self.assertIn("mw4", self.window.camo_game_buttons)
            self.assertEqual(self.window.camo_title_label.text(), "Weapon Camos")
            self.assertEqual(self.window.camo_weapon_list.count(), 1)
            self.assertIn("1 weapons", self.window.camo_subtitle_label.text())
            # Expansion shows per-challenge rows with a working Mark button.
            card = self.window.camo_weapon_list.itemWidget(self.window.camo_weapon_list.item(0))
            expanders = [b for b in card.findChildren(object) if b.objectName() == "camoExpander"]
            self.assertTrue(expanders)
            expanders[0].click()
            self.assertIn("M4", self.window._camo_expanded)
            # Mark the first incomplete camo saved; the row count moves to 1/3.
            self.window.mark_camo_challenge("M4", "Military Camo 1", "saved")
            self.assertTrue(self.window.store.camo_detections())
            card = self.window.camo_weapon_list.itemWidget(self.window.camo_weapon_list.item(0))
            rings = [label.text() for label in card.findChildren(QLabel) if label.objectName() == "camoRing"]
            self.assertEqual(rings, ["1/3"])
        finally:
            C.load_camo_catalogue = real


    def test_stats_page_shows_lifetime_and_summary(self):
        snapshot = {"personal": {"multiplayer": {"stats": {
            "kills": 10051, "deaths": 9083, "games_played": 478,
            "wins": 187, "losses": 278,
            "avg_kills_game": 21.0, "highest_kills_game": 83,
            "highest_streak": 19, "score_per_min": 507.0,
            "time_played_s": 121560,
            "wins_by_mode": {"hardpoint": 12}}}},
            "benchmarks": {"mw4_multiplayer": {"kdMedian": 3.42, "kdP90": 7.32}}}
        self.window.store.set_meta("codmunity_stats", snapshot)
        self.window.refresh_stats()
        cards = self.window.stats_cards
        self.assertAlmostEqual(float(cards["kd"].text()), 10051 / 9083, places=2)
        self.assertEqual(cards["wins"].text(), "187")
        self.assertEqual(cards["games"].text(), "478")
        self.assertEqual(cards["best"].text(), "83")
        self.assertIn("hardpoint 12", self.window.stats_modes_label.text())
        self.assertIn("median K/D 3.42", self.window.stats_benchmark_label.text())
        from PySide6.QtWidgets import QApplication
        self.window.copy_stats_summary()
        self.assertIn("MW4 Multiplayer:", QApplication.clipboard().text())
        # Warzone without cached lifetime shows dashes, no crash.
        self.window.dash_profile_game_buttons[("warzone", "resurgence")].click()
        self.assertEqual(cards["kd"].text(), "—")
        self.assertIn("No lifetime stats", self.window.stats_status.text())


    def test_share_buttons_and_clear_cache(self):
        import app as appmod
        opened = []
        real = appmod.QDesktopServices
        class FakeDesktop:
            @staticmethod
            def openUrl(url):
                opened.append(url.toString() if hasattr(url, "toString") else str(url))
                return True
        appmod.QDesktopServices = FakeDesktop
        try:
            self.window.share_to("X")
            self.assertTrue(any("x.com/intent/post" in u for u in opened))
            self.window.share_to("WhatsApp")
            self.assertTrue(any("wa.me" in u for u in opened))
        finally:
            appmod.QDesktopServices = real
        import tempfile
        from pathlib import Path
        for name in ("camo-images", "avatars"):
            folder = Path(appmod.E.DATA) / name
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "probe.img").write_bytes(b"0" * 10)
        self.window.refresh_cache_size_label()
        self.assertIn("KB", self.window.cache_size_label.text())
        self.window.clear_image_caches()
        self.assertIn("0 B", self.window.cache_size_label.text())











    def test_grid_view_lists_same_weapons(self):
        from PySide6.QtWidgets import QListView
        self.window.camo_view_buttons["grid"].click()
        self.assertEqual(self.window.camo_weapon_list.viewMode(), QListView.IconMode)
        self.assertGreater(self.window.camo_weapon_list.count(), 0)
        self.window.camo_view_buttons["list"].click()
        self.assertEqual(self.window.camo_weapon_list.viewMode(), QListView.ListMode)


    def test_how_it_works_page_exists(self):
        self.assertIn("How it works", self.window.pages)


    def test_sync_page_shows_autotrack_status_and_verify(self):
        self.window.refresh_codmunity_sync()
        self.assertIn("Auto tracking:", self.window.sync_state_label.text())
        self.assertFalse(self.window.sync_verify_button.isEnabled())
        self.window.notice = lambda *args, **kwargs: setattr(self.window, "_noticed", str(args[0]) if args else "")
        self.window.send_verification_email()
        self.assertTrue(hasattr(self.window, "_noticed"))


    def test_password_reset_needs_email_then_sends(self):
        import codmunity_player_stats as C
        real = C.request_password_reset
        calls = []
        C.request_password_reset = lambda email: calls.append(email) or None
        try:
            self.window.codmunity_email_edit.setText("")
            self.window.codmunity_reset_password()
            self.assertIn("Enter your", self.window.codmunity_account_status.text())
            self.window.codmunity_email_edit.setText("me@example.com")
            self.window.codmunity_reset_password()
            end = __import__("time").time() + 10
            while __import__("time").time() < end and not calls:
                __import__("time").sleep(0.05)
                self.qt.processEvents()
            self.assertEqual(calls, ["me@example.com"])
        finally:
            C.request_password_reset = real


    def test_leaderboards_refresh_with_empty_snapshot(self):
        # The isolated store is shared across runs: clear first.
        self.window.store.set_meta("codmunity_stats", {})
        self.window.refresh_leaderboards()
        self.window.refresh_board_table()
        self.assertTrue(hasattr(self.window, "board_table"))
        self.assertEqual(self.window.board_title_label.text(), "♛ MW4 · Multiplayer")
        self.assertIn("No cached entries", self.window.board_status.text())
        self.assertIn("No stats yet", self.window.board_you_sub.text())


    def test_leaderboards_renders_rows_medals_and_metrics(self):
        from tests.test_codmunity_camo import _board_snapshot
        self.window.store.set_meta("codmunity_stats", _board_snapshot())
        self.window.refresh_leaderboards()
        self.assertEqual(self.window.board_table.rowCount(), 3)
        from PySide6.QtWidgets import QLabel
        first_player = self.window.board_table.cellWidget(0, 1)
        names = [label.text() for label in first_player.findChildren(QLabel)
                 if label.objectName() == "boardPlayerName"]
        self.assertEqual(names, ["Aaa"])
        self.assertEqual(self.window.board_table.item(0, 2).text(), "4.91")
        gold = self.window._rank_badge(1)
        self.assertEqual(gold.objectName(), "boardMedalGold")
        self.assertIn("♛", gold.text())
        self.assertEqual(self.window._rank_badge(4).objectName(), "boardMedalPlain")
        # Wins metric re-sorts: bbb (497) jumps to the top.
        self.window.board_metric_buttons["wins"].click()
        self.assertEqual(self.window.board_table.item(0, 3).text(), "497")
        # YOU row shows local lifetime numbers for MW4.
        self.assertEqual(self.window.board_you_wins.text(), "187")
        self.assertEqual(self.window.board_you_games.text(), "478")
        # Warzone tab swaps the board and shows the mode picker.
        self.window.board_warzone_button.click()
        self.assertEqual(self.window.board_title_label.text(), "♛ Warzone · Resurgence")
        self.assertFalse(self.window.board_mode_combo.isHidden())
        self.assertEqual(self.window.board_table.rowCount(), 1)
        # No Warzone lifetime in the fixture: YOU row correctly shows dashes.
        self.assertEqual(self.window.board_you_wins.text(), "—")
        # Toggle is disabled without sign-in and reverts with a message.
        self.assertFalse(self.window.board_visibility_toggle.isEnabled())
        self.window.board_visibility_toggle.setChecked(False)
        self.assertTrue(self.window.board_visibility_toggle.isChecked())
        self.assertIn("Sign in", self.window.board_status.text())




if __name__ == "__main__":
    unittest.main()
