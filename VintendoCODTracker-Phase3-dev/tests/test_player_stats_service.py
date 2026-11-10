"""Tests for the shared Vintendo Player Stats Service (offline fixtures only)."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import engine as E
import player_stats_service as S

DIST = {'game': 'warzone', 'mode': 'resurgence', 'scope': 'lifetime',
        'kd': [0.8, 1.0, 1.5, 2.0, 2.75, 3.47, 4.2, 4.94, 5.86, 7.43, 8.84, 12.03],
        'sampled': 492, 'users_total': 515,
        'updated_at': '2026-11-10T12:00:00Z', 'source': 'test'}


def _player(pid='rank-a', name='Vintendo', **extra):
    base = {'id': pid, 'name': name, 'display_name': name + ' local',
            'aliases': ['VintendoPower'], 'photo': '', 'enabled': True,
            'consent_meta': True, 'codmunity_username': 'vintendopower',
            'codmunity_uid': 'uid-primary',
            'codmunity_display_name': 'VintendoPower',
            'profile_url': 'https://codmunity.gg/profile/vintendopower'}
    base.update(extra)
    return base


def _win(store, name='VintendoPower', kills=341, deaths=100, result='win', mode='resurgence'):
    mid, _ = store.add({'matchId': 'm-' + os.urandom(4).hex(), 'game': 'warzone',
                        'modeKey': mode, 'playedAt': '2026-11-10T12:00:00Z', 'result': result})
    store.update(mid, players=json.dumps(
        [{'name': name, 'verified': True, 'stats': {'kills': kills, 'deaths': deaths}}]))
    return mid


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='pss-'))
        self.addCleanup(lambda: __import__('shutil').rmtree(self.tmp, ignore_errors=True))
        self.store = E.Store(self.tmp / 'history.sqlite3')
        self.store.save_player(_player())
        self.store.save_player(_player(pid='rank-b', name='Goomy11', display_name='Goomy local',
                                       aliases=[], consent_meta=False, codmunity_username='',
                                       codmunity_uid='', codmunity_display_name='', profile_url=''))
        self.store.set_meta('codmunity_public_distributions',
                            {'warzone|resurgence|lifetime': dict(DIST)})

    def test_snapshot_has_identity_rank_and_provenance(self):
        player = next(p for p in self.store.roster() if p['id'] == 'rank-a')
        _win(self.store)
        snap = S.snapshot_for_player(self.store, player, 'warzone', 'resurgence', 'lifetime')
        self.assertEqual(snap['uid'], 'uid-primary')
        self.assertEqual(snap['kd'], 3.41)
        self.assertEqual(snap['kd_source'], '1 tracked match')
        self.assertEqual(snap['sample_matches'], 1)
        self.assertEqual(snap['rank']['tier'], 'Solid')
        self.assertAlmostEqual(snap['rank']['percentile'], 49.17, places=1)
        self.assertEqual(snap['distribution']['sampled'], 492)
        self.assertTrue(snap['updated_at'])

    def test_snapshot_unavailable_without_kd_or_distribution(self):
        goomy = next(p for p in self.store.roster() if p['id'] == 'rank-b')
        snap = S.snapshot_for_player(self.store, goomy, 'warzone', 'resurgence', 'lifetime')
        self.assertIsNone(snap['kd'])
        self.assertIsNone(snap['rank'])
        _win(self.store)
        snap = S.snapshot_for_player(self.store, goomy, 'warzone', 'battle_royale', 'lifetime')
        self.assertIsNone(snap['rank'])

    def test_stored_lifetime_kd_wins_over_tracked_sample(self):
        player = next(p for p in self.store.roster() if p['id'] == 'rank-a')
        _win(self.store)
        player['codmunity_player_stats'] = {'warzoneLifetime': {'resurgence': {'kd': 2.0, 'kills': 200, 'deaths': 100}}}
        self.store.save_player(player)
        snap = S.snapshot_for_player(self.store, player, 'warzone', 'resurgence', 'lifetime')
        self.assertEqual(snap['kd'], 2.0)
        self.assertEqual(snap['kd_source'], 'lifetime')

    def test_totals_aggregate_wins_and_best(self):
        player = next(p for p in self.store.roster() if p['id'] == 'rank-a')
        _win(self.store, kills=10, deaths=5, result='win')
        _win(self.store, kills=30, deaths=10, result='loss')
        totals = S.totals_for_player(self.store, player, 'warzone', 'resurgence')
        self.assertEqual(totals['matches'], 2)
        self.assertEqual(totals['wins'], 1)
        self.assertEqual(totals['kills'], 40.0)
        self.assertAlmostEqual(totals['kd'], 40.0 / 15.0)
        self.assertEqual(totals['best_kills'], 30.0)
        self.assertTrue(totals['best_kills_match'])

    def test_squad_table_orders_and_flags_unavailable(self):
        players = self.store.roster()
        _win(self.store)
        table = S.squad_table(self.store, players, 'warzone', 'resurgence', 'lifetime')
        self.assertEqual([r['snapshot']['player_id'] for r in table], ['rank-a', 'rank-b'])
        self.assertTrue(table[0]['available'])
        self.assertFalse(table[1]['available'])

    def test_record_snapshot_throttles_and_caps(self):
        _win(self.store)
        first = S.record_snapshot(self.store, 'rank-a', 'warzone', 'resurgence', 'lifetime')
        self.assertIsNotNone(first)
        self.assertIsNone(S.record_snapshot(self.store, 'rank-a', 'warzone', 'resurgence', 'lifetime'))
        forced = S.record_snapshot(self.store, 'rank-a', 'warzone', 'resurgence', 'lifetime', min_interval=0)
        self.assertIsNotNone(forced)
        history = S.snapshot_history(self.store, 'rank-a', 'warzone', 'resurgence', 'lifetime')
        self.assertEqual(len(history), 2)
        self.assertIsNone(S.record_snapshot(self.store, 'rank-a', 'warzone', 'resurgence', 'lifetime'))
        self.assertIsNone(S.record_snapshot(self.store, 'rank-b', 'warzone', 'resurgence', 'lifetime'))

    def test_detect_rank_events_promotion_pb_and_no_dupes(self):
        _win(self.store, kills=100, deaths=100)
        S.record_snapshot(self.store, 'rank-a', 'warzone', 'resurgence', 'lifetime', min_interval=0)
        _win(self.store, kills=600, deaths=100)
        S.record_snapshot(self.store, 'rank-a', 'warzone', 'resurgence', 'lifetime', min_interval=0)
        events = S.detect_stat_events(self.store, 'rank-a', 'warzone', 'resurgence', 'lifetime')
        kinds = {e['type'] for e in events}
        self.assertIn('rank_promotion', kinds)
        self.assertIn('pb_kd', kinds)
        promotion = next(e for e in events if e['type'] == 'rank_promotion')
        self.assertGreater([t for _, t in __import__('codmunity_player_stats').LADDER_TIERS].index(promotion['to_tier']),
                           [t for _, t in __import__('codmunity_player_stats').LADDER_TIERS].index(promotion['from_tier']))
        self.assertEqual(S.detect_stat_events(self.store, 'rank-a', 'warzone', 'resurgence', 'lifetime'), [])
        self.assertTrue(S.awards_for_player(self.store, 'rank-a'))

    def test_discord_formatters_are_offline_and_complete(self):
        player = next(p for p in self.store.roster() if p['id'] == 'rank-a')
        _win(self.store)
        snap = S.snapshot_for_player(self.store, player, 'warzone', 'resurgence', 'lifetime')
        rank_text = S.format_rank_message(snap)
        self.assertIn('Solid', rank_text)
        self.assertIn('3.41', rank_text)
        self.assertIn('Sweat', rank_text)
        table = S.squad_table(self.store, self.store.roster(), 'warzone', 'resurgence', 'lifetime')
        board = S.format_leaderboard_message(table, 'warzone', 'resurgence', 'lifetime')
        self.assertIn('rank-a', board.replace('Vintendo local', 'rank-a'))
        self.assertIn('Unavailable', board)
        self.assertIn('unavailable', S.format_rank_message(table[1]['snapshot']).lower())

    def test_prompt_block_prefers_match_stats_never_lifetime(self):
        player = next(p for p in self.store.roster() if p['id'] == 'rank-a')
        _win(self.store)
        snap = S.snapshot_for_player(self.store, player, 'warzone', 'resurgence', 'lifetime')
        block = S.prompt_stat_block(snap, {'kills': 19, 'deaths': 8, 'result': 'Victory'})
        self.assertIn('Solid', block)
        self.assertIn('2.38', block)
        self.assertIn('Victory', block)
        self.assertEqual(S.prompt_stat_block({'rank': None}), '')


if __name__ == '__main__':
    unittest.main()
