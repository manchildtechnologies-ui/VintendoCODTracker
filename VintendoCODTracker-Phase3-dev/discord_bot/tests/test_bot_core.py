import asyncio
import json
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from codmunity_bot.client import CODMunityClient, CODMunityError, RateLimited
from codmunity_bot.decoder import DecodeError, decode_typed, extract_result
from codmunity_bot.stats import leaderboard_rows, match_summary, profile_modes, stat_lines
from codmunity_bot.store import Store
from codmunity_bot.validation import valid_uid, valid_username


def obj(**values):
    return {'t': 10, 'i': 0, 'p': {'k': list(values), 'v': list(values.values())}, 'o': 0}


def string(value):
    return {'t': 1, 's': value}


class DecoderTests(unittest.TestCase):
    def test_decodes_nested_data_and_preserves_null_and_missing(self):
        decoded = extract_result(obj(result=string('ok'), lifetime=obj(kills=17), absent={'t': 0}))
        self.assertEqual(decoded['result'], 'ok')
        self.assertEqual(decoded['lifetime']['kills'], 17)
        self.assertIsNone(decoded['absent'])
        self.assertNotIn('not_in_response', decoded)

    def test_array_and_empty_response(self):
        self.assertEqual(decode_typed({'t': 9, 'p': [string('a'), string('b')]}), ['a', 'b'])
        self.assertEqual(decode_typed({'t': 9, 'p': {'a': [string('a')]}}), ['a'])
        self.assertEqual(extract_result(obj()), {})
        with self.assertRaises(DecodeError):
            extract_result([])

    def test_handles_malformed_object_without_fabricating_stats(self):
        result = decode_typed({'t': 10, 'p': {'k': ['kills'], 'v': []}})
        self.assertEqual(result, {})


class ClientTests(unittest.TestCase):
    def test_payload_supports_independent_uids(self):
        one = CODMunityClient.payload('alpha12345')
        two = CODMunityClient.payload('beta123456')
        self.assertNotEqual(one, two)
        self.assertEqual(one['p']['v'][0]['p']['v'][0]['s'], 'alpha12345')

    def test_http_failure_and_rate_limit(self):
        client = CODMunityClient(min_interval=0)
        with patch('urllib.request.urlopen', side_effect=urllib.error.HTTPError('u', 403, 'blocked', {}, None)):
            with self.assertRaisesRegex(CODMunityError, 'HTTP 403'):
                asyncio.run(client.get_player('alpha12345'))
        with patch('urllib.request.urlopen', side_effect=urllib.error.HTTPError('u', 429, 'slow', {}, None)):
            with self.assertRaises(RateLimited):
                asyncio.run(client.get_player('beta123456'))

    def test_cache_avoids_repeat_request(self):
        client = CODMunityClient(min_interval=0)
        with patch.object(client, '_fetch', return_value={'lifetime': {'kills': 2}}) as fetch:
            first = asyncio.run(client.get_player('alpha12345'))
            second = asyncio.run(client.get_player('alpha12345'))
        self.assertEqual(first, second)
        fetch.assert_called_once()


class StoreAndStatsTests(unittest.TestCase):
    def setUp(self):
        self.db_files = []

    def tearDown(self):
        for path in self.db_files:
            for suffix in ('', '-journal', '-wal', '-shm'):
                candidate = Path(str(path) + suffix)
                if candidate.exists():
                    candidate.unlink()

    def new_db(self, name):
        path = Path(__file__).resolve().parents[3] / name
        self.db_files.append(path)
        if path.exists():
            path.unlink()
        return path

    def test_registration_validation_and_persistence(self):
        self.assertTrue(valid_username('Player One'))
        self.assertFalse(valid_username(' '))
        self.assertTrue(valid_uid('Abcd123456'))
        self.assertFalse(valid_uid('bad uid'))
        path = self.new_db('test-bot-registration.sqlite3')
        store = Store(path)
        store.register('discord-1', 'Player One', 'Abcd123456')
        self.assertEqual(Store(path).registration('discord-1')['uid'], 'Abcd123456')

    def test_duplicate_matches_and_initial_baseline(self):
        store = Store(self.new_db('test-bot-duplicates.sqlite3'))
        items = [{'id': 'match-a', 'mode': 'resurgence', 'stats': {'kills': 4}}]
        self.assertEqual(store.record_matches('uid1', items), [])  # baseline is never advertised
        self.assertEqual(store.record_matches('uid1', items), [])
        newer = [{'id': 'match-b', 'mode': 'battle_royale'}]
        self.assertEqual(store.record_matches('uid1', newer), newer)
        self.assertEqual(store.record_matches('uid1', newer), [])
        self.assertEqual(len(store.recent('uid1')), 2)
        partial = [{'mode': 'multiplayer'}, {'mode': 'resurgence'}]
        self.assertEqual(store.record_matches('uid2', partial), [])
        self.assertEqual(len(store.recent('uid2')), 2)

    def test_leaderboard_omits_missing_and_sorts(self):
        regs = [{'uid': 'a', 'username': 'A'}, {'uid': 'b', 'username': 'B'}, {'uid': 'c', 'username': 'C'}]
        data = {'a': {'lifetime': {'kills': 2}}, 'b': {'lifetime': {'kills': 7}}, 'c': {'lifetime': {}}}
        self.assertEqual(leaderboard_rows(regs, data, 'Multiplayer', 'kills'), [(7, 'B'), (2, 'A')])
        self.assertEqual(leaderboard_rows(regs, data, 'Resurgence', 'kills'), [])

    def test_missing_statistics_and_partial_match_formatting(self):
        self.assertEqual(stat_lines({}), [])
        profile = {'warzoneLifetime': {'br': {'wins': 4}, 'resurgence': {'kills': 10}}}
        self.assertEqual(set(profile_modes(profile)), {'Battle Royale', 'Resurgence'})
        summary = match_summary({'mode': 'resurgence', 'stats': {'kills': 5}})
        self.assertIn('Kills 5', summary)
        self.assertIn('date unavailable', summary)


if __name__ == '__main__':
    unittest.main()
