import unittest

from codmunity_player_stats import (
    CodmunityPublicClient,
    CommunityDistribution,
    GLOBAL_STATS_API,
    ModularPlayerStatsService,
    PlayerStatsUnavailable,
    ladder_rank,
    distribution_rank,
    parse_profile_banner,
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

    def test_profile_banner_parses_display_fields(self):
        banner = parse_profile_banner(
            {"user": {"username": "vintendopower", "uid": "uid-9",
                      "displayName": "Vintendo", "description": "TIme to SMell it",
                      "followers": 3,
                      "imageUrl": "https://assets.codmunity.gg/profiles/pi-a.webp",
                      "imageUrlOptimized": "https://assets.codmunity.gg/profiles/pi-b.webp"},
             "countLoadouts": 2, "countLikes": 7},
            "vintendopower",
        )
        self.assertEqual(banner["username"], "vintendopower")
        self.assertEqual(banner["uid"], "uid-9")
        self.assertEqual(banner["display_name"], "Vintendo")
        self.assertEqual(banner["avatar"], "https://assets.codmunity.gg/profiles/pi-b.webp")
        self.assertEqual(banner["bio"], "TIme to SMell it")
        self.assertEqual(banner["followers"], 3)
        self.assertEqual(banner["loadouts"], 2)
        self.assertEqual(banner["likes"], 7)

    def test_profile_banner_rejects_bad_identity_but_zeroes_extras(self):
        with self.assertRaises(ValueError):
            parse_profile_banner({"user": {"username": "other", "uid": "uid-1"}}, "vintendopower")
        with self.assertRaises(ValueError):
            parse_profile_banner({"user": {"username": "vintendopower"}}, "vintendopower")
        banner = parse_profile_banner(
            {"user": {"username": "vintendopower", "uid": "uid-9",
                      "imageUrl": "ftp://example.com/x.webp",
                      "followers": True, "displayName": "  "},
             "countLoadouts": -1, "countLikes": None},
            "vintendopower",
        )
        self.assertEqual(banner["avatar"], "")
        self.assertEqual(banner["bio"], "")
        self.assertIsNone(banner["display_name"])
        self.assertEqual(banner["followers"], 0)
        self.assertEqual(banner["loadouts"], 0)
        self.assertEqual(banner["likes"], 0)

    def test_distribution_keeps_optional_arrays(self):
        from codmunity_player_stats import CommunityDistribution
        dist = CommunityDistribution.from_payload({
            "data": {
                "game": "mw4", "mode": "multiplayer", "scope": "lifetime",
                "dist": {"kd": [0.5, 0.7, 0.9, 1.1, 1.3, 1.5, 1.8, 2.2, 2.8, 3.5, 4.5, 6.0],
                         "wl": [0.2, 0.4, 0.6, 0.8, 1.0, 1.2, 1.4, 1.6, 1.8, 2.0, 2.2, 2.4],
                         "winRate": [0.05, 0.1, 0.15, 0.2, 0.25, 0.3, 0.35, 0.4, 0.45, 0.5, 0.55, 0.6]}}})
        self.assertEqual(len(dist.wl), 12)
        self.assertEqual(len(dist.win_rate), 12)

    def test_distribution_handles_missing_arrays(self):
        from codmunity_player_stats import CommunityDistribution
        dist = CommunityDistribution.from_payload({
            "data": {
                "game": "warzone", "mode": "resurgence", "scope": "lifetime",
                "dist": {"kd": [0.8, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0, 6.0, 7.0]}}})
        self.assertIsNone(dist.wl)
        self.assertIsNone(dist.win_rate)

    def test_percentile_and_median_helpers(self):
        from codmunity_player_stats import median_of, percentile_of
        values = [0.5, 0.7, 0.9, 1.1, 1.3, 1.5, 1.8, 2.2, 2.8, 3.5, 4.5, 6.0]
        self.assertAlmostEqual(percentile_of(1.5, values), 50.0)
        self.assertAlmostEqual(median_of(values), 1.5)
        self.assertAlmostEqual(percentile_of(0.1, values), 5.0)
        self.assertAlmostEqual(percentile_of(99.0, values), 99.0)
        with self.assertRaises(ValueError):
            percentile_of("junk", values)
        with self.assertRaises(ValueError):
            median_of([1.0, 2.0])

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


class DistributionRankTests(unittest.TestCase):
    def _dist(self, sampled, kd):
        return CommunityDistribution.from_payload({
            "data": {"game": "mw4", "mode": "multiplayer", "scope": "lifetime",
                     "sampled": sampled, "usersTotal": 120,
                     "dist": {"kd": kd}}})

    def test_tiny_sample_refuses_rank(self):
        tiny = self._dist(10, [0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0, 5.5, 6.0])
        self.assertIsNone(distribution_rank(1.11, tiny))

    def test_healthy_sample_ranks(self):
        kd = [0.88, 0.93, 0.96, 0.98, 1.0, 1.02, 1.05, 1.08, 1.14, 1.27, 1.39, 2.09]
        big = self._dist(841, kd)
        rank = distribution_rank(1.11, big)
        self.assertIsNotNone(rank)
        self.assertEqual(rank.tier, "Sweat")


if __name__ == "__main__":
    unittest.main()
