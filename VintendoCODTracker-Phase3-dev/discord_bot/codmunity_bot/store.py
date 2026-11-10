from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS registrations(
                    discord_id TEXT PRIMARY KEY, username TEXT NOT NULL, uid TEXT NOT NULL,
                    registered_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, last_success TEXT
                );
                CREATE TABLE IF NOT EXISTS snapshots(
                    uid TEXT PRIMARY KEY, data TEXT NOT NULL, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS matches(
                    uid TEXT NOT NULL, match_key TEXT NOT NULL, mode TEXT, played_at TEXT,
                    data TEXT NOT NULL, posted_at TEXT, PRIMARY KEY(uid, match_key)
                );
                CREATE TABLE IF NOT EXISTS monitor_state(uid TEXT PRIMARY KEY, baseline_done INTEGER NOT NULL DEFAULT 0);
            ''')

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def register(self, discord_id: str, username: str, uid: str) -> None:
        with self._connect() as db:
            db.execute('''INSERT INTO registrations(discord_id,username,uid) VALUES(?,?,?)
                ON CONFLICT(discord_id) DO UPDATE SET username=excluded.username,uid=excluded.uid''',
                (str(discord_id), username, uid))

    def registration(self, discord_id: str):
        with self._connect() as db:
            row = db.execute('SELECT * FROM registrations WHERE discord_id=?', (str(discord_id),)).fetchone()
        return dict(row) if row else None

    def registrations(self):
        with self._connect() as db:
            return [dict(row) for row in db.execute('SELECT * FROM registrations ORDER BY username COLLATE NOCASE')]

    def save_snapshot(self, uid: str, data: dict[str, Any]) -> None:
        with self._connect() as db:
            db.execute('''INSERT INTO snapshots(uid,data) VALUES(?,?) ON CONFLICT(uid) DO UPDATE SET
                data=excluded.data,updated_at=CURRENT_TIMESTAMP''', (uid, json.dumps(data, ensure_ascii=False)))
            db.execute('UPDATE registrations SET last_success=CURRENT_TIMESTAMP WHERE uid=?', (uid,))

    def snapshot(self, uid: str):
        with self._connect() as db:
            row = db.execute('SELECT data FROM snapshots WHERE uid=?', (uid,)).fetchone()
        return json.loads(row['data']) if row else None

    def record_matches(self, uid: str, matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Persist first; first successful poll seeds history and never announces matches."""
        fresh = []
        with self._connect() as db:
            state = db.execute('SELECT baseline_done FROM monitor_state WHERE uid=?', (uid,)).fetchone()
            is_baseline = state is None or not state['baseline_done']
            for item in matches:
                key = match_key(item)
                cur = db.execute('INSERT OR IGNORE INTO matches(uid,match_key,mode,played_at,data) VALUES(?,?,?,?,?)',
                    (uid, key, str(item.get('mode') or item.get('gameMode') or ''),
                     str(item.get('playedAt') or item.get('date') or ''), json.dumps(item, ensure_ascii=False)))
                if cur.rowcount and not is_baseline:
                    fresh.append(item)
            db.execute('INSERT INTO monitor_state(uid,baseline_done) VALUES(?,1) ON CONFLICT(uid) DO UPDATE SET baseline_done=1', (uid,))
        return fresh

    def recent(self, uid: str, limit: int = 10):
        with self._connect() as db:
            rows = db.execute('SELECT data FROM matches WHERE uid=? ORDER BY played_at DESC,rowid DESC LIMIT ?',
                              (uid, max(1, min(limit, 25)))).fetchall()
        return [json.loads(row['data']) for row in rows]


def match_key(item: dict[str, Any]) -> str:
    for key in ('id', 'matchId', 'gameId', 'uuid'):
        if item.get(key) is not None:
            return f"id:{item[key]}"
    # Include all stable fields available so distinct partial records do not collide casually.
    return 'data:' + json.dumps(item, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
