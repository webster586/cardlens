from __future__ import annotations

import sqlite3
from pathlib import Path


class Database:
    def __init__(self, database_path: str) -> None:
        self.database_path = Path(database_path)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout = 5000")
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
