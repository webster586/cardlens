from __future__ import annotations

import datetime as dt
from typing import Any

from src.pokemon_scanner.db.database import Database

# Skip PRAGMA table_info once a table has been migrated in this process lifetime
_schema_checked: set[str] = set()


class CollectionRepository:
    def __init__(self, database: Database) -> None:
        self.database = database
        self._migrate()

    def _migrate(self) -> None:
        """Add api_id column if it doesn't exist yet, then backfill from card_catalog."""
        if "collection_entries" in _schema_checked:
            return
        with self.database.connect() as conn:
            cols = [row[1] for row in conn.execute("PRAGMA table_info(collection_entries)").fetchall()]
            if "api_id" not in cols:
                conn.execute("ALTER TABLE collection_entries ADD COLUMN api_id TEXT")
            if "condition" not in cols:
                conn.execute("ALTER TABLE collection_entries ADD COLUMN condition TEXT DEFAULT 'NM'")
            if "album_page" not in cols:
                conn.execute("ALTER TABLE collection_entries ADD COLUMN album_page TEXT DEFAULT ''")
            # Backfill api_id for existing rows that are missing it, by matching
            # name + set_name + card_number against card_catalog (same DB file).
            catalog_exists = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='card_catalog'"
            ).fetchone()
            if catalog_exists:
                conn.execute("""
                    UPDATE collection_entries
                    SET api_id = (
                        SELECT c.api_id FROM card_catalog c
                        WHERE LOWER(TRIM(c.name))        = LOWER(TRIM(collection_entries.name))
                          AND LOWER(TRIM(c.set_name))    = LOWER(TRIM(IFNULL(collection_entries.set_name, '')))
                          AND LOWER(TRIM(c.card_number)) = LOWER(TRIM(IFNULL(collection_entries.card_number, '')))
                        ORDER BY c.updated_at DESC
                        LIMIT 1
                    )
                    WHERE api_id IS NULL
                """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_ce_api_id ON collection_entries(api_id)"
            )
            conn.commit()
        _schema_checked.add("collection_entries")

    def upsert_by_identity(
        self,
        *,
        api_id: str | None = None,
        name: str,
        set_name: str,
        card_number: str,
        language: str,
        last_price: float | None,
        price_currency: str | None,
        notes: str = "",
        image_path: str | None = None,
        condition: str = "NM",
        album_page: str = "",
    ) -> None:
        now = dt.datetime.utcnow().isoformat()
        with self.database.connect() as conn:
            # 1) Match by api_id (strongest identity signal — same card regardless of language field)
            existing = None
            if api_id:
                existing = conn.execute(
                    "SELECT id, quantity FROM collection_entries WHERE api_id = ? LIMIT 1",
                    (api_id,),
                ).fetchone()

            # 2) Fallback: match by name + set + number + language
            if existing is None:
                existing = conn.execute(
                    '''
                    SELECT id, quantity
                    FROM collection_entries
                    WHERE name = ? AND IFNULL(set_name, '') = ? AND IFNULL(card_number, '') = ? AND IFNULL(language, '') = ?
                    ''',
                    (name, set_name or "", card_number or "", language or ""),
                ).fetchone()

            if existing:
                conn.execute(
                    '''
                    UPDATE collection_entries
                    SET quantity = ?, last_price = ?, price_currency = ?, notes = ?, image_path = ?, api_id = COALESCE(api_id, ?), album_page = COALESCE(NULLIF(?, ''), album_page), updated_at = ?
                    WHERE id = ?
                    ''',
                    (
                        existing["quantity"] + 1,
                        last_price,
                        price_currency,
                        notes,
                        image_path,
                        api_id,
                        album_page,
                        now,
                        existing["id"],
                    ),
                )
            else:
                conn.execute(
                    '''
                    INSERT INTO collection_entries
                    (api_id, name, set_name, card_number, language, quantity, last_price, price_currency, notes, image_path, condition, album_page, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''',
                    (
                        api_id,
                        name,
                        set_name,
                        card_number,
                        language,
                        1,
                        last_price,
                        price_currency,
                        notes,
                        image_path,
                        condition,
                        album_page,
                        now,
                        now,
                    ),
                )
            conn.commit()

    def update_condition(self, entry_id: int, condition: str) -> None:
        now = dt.datetime.utcnow().isoformat()
        with self.database.connect() as conn:
            conn.execute(
                "UPDATE collection_entries SET condition = ?, updated_at = ? WHERE id = ?",
                (condition, now, entry_id),
            )
            conn.commit()

    def find_by_identity(
        self,
        *,
        name: str,
        set_name: str,
        card_number: str,
        language: str,
        api_id: str | None = None,
    ) -> dict[str, Any] | None:
        with self.database.connect() as conn:
            if api_id:
                row = conn.execute(
                    "SELECT id, api_id, name, set_name, card_number, language, quantity, last_price, price_currency, condition"
                    " FROM collection_entries WHERE api_id = ? LIMIT 1",
                    (api_id,),
                ).fetchone()
                if row:
                    return dict(row)
            row = conn.execute(
                '''
                SELECT id, api_id, name, set_name, card_number, language, quantity, last_price, price_currency, condition
                FROM collection_entries
                WHERE name = ?
                  AND IFNULL(set_name, '')     = ?
                  AND IFNULL(card_number, '')  = ?
                  AND IFNULL(language, '')     = ?
                LIMIT 1
                ''',
                (name, set_name, card_number, language),
            ).fetchone()
        return dict(row) if row else None

    def list_all(self) -> list[dict[str, Any]]:
        with self.database.connect() as conn:
            rows = conn.execute(
                '''
                SELECT id, api_id, name, set_name, card_number, language, quantity, last_price, price_currency, notes, image_path, condition, created_at, updated_at
                FROM collection_entries
                ORDER BY updated_at DESC, id DESC
                '''
            ).fetchall()
        return [dict(row) for row in rows]

    def get_owned_lookup(self) -> dict[str, dict]:
        """Returns {api_id: row} for all owned cards that have an api_id."""
        with self.database.connect() as conn:
            rows = conn.execute(
                "SELECT id, api_id, name, set_name, card_number, last_price, price_currency, quantity"
                " FROM collection_entries WHERE api_id IS NOT NULL"
            ).fetchall()
        return {row["api_id"]: dict(row) for row in rows}

    def delete_entry(self, entry_id: int) -> None:
        with self.database.connect() as conn:
            conn.execute("DELETE FROM collection_entries WHERE id = ?", (entry_id,))
            conn.commit()

    def clear_collection(self) -> None:
        """Delete every entry from the collection (factory reset). Catalog is untouched."""
        with self.database.connect() as conn:
            conn.execute("DELETE FROM collection_entries")
            conn.commit()

    def merge_duplicates(self) -> int:
        """Merge duplicate collection entries, summing quantities.

        Two rows are considered duplicates when they share the same api_id
        (strongest signal) OR the same (name, set_name, card_number, language)
        combination.  The row with the lowest id is kept; all others are deleted
        after their quantities have been summed into the survivor.

        Returns the number of duplicate rows removed.
        """
        removed = 0
        with self.database.connect() as conn:
            rows = conn.execute(
                "SELECT id, api_id, name, set_name, card_number, language, quantity "
                "FROM collection_entries ORDER BY id"
            ).fetchall()

            # Group by api_id (non-null) first, then by identity tuple
            seen_api: dict[str, list] = {}
            seen_identity: dict[tuple, list] = {}
            for row in rows:
                row = dict(row)
                if row["api_id"]:
                    seen_api.setdefault(row["api_id"], []).append(row)
                else:
                    key = (
                        row["name"] or "",
                        row["set_name"] or "",
                        row["card_number"] or "",
                        row["language"] or "",
                    )
                    seen_identity.setdefault(key, []).append(row)

            # Also check identity-key duplicates among api_id rows to catch mixed cases
            for row in rows:
                row = dict(row)
                key = (
                    row["name"] or "",
                    row["set_name"] or "",
                    row["card_number"] or "",
                    row["language"] or "",
                )
                seen_identity.setdefault(key, []).append(row)

            def _merge_group(group: list[dict]) -> None:
                nonlocal removed
                if len(group) <= 1:
                    return
                # Keep the row with the smallest id, sum all quantities
                group.sort(key=lambda r: r["id"])
                keeper = group[0]
                total_qty = sum(r["quantity"] or 1 for r in group)
                ids_to_delete = [r["id"] for r in group[1:]]
                conn.execute(
                    "UPDATE collection_entries SET quantity = ? WHERE id = ?",
                    (total_qty, keeper["id"]),
                )
                for did in ids_to_delete:
                    conn.execute("DELETE FROM collection_entries WHERE id = ?", (did,))
                removed += len(ids_to_delete)

            already_merged: set = set()

            for group in seen_api.values():
                ids = frozenset(r["id"] for r in group)
                if ids not in already_merged:
                    _merge_group(group)
                    already_merged.add(ids)

            for group in seen_identity.values():
                # De-duplicate across the two passes (same rows might appear in both dicts)
                unique = {r["id"]: r for r in group}
                deduped = list(unique.values())
                ids = frozenset(r["id"] for r in deduped)
                if ids not in already_merged:
                    _merge_group(deduped)
                    already_merged.add(ids)

            conn.commit()
        return removed

    def set_api_id(self, entry_id: int, api_id: str) -> None:
        with self.database.connect() as conn:
            conn.execute("UPDATE collection_entries SET api_id = ? WHERE id = ?", (api_id, entry_id))
            conn.commit()

    def create_scan_event(
        self,
        *,
        image_path: str,
        selected_candidate_name: str,
        selected_candidate_set: str,
        selected_candidate_number: str,
        selected_candidate_language: str,
        confidence: float,
    ) -> None:
        now = dt.datetime.utcnow().isoformat()
        with self.database.connect() as conn:
            conn.execute(
                '''
                INSERT INTO scan_events
                (image_path, selected_candidate_name, selected_candidate_set, selected_candidate_number, selected_candidate_language, confidence, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ''',
                (
                    image_path,
                    selected_candidate_name,
                    selected_candidate_set,
                    selected_candidate_number,
                    selected_candidate_language,
                    confidence,
                    now,
                ),
            )
            conn.commit()


class AlbumPageRepository:
    """Persists album page names keyed by their image file path."""

    def __init__(self, database: Database) -> None:
        self.database = database
        self._migrate()

    def _migrate(self) -> None:
        with self.database.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS album_pages (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    image_path TEXT UNIQUE NOT NULL,
                    name       TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.commit()

    def find_name(self, image_path: str) -> str:
        """Return saved page name for *image_path*, or '' if not found."""
        with self.database.connect() as conn:
            row = conn.execute(
                "SELECT name FROM album_pages WHERE image_path = ? LIMIT 1",
                (image_path,),
            ).fetchone()
        return row["name"] if row else ""

    def save(self, image_path: str, name: str) -> None:
        now = dt.datetime.utcnow().isoformat()
        with self.database.connect() as conn:
            conn.execute(
                """
                INSERT INTO album_pages (image_path, name, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(image_path) DO UPDATE SET name = excluded.name, updated_at = excluded.updated_at
                """,
                (image_path, name, now, now),
            )
            conn.commit()


class OcrCorrectionRepository:
    """Stores user-confirmed OCR corrections for future lookup.

    Maps raw OCR text (and optionally a perceptual image hash) to the
    correct card api_id/name, allowing the recognition pipeline to skip
    repeated API calls for cards the user has previously identified.
    """

    def __init__(self, database: Database) -> None:
        self.database = database
        self._text_cache: dict[str, dict] | None = None  # in-memory top-500 corrections
        self._migrate()

    def _migrate(self) -> None:
        if "ocr_corrections" in _schema_checked:
            return
        with self.database.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS ocr_corrections (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ocr_raw TEXT NOT NULL,
                    image_phash TEXT NOT NULL DEFAULT '',
                    correct_api_id TEXT NOT NULL,
                    correct_name TEXT NOT NULL,
                    correct_set_name TEXT NOT NULL DEFAULT '',
                    correct_card_number TEXT NOT NULL DEFAULT '',
                    used_count INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_ocr_corrections_raw ON ocr_corrections(ocr_raw)"
            )
            # Add new columns to existing databases
            existing_cols = [r[1] for r in conn.execute("PRAGMA table_info(ocr_corrections)").fetchall()]
            if "correct_set_name" not in existing_cols:
                conn.execute("ALTER TABLE ocr_corrections ADD COLUMN correct_set_name TEXT NOT NULL DEFAULT ''")
            if "correct_card_number" not in existing_cols:
                conn.execute("ALTER TABLE ocr_corrections ADD COLUMN correct_card_number TEXT NOT NULL DEFAULT ''")
            conn.commit()
        _schema_checked.add("ocr_corrections")

    def _load_text_cache(self) -> None:
        """Populate in-memory cache of top-500 corrections by used_count."""
        with self.database.connect() as conn:
            rows = conn.execute(
                "SELECT ocr_raw, correct_api_id, correct_name, correct_set_name,"
                " correct_card_number, used_count"
                " FROM ocr_corrections ORDER BY used_count DESC LIMIT 500"
            ).fetchall()
        self._text_cache = {row["ocr_raw"]: dict(row) for row in rows}

    def save_correction(
        self,
        ocr_raw: str,
        correct_api_id: str,
        correct_name: str,
        image_phash: str = "",
        correct_set_name: str = "",
        correct_card_number: str = "",
    ) -> None:
        """Upsert a correction: increment used_count if the same (ocr_raw, api_id) pair exists."""
        now = dt.datetime.utcnow().isoformat()
        with self.database.connect() as conn:
            existing = conn.execute(
                "SELECT id, used_count FROM ocr_corrections WHERE ocr_raw = ? AND correct_api_id = ? LIMIT 1",
                (ocr_raw, correct_api_id),
            ).fetchone()
            if existing:
                conn.execute(
                    """
                    UPDATE ocr_corrections
                    SET used_count = ?,
                        image_phash = CASE WHEN ? != '' THEN ? ELSE image_phash END,
                        correct_set_name = CASE WHEN ? != '' THEN ? ELSE correct_set_name END,
                        correct_card_number = CASE WHEN ? != '' THEN ? ELSE correct_card_number END,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        existing["used_count"] + 1,
                        image_phash, image_phash,
                        correct_set_name, correct_set_name,
                        correct_card_number, correct_card_number,
                        now, existing["id"],
                    ),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO ocr_corrections
                    (ocr_raw, image_phash, correct_api_id, correct_name, correct_set_name,
                     correct_card_number, used_count, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
                    """,
                    (ocr_raw, image_phash, correct_api_id, correct_name,
                     correct_set_name, correct_card_number, now, now),
                )
            conn.commit()
        # Invalidate in-memory cache so next lookup reloads fresh data
        self._text_cache = None

    def find_best_by_text(self, ocr_raw: str, threshold: float = 0.8) -> dict | None:
        """Return the best correction for *ocr_raw* using fuzzy text matching.

        Uses an in-memory cache of top-500 corrections (by used_count) to avoid
        repeated DB round-trips.  Falls back to DB if the cache is empty.
        Rows with higher ``used_count`` are preferred on tie.
        """
        import difflib
        if not ocr_raw:
            return None
        if self._text_cache is None:
            self._load_text_cache()
        rows = self._text_cache or {}
        if not rows:
            return None
        qlen = len(ocr_raw)
        min_len = max(1, int(qlen * 0.5))
        max_len = int(qlen * 2.0)
        ocr_lower = ocr_raw.lower()
        best_ratio = 0.0
        best_row: dict | None = None
        for key, row in rows.items():
            klen = len(key)
            if klen < min_len or klen > max_len:
                continue
            ratio = difflib.SequenceMatcher(None, ocr_lower, key.lower()).ratio()
            if ratio > best_ratio or (
                ratio == best_ratio and best_row is not None
                and row["used_count"] > best_row["used_count"]
            ):
                best_ratio = ratio
                best_row = row
        if best_ratio >= threshold and best_row is not None:
            return best_row
        return None

    def find_best_by_phash(self, phash_str: str, max_distance: int = 10) -> dict | None:
        """Return the best correction by perceptual hash Hamming distance.

        Requires the ``imagehash`` package; silently returns ``None`` if
        unavailable or if no stored hash is within *max_distance*.
        """
        if not phash_str:
            return None
        try:
            import imagehash
        except ImportError:
            return None
        with self.database.connect() as conn:
            rows = conn.execute(
                "SELECT ocr_raw, image_phash, correct_api_id, correct_name, correct_set_name, correct_card_number, used_count "
                "FROM ocr_corrections WHERE image_phash != '' ORDER BY used_count DESC"
            ).fetchall()
        if not rows:
            return None
        try:
            query_hash = imagehash.hex_to_hash(phash_str)
        except Exception:
            return None
        best_dist = max_distance + 1
        best_row: dict | None = None
        for row in rows:
            try:
                stored_hash = imagehash.hex_to_hash(row["image_phash"])
                dist = query_hash - stored_hash
                if dist < best_dist:
                    best_dist = dist
                    best_row = dict(row)
                    if best_dist == 0:
                        break  # perfect hash match — no point checking further
            except Exception:
                continue
        if best_dist <= max_distance and best_row is not None:
            return best_row
        return None

    def list_all(self) -> list[dict]:
        with self.database.connect() as conn:
            rows = conn.execute(
                "SELECT id, ocr_raw, correct_name, correct_api_id, used_count, created_at "
                "FROM ocr_corrections ORDER BY used_count DESC, updated_at DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def delete(self, correction_id: int) -> None:
        with self.database.connect() as conn:
            conn.execute("DELETE FROM ocr_corrections WHERE id = ?", (correction_id,))
            conn.commit()
