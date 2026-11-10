import unittest

from codmunity_player_stats import (
    CodmunityPublicClient,
    CommunityDistribution,
    GLOBAL_STATS_API,
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


class GlobalComboTests(unittest.TestCase):
    """The four concrete catalog routes fetch and parse; mismatches are rejected."""

    COMBOS = [
        ("warzone", "all", "recent"),
        ("warzone", "resurgence", "lifetime"),
        ("warzone", "battle_royale", "lifetime"),
        ("mw4", "multiplayer", "lifetime"),
    ]
    KD = [0.5, 0.8, 1.0, 1.5, 2.0, 2.5, 3.0, 3.8, 4.5, 5.5, 7.0, 9.0]

    def _client(self, seen, flat=False):
        import json as _json
        from urllib.parse import urlparse as _urlparse, parse_qs as _parse_qs

        class FakeResponse:
            def __init__(self, body):
                self._body = body

            def read(self):
                return self._body

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        def opener(request, timeout=0):
            seen.append(request.full_url)
            query = _parse_qs(_urlparse(request.full_url).query)
            data = {"game": query["game"][0], "mode": query["mode"][0], "scope": query["scope"][0],
                    "sampled": 100, "usersTotal": 120, "dist": {"kd": list(self.KD)}}
            return FakeResponse(_json.dumps(data if flat else {"data": data}).encode("utf-8"))

        return CodmunityPublicClient(opener=opener)

    def test_catalog_combos_fetch_and_parse(self):
        seen = []
        client = self._client(seen)
        for game, mode, scope in self.COMBOS:
            distribution = client.community_distribution(game, mode, scope)
            self.assertEqual((distribution.game, distribution.mode, distribution.scope), (game, mode, scope))
            self.assertEqual(len(distribution.kd), 12)
        self.assertEqual(len(seen), 4)
        for url, (game, mode, scope) in zip(seen, self.COMBOS):
            self.assertTrue(url.startswith(GLOBAL_STATS_API + "?"))
            self.assertIn("game=" + game, url)
            self.assertIn("mode=" + mode, url)
            self.assertIn("scope=" + scope, url)

    def test_flat_live_envelope_and_mismatch_guard(self):
        seen = []
        distribution = self._client(seen, flat=True).community_distribution("mw4", "multiplayer", "lifetime")
        self.assertEqual((distribution.game, distribution.mode, distribution.scope), ("mw4", "multiplayer", "lifetime"))

        def bad_opener(request, timeout=0):
            import json as _json

            class FakeResponse:
                def read(self):
                    return _json.dumps({"data": {"game": "warzone", "mode": "all", "scope": "recent",
                                                  "dist": {"kd": list(GlobalComboTests.KD)}}}).encode("utf-8")

                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    return False

            return FakeResponse()

        with self.assertRaises(ValueError):
            CodmunityPublicClient(opener=bad_opener).community_distribution("mw4", "multiplayer", "lifetime")


if __name__ == "__main__":
    unittest.main()
