import json, tempfile, unittest, uuid
from pathlib import Path
import engine as E
import codmunity_stats as C


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding='utf-8')


def build_stats_tree(root):
    """Synthetic CODMunity stats tree mirroring the real cache shapes."""
    stats = root / 'stats'
    user = stats / 'u-testuser'
    write(stats / 'global.json', {'fetchedAt': 1794197745, 'data': {
        'game': 'mw4', 'mode': 'multiplayer', 'scope': 'lifetime', 'ok': True,
        'players': 360, 'sampled': 346, 'usersTotal': 1428, 'wins': 10, 'losses': 12,
        'kills': 1000, 'deaths': 900, 'hours': 72, 'kdMedian': 1.02, 'kdP90': 1.28,
        'dist': {'kd': [0.88, 0.93, 0.96, 0.99, 1.0, 1.02, 1.05, 1.08, 1.12, 1.28, 1.47, 2.54],
                 'killsPerGame': [13.0, 14.0, 17.0, 18.0, 19.0, 20.0, 21.0, 22.0, 24.0, 26.0, 29.0, 38.0]},
        'kdBuckets': [{'max': 1.0, 'players': 3}]}})
    write(stats / 'global-warzone-resurgence-lifetime.json', {'fetchedAt': 1794197750, 'data': {
        'game': 'warzone', 'mode': 'resurgence', 'scope': 'lifetime', 'ok': True,
        'sampled': 492, 'usersTotal': 515, 'kdMedian': 3.38, 'kdP90': 7.43,
        'dist': {'kd': [0.8, 1.0, 1.5, 2.03, 2.77, 3.38, 4.12, 4.91, 5.86, 7.43, 8.84, 12.03],
                 'avgElims': [2, 3, 4, 5, 6, 8, 10, 12, 13, 14, 15, 15]}}})
    write(stats / 'lb-warzone-battle_royale-kd.json', {'fetchedAt': 1794150605, 'data': {
        'game': 'warzone', 'mode': 'battle_royale', 'metric': 'kd', 'scope': 'lifetime', 'ok': True,
        'entries': [
            {'rank': 1, 'username': 'someone', 'displayName': 'Someone', 'value': 20.25,
             'kills': 81, 'deaths': 5, 'gamesPlayed': 5, 'wins': 2, 'losses': 3, 'avatar': None},
            {'rank': 42, 'username': 'userinvyyxdp', 'displayName': 'The ManChild Show', 'value': 5.08,
             'kills': 71, 'deaths': 17, 'gamesPlayed': 2, 'wins': 2, 'losses': 5, 'avatar': None}]}})
    write(stats / 'lb-mw4-multiplayer-wins.json', {'data': None, 'fetchedAt': 1793991534})
    write(user / 'local.json', {'at': '2026-11-06T24:58:52Z', 'game': 'mw4', 'mode': 'killconfirmed',
                                'scope': 'lifetime', 'stats': {'kills': 8260, 'deaths': 1438, 'games_played': 607,
                                                               'wins': 461, 'losses': 234, 'level': 47}})
    write(user / 'wz-modes.json', {
        'resurgence': {'at': '2026-11-09T05:26:31Z', 'sent': True,
                       'stats': {'games_played': 29, 'kd': 3.26, 'kills': 976, 'top10': 30, 'wins': 36}},
        'battle_royale': {'at': '2026-11-09T05:44:39Z', 'sent': True,
                          'stats': {'games_played': 2, 'kd': 4.17, 'top10': 8}}})
    write(user / 'matches.json', [
        {'game': 'warzone', 'mode': 'resurgence', 'matchId': 'm-1', 'playedAt': '2026-11-07T10:00:00Z',
         'result': 'win', 'sent': True, 'stats': {'kills': 18, 'damage': 7356, 'placement': 1}},
        {'game': 'warzone', 'mode': 'resurgence', 'playedAt': '2026-11-07T11:00:00Z',
         'result': 'loss', 'sent': True, 'stats': {'kills': 4, 'placement': 10}}])
    write(user / 'feed.json', [
        {'id': 1, 'at': 1794200000000, 'kind': 'match', 'state': 'saved', 'reason': None,
         'data': {'game': 'warzone', 'mode': 'resurgence', 'playedAt': '2026-11-07T11:00:00Z',
                  'result': 'loss', 'stats': {'kills': 4, 'placement': 10}}},
        {'id': 2, 'at': 1794200001000, 'kind': 'match', 'state': 'saved', 'reason': None,
         'data': {'game': 'warzone', 'mode': 'resurgence', 'playedAt': '2026-11-07T12:00:00Z',
                  'result': 'loss', 'stats': {'kills': 17, 'placement': 2}}},
        {'id': 3, 'at': 1794200002000, 'kind': 'record', 'state': 'saved', 'reason': None,
         'data': {'game': 'warzone', 'mode': 'resurgence', 'stats': {'kd': 3.26, 'kills': 976}}},
        {'id': 4, 'at': 1794200003000, 'kind': 'record', 'state': 'skipped', 'reason': 'not_lifetime',
         'data': {'why': 'NotLifetimeMultiplayer'}}])
    write(user / 'rejected.json', None)
    return stats, user


class CodmunityStatsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='codmunity-stats-'))
        self.stats, self.user = build_stats_tree(self.tmp)
        self.snapshot = C.load_snapshot(self.stats, aliases=('vintendo', 'userinvyyxdp'))

    def test_snapshot_captures_every_source_including_empty_ones(self):
        snap = self.snapshot
        self.assertEqual(snap['counts']['matches'], 2)
        self.assertEqual(snap['counts']['feed_events'], 4)
        self.assertIn('mw4_multiplayer', snap['benchmarks'])
        self.assertIn('warzone_resurgence', snap['benchmarks'])
        self.assertEqual(snap['leaderboards']['warzone_battle_royale']['count'], 2)
        self.assertIsNone(snap['leaderboards']['mw4_multiplayer_wins']['entries'])
        self.assertEqual(snap['personal']['multiplayer']['stats']['level'], 47)
        self.assertEqual(snap['personal']['warzone']['resurgence']['stats']['kd'], 3.26)

    def test_percentile_anchors_match_codmunity_grid(self):
        dist = self.snapshot['benchmarks']['mw4_multiplayer']['dist']['kd']
        self.assertAlmostEqual(C.percentile_estimate(dist, 1.02), 50.0)
        self.assertAlmostEqual(C.percentile_estimate(dist, 1.28), 90.0)
        self.assertEqual(C.percentile_estimate(dist, 99.0), 100.0)
        self.assertIsNone(C.percentile_estimate([1.0, 2.0], 1.5))
        self.assertIsNone(C.percentile_estimate(dist, None))

    def test_quality_flags_inconsistent_lifetime_instead_of_hiding_it(self):
        flags = ' '.join(self.snapshot['quality'])
        self.assertIn('multiplayer', flags)
        self.assertIn('wins', flags)
        derived = self.snapshot['personal']['multiplayer']['derived']
        self.assertAlmostEqual(derived['kd'], 8260 / 1438, places=4)

    def test_identity_hit_found_on_leaderboard(self):
        hits = self.snapshot['identity']['hits']
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]['leaderboard'], 'warzone_battle_royale')
        self.assertEqual(hits[0]['rank'], 42)

    def test_recap_and_poster_gain_benchmark_context_only_when_snapshot_given(self):
        match = {'game': 'warzone', 'mode': 'resurgence', 'playedAt': '2026-11-07T12:00:00Z',
                 'result': 'loss', 'stats': {'kills': 17, 'placement': 2}}
        plain = E.recap(match, [])
        self.assertNotIn('CODMunity benchmark', plain)
        enriched = E.recap(match, [], snapshot=self.snapshot)
        self.assertIn('CODMunity benchmark', enriched)
        self.assertIn('median', enriched.lower())
        poster = E.match_poster_prompt(
            [dict(name='Alpha', stats={'kills': 12}, verified=True)], match, snapshot=self.snapshot)
        self.assertIn('benchmark context', poster.lower())
        self.assertIn('do not render', poster.lower())

    def test_import_is_idempotent_and_keeps_matches_as_baseline(self):
        store = E.Store(self.tmp / 'history.sqlite3')
        report = C.import_snapshot(store, self.stats, aliases=('userinvyyxdp',))
        self.assertEqual(report['matches_seen'], 3)
        self.assertEqual(report['matches_added'], 3)
        self.assertTrue(all(row['status'] == 'baseline' for row in store.history()))
        stored = store.meta(C.SNAPSHOT_KEY)
        self.assertEqual(stored['counts']['matches'], 2)
        again = C.import_snapshot(store, self.stats, aliases=('userinvyyxdp',))
        self.assertEqual(again['matches_added'], 0)
        self.assertEqual(len(store.history()), 3)

    def test_personal_bests_and_lifetime_kills_reach_recap_lines(self):
        bests = self.snapshot['personal']['matches_summary']['bests']
        self.assertEqual(bests['best_kills'], 18)
        self.assertEqual(self.snapshot['personal']['matches_summary']['bests_warzone']['best_kills'], 18)
        self.assertEqual(self.snapshot['personal']['matches_summary']['bests_multiplayer']['matches'], 0)
        self.assertEqual(bests['best_damage'], 7356)
        lines = C.benchmark_lines(
            {'game': 'warzone', 'mode': 'resurgence', 'result': 'win',
             'stats': {'kills': 5}}, self.snapshot)
        joined = ' '.join(lines)
        self.assertIn('976 lifetime kills', joined)
        self.assertIn('Personal bests', joined)

    def test_personal_row_merge_never_duplicates_ocr_coverage(self):
        match = {'game': 'warzone', 'mode': 'resurgence',
                 'stats': {'kills': 17, 'damage': 5000, 'placement': 2, 'contracts': 1}}
        row = E.codmunity_personal_row(match, 'Vintendo')
        self.assertEqual(row['source'], 'codmunity_local')
        self.assertNotIn('placement', row['stats'])
        self.assertNotIn('contracts', row['stats'])
        merged, added = E.merge_personal_row([], match, 'Vintendo')
        self.assertTrue(added)
        self.assertEqual(len(merged), 1)
        again, added_again = E.merge_personal_row(
            [{'name': 'userinvyyxdp', 'verified': True, 'stats': {}}],
            match, 'Vintendo', aliases=('userinvyyxdp',))
        self.assertFalse(added_again)
        self.assertEqual(len(again), 1)
        self.assertIsNone(E.codmunity_personal_row(
            {'stats': {'placement': 1}}, 'Vintendo'))
        covered, added_covered = E.merge_personal_row(merged, match, 'userinvyyxdp')
        self.assertFalse(added_covered)
        self.assertEqual(len(covered), 1)

    def test_roster_alias_sync_from_identity_hit_is_idempotent(self):
        store = E.Store(self.tmp / 'roster.sqlite3')
        store.save_player({'id': 'vintendo', 'name': 'Vintendo', 'aliases': [],
                           'photo': '', 'enabled': True, 'consent_meta': True})
        report = C.import_snapshot(store, self.stats, aliases=('userinvyyxdp',),
                                   roster_player='Vintendo')
        self.assertEqual(report['roster_aliases_added'], 2)
        aliases = store.roster()[0]['aliases']
        self.assertIn('userinvyyxdp', aliases)
        self.assertIn('The ManChild Show', aliases)
        again = C.import_snapshot(store, self.stats, aliases=('userinvyyxdp',),
                                  roster_player='Vintendo')
        self.assertEqual(again['roster_aliases_added'], 0)

    def test_match_above_cached_range_is_not_called_100th_percentile(self):
        match = {'game': 'warzone', 'mode': 'resurgence', 'result': 'win',
                 'stats': {'kills': 99}}
        lines = C.benchmark_lines(match, self.snapshot)
        self.assertTrue(any('above the cached per-game' in line for line in lines))
        self.assertFalse(any('100th percentile' in line for line in lines))

    def test_missing_folder_yields_empty_snapshot_not_exception(self):
        snap = C.load_snapshot(self.tmp / 'does-not-exist')
        self.assertEqual(snap['counts']['matches'], 0)
        self.assertEqual(E.recap({'game': 'warzone', 'mode': 'resurgence', 'result': 'win',
                                  'stats': {'kills': 1}}, [], snapshot=snap),
                         E.recap({'game': 'warzone', 'mode': 'resurgence', 'result': 'win',
                                  'stats': {'kills': 1}}, []))


if __name__ == '__main__':
    unittest.main()
