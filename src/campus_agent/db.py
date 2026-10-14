from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path


def dumps(value):
    return json.dumps(value, ensure_ascii=False, default=str)


class Store:
    """Each service owns its database. Transactions use independent connections."""
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as con:
            con.execute('PRAGMA journal_mode=WAL')
            con.execute('CREATE TABLE IF NOT EXISTS kv (kind TEXT, id TEXT, value TEXT NOT NULL, PRIMARY KEY(kind,id))')

    @contextmanager
    def connect(self):
        con = sqlite3.connect(self.path, timeout=15)
        con.row_factory = sqlite3.Row
        try:
            yield con
            con.commit()
        except BaseException:
            con.rollback()
            raise
        finally:
            con.close()

    def get(self, kind, key, default=None, con=None):
        if con is None:
            with self.connect() as db:
                return self.get(kind, key, default, db)
        row = con.execute('SELECT value FROM kv WHERE kind=? AND id=?', (kind, key)).fetchone()
        return json.loads(row['value']) if row else default

    def put(self, kind, key, value, con=None):
        if con is None:
            with self.connect() as db:
                return self.put(kind, key, value, db)
        con.execute('INSERT INTO kv VALUES(?,?,?) ON CONFLICT(kind,id) DO UPDATE SET value=excluded.value',
                    (kind, key, dumps(value)))

    def all(self, kind, con=None):
        if con is None:
            with self.connect() as db:
                return self.all(kind, db)
        return [json.loads(r['value']) for r in con.execute('SELECT value FROM kv WHERE kind=? ORDER BY rowid', (kind,))]

    def remove(self, kind, key):
        with self.connect() as db:
            db.execute('DELETE FROM kv WHERE kind=? AND id=?', (kind, key))
