from __future__ import annotations

import sqlite3
import threading
from pathlib import Path


class Database:
    """SQLite database wrapper with per-thread connection caching.

    Each thread gets its own persistent connection (WAL mode allows concurrent
    readers + one writer).  Re-using the same connection avoids the overhead of
    opening a new file handle on every repository call — the most common perf
    bottleneck in a heavily-queried desktop app.
    """

    def __init__(self, database_path: str) -> None:
        self.database_path = Path(database_path)
        self._local = threading.local()

    def connect(self) -> sqlite3.Connection:
        conn: sqlite3.Connection | None = getattr(self._local, "connection", None)
        if conn is None:
            conn = sqlite3.connect(str(self.database_path))
            conn.row_factory = sqlite3.Row
            # WAL is persistent after the first set — only needs to be applied once
            # per new file handle (i.e. once per thread).
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout = 5000")
            conn.execute("PRAGMA cache_size = -8000")   # 8 MB page cache per connection
            self._local.connection = conn
        return conn

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        schema_path = Path(__file__).with_name("schema.sql")
        with self.connect() as conn:
            conn.executescript(schema_path.read_text(encoding="utf-8"))
            # Migration: add purchase_price column if missing (pre-existing DBs)
            cols = {r[1] for r in conn.execute("PRAGMA table_info(collection_entries)").fetchall()}
            if "purchase_price" not in cols:
                conn.execute("ALTER TABLE collection_entries ADD COLUMN purchase_price REAL")
            conn.commit()
