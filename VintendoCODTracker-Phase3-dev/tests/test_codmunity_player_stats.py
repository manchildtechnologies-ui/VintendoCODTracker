import unittest

from codmunity_player_stats import (
    CommunityDistribution,
    ModularPlayerStatsService,
    PlayerStatsUnavailable,
    ladder_rank,
    parse_profile_identity,
    player_kd_for_mode,
    tracked_match_kd,
)


# Synthetic public-shape fixtures. IDs are inputs in fixtures only; the resolver
# obtains the UID from response.user.uid instead of a username lookup table.
class PlayerDataTests(unittest.TestCase):
    def test_profile_uid_is_parsed_for_each_username_without_mapping_constants(self):
        first = parse_profile_identity(
            {"user": {"username": "vintendopower", "uid": "9Vhs7iIIqKQ73vgW557Fw2JZwrx1", "displayName": "Vintendo"}},
            "vintendopower",
        )
        second = parse_profile_identity(
            {"user": {"username": "Goomy11", "uid": "m97XtraDplQAvzz8xx0XrB15XXC3", "displayName": "Goomy"}},
            "Goomy11",
        )
        self.assertEqual(first.uid, "9Vhs7iIIqKQ73vgW557Fw2JZwrx1")
        self.assertEqual(second.uid, "m97XtraDplQAvzz8xx0XrB15XXC3")
        self.assertNotEqual(first.uid, second.uid)

    def test_profile_parser_rejects_username_mismatch(self):
        with self.assertRaises(ValueError):
            parse_profile_identity({"user": {"username": "other", "uid": "uid-1"}}, "vintendopower")

    def test_distribution_parser_and_resurgence_fixture(self):
        distribution = CommunityDistribution.from_payload({
            "data": {
                "game": "warzone",
                "mode": "resurgence",
                "scope": "lifetime",
                "sampled": 492,
                "usersTotal": 515,
                "dist": {"kd": [0.8, 1.0, 1.5, 2.0, 2.75, 3.47, 4.2, 4.94, 5.86, 7.43, 8.84, 12.03]},
            }
        })
        result = ladder_rank(3.41, distribution)
        self.assertAlmostEqual(result.percentile, 49.1666667, places=5)
        self.assertEqual(result.tier, "Solid")
        self.assertEqual(result.next_tier, "Sweat")
        self.assertAlmostEqual(result.next_tier_kd, 4.94, places=7)

    def test_distribution_validation_rejects_bad_shape_and_order(self):
        base = {"game": "warzone", "mode": "resurgence", "scope": "lifetime"}
        with self.assertRaises(ValueError):
            CommunityDistribution.from_payload({"data": {**base, "dist": {"kd": [1, 2]}}})
        with self.assertRaises(ValueError):
            CommunityDistribution.from_payload({"data": {**base, "dist": {"kd": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 9, 12]}}})

    def test_player_stats_provider_is_explicitly_unconfigured_by_default(self):
        with self.assertRaises(PlayerStatsUnavailable):
            ModularPlayerStatsService().retrieve("uid-a")
        service = ModularPlayerStatsService(lambda uid: {"state": "public", "lifetime": {"kd": 2.0}, "uid": uid})
        self.assertEqual(service.retrieve("uid-b")["uid"], "uid-b")

    def test_only_verified_exact_name_rows_contribute_to_local_sample(self):
        rows = [
            {"payload": '{"game":"warzone","modeKey":"resurgence"}',
             "players": '[{"name":"VintendoPower","verified":true,"stats":{"kills":341,"deaths":100}}]'},
            {"payload": '{"game":"warzone","modeKey":"resurgence"}',
             "players": '[{"name":"VintendoPower","verified":false,"stats":{"kills":900,"deaths":1}}]'},
            {"payload": '{"game":"warzone","modeKey":"battle_royale"}',
             "players": '[{"name":"VintendoPower","verified":true,"stats":{"kills":100,"deaths":1}}]'},
        ]
        sample = tracked_match_kd(rows, ["vintendopower"], "warzone", "resurgence")
        self.assertEqual(sample.matches, 1)
        self.assertAlmostEqual(sample.kd, 3.41)

    def test_player_kd_extraction_is_mode_specific_and_labeled(self):
        payload = {"warzoneLifetime": {
            "resurgence": {"stats": {"kills": 341, "deaths": 100}, "updatedAt": "2026-11-10T12:00:00Z"},
            "battle_royale": {"stats": {"kd": 2.25}},
        }}
        result = player_kd_for_mode(payload, "warzone", "resurgence")
        self.assertAlmostEqual(result.kd, 3.41)
        self.assertEqual(result.source, "CODMunity Warzone resurgence lifetime")
        self.assertIsNone(player_kd_for_mode(payload, "warzone", "all").kd)


if __name__ == "__main__":
    unittest.main()
