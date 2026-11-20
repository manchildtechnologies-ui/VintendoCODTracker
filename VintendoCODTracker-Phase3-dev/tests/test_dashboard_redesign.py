"""Native dashboard redesign: header, toolbar, lifetime cards, compact match center, summary."""
import os
import unittest
import uuid

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLabel, QWidget

import app


def _seed_stats(window):
    window.store.set_meta("codmunity_stats", {"personal": {"multiplayer": {"stats": {
        "wins": 7, "games_played": 20, "kills": 200, "deaths": 100,
        "avg_kills_game": 10.0, "highest_kills_game": 30,
        "highest_streak": 12, "score_per_min": 400, "time_played_s": 3600}}}})


def _seed_win(window):
    mid, _ = window.store.add(
        {"matchId": "dash-" + uuid.uuid4().hex, "game": "warzone",
         "mode": "battle_royale", "playedAt": "2026-10-01T08:00:00Z",
         "result": "win",
         "stats": {"kills": 17, "eliminations": 19, "damage": 2500,
                   "deaths": 4, "assists": 3}},
        "needs_review")
    return mid


class DashboardRedesignTests(unittest.TestCase):
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

    def test_header_toolbar_present(self):
        w = self.window
        self.assertTrue(w.status.text().endswith("STANDBY"))
        for button in (w.start_button, w.stop_button):
            self.assertTrue(button.text())
            self.assertEqual(button.minimumWidth(), 150)
        self.assertFalse(hasattr(w, "pause_button"))
        self.assertFalse(hasattr(w, "brand_ver"))
        self.assertIn(w.mode.currentText(), ("Solo Ops", "Squad Night"))
        self.assertTrue(w.mode.isHidden())
        self.assertEqual(w.dashboard_health, {})
        self.assertFalse(w.dash_gear.icon().isNull())
        logo_px = w.dash_logo.pixmap()
        self.assertIsNotNone(logo_px)
        self.assertFalse(logo_px.isNull())
        self.assertEqual(w.dash_logo.pixmap().height(), 100)
        titles = [c.text() for c in w.pages["Dashboard"][0].findChildren(QLabel)]
        self.assertNotIn("COD TRACKER", titles)
        self.assertNotIn("Live tracking, lifetime stats and the latest match in one command center.", titles)

    def test_hotkey_pill_leads_corner_bar(self):
        w = self.window
        bar = w.corner_controls.layout()
        self.assertIs(bar.itemAt(0).widget(), w.hotkey_status)
        self.assertIs(bar.itemAt(1).widget(), w.status)
        self.assertTrue(w.hotkey_status.toolTip() != "")

    def test_start_stop_sit_left_of_refresh(self):
        w = self.window
        order = []
        layout = w.dash_stat_head
        for i in range(layout.count()):
            widget = layout.itemAt(i).widget()
            if widget is not None:
                order.append(widget)
        self.assertLess(order.index(w.start_button), order.index(w.stop_button))
        refresh = next(b for b in order if getattr(b, "text", lambda: "")() == "Refresh")
        copy = next(b for b in order if getattr(b, "text", lambda: "")() == "Copy summary")
        self.assertLess(order.index(w.stop_button), order.index(refresh))
        self.assertLess(order.index(refresh), order.index(copy))

    def test_profile_banner_sits_above_match_center(self):
        w = self.window
        left = w.dash_bottom.itemAt(0).widget()
        right = w.dash_bottom.itemAt(1).widget()
        self.assertIs(right, w.dash_right)
        self.assertIn(w.board_table, left.findChildren(QWidget))
        self.assertNotIn(w.board_table, right.findChildren(QWidget))
        self.assertIs(right.layout().itemAt(0).widget(), w.dash_profile_card)
        self.assertNotIn(w.latest_card, right.findChildren(QWidget))
        self.assertIn(w.latest_card, w._match_holder.findChildren(QWidget))
        self.assertTrue(w._match_holder.isHidden())
        self.assertEqual(set(w.dash_meters), {"kd", "wl", "wr"})
        self.assertEqual(w.dash_meters["kd"]["value"].text(), "—")
        self.assertEqual(len(w.dash_profile_game_buttons), 3)
        self.assertTrue(w.dash_profile_game_buttons[("mw4", "multiplayer")].isChecked())

    def test_meter_cards_show_verified_comparison(self):
        import tempfile as _tf
        from pathlib import Path as _Path
        w = self.window
        w.store = app.E.Store(str(_Path(_tf.mkdtemp()) / "history.sqlite3"))
        w.store.set_meta("codmunity_stats", {"personal": {"multiplayer": {"stats": {
            "wins": 187, "losses": 278, "games_played": 478,
            "kills": 10051, "deaths": 9083}}}})
        dist = [0.5, 0.7, 0.9, 1.1, 1.3, 1.5, 1.8, 2.2, 2.8, 3.5, 4.5, 6.0]
        w.store.set_meta("codmunity_public_distributions", {"mw4|multiplayer|lifetime": {
            "game": "mw4", "mode": "multiplayer", "scope": "lifetime",
            "sampled": 100, "users_total": 500,
            "kd": dist,
            "wl": [round(0.2 + i * 0.2, 2) for i in range(12)],
            "winRate": [round(0.05 + i * 0.05, 2) for i in range(12)]}})
        w.refresh_stats()
        kd = w.dash_meters["kd"]
        self.assertEqual(kd["value"].text(), "1.11")
        self.assertIn("10,051 kills", kd["sub"].text())
        self.assertIn("Better than ", kd["cap"].text())
        self.assertIn("Median", kd["cap"].text())
        self.assertGreater(kd["bar"].pct, 0)
        wl = w.dash_meters["wl"]
        self.assertEqual(wl["value"].text(), "0.67")
        self.assertIn("187 wins", wl["sub"].text())
        wr = w.dash_meters["wr"]
        self.assertEqual(wr["value"].text(), "39%")
        self.assertIn("478 games played", wr["sub"].text())
        self.assertEqual(wr["ring"].text, "39%")
        self.assertGreater(wr["ring"].pct, 0)

    def test_profile_tiles_page_and_pager(self):
        import tempfile as _tf
        from pathlib import Path as _Path
        w = self.window
        w.store = app.E.Store(str(_Path(_tf.mkdtemp()) / "history.sqlite3"))
        self.assertEqual(w.dash_profile_pages.count(), 2)
        self.assertEqual(w.dash_profile_pages.currentIndex(), 0)
        self.assertEqual(set(w.dash_profile_tiles),
                         {"kills", "deaths", "wins", "losses", "games",
                          "time", "streak", "best", "spm", "avg"})
        w.store.set_meta("codmunity_stats", {"personal": {"multiplayer": {"stats": {
            "wins": 187, "losses": 278, "games_played": 478,
            "kills": 10051, "deaths": 9083, "time_played_s": 121560,
            "highest_kills_game": 83, "highest_streak": 19,
            "score_per_min": 507.0, "avg_kills_game": 21.0}}}})
        dist = [0.5, 0.7, 0.9, 1.1, 1.3, 1.5, 1.8, 2.2, 2.8, 3.5, 4.5, 6.0]
        w.store.set_meta("codmunity_public_distributions", {"mw4|multiplayer|lifetime": {
            "game": "mw4", "mode": "multiplayer", "scope": "lifetime",
            "sampled": 100, "users_total": 500,
            "kd": dist,
            "kills": [1207.0, 2017.0, 3148.0, 4238.0, 5400.0, 6800.0, 8500.0, 10500.0, 13000.0, 16000.0, 20000.0, 25000.0],
            "timePlayedH": [0.0, 2.0, 5.0, 9.0, 14.0, 20.0, 28.0, 38.0, 52.0, 70.0, 95.0, 130.0],
            "spm": [250.0, 313.0, 374.6, 409.7, 445.0, 480.0, 520.0, 560.0, 610.0, 670.0, 740.0, 820.0],
            "killsPerGame": [13.06, 15.01, 16.97, 18.18, 19.5, 21.0, 22.6, 24.3, 26.2, 28.5, 31.0, 34.0]}})
        w.refresh_stats()
        tiles = w.dash_profile_tiles
        self.assertEqual(tiles["kills"]["value"].text(), "10,051")
        self.assertIn("TOP", tiles["kills"]["badge"].text())
        self.assertFalse(tiles["kills"]["badge"].isHidden())
        self.assertEqual(tiles["deaths"]["value"].text(), "9,083")
        self.assertTrue(tiles["deaths"]["badge"].isHidden())
        self.assertEqual(tiles["wins"]["value"].text(), "187")
        self.assertEqual(tiles["losses"]["value"].text(), "278")
        self.assertEqual(tiles["games"]["value"].text(), "478")
        self.assertEqual(tiles["time"]["value"].text(), "1d 9h")
        self.assertIn("TOP", tiles["time"]["badge"].text())
        self.assertEqual(tiles["streak"]["value"].text(), "19")
        self.assertEqual(tiles["best"]["value"].text(), "83")
        self.assertEqual(tiles["spm"]["value"].text(), "507")
        self.assertIn("TOP", tiles["spm"]["badge"].text())
        self.assertEqual(tiles["avg"]["value"].text(), "21.0")
        self.assertIn("TOP", tiles["avg"]["badge"].text())
        w.dash_profile_next.click()
        self.assertEqual(w.dash_profile_pages.currentIndex(), 1)
        w.dash_profile_prev.click()
        self.assertEqual(w.dash_profile_pages.currentIndex(), 0)

    def test_distributions_cache_extra_percentiles(self):
        import tempfile as _tf
        from pathlib import Path as _Path
        import codmunity_player_stats as cps
        w = self.window
        w.store = app.E.Store(str(_Path(_tf.mkdtemp()) / "history.sqlite3"))
        w._codmunity_player_record = lambda pid=None: {"id": "p1"}
        dist = cps.CommunityDistribution(
            game="mw4", mode="multiplayer", scope="lifetime",
            kd=tuple(float(v) for v in [0.5, 0.7, 0.9, 1.1, 1.3, 1.5, 1.8, 2.2, 2.8, 3.5, 4.5, 6.0]),
            sampled=100, users_total=500,
            extra=(("kills", tuple(float(v) for v in [1207, 2017, 3148, 4238, 5400, 6800, 8500, 10500, 13000, 16000, 20000, 25000])),))
        w.on_codmunity_distribution("p1", "mw4|multiplayer|lifetime", dist)
        cached = w.store.meta("codmunity_public_distributions") or {}
        self.assertEqual(cached["mw4|multiplayer|lifetime"]["kills"][:3], [1207.0, 2017.0, 3148.0])

    def test_leaderboard_lives_on_dashboard(self):
        w = self.window
        rows = [r for r in range(w.nav.count())
                if w.nav.item(r).data(Qt.UserRole) == "Leaderboards"]
        self.assertEqual(len(rows), 1)
        self.assertTrue(w.nav.item(rows[0]).isHidden())
        self.assertIn("Leaderboards", w.pages)
        left = w.dash_bottom.itemAt(0).widget()
        self.assertIn(w.board_table, left.findChildren(QWidget))
        self.assertTrue(w._metrics_holder.isHidden())

    def test_rank_ladder_reflects_lifetime(self):
        import tempfile as _tf
        from pathlib import Path as _Path
        w = self.window
        w.store = app.E.Store(str(_Path(_tf.mkdtemp()) / "history.sqlite3"))
        self.assertEqual(w.dash_rank_bar.tier_index, -1)
        _seed_stats(w)
        w.refresh_stats()
        self.assertIn("Refresh community data", w.dash_rank_next.text())
        self.assertEqual(w.dash_rank_current.text(), "—")
        dist = {"game": "mw4", "mode": "multiplayer", "scope": "lifetime",
                "sampled": 100, "users_total": 500,
                "kd": [0.4, 0.6, 0.8, 1.0, 1.2, 1.5, 1.8, 2.2, 2.8, 3.5, 4.5, 6.0]}
        w.store.set_meta("codmunity_public_distributions", {"mw4|multiplayer|lifetime": dist})
        w.refresh_stats()
        self.assertGreaterEqual(w.dash_rank_bar.tier_index, 0)
        self.assertLess(w.dash_rank_bar.tier_index, 7)
        self.assertTrue(w.dash_rank_next.text().startswith("Next:")
                        or w.dash_rank_next.text() == "MAX RANK")
        self.assertEqual(w.stats_cards["kd"].text(), "2.00")
        import codmunity_player_stats as cps
        self.assertEqual(w.dash_rank_current.text(),
                        cps.LADDER_TIERS[w.dash_rank_bar.tier_index][1])
        w.dash_rank_bar.grab()

    def test_game_selector_drives_cards(self):
        w = self.window
        self.assertEqual(len(w.dash_profile_game_buttons), 3)
        self.assertIn(("mw4", "multiplayer"), w.dash_profile_game_buttons)
        self.assertIn(("warzone", "resurgence"), w.dash_profile_game_buttons)
        self.assertIn(("warzone", "battle_royale"), w.dash_profile_game_buttons)
        w.store.set_meta("codmunity_stats", {"personal": {"warzone": {"resurgence": {"stats": {
            "wins": 3, "games_played": 10, "kills": 50, "deaths": 25}}}}})
        w.dash_profile_game_buttons[("warzone", "resurgence")].click()
        self.assertTrue(w.dash_profile_game_buttons[("warzone", "resurgence")].isChecked())
        self.assertFalse(w.dash_profile_game_buttons[("mw4", "multiplayer")].isChecked())
        self.assertFalse(w.dash_profile_game_buttons[("warzone", "battle_royale")].isChecked())
        self.assertEqual(w.dash_meters["kd"]["value"].text(), "2.00")
        self.assertEqual(w.dash_meters["kd"]["sub"].text(), "50 kills · 25 deaths")
        self.assertEqual(w.stats_cards["kd"].text(), "2.00")

    def test_stats_page_hidden_but_registered(self):
        w = self.window
        rows = [r for r in range(w.nav.count())
                if w.nav.item(r).data(Qt.UserRole) == "Stats"]
        self.assertEqual(len(rows), 1)
        self.assertTrue(w.nav.item(rows[0]).isHidden())
        self.assertIn("Stats", w.pages)

    def test_match_center_compact_and_summary_updates(self):
        import tempfile as _tf
        from pathlib import Path as _Path
        w = self.window
        self.assertLessEqual(w.latest_card.maximumHeight(), 220)
        w.refresh_match_center([])
        self.assertIn("No matches", w.latest_detail.text())
        self.assertEqual(set(w.dashboard_metrics),
                         {"matches", "squad_posts", "reviews",
                          "games", "win_rate", "kd", "kills_per_game"})
        w.store = app.E.Store(str(_Path(_tf.mkdtemp()) / "history.sqlite3"))
        _seed_win(w)
        w.refresh_history()
        self.assertIn("WIN", w.latest_result.text())
        self.assertIn("1 match tracked", w.center_count.text())
        self.assertEqual(w.dashboard_metrics["matches"].text(), "1")
        self.assertTrue(w.latest_review.isEnabled())
