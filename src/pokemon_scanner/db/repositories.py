from __future__ import annotations

import datetime as dt
from typing import Any

from src.pokemon_scanner.db.database import Database


class CollectionRepository:
    def __init__(self, database: Database) -> None:
        self.database = database
        self._migrate()

    def _migrate(self) -> None:
        """Add api_id column if it doesn't exist yet, then backfill from card_catalog."""
        with self.database.connect() as conn:
            cols = [row[1] for row in conn.execute("PRAGMA table_info(collection_entries)").fetchall()]
            if "api_id" not in cols:
                conn.execute("ALTER TABLE collection_entries ADD COLUMN api_id TEXT")
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
                    SET quantity = ?, last_price = ?, price_currency = ?, notes = ?, image_path = ?, api_id = COALESCE(api_id, ?), updated_at = ?
                    WHERE id = ?
                    ''',
                    (
                        existing["quantity"] + 1,
                        last_price,
                        price_currency,
                        notes,
                        image_path,
                        api_id,
                        now,
                        existing["id"],
                    ),
                )
            else:
                conn.execute(
                    '''
                    INSERT INTO collection_entries
                    (api_id, name, set_name, card_number, language, quantity, last_price, price_currency, notes, image_path, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        now,
                        now,
                    ),
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
                    "SELECT id, api_id, name, set_name, card_number, language, quantity, last_price, price_currency"
                    " FROM collection_entries WHERE api_id = ? LIMIT 1",
                    (api_id,),
                ).fetchone()
                if row:
                    return dict(row)
            row = conn.execute(
                '''
                SELECT id, api_id, name, set_name, card_number, language, quantity, last_price, price_currency
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
                SELECT id, api_id, name, set_name, card_number, language, quantity, last_price, price_currency, notes, image_path, created_at, updated_at
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
