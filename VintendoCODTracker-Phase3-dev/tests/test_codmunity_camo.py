"""Focused tests for the native Camo/Loadout/Leaderboard data layer."""
import json
import tempfile
import unittest
from pathlib import Path

import codmunity_camo as C
import engine as E


def _cache():
    root = Path(tempfile.mkdtemp(prefix="camo-cache-"))
    camo_page = {
        "camoGrinds": [{"id": "g1", "name": "mw4", "title": "Weapon Camos",
                        "saveField": "mw4-mp", "game": "mw4",
                        "camoConfig": {"camos": [{"name": "Gold", "order": 2,
                                                  "image": "http://img/gold.webp"},
                                                 {"name": "Platinum", "order": 3}]}}],
        "weapons": [{"WeaponName": "M4", "Category": "Assault Rifle",
                     "Unlock": "Level 4", "game": "mw4"},
                    {"WeaponName": "Oden", "Category": "Assault Rifle",
                     "Unlock": "Level 28", "game": "mw4"}],
        "challenges": [{"WeaponName": "M4", "Name": "Gold",
                        "Challenge": "Get 100 kills", "CamoGrind": "mw4",
                        "SubCategory": "Completionist"}],
        "games": [{"name": "Modern Warfare 4", "code": "mw4"}],
    }
    (root / "camo-page.json").write_text(json.dumps(camo_page), encoding="utf-8")
    creator = {
        "weapons": [{"WeaponName": "M4", "Category": "Assault Rifle"}],
        "attachments": [{"Attachmentname": "Red Dot", "Slot": "Optic",
                         "WeaponUnlock": "M4"},
                        {"Attachmentname": "Grip", "Slot": "Underbarrel",
                         "WeaponUnlock": "M4"}],
    }
    (root / "loadout-creator.json").write_text(json.dumps(creator), encoding="utf-8")
    feed = [{"id": 1, "at": 1700000000000, "weapon": "M4", "camo": "Gold",
             "grind": "mw4", "state": "saved", "changed": [], "read": ["CHALLENGE COMPLETED"]}]
    (root / "autotrack-feed.json").write_text(json.dumps(feed), encoding="utf-8")
    return root


def _store():
    return E.Store(Path(tempfile.mkdtemp(prefix="camo-store-")) / "history.sqlite3")


class CamoDataTests(unittest.TestCase):
    def test_missing_cache_yields_empty_catalogue(self):
        empty = Path(tempfile.mkdtemp(prefix="camo-empty-"))
        catalogue = C.load_camo_catalogue(empty)
        self.assertEqual(catalogue, {"grinds": [], "weapons": [], "challenges": []})

    def test_catalogue_parses_grinds_weapons_challenges(self):
        catalogue = C.load_camo_catalogue(_cache())
        self.assertEqual(len(catalogue["grinds"]), 1)
        self.assertEqual([c["name"] for c in catalogue["grinds"][0]["camos"]], ["Gold", "Platinum"])
        self.assertEqual(catalogue["grinds"][0]["camos"][0]["image"], "http://img/gold.webp")
        self.assertEqual(catalogue["grinds"][0]["game"], "mw4")
        self.assertEqual([w["name"] for w in catalogue["weapons"]], ["M4", "Oden"])
        self.assertEqual(catalogue["challenges"][0]["challenge"], "Get 100 kills")

    def test_progress_prefers_tracker_rows_over_feed(self):
        root = _cache()
        store = _store()
        progress = C.progress_map(store, root / "autotrack-feed.json")
        self.assertEqual(progress[("m4", "gold")]["state"], "saved")
        C.mark_progress(store, "M4", "Gold", "dismissed")
        progress = C.progress_map(store, root / "autotrack-feed.json")
        self.assertEqual(progress[("m4", "gold")]["state"], "dismissed")

    def test_mark_progress_rejects_bad_state(self):
        with self.assertRaises(ValueError):
            C.mark_progress(_store(), "M4", "Gold", "nope")
        self.assertEqual(C.mark_progress(None, "M4", "Gold", "saved"), "")

    def test_leaderboard_tables_from_snapshot(self):
        snapshot = {"leaderboards": {"warzone_resurgence": {
            "entries": [{"rank": 3, "username": "vintendo", "value": 3.1}]}},
            "identity": {"hits": [{"leaderboard": "warzone_resurgence",
                                   "username": "vintendo", "rank": 3}]}}
        tables = C.leaderboard_tables(snapshot)
        self.assertEqual(tables["warzone_resurgence"]["count"], 1)
        self.assertEqual(tables["warzone_resurgence"]["label"], "Warzone Resurgence K/D")
        self.assertEqual(len(C.identity_hits(snapshot)), 1)
        self.assertEqual(C.leaderboard_tables({}), {})
        self.assertEqual(C.identity_hits(None), [])

def _board_snapshot():
    return {
        "leaderboards": {
            "mw4_multiplayer_kd": {"entries": [
                {"rank": 1, "username": "aaa", "displayName": "Aaa",
                 "value": 4.91, "wins": 133, "kills": 16447,
                 "gamesPlayed": 331, "timePlayedS": 7200, "avatar": None},
                {"rank": 2, "username": "bbb", "displayName": "Bbb",
                 "value": 3.79, "wins": 497, "kills": 8000,
                 "gamesPlayed": 557, "timePlayedS": 3600, "avatar": None},
                {"rank": 3, "username": "ccc", "displayName": "Ccc",
                 "value": 3.38, "wins": 10, "kills": 100,
                 "gamesPlayed": 68, "timePlayedS": None, "avatar": None}]},
            "warzone_resurgence": {"entries": [
                {"rank": 1, "username": "wz1", "displayName": "Wz1",
                 "value": 44, "wins": 2, "kills": 449,
                 "gamesPlayed": 19, "timePlayedS": 900, "avatar": None}]}},
        "personal": {"multiplayer": {"stats": {
            "kills": 10051, "deaths": 9083, "games_played": 478,
            "wins": 187, "losses": 278}}},
        "identity": {"hits": [{"leaderboard": "mw4_multiplayer_kd",
                               "username": "vintendo", "rank": 12}]},
    }


class BoardDataTests(unittest.TestCase):
    def test_board_for_picks_game_tables(self):
        snapshot = _board_snapshot()
        key, label, entries = C.board_for("mw4", "resurgence", snapshot)
        self.assertEqual((key, label), ("mw4_multiplayer_kd", "MW4 · Multiplayer"))
        self.assertEqual(len(entries), 3)
        key, label, entries = C.board_for("warzone", "resurgence", snapshot)
        self.assertEqual((key, label), ("warzone_resurgence", "Warzone · Resurgence"))
        self.assertEqual(len(entries), 1)
        key, label, entries = C.board_for("warzone", "battle_royale", snapshot)
        self.assertEqual(entries, [])
        self.assertEqual(C.board_for("mw4", "x", {}), ("mw4_multiplayer_kd", "MW4 · Multiplayer", []))

    def test_sort_entries_per_metric_nones_last(self):
        entries = _board_snapshot()["leaderboards"]["mw4_multiplayer_kd"]["entries"]
        by_kd = [e["username"] for e in C.sort_entries(entries, "kd")]
        self.assertEqual(by_kd, ["aaa", "bbb", "ccc"])
        by_wins = [e["username"] for e in C.sort_entries(entries, "wins")]
        self.assertEqual(by_wins, ["bbb", "aaa", "ccc"])
        extra = entries + [{"username": "mystery"}]
        by_time = [e["username"] for e in C.sort_entries(extra, "time")]
        self.assertEqual(by_time[-1], "mystery")
        self.assertEqual(C.sort_entries([], "kd"), [])

    def test_metric_value_and_formats(self):
        entry = {"value": 4.91, "wins": 133, "kills": 16447, "timePlayedS": 7261}
        self.assertEqual(C.metric_value(entry, "kd"), 4.91)
        self.assertEqual(C.metric_value(entry, "wins"), 133)
        self.assertEqual(C.metric_value(entry, "time"), 7261)
        self.assertIsNone(C.metric_value(entry, "bogus"))
        self.assertIsNone(C.metric_value({"value": True}, "kd"))
        self.assertEqual(C.format_count(711208), "711,208")
        self.assertEqual(C.format_count(None), "—")
        self.assertEqual(C.format_time(7261), "2h 01m")
        self.assertEqual(C.format_time(95), "1m 35s")
        self.assertEqual(C.format_time(None), "—")

    def test_medal_tiers(self):
        self.assertEqual(C.medal(1), "gold")
        self.assertEqual(C.medal(2), "silver")
        self.assertEqual(C.medal(3), "bronze")
        self.assertIsNone(C.medal(4))
        self.assertIsNone(C.medal("x"))

    def test_game_tabs_split_grinds_and_challenges(self):
        catalogue = {"grinds": [{"name": "mw4", "title": "Weapon Camos"},
                                {"name": "wz-mw4", "title": "MW4"}],
                     "weapons": [],
                     "challenges": [
                         {"weapon": "M4", "camo": "Gold", "challenge": "Get 100 kills",
                          "challenge_wz": "", "grind": "mw4", "subcategory": ""},
                         {"weapon": "M4", "camo": "Abyss", "challenge": "Get 5 kills",
                          "challenge_wz": "Get 5 Warzone kills", "grind": "wz-mw4",
                          "subcategory": ""}]}
        mw4_grinds = C.grinds_for_game(catalogue, "mw4")
        self.assertEqual([g["name"] for g in mw4_grinds], ["mw4"])
        wz_grinds = C.grinds_for_game(catalogue, "warzone")
        self.assertEqual([g["name"] for g in wz_grinds], ["wz-mw4"])
        self.assertEqual(C.grinds_for_game(catalogue, "bogus"), [])
        self.assertEqual(C.grinds_for_game({}, "mw4"), [])
        mw4_rows = C.challenges_for_game(catalogue, "mw4")
        self.assertEqual(len(mw4_rows), 1)
        self.assertEqual(mw4_rows[0]["challenge"], "Get 100 kills")
        wz_rows = C.challenges_for_game(catalogue, "warzone")
        self.assertEqual(len(wz_rows), 1)
        self.assertEqual(wz_rows[0]["challenge"], "Get 5 Warzone kills")
        self.assertEqual(C.challenges_for_game(catalogue, "bogus"), [])

    def test_tier_images_map_names_to_art(self):
        catalogue = C.load_camo_catalogue(_cache())
        self.assertEqual(C.tier_images(catalogue, "mw4"),
                         {"Gold": "http://img/gold.webp", "Platinum": ""})
        self.assertEqual(C.tier_images(catalogue, "bogus"), {})
        self.assertEqual(C.tier_images({}, "mw4"), {})

    def test_game_tabs_list_catalogue_games(self):
        catalogue = {"grinds": [{"name": "mw4", "game": "mw4"},
                                {"name": "bo7-multi", "game": "bo7"}]}
        self.assertEqual(C.game_tabs(catalogue), [("mw4", "MW4"), ("bo7", "BO7")])
        self.assertEqual(C.game_tabs({}), [])

    def test_tracked_game_tabs_only_mw4_and_warzone(self):
        catalogue = {"grinds": [{"name": "mw4", "game": "mw4"},
                                {"name": "bo7-multi", "game": "bo7"},
                                {"name": "wz-mw4", "game": "warzone-2"},
                                {"name": "mw19", "game": "mw2019"}]}
        self.assertEqual(C.tracked_game_tabs(catalogue),
                         [("mw4", "MW4"), ("warzone-2", "WARZONE")])
        self.assertEqual(C.tracked_game_tabs({}), [])

    def test_weapon_rows_locks_and_next(self):
        catalogue = {"weapons": [{"WeaponName": "M4", "Category": "Assault Rifle",
                                  "Unlock": "Level 4", "game": "mw4",
                                  "popularityByGame": {"mw4": 5.0}}],
                     "grinds": [{"name": "mw4", "game": "mw4",
                                 "camos": [{"name": "Gold", "order": 2},
                                           {"name": "Platinum", "order": 3}]}],
                     "challenges": [
                         {"weapon": "M4", "camo": "Mil 1", "challenge": "Reach L9",
                          "grind": "mw4", "Order": 1, "Image": ""},
                         {"weapon": "M4", "camo": "Gold", "challenge": "100 kills",
                          "grind": "mw4", "Order": 10, "Image": ""},
                         {"weapon": "M4", "camo": "Platinum", "challenge": "200 kills",
                          "grind": "mw4", "Order": 11, "Image": ""}]}
        rows = C.weapon_rows(catalogue, {}, "mw4", "mw4")
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["total"], 3)
        self.assertEqual(row["done"], 0)
        self.assertEqual(row["next"]["name"], "Mil 1")
        gold = next(c for c in row["camos"] if c["name"] == "Gold")
        plat = next(c for c in row["camos"] if c["name"] == "Platinum")
        self.assertTrue(gold["locked"])
        self.assertTrue(plat["locked"])
        progress = {("m4", "mil 1"): {"state": "saved"}}
        rows = C.weapon_rows(catalogue, progress, "mw4", "mw4")
        row = rows[0]
        self.assertEqual(row["done"], 1)
        self.assertEqual(row["next"]["name"], "Gold")
        self.assertFalse(next(c for c in row["camos"] if c["name"] == "Gold")["locked"])
        done, total = C.class_completion(rows, "Gold")
        self.assertEqual((done, total), (0, 1))
        self.assertEqual(C.tier_order(catalogue, "mw4"), ["Gold", "Platinum"])

    def test_mastery_progress_and_last_detection(self):
        catalogue = {"grinds": [{"name": "mw4-mastery", "game": "mw4",
                                 "grindKind": "account-mastery"}],
                     "challenges": [{"weapon": "M4", "camo": "Poly",
                                     "grind": "mw4-mastery"}]}
        self.assertEqual(C.mastery_progress(catalogue, {}, "mw4"), (0, 1))
        progress = {("m4", "poly"): {"state": "saved"}}
        self.assertEqual(C.mastery_progress(catalogue, progress, "mw4"), (1, 1))
        self.assertEqual(C.mastery_progress(catalogue, progress, "bo7"), (0, 0))
        self.assertEqual(C.mastery_grind_for_game(catalogue, "mw4")["name"], "mw4-mastery")
        self.assertEqual(C.mastery_grind_for_game(catalogue, "bo7"), {})
        self.assertEqual(C.last_detection(None), {})
        self.assertIn("h ago", C.ago_text(__import__("time").time() - 7200))
        self.assertEqual(C.ago_text(None), "")

    def test_lifetime_stats_mapping_and_kd(self):
        snapshot = {"personal": {
            "multiplayer": {"stats": {"kills": 10051, "deaths": 9083,
                                       "games_played": 478, "wins": 187,
                                       "losses": 278,
                                       "wins_by_mode": {"hardpoint": 12}}},
            "warzone": {"resurgence": {"stats": {"kd": 3.21, "wins": 36,
                                                  "games_played": 51, "kills": 976}}}}}
        multi = C.lifetime_stats(snapshot, "mw4", "multiplayer")
        self.assertEqual(multi["kills"], 10051)
        self.assertAlmostEqual(C.lifetime_kd(multi), 10051 / 9083, places=2)
        wz = C.lifetime_stats(snapshot, "warzone", "resurgence")
        self.assertEqual(C.lifetime_kd(wz), 3.21)
        self.assertEqual(C.lifetime_stats(snapshot, "warzone", "battle_royale"), {})
        self.assertEqual(C.lifetime_stats({}, "mw4", "multiplayer"), {})
        self.assertIsNone(C.lifetime_kd({}))
        self.assertIsNone(C.lifetime_kd({"kills": 5, "deaths": 0}))
        line = C.summary_line(snapshot, "mw4", "multiplayer")
        self.assertIn("MW4 Multiplayer:", line)
        self.assertIn("187W/278L", line)
        self.assertIn("478 games", line)
        self.assertIn("10,051 kills", line)
        self.assertEqual(C.summary_line(snapshot, "warzone", "battle_royale"), "")
        self.assertEqual(C.summary_line({}, "mw4", "multiplayer"), "")

    def test_format_duration_and_share_urls(self):
        self.assertEqual(C.format_duration(121560), "33h 46m")
        self.assertEqual(C.format_duration(95), "1m 35s")
        self.assertEqual(C.format_duration(30), "30s")
        self.assertEqual(C.format_duration(None), "—")
        urls = C.share_urls("https://codmunity.gg/profile/vintendo", "My COD stats")
        self.assertIn("x.com/intent/post", urls["X"])
        self.assertIn("reddit.com/submit", urls["Reddit"])
        self.assertIn("facebook.com/sharer", urls["Facebook"])
        self.assertIn("wa.me", urls["WhatsApp"])
        for url in urls.values():
            self.assertIn("codmunity.gg%2Fprofile%2Fvintendo", url)

    def test_clear_caches_roundtrip(self):
        import tempfile
        from pathlib import Path
        root = Path(tempfile.mkdtemp(prefix="camo-cache-"))
        (root / "camo-images").mkdir()
        (root / "avatars").mkdir()
        (root / "camo-images" / "a.img").write_bytes(b"0" * 100)
        (root / "avatars" / "b.img").write_bytes(b"0" * 200)
        sizes = C.cache_sizes(root)
        self.assertEqual(sizes, {"camo-images": 100, "avatars": 200})
        self.assertEqual(C.clear_caches(root), 2)
        self.assertEqual(C.cache_sizes(root), {"camo-images": 0, "avatars": 0})
        self.assertEqual(C.clear_caches(root), 0)

    def test_progress_map_newest_row_wins(self):
        class FakeStore:
            def camo_detections(self, limit=50):
                return [{"weapon": "M4", "camo": "Gold", "state": "dismissed", "at": 200.0},
                        {"weapon": "M4", "camo": "Gold", "state": "saved", "at": 100.0},
                        {"weapon": "M4", "camo": "Platinum", "state": "saved", "at": 50.0}]

        progress = C.progress_map(FakeStore(), None)
        self.assertEqual(progress[("m4", "gold")]["state"], "dismissed")
        self.assertEqual(progress[("m4", "platinum")]["state"], "saved")

    def test_you_row_stats_from_personal(self):
        stats = C.you_row_stats(_board_snapshot(), "mw4", "resurgence")
        self.assertAlmostEqual(stats["kd"], 10051 / 9083, places=2)
        self.assertEqual(stats["wins"], 187)
        self.assertEqual(stats["games"], 478)
        self.assertTrue(stats["has_stats"])
        empty = C.you_row_stats({}, "mw4", "resurgence")
        self.assertFalse(empty["has_stats"])
        self.assertIsNone(empty["kd"])
        wz = C.you_row_stats({"personal": {"warzone": {"resurgence": {
            "stats": {"kd": 2.5, "wins": 3, "games": 10}}}}}, "warzone", "resurgence")
        self.assertEqual((wz["kd"], wz["wins"], wz["games"]), (2.5, 3, 10))


if __name__ == "__main__":
    unittest.main()
