"""Store migration tests (SINGLE_EXE_DESIGN section 8.2, Phase 1 scope)."""
import json
import tempfile
import unittest
from pathlib import Path

import engine as E


def tmp_store():
    root = Path(tempfile.mkdtemp(prefix="autotrack-store-"))
    return E.Store(root / "history.sqlite3"), root


class AutotrackStoreTests(unittest.TestCase):
    def test_fresh_db_creates_autotrack_tables(self):
        store, _root = tmp_store()
        with store.connect() as c:
            tables = {row[0] for row in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in ("lifetime_snapshots", "camo_detections", "tracking_feed", "sync_outbox"):
            self.assertIn(table, tables)

    def test_legacy_db_opens_clean_and_gains_tables(self):
        import sqlite3
        root = Path(tempfile.mkdtemp(prefix="legacy-"))
        path = root / "history.sqlite3"
        conn = sqlite3.connect(path)
        # Realistic pre-Phase-1 schema: full matches table, no autotrack tables.
        conn.execute("CREATE TABLE matches(id TEXT PRIMARY KEY, payload TEXT NOT NULL, status TEXT NOT NULL, created REAL, updated REAL, capture TEXT DEFAULT '', ocr TEXT DEFAULT '', players TEXT DEFAULT '[]', theme TEXT DEFAULT '', prompt TEXT DEFAULT '', image TEXT DEFAULT '', attempts INTEGER DEFAULT 0, error TEXT DEFAULT '', message_id TEXT DEFAULT '', artwork TEXT DEFAULT '', poster TEXT DEFAULT '', gen_state TEXT DEFAULT 'idle', gen_attempts INTEGER DEFAULT 0)")
        conn.execute("INSERT INTO matches(id,payload,status,created,updated) VALUES(?,?,?,?,?)", ("legacy-1", "{}", "needs_review", 1.0, 1.0))
        conn.commit()
        conn.close()
        store = E.Store(path)
        self.assertEqual(len(store.history()), 1)
        with store.connect() as c:
            tables = {row[0] for row in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in ("lifetime_snapshots", "camo_detections", "tracking_feed", "sync_outbox"):
            self.assertIn(table, tables)

    def test_after_action_and_poll_collapse_to_one_row(self):
        store, _root = tmp_store()
        after_action = {"modeKey": "team_deathmatch", "result": "win", "scoreOwn": 100,
                        "scoreOpp": 88, "playedAt": "2026-11-07T10:00:00Z"}
        poll = {"modeKey": "team_deathmatch", "result": "win", "scoreOwn": 100,
                "scoreOpp": 88, "playedAt": "2026-11-07T10:00:00Z", "matchId": "codmunity-source-id"}
        mid, added = store.add(after_action, "needs_review")
        self.assertTrue(added)
        mid2, added2 = store.add(poll, "needs_review")
        self.assertEqual(mid, mid2)
        self.assertFalse(added2)
        self.assertEqual(len(store.history()), 1)

    def test_autotrack_log_tail_is_capped(self):
        store, _root = tmp_store()
        for index in range(250):
            store.log_autotrack(f"line {index}")
        tail = store.autotrack_log_tail()
        self.assertEqual(len(tail), 200)
        self.assertIn("line 249", tail[-1])
        self.assertIn("line 50", tail[0])

    def test_enqueue_sync_roundtrip(self):
        store, _root = tmp_store()
        mid, _ = store.add({"matchId": "sync-1", "modeKey": "resurgence", "result": "win"}, "needs_review")
        outbox_id = store.enqueue_sync("discord_match", mid)
        pending = store.pending_sync()
        self.assertEqual([row["id"] for row in pending], [outbox_id])
        self.assertEqual(pending[0]["kind"], "discord_match")
        self.assertTrue(store.mark_sync(outbox_id, "delivered"))
        self.assertEqual(store.pending_sync(), [])

    def test_lifetime_and_camo_roundtrip(self):
        store, _root = tmp_store()
        snap_id = store.save_lifetime_snapshot({"game": "mw4", "mode": "multiplayer", "scope": "lifetime",
                                                "kd_num": 10051, "kd_den": 9083, "wins": 187,
                                                "losses": 278, "games": 478, "raw": "K/D 10051 / 9083"})
        self.assertEqual(store.lifetime_snapshots()[0]["id"], snap_id)
        det_id = store.save_camo_detection({"weapon": "assault rifle", "camo": "MIDNIGHT",
                                            "grind": "mw4", "state": "saved",
                                            "changed": [["MCX", "assault rifle", "MCX-MIDNIGHT"]],
                                            "read": ["CHALLENGE", "ASSAULT RIFLE"]})
        self.assertEqual(store.camo_detections()[0]["id"], det_id)
        feed_id = store.record_tracking_feed("record", "saved", "", {"snapshot_id": snap_id})
        self.assertTrue(feed_id)
        self.assertEqual(store.tracking_feed()[0]["kind"], "record")


if __name__ == "__main__":
    unittest.main()
