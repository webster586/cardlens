from __future__ import annotations

import datetime as dt
from typing import Any

from src.pokemon_scanner.db.database import Database


class CollectionRepository:
    def __init__(self, database: Database) -> None:
        self.database = database
        self._schema_initialized: bool = False
        self._migrate()

    def _migrate(self) -> None:
        """Add api_id column if it doesn't exist yet, then backfill from card_catalog."""
        if self._schema_initialized:
            return
        with self.database.connect() as conn:
            cols = [row[1] for row in conn.execute("PRAGMA table_info(collection_entries)").fetchall()]
            if "api_id" not in cols:
                conn.execute("ALTER TABLE collection_entries ADD COLUMN api_id TEXT")
            if "condition" not in cols:
                conn.execute("ALTER TABLE collection_entries ADD COLUMN condition TEXT DEFAULT 'NM'")
            if "album_page" not in cols:
                conn.execute("ALTER TABLE collection_entries ADD COLUMN album_page TEXT DEFAULT ''")
            if "is_foil" not in cols:
                conn.execute("ALTER TABLE collection_entries ADD COLUMN is_foil INTEGER DEFAULT 0")
            if "finish" not in cols:
                conn.execute("ALTER TABLE collection_entries ADD COLUMN finish TEXT DEFAULT ''")
                # Back-fill: old holo entries get finish = 'holo'
                conn.execute(
                    "UPDATE collection_entries SET finish = 'holo' WHERE is_foil = 1 AND (finish IS NULL OR finish = '')"
                )
            if "sale_status" not in cols:
                conn.execute("ALTER TABLE collection_entries ADD COLUMN sale_status TEXT")
            if "sale_price" not in cols:
                conn.execute("ALTER TABLE collection_entries ADD COLUMN sale_price REAL")
            if "sale_listed_at" not in cols:
                conn.execute("ALTER TABLE collection_entries ADD COLUMN sale_listed_at TEXT")
            if "sale_sold_at" not in cols:
                conn.execute("ALTER TABLE collection_entries ADD COLUMN sale_sold_at TEXT")
            if "price_alert" not in cols:
                conn.execute("ALTER TABLE collection_entries ADD COLUMN price_alert REAL")
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
            # Compound index for the identity-based fallback lookup used by
            # upsert_by_identity() and find_by_identity() when api_id is absent.
            conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_ce_identity"
                " ON collection_entries(name, set_name, card_number, language)"
            )
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sale_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    collection_entry_id INTEGER NOT NULL,
                    api_id TEXT,
                    name TEXT NOT NULL,
                    set_name TEXT,
                    card_number TEXT,
                    language TEXT,
                    condition TEXT,
                    standort TEXT,
                    image_path TEXT,
                    sale_price REAL,
                    shipping_cost REAL,
                    platform TEXT,
                    buyer_note TEXT,
                    purchase_price REAL,
                    market_price_at_sale REAL,
                    sale_listed_at TEXT,
                    sale_date TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)
            # ── Gesamtwert-Verlauf ─────────────────────────────────────────
            conn.execute("""
                CREATE TABLE IF NOT EXISTS collection_value_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    snapshot_date TEXT NOT NULL UNIQUE,
                    total_value REAL NOT NULL,
                    unique_cards INTEGER NOT NULL,
                    total_qty INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_cvs_date"
                " ON collection_value_snapshots(snapshot_date)"
            )
            # ── Katalog-Wunschpreise ───────────────────────────────────────
            conn.execute("""
                CREATE TABLE IF NOT EXISTS catalog_watch (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    api_id TEXT NOT NULL UNIQUE,
                    wish_price REAL NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_cw_api_id ON catalog_watch(api_id)"
            )
            # Clean up dangling album_slots (collection_entry was deleted but slot remained)
            conn.execute("""
                DELETE FROM album_slots
                WHERE collection_entry_id IS NOT NULL
                  AND collection_entry_id NOT IN (SELECT id FROM collection_entries)
            """)
            conn.commit()
        self._schema_initialized = True

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

    def update_entry(
        self,
        entry_id: int,
        *,
        quantity: int,
        language: str,
        condition: str,
        finish: str = "",
        notes: str,
        album_page: str,
        purchase_price: float | None = None,
    ) -> None:
        now = dt.datetime.utcnow().isoformat()
        with self.database.connect() as conn:
            conn.execute(
                """
                UPDATE collection_entries
                SET quantity = ?, language = ?, condition = ?, finish = ?,
                    is_foil = ?, notes = ?, album_page = ?, purchase_price = ?, updated_at = ?
                WHERE id = ?
                """,
                (quantity, language, condition, finish,
                 1 if finish else 0,
                 notes, album_page,
                 purchase_price if purchase_price else None, now, entry_id),
            )
            conn.commit()

    def update_album_page(self, entry_id: int, album_page: str) -> None:
        """Overwrite only the album_page field for a collection entry."""
        now = dt.datetime.utcnow().isoformat()
        with self.database.connect() as conn:
            conn.execute(
                "UPDATE collection_entries SET album_page = ?, updated_at = ? WHERE id = ?",
                (album_page, now, entry_id),
            )
            conn.commit()

    def get_entry(self, entry_id: int) -> dict | None:
        with self.database.connect() as conn:
            row = conn.execute(
                "SELECT id, api_id, name, set_name, card_number, language, quantity,"
                " last_price, purchase_price, price_currency, notes, image_path, condition, album_page,"
                " is_foil, finish, created_at, updated_at"
                " FROM collection_entries WHERE id = ?",
                (entry_id,),
            ).fetchone()
        return dict(row) if row else None

    def get_entries_by_api_id(self, api_id: str) -> list[dict]:
        """Return all collection entries that share the given api_id, ordered by id."""
        with self.database.connect() as conn:
            rows = conn.execute(
                "SELECT id, api_id, name, set_name, card_number, language, quantity,"
                " last_price, purchase_price, price_currency, notes, image_path, condition, album_page,"
                " is_foil, finish, created_at, updated_at"
                " FROM collection_entries WHERE api_id = ? ORDER BY id ASC",
                (api_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_or_create_entry_by_api_id(
        self,
        *,
        api_id: str,
        name: str,
        set_name: str,
        card_number: str,
        image_path: str | None,
        language: str = "en",
    ) -> int | None:
        """Return existing collection entry id for api_id, or create one (qty=1, NM)."""
        with self.database.connect() as conn:
            row = conn.execute(
                "SELECT id FROM collection_entries WHERE api_id = ? LIMIT 1",
                (api_id,),
            ).fetchone()
            if row:
                return row["id"]
            now = dt.datetime.utcnow().isoformat()
            cur = conn.execute(
                """INSERT INTO collection_entries
                   (api_id, name, set_name, card_number, language, quantity,
                    condition, image_path, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, 1, 'NM', ?, ?, ?)""",
                (api_id, name, set_name, card_number, language, image_path, now, now),
            )
            conn.commit()
            return cur.lastrowid

    def split_entry(self, entry_id: int) -> list[int]:
        """Split a quantity-N entry into N separate entries with quantity 1 each.

        Returns the list of all entry IDs (original first, then newly created ones).
        If quantity <= 1 the entry is unchanged and [entry_id] is returned.
        """
        row = self.get_entry(entry_id)
        if not row:
            return [entry_id]
        qty = int(row.get("quantity") or 1)
        if qty <= 1:
            return [entry_id]
        now = dt.datetime.utcnow().isoformat()
        new_ids: list[int] = [entry_id]
        with self.database.connect() as conn:
            conn.execute(
                "UPDATE collection_entries SET quantity = 1, updated_at = ? WHERE id = ?",
                (now, entry_id),
            )
            for _ in range(qty - 1):
                cursor = conn.execute(
                    "INSERT INTO collection_entries"
                    " (api_id, name, set_name, card_number, language, quantity,"
                    "  last_price, price_currency, notes, image_path, condition,"
                    "  album_page, is_foil, finish, created_at, updated_at)"
                    " VALUES (?,?,?,?,?,1,?,?,?,?,?,?,?,?,?,?)",
                    (
                        row["api_id"], row["name"], row["set_name"],
                        row["card_number"], row["language"],
                        row["last_price"], row["price_currency"],
                        row["notes"], row["image_path"],
                        row["condition"], row.get("album_page") or "",
                        int(row.get("is_foil") or 0),
                        row.get("finish") or "",
                        now, now,
                    ),
                )
                new_ids.append(cursor.lastrowid)
            conn.commit()
        return new_ids

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
                SELECT id, api_id, name, set_name, card_number, language, quantity, last_price, price_currency, notes, image_path, condition, is_foil, finish, album_page, created_at, updated_at
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
            conn.execute("DELETE FROM album_slots WHERE collection_entry_id = ?", (entry_id,))
            conn.execute("DELETE FROM collection_entries WHERE id = ?", (entry_id,))
            conn.commit()

    def clear_image_paths(self) -> None:
        """Clear the image_path column for all entries (e.g. after deleting scan photos)."""
        with self.database.connect() as conn:
            conn.execute("UPDATE collection_entries SET image_path = NULL WHERE image_path IS NOT NULL")
            conn.commit()

    def clear_collection(self) -> None:
        """Delete every entry from the collection (factory reset). Catalog is untouched."""
        with self.database.connect() as conn:
            conn.execute("DELETE FROM album_slots")
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
                    # Reassign album slots to the keeper before deleting the duplicate,
                    # so no slots become dangling after the merge.
                    conn.execute(
                        "UPDATE album_slots SET collection_entry_id = ? "
                        "WHERE collection_entry_id = ?",
                        (keeper["id"], did),
                    )
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

    def set_quantity(self, entry_id: int, quantity: int) -> None:
        now = dt.datetime.utcnow().isoformat()
        with self.database.connect() as conn:
            conn.execute(
                "UPDATE collection_entries SET quantity = ?, updated_at = ? WHERE id = ?",
                (quantity, now, entry_id),
            )
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


    # ------------------------------------------------------------------
    # Market / Verkauf
    # ------------------------------------------------------------------

    def set_for_sale(self, entry_id: int, price: float) -> None:
        """Mark an entry as for-sale with the given asking price."""
        now = dt.datetime.utcnow().isoformat()
        with self.database.connect() as conn:
            conn.execute(
                "UPDATE collection_entries SET sale_status='for_sale', sale_price=?, "
                "sale_listed_at=?, updated_at=? WHERE id=?",
                (price, now, now, entry_id),
            )
            conn.commit()

    def mark_sold(self, entry_id: int) -> None:
        """Transition an entry from for_sale → sold and record the sale in sale_history."""
        now = dt.datetime.utcnow().isoformat()
        with self.database.connect() as conn:
            row = conn.execute(
                "SELECT ce.*, a.name AS standort "
                "FROM collection_entries ce "
                "LEFT JOIN album_slots sl ON sl.collection_entry_id = ce.id "
                "LEFT JOIN albums a ON a.id = sl.album_id "
                "WHERE ce.id = ? LIMIT 1",
                (entry_id,),
            ).fetchone()
            if row:
                r = dict(row)
                conn.execute(
                    """
                    INSERT INTO sale_history
                        (collection_entry_id, api_id, name, set_name, card_number, language,
                         condition, standort, image_path, sale_price, shipping_cost, platform,
                         buyer_note, purchase_price, market_price_at_sale, sale_listed_at,
                         sale_date, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, ?, ?, ?, ?, ?)
                    """,
                    (
                        entry_id,
                        r.get("api_id"),
                        r.get("name") or "",
                        r.get("set_name"),
                        r.get("card_number"),
                        r.get("language"),
                        r.get("condition"),
                        r.get("standort"),
                        r.get("image_path"),
                        r.get("sale_price"),
                        r.get("purchase_price"),
                        r.get("last_price"),
                        r.get("sale_listed_at"),
                        now,
                        now,
                    ),
                )
            conn.execute(
                "UPDATE collection_entries SET sale_status='sold', sale_sold_at=?, "
                "updated_at=? WHERE id=?",
                (now, now, entry_id),
            )
            conn.commit()

    def remove_listing(self, entry_id: int) -> None:
        """Cancel a for-sale listing, clearing all sale fields."""
        now = dt.datetime.utcnow().isoformat()
        with self.database.connect() as conn:
            conn.execute(
                "UPDATE collection_entries SET sale_status=NULL, sale_price=NULL, "
                "sale_listed_at=NULL, updated_at=? WHERE id=?",
                (now, entry_id),
            )
            conn.commit()

    def list_all_for_market(self) -> list[dict]:
        """Return all collection entries with sale columns and album location, ordered by name.

        Uses GROUP BY ce.id so that entries placed in multiple album slots are
        never returned as duplicates.  Multiple album names are concatenated.
        """
        with self.database.connect() as conn:
            rows = conn.execute(
                "SELECT ce.id, ce.api_id, ce.name, ce.set_name, ce.card_number, ce.language, "
                "ce.condition, ce.quantity, ce.last_price, ce.price_currency, ce.purchase_price, "
                "ce.image_path, ce.sale_status, ce.sale_price, ce.sale_listed_at, ce.sale_sold_at, "
                "ce.price_alert, "
                "GROUP_CONCAT(DISTINCT a.name) AS standort "
                "FROM collection_entries ce "
                "LEFT JOIN album_slots sl ON sl.collection_entry_id = ce.id "
                "LEFT JOIN albums a ON a.id = sl.album_id "
                "GROUP BY ce.id "
                "ORDER BY ce.name ASC"
            ).fetchall()
        return [dict(row) for row in rows]

    def set_price_alert(self, entry_id: int, threshold: float | None) -> None:
        """Set or clear a price-alert threshold for a collection entry."""
        with self.database.connect() as conn:
            conn.execute(
                "UPDATE collection_entries SET price_alert = ? WHERE id = ?",
                (threshold, entry_id),
            )
            conn.commit()

    def get_collection_stats(self) -> dict:
        """Return aggregate statistics about the collection."""
        with self.database.connect() as conn:
            row = conn.execute(
                """
                SELECT
                    COUNT(*) AS total_cards,
                    COALESCE(SUM(quantity), 0) AS total_quantity,
                    COALESCE(SUM(COALESCE(last_price, 0) * COALESCE(quantity, 1)), 0) AS total_value,
                    SUM(CASE WHEN sale_status = 'for_sale' THEN 1 ELSE 0 END) AS for_sale_count,
                    SUM(CASE WHEN sale_status = 'sold' THEN 1 ELSE 0 END) AS sold_count,
                    COALESCE(SUM(CASE WHEN sale_status = 'sold'
                        THEN COALESCE(sale_price, 0) ELSE 0 END), 0) AS sold_revenue
                FROM collection_entries
                """
            ).fetchone()
            stats = dict(row) if row else {}
            profit_row = conn.execute(
                """
                SELECT COALESCE(SUM(
                    sale_price
                    - COALESCE(shipping_cost, 0)
                    - COALESCE(purchase_price, 0)
                ), 0) AS profit
                FROM sale_history
                WHERE purchase_price IS NOT NULL
                """
            ).fetchone()
        stats["estimated_profit"] = float(profit_row["profit"]) if profit_row else 0.0
        return stats

    def get_sold_history(self) -> list[dict]:
        """Return all entries from sale_history, most-recently-sold first."""
        with self.database.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM sale_history ORDER BY sale_date DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def update_sale_history_entry(
        self,
        history_id: int,
        *,
        shipping_cost: float | None,
        platform: str | None,
        buyer_note: str | None,
    ) -> None:
        """Update optional sale details (shipping, platform, buyer note) in sale_history."""
        with self.database.connect() as conn:
            conn.execute(
                "UPDATE sale_history SET shipping_cost=?, platform=?, buyer_note=? WHERE id=?",
                (shipping_cost, platform or None, buyer_note or None, history_id),
            )
            conn.commit()

    # ── Gesamtwert-Verlauf ─────────────────────────────────────────────────

    def record_collection_value_snapshot(self) -> None:
        """Save today's total collection value. Silently ignored if already saved today."""
        today = dt.datetime.utcnow().date().isoformat()
        now = dt.datetime.utcnow().isoformat()
        with self.database.connect() as conn:
            # Check if today already exists
            exists = conn.execute(
                "SELECT 1 FROM collection_value_snapshots WHERE snapshot_date = ?", (today,)
            ).fetchone()
            if exists:
                return
            row = conn.execute(
                """
                SELECT
                    COALESCE(SUM(COALESCE(last_price, 0) * COALESCE(quantity, 1)), 0) AS total_value,
                    COUNT(*) AS unique_cards,
                    COALESCE(SUM(quantity), 0) AS total_qty
                FROM collection_entries
                """
            ).fetchone()
            if row:
                conn.execute(
                    "INSERT OR IGNORE INTO collection_value_snapshots"
                    " (snapshot_date, total_value, unique_cards, total_qty, created_at)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (today, float(row["total_value"]), int(row["unique_cards"]),
                     int(row["total_qty"]), now),
                )
                conn.commit()

    def get_collection_value_history(self) -> list[dict]:
        """Return all value snapshots ordered oldest → newest."""
        with self.database.connect() as conn:
            rows = conn.execute(
                "SELECT snapshot_date, total_value, unique_cards, total_qty"
                " FROM collection_value_snapshots ORDER BY snapshot_date ASC"
            ).fetchall()
        return [dict(r) for r in rows]

    # ── Katalog-Wunschpreise ───────────────────────────────────────────────

    def set_wish_price(self, api_id: str, wish_price: float | None) -> None:
        """Set or clear a wish-price for a catalog card.

        Pass *wish_price=None* to remove the entry.
        """
        now = dt.datetime.utcnow().isoformat()
        with self.database.connect() as conn:
            if wish_price is None:
                conn.execute("DELETE FROM catalog_watch WHERE api_id = ?", (api_id,))
            else:
                conn.execute(
                    """
                    INSERT INTO catalog_watch (api_id, wish_price, created_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(api_id) DO UPDATE SET wish_price = excluded.wish_price
                    """,
                    (api_id, wish_price, now),
                )
            conn.commit()

    def get_watch_entries(self) -> list[dict]:
        """Return all catalog_watch rows as [{api_id, wish_price}, ...]."""
        with self.database.connect() as conn:
            rows = conn.execute(
                "SELECT api_id, wish_price FROM catalog_watch"
            ).fetchall()
        return [dict(r) for r in rows]

    def get_triggered_watch_entries(self) -> list[dict]:
        """Return catalog_watch rows where the current catalog price ≤ wish_price.

        Joins catalog_watch with card_catalog on api_id.
        Result keys: api_id, wish_price, name, set_name, card_number, best_price, eur_price.
        """
        with self.database.connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    cw.api_id,
                    cw.wish_price,
                    cc.name,
                    cc.set_name,
                    cc.card_number,
                    cc.best_price,
                    cc.eur_price
                FROM catalog_watch cw
                JOIN card_catalog cc ON cc.api_id = cw.api_id
                WHERE
                    COALESCE(cc.eur_price, cc.best_price) IS NOT NULL
                    AND COALESCE(cc.eur_price, cc.best_price) <= cw.wish_price
                ORDER BY cc.name ASC
                """
            ).fetchall()
        return [dict(r) for r in rows]

    def get_owned_counts_by_api_id(self) -> dict[str, int]:
        """Return {api_id: total_quantity} for all collection entries with an api_id."""
        with self.database.connect() as conn:
            rows = conn.execute(
                "SELECT api_id, SUM(quantity) AS qty"
                " FROM collection_entries"
                " WHERE api_id IS NOT NULL"
                " GROUP BY api_id"
            ).fetchall()
        return {r["api_id"]: int(r["qty"] or 0) for r in rows}


class AlbumRepository:
    """Manages physical binder albums: albums, pages, and card slots."""

    def __init__(self, database: Database) -> None:
        self.database = database
        self._schema_initialized: bool = False
        self._migrate()

    def _migrate(self) -> None:
        if self._schema_initialized:
            return
        with self.database.connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS albums (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    cols INTEGER NOT NULL DEFAULT 3,
                    rows INTEGER NOT NULL DEFAULT 3,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS album_slots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    album_id INTEGER NOT NULL,
                    page_num INTEGER NOT NULL,
                    slot_index INTEGER NOT NULL,
                    collection_entry_id INTEGER,
                    FOREIGN KEY(album_id) REFERENCES albums(id) ON DELETE CASCADE,
                    UNIQUE(album_id, page_num, slot_index)
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_album_slots_album ON album_slots(album_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_album_slots_entry ON album_slots(collection_entry_id)"
            )
            conn.commit()
        self._schema_initialized = True

    def create_album(self, name: str, cols: int = 3, rows: int = 3) -> int:
        now = dt.datetime.utcnow().isoformat()
        with self.database.connect() as conn:
            cursor = conn.execute(
                "INSERT INTO albums (name, cols, rows, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (name, cols, rows, now, now),
            )
            conn.commit()
            return cursor.lastrowid

    def delete_album(self, album_id: int) -> None:
        with self.database.connect() as conn:
            conn.execute("DELETE FROM album_slots WHERE album_id = ?", (album_id,))
            conn.execute("DELETE FROM albums WHERE id = ?", (album_id,))
            conn.commit()

    def rename_album(self, album_id: int, name: str) -> None:
        now = dt.datetime.utcnow().isoformat()
        with self.database.connect() as conn:
            conn.execute(
                "UPDATE albums SET name = ?, updated_at = ? WHERE id = ?",
                (name, now, album_id),
            )
            conn.commit()

    def list_albums(self) -> list[dict]:
        with self.database.connect() as conn:
            rows = conn.execute(
                "SELECT id, name, cols, rows, created_at, updated_at FROM albums ORDER BY created_at ASC"
            ).fetchall()
        return [dict(r) for r in rows]

    def get_album(self, album_id: int) -> dict | None:
        with self.database.connect() as conn:
            row = conn.execute(
                "SELECT id, name, cols, rows, created_at, updated_at FROM albums WHERE id = ?",
                (album_id,),
            ).fetchone()
        return dict(row) if row else None

    def get_album_page_count(self, album_id: int) -> int:
        """Return number of pages based on highest page_num in use (0 if empty)."""
        with self.database.connect() as conn:
            row = conn.execute(
                "SELECT MAX(page_num) AS mp FROM album_slots WHERE album_id = ?",
                (album_id,),
            ).fetchone()
        max_page = row["mp"] if row and row["mp"] is not None else -1
        return max_page + 1

    def get_album_totals(self, album_id: int) -> dict:
        """Return summed market value and purchase cost for all slots in the album.

        Keys: ``market`` (float), ``purchase`` (float | None – None if no entry has
        a purchase_price set).
        """
        with self.database.connect() as conn:
            row = conn.execute(
                """
                SELECT
                    SUM(COALESCE(ce.last_price, cc.best_price)) AS market,
                    SUM(ce.purchase_price)                       AS purchase
                FROM album_slots s
                LEFT JOIN collection_entries ce ON ce.id = s.collection_entry_id
                LEFT JOIN card_catalog cc ON cc.api_id = ce.api_id
                WHERE s.album_id = ?
                """,
                (album_id,),
            ).fetchone()
        market = float(row["market"]) if row and row["market"] is not None else 0.0
        purchase = float(row["purchase"]) if row and row["purchase"] is not None else None
        return {"market": market, "purchase": purchase}

    def get_album_pages_summary(self, album_id: int) -> list[dict]:
        """Return per-page stats for the TOC.

        card_entries_raw encodes each card as ``set_name|||card_name`` joined
        by ``||`` so the UI can group + sort cards by set name.
        """
        with self.database.connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    s.page_num,
                    COUNT(*) AS card_count,
                    SUM(COALESCE(ce.last_price, cc.best_price, 0.0)) AS market_value,
                    SUM(COALESCE(ce.purchase_price, 0.0)) AS purchase_total,
                    GROUP_CONCAT(
                        COALESCE(ce.set_name, '') || '|||' || COALESCE(ce.name, ''),
                        '||'
                    ) AS card_entries_raw
                FROM album_slots s
                INNER JOIN collection_entries ce ON ce.id = s.collection_entry_id
                LEFT JOIN card_catalog cc ON cc.api_id = ce.api_id
                WHERE s.album_id = ?
                GROUP BY s.page_num
                ORDER BY s.page_num
                """,
                (album_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_album_pages_detail(self, album_id: int) -> list[dict]:
        """Return one row per card slot for the TOC detail view.

        Columns: page_num, set_name, card_name, card_value, purchase_price.
        Rows are ordered by page, then set name, then card name.
        """
        with self.database.connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    s.page_num,
                    COALESCE(ce.set_name, '') AS set_name,
                    COALESCE(ce.name, '') AS card_name,
                    COALESCE(ce.last_price, cc.best_price, 0.0) AS card_value,
                    COALESCE(ce.purchase_price, 0.0) AS purchase_price
                FROM album_slots s
                INNER JOIN collection_entries ce ON ce.id = s.collection_entry_id
                LEFT JOIN card_catalog cc ON cc.api_id = ce.api_id
                WHERE s.album_id = ?
                ORDER BY s.page_num,
                         COALESCE(ce.set_name, ''),
                         COALESCE(ce.name, '')
                """,
                (album_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_page_slots_with_entries(self, album_id: int, page_num: int) -> list[dict]:
        """Return slot data for a page, joined with collection + catalog info.

        Dangling slots (collection_entry_id references a deleted entry) are
        silently skipped so they don't render as phantom filled slots.
        """
        with self.database.connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    s.slot_index,
                    s.collection_entry_id,
                    ce.name,
                    ce.set_name,
                    ce.card_number,
                    ce.api_id,
                    COALESCE(cc.local_image_path, ce.image_path) AS image_path,
                    COALESCE(ce.last_price, cc.best_price) AS market_price,
                    ce.purchase_price,
                    cc.image_url AS catalog_image_url
                FROM album_slots s
                INNER JOIN collection_entries ce ON ce.id = s.collection_entry_id
                LEFT JOIN card_catalog cc ON cc.api_id = ce.api_id
                WHERE s.album_id = ? AND s.page_num = ?
                ORDER BY s.slot_index ASC
                """,
                (album_id, page_num),
            ).fetchall()
        return [dict(r) for r in rows]

    def set_slot(
        self,
        album_id: int,
        page_num: int,
        slot_index: int,
        collection_entry_id: int | None,
    ) -> None:
        if collection_entry_id is None:
            with self.database.connect() as conn:
                conn.execute(
                    "DELETE FROM album_slots WHERE album_id=? AND page_num=? AND slot_index=?",
                    (album_id, page_num, slot_index),
                )
                conn.commit()
        else:
            with self.database.connect() as conn:
                conn.execute(
                    """
                    INSERT INTO album_slots (album_id, page_num, slot_index, collection_entry_id)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(album_id, page_num, slot_index)
                    DO UPDATE SET collection_entry_id = excluded.collection_entry_id
                    """,
                    (album_id, page_num, slot_index, collection_entry_id),
                )
                conn.commit()

    def get_slot_entry_id(self, album_id: int, page_num: int, slot_index: int) -> int | None:
        """Return the collection_entry_id in the given slot, or None if empty."""
        with self.database.connect() as conn:
            row = conn.execute(
                "SELECT collection_entry_id FROM album_slots"
                " WHERE album_id=? AND page_num=? AND slot_index=?",
                (album_id, page_num, slot_index),
            ).fetchone()
        return row["collection_entry_id"] if row else None

    def swap_slots(self, album_id: int, p1: int, s1: int, p2: int, s2: int) -> None:
        with self.database.connect() as conn:
            r1 = conn.execute(
                "SELECT collection_entry_id FROM album_slots WHERE album_id=? AND page_num=? AND slot_index=?",
                (album_id, p1, s1),
            ).fetchone()
            r2 = conn.execute(
                "SELECT collection_entry_id FROM album_slots WHERE album_id=? AND page_num=? AND slot_index=?",
                (album_id, p2, s2),
            ).fetchone()
            eid1 = r1["collection_entry_id"] if r1 else None
            eid2 = r2["collection_entry_id"] if r2 else None
            conn.execute(
                "DELETE FROM album_slots WHERE album_id=? AND page_num=? AND slot_index=?",
                (album_id, p1, s1),
            )
            conn.execute(
                "DELETE FROM album_slots WHERE album_id=? AND page_num=? AND slot_index=?",
                (album_id, p2, s2),
            )
            if eid2 is not None:
                conn.execute(
                    "INSERT INTO album_slots (album_id,page_num,slot_index,collection_entry_id) VALUES(?,?,?,?)",
                    (album_id, p1, s1, eid2),
                )
            if eid1 is not None:
                conn.execute(
                    "INSERT INTO album_slots (album_id,page_num,slot_index,collection_entry_id) VALUES(?,?,?,?)",
                    (album_id, p2, s2, eid1),
                )
            conn.commit()

    def get_album_card_count(self, album_id: int) -> int:
        """Return number of filled slots in the album."""
        with self.database.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM album_slots"
                " WHERE album_id = ? AND collection_entry_id IS NOT NULL",
                (album_id,),
            ).fetchone()
        return int(row["n"]) if row else 0

    def get_album_api_ids(self, album_id: int) -> list[str]:
        """Return distinct api_ids for all filled slots in the album."""
        with self.database.connect() as conn:
            rows = conn.execute(
                """SELECT DISTINCT ce.api_id
                   FROM album_slots s
                   JOIN collection_entries ce ON ce.id = s.collection_entry_id
                   WHERE s.album_id = ? AND ce.api_id IS NOT NULL AND ce.api_id != ''""",
                (album_id,),
            ).fetchall()
        return [r["api_id"] for r in rows]

    def get_album_missing_price_api_ids(self, album_id: int) -> list[str]:
        """Return api_ids for all album cards that have no price yet (all pages)."""
        with self.database.connect() as conn:
            rows = conn.execute(
                """SELECT DISTINCT ce.api_id
                   FROM album_slots s
                   JOIN collection_entries ce ON ce.id = s.collection_entry_id
                   LEFT JOIN card_catalog cc ON cc.api_id = ce.api_id
                   WHERE s.album_id = ?
                     AND ce.api_id IS NOT NULL AND ce.api_id != ''
                     AND COALESCE(ce.last_price, cc.best_price) IS NULL""",
                (album_id,),
            ).fetchall()
        return [r["api_id"] for r in rows]

    def get_album_cover_path(self, album_id: int) -> str | None:
        """Return local image path of the first card in the album (for cover thumbnail)."""
        with self.database.connect() as conn:
            row = conn.execute(
                """
                SELECT COALESCE(cc.local_image_path, ce.image_path) AS img
                FROM album_slots s
                JOIN collection_entries ce ON ce.id = s.collection_entry_id
                LEFT JOIN card_catalog cc ON cc.api_id = ce.api_id
                WHERE s.album_id = ?
                  AND (cc.local_image_path IS NOT NULL OR ce.image_path IS NOT NULL)
                ORDER BY s.page_num ASC, s.slot_index ASC
                LIMIT 1
                """,
                (album_id,),
            ).fetchone()
        return str(row["img"]) if row and row["img"] else None

    def get_album_first_card_info(self, album_id: int) -> dict | None:
        """Return api_id, image_url, local_image_path for the first filled slot."""
        with self.database.connect() as conn:
            row = conn.execute(
                """
                SELECT ce.api_id,
                       COALESCE(cc.local_image_path, '') AS local_image_path,
                       COALESCE(cc.image_url, '')        AS image_url
                FROM album_slots s
                JOIN collection_entries ce ON ce.id = s.collection_entry_id
                LEFT JOIN card_catalog cc  ON cc.api_id = ce.api_id
                WHERE s.album_id = ? AND ce.api_id IS NOT NULL AND ce.api_id != ''
                ORDER BY s.page_num ASC, s.slot_index ASC
                LIMIT 1
                """,
                (album_id,),
            ).fetchone()
        if row and row["api_id"]:
            return {
                "api_id": row["api_id"],
                "local_image_path": row["local_image_path"],
                "image_url": row["image_url"],
            }
        return None

    def get_album_value(self, album_id: int) -> tuple[float, float]:
        """Return (eur_sum, usd_sum) of best_price for all slots in the album."""
        with self.database.connect() as conn:
            rows = conn.execute(
                """
                SELECT cc.best_price, cc.price_currency
                FROM album_slots s
                JOIN collection_entries ce ON ce.id = s.collection_entry_id
                LEFT JOIN card_catalog cc  ON cc.api_id = ce.api_id
                WHERE s.album_id = ? AND s.collection_entry_id IS NOT NULL
                """,
                (album_id,),
            ).fetchall()
        eur, usd = 0.0, 0.0
        for r in rows:
            price = r["best_price"] or 0.0
            cur = (r["price_currency"] or "EUR").upper()
            if cur == "USD":
                usd += price
            else:
                eur += price
        return (eur, usd)

    def get_album_set_logos(self, album_id: int) -> list[tuple[str, str | None]]:
        """Return list of (set_name, local_logo_path) for distinct sets in this album."""
        with self.database.connect() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT ce.set_name, cc.set_local_logo_path
                FROM album_slots s
                JOIN collection_entries ce ON ce.id = s.collection_entry_id
                LEFT JOIN card_catalog cc ON cc.api_id = ce.api_id
                WHERE s.album_id = ? AND ce.set_name IS NOT NULL AND ce.set_name != ''
                ORDER BY ce.set_name
                """,
                (album_id,),
            ).fetchall()
        seen: set[str] = set()
        result: list[tuple[str, str | None]] = []
        for r in rows:
            sn = r["set_name"]
            if sn not in seen:
                seen.add(sn)
                result.append((sn, r["set_local_logo_path"]))
        return result


class AlbumPageRepository:
    """Persists album page names keyed by their image file path."""

    def __init__(self, database: Database) -> None:
        self.database = database
        self._schema_initialized: bool = False
        self._migrate()

    def _migrate(self) -> None:
        if self._schema_initialized:
            return
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
        self._schema_initialized = True

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
        self._schema_initialized: bool = False
        self._migrate()

    def _migrate(self) -> None:
        if self._schema_initialized:
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
        self._schema_initialized = True

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
