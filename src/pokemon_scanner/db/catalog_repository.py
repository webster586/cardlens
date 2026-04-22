from __future__ import annotations

import datetime as dt
import logging
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import requests as _req

from src.pokemon_scanner.core.paths import CATALOG_IMAGES_DIR
from src.pokemon_scanner.datasources.base import CardCandidate
from src.pokemon_scanner.db.database import Database

_LOG = logging.getLogger(__name__)


def _extract_api_id(candidate: CardCandidate) -> str | None:
    """Extract the pokemontcg.io card ID from the notes field ('ID: sv3pt5-76')."""
    if candidate.notes and candidate.notes.startswith("ID: "):
        return candidate.notes[4:].strip()
    return None


class CatalogRepository:
    def __init__(self, database: Database) -> None:
        self.database = database
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        """Create table if missing and run column migrations."""
        with self.database.connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS card_catalog (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    api_id TEXT UNIQUE NOT NULL,
                    name TEXT NOT NULL,
                    set_name TEXT,
                    card_number TEXT,
                    language TEXT,
                    best_price REAL,
                    price_currency TEXT,
                    image_url TEXT,
                    local_image_path TEXT,
                    set_logo_url TEXT DEFAULT '',
                    set_local_logo_path TEXT,
                    fetched_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS price_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    api_id TEXT NOT NULL,
                    snapshot_date TEXT NOT NULL,
                    price REAL NOT NULL,
                    currency TEXT NOT NULL DEFAULT 'USD',
                    UNIQUE(api_id, snapshot_date)
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_ph_api_date"
                " ON price_history(api_id, snapshot_date)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_cc_set_name"
                " ON card_catalog(set_name)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_cc_name"
                " ON card_catalog(name)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_cc_set_card"
                " ON card_catalog(set_name, card_number)"
            )
            conn.execute("""
                CREATE TABLE IF NOT EXISTS set_sealed_prices (
                    set_name TEXT NOT NULL,
                    product_type TEXT NOT NULL,
                    price_usd REAL,
                    price_eur REAL,
                    fetched_at TEXT NOT NULL,
                    PRIMARY KEY (set_name, product_type)
                )
            """)
            self._migrate(conn)

    def _migrate(self, conn) -> None:
        """Add columns introduced after initial schema to existing DBs."""
        cols = {row[1] for row in conn.execute("PRAGMA table_info(card_catalog)").fetchall()}
        if "set_logo_url" not in cols:
            conn.execute("ALTER TABLE card_catalog ADD COLUMN set_logo_url TEXT DEFAULT ''")
        if "set_local_logo_path" not in cols:
            conn.execute("ALTER TABLE card_catalog ADD COLUMN set_local_logo_path TEXT")
        if "set_release_date" not in cols:
            conn.execute("ALTER TABLE card_catalog ADD COLUMN set_release_date TEXT")
        new_cols = {
            "rarity": "TEXT",
            "supertype": "TEXT",
            "subtypes": "TEXT",
            "hp": "TEXT",
            "types": "TEXT",
            "artist": "TEXT",
            "pokedex_numbers": "TEXT",
            "regulation_mark": "TEXT",
            "legalities": "TEXT",
            "set_series": "TEXT",
            "set_total": "INTEGER",
            "set_symbol_url": "TEXT",
            "set_symbol_local_path": "TEXT",
            "eur_price": "REAL",
            "usd_price": "REAL",
        }
        for col_name, col_type in new_cols.items():
            if col_name not in cols:
                conn.execute(f"ALTER TABLE card_catalog ADD COLUMN {col_name} {col_type}")
        conn.commit()

    def get_sealed_prices(self, set_names: list[str]) -> dict[str, dict[str, dict]]:
        """Return {set_name: {'etb': {'usd': x, 'eur': y}, 'bundle': {...}}} for the given sets."""
        if not set_names:
            return {}
        placeholders = ",".join("?" * len(set_names))
        with self.database.connect() as conn:
            rows = conn.execute(
                f"SELECT set_name, product_type, price_usd, price_eur"
                f" FROM set_sealed_prices WHERE set_name IN ({placeholders})",
                set_names,
            ).fetchall()
        result: dict[str, dict] = {}
        for row in rows:
            sn, pt, usd, eur = row["set_name"], row["product_type"], row["price_usd"], row["price_eur"]
            result.setdefault(sn, {})[pt] = {"usd": usd, "eur": eur}
        return result

    def upsert_sealed_price(
        self, set_name: str, product_type: str,
        price_usd: float | None, price_eur: float | None,
    ) -> None:
        now = dt.datetime.utcnow().isoformat()
        with self.database.connect() as conn:
            conn.execute(
                """
                INSERT INTO set_sealed_prices (set_name, product_type, price_usd, price_eur, fetched_at)
                VALUES (?,?,?,?,?)
                ON CONFLICT(set_name, product_type) DO UPDATE SET
                    price_usd=excluded.price_usd,
                    price_eur=excluded.price_eur,
                    fetched_at=excluded.fetched_at
                """,
                (set_name, product_type, price_usd, price_eur, now),
            )
            conn.commit()

    def upsert_candidates(self, candidates: list[CardCandidate]) -> list[str]:
        """Save all candidates to card_catalog. Returns list of api_ids that need image download."""
        now = dt.datetime.utcnow().isoformat()
        needs_download: list[str] = []

        with self.database.connect() as conn:
            for c in candidates:
                api_id = _extract_api_id(c)
                if not api_id:
                    continue
                existing = conn.execute(
                    "SELECT api_id, local_image_path FROM card_catalog WHERE api_id = ?",
                    (api_id,),
                ).fetchone()
                if existing:
                    # Update price + metadata + timestamp
                    conn.execute(
                        """UPDATE card_catalog
                           SET best_price=?, price_currency=?, image_url=?,
                               set_logo_url=?, set_symbol_url=?,
                               rarity=?, supertype=?, subtypes=?, hp=?, types=?,
                               artist=?, pokedex_numbers=?, regulation_mark=?,
                               legalities=?, set_series=?, set_total=?,
                               eur_price=?, usd_price=?,
                               updated_at=?
                           WHERE api_id=?""",
                        (c.best_price, c.price_currency, c.image_url,
                         c.set_logo_url, c.set_symbol_url,
                         c.rarity, c.supertype, c.subtypes, c.hp, c.types,
                         c.artist, c.pokedex_numbers, c.regulation_mark,
                         c.legalities, c.set_series, c.set_total,
                         c.eur_price, c.usd_price,
                         now, api_id),
                    )
                    if not existing["local_image_path"] and c.image_url:
                        needs_download.append(api_id)
                else:
                    conn.execute(
                        """INSERT INTO card_catalog
                           (api_id, name, set_name, card_number, language,
                            best_price, price_currency, image_url, local_image_path,
                            set_logo_url, set_local_logo_path,
                            rarity, supertype, subtypes, hp, types,
                            artist, pokedex_numbers, regulation_mark,
                            legalities, set_series, set_total,
                            set_symbol_url, eur_price, usd_price,
                            fetched_at, updated_at)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            api_id, c.name, c.set_name, c.card_number, c.language,
                            c.best_price, c.price_currency, c.image_url, None,
                            c.set_logo_url, None,
                            c.rarity, c.supertype, c.subtypes, c.hp, c.types,
                            c.artist, c.pokedex_numbers, c.regulation_mark,
                            c.legalities, c.set_series, c.set_total,
                            c.set_symbol_url, c.eur_price, c.usd_price,
                            now, now,
                        ),
                    )
                    if c.image_url:
                        needs_download.append(api_id)
            conn.commit()
        return needs_download

    def save_local_image(self, api_id: str, url: str) -> Path | None:
        """Download image from URL, save to catalog_images/{api_id}.jpg, update DB."""
        if not url:
            return None
        CATALOG_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
        safe_id = api_id.replace("/", "_").replace("\\", "_")
        dest = CATALOG_IMAGES_DIR / f"{safe_id}.jpg"
        # Re-download if missing or zero-byte (partial write)
        if dest.exists() and dest.stat().st_size > 1024:
            self._update_local_path(api_id, dest)
            return dest
        try:
            resp = _req.get(
                url,
                headers={"User-Agent": "CardLens/1.0"},
                timeout=20,
                stream=True,
            )
            resp.raise_for_status()
            dest.write_bytes(resp.content)
            if dest.stat().st_size < 100:
                dest.unlink(missing_ok=True)
                _LOG.warning("Image too small after download, discarded: %s", api_id)
                return None
            self._update_local_path(api_id, dest)
            _LOG.debug("Catalog image saved: %s (%d bytes)", dest.name, dest.stat().st_size)
            return dest
        except Exception as exc:
            _LOG.warning("Failed to download catalog image %s: %s", api_id, exc)
            return None

    def _update_local_path(self, api_id: str, path: Path) -> None:
        now = dt.datetime.utcnow().isoformat()
        with self.database.connect() as conn:
            conn.execute(
                "UPDATE card_catalog SET local_image_path=?, updated_at=? WHERE api_id=?",
                (str(path), now, api_id),
            )
            conn.commit()

    def save_set_symbol(self, set_name: str, url: str) -> Path | None:
        """Download set symbol icon, cache locally, update all cards in this set."""
        if not url:
            return None
        CATALOG_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
        safe_name = set_name.replace("/", "_").replace("\\", "_").replace(" ", "_")
        dest = CATALOG_IMAGES_DIR / f"symbol_{safe_name}.png"
        if not dest.exists() or dest.stat().st_size < 100:
            try:
                resp = _req.get(
                    url,
                    headers={"User-Agent": "CardLens/1.0"},
                    timeout=15,
                )
                resp.raise_for_status()
                dest.write_bytes(resp.content)
            except Exception as exc:
                _LOG.warning("Failed to download set symbol for %r: %s", set_name, exc)
                return None
        now = dt.datetime.utcnow().isoformat()
        with self.database.connect() as conn:
            conn.execute(
                "UPDATE card_catalog SET set_symbol_local_path=?, updated_at=? WHERE set_name=?",
                (str(dest), now, set_name),
            )
            conn.commit()
        _LOG.debug("Set symbol cached: %s", dest.name)
        return dest

    def save_set_logo(self, set_name: str, url: str) -> Path | None:
        """Download set logo, cache locally, update all cards in this set."""
        if not url:
            return None
        CATALOG_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
        safe_name = set_name.replace("/", "_").replace("\\", "_").replace(" ", "_")
        dest = CATALOG_IMAGES_DIR / f"logo_{safe_name}.png"
        if not dest.exists() or dest.stat().st_size < 100:
            try:
                resp = _req.get(
                    url,
                    headers={"User-Agent": "CardLens/1.0"},
                    timeout=15,
                )
                resp.raise_for_status()
                dest.write_bytes(resp.content)
            except Exception as exc:
                _LOG.warning("Failed to download set logo for %r: %s", set_name, exc)
                return None
        now = dt.datetime.utcnow().isoformat()
        with self.database.connect() as conn:
            conn.execute(
                "UPDATE card_catalog SET set_local_logo_path=?, updated_at=? WHERE set_name=?",
                (str(dest), now, set_name),
            )
            conn.commit()
        _LOG.debug("Set logo cached: %s", dest.name)
        return dest

    def update_price(self, api_id: str, price: float | None, currency: str, image_url: str | None = None) -> None:
        """Update best_price (and optionally image_url) for a single card."""
        now = dt.datetime.utcnow().isoformat()
        with self.database.connect() as conn:
            if image_url:
                conn.execute(
                    "UPDATE card_catalog SET best_price=?, price_currency=?, image_url=?, updated_at=? WHERE api_id=?",
                    (price, currency, image_url, now, api_id),
                )
            else:
                conn.execute(
                    "UPDATE card_catalog SET best_price=?, price_currency=?, updated_at=? WHERE api_id=?",
                    (price, currency, now, api_id),
                )
            conn.commit()

    def list_all(self) -> list[dict[str, Any]]:
        with self.database.connect() as conn:
            rows = conn.execute(
                """SELECT api_id, name, set_name, card_number, language,
                          best_price, price_currency, image_url, local_image_path,
                          set_logo_url, set_local_logo_path, set_release_date,
                          rarity, supertype, subtypes, hp, types,
                          artist, pokedex_numbers, regulation_mark,
                          legalities, set_series, set_total,
                          set_symbol_url, set_symbol_local_path, eur_price, usd_price,
                          fetched_at, updated_at
                   FROM card_catalog ORDER BY set_name ASC, card_number ASC"""
            ).fetchall()
        return [dict(r) for r in rows]

    def search(self, query: str) -> list[dict[str, Any]]:
        q = f"%{query}%"
        with self.database.connect() as conn:
            rows = conn.execute(
                """SELECT api_id, name, set_name, card_number, language,
                          best_price, price_currency, image_url, local_image_path,
                          set_logo_url, set_local_logo_path, set_release_date,
                          rarity, supertype, subtypes, hp, types,
                          artist, pokedex_numbers, regulation_mark,
                          legalities, set_series, set_total,
                          set_symbol_url, set_symbol_local_path, eur_price, usd_price,
                          fetched_at, updated_at
                   FROM card_catalog
                   WHERE name LIKE ? OR set_name LIKE ? OR card_number LIKE ?
                   ORDER BY set_name ASC, card_number ASC""",
                (q, q, q),
            ).fetchall()
        return [dict(r) for r in rows]

    def update_release_dates(self, mapping: dict[str, str]) -> None:
        """Set set_release_date for all catalog entries matching each set_name."""
        with self.database.connect() as conn:
            for set_name, release_date in mapping.items():
                conn.execute(
                    "UPDATE card_catalog SET set_release_date=?"
                    " WHERE set_name=? AND (set_release_date IS NULL OR set_release_date='')",
                    (release_date, set_name),
                )
            conn.commit()

    def get_top_performers(
        self,
        limit: int = 1000,
        min_year: int = 2016,
        max_year: int = 2026,
        language: str | None = None,
        owned_ids: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Return top cards by normalized price score: best_price / max(1, age_years).

        Higher score = expensive card relative to how long ago it was released.
        """
        current_year = dt.date.today().year
        wheres = ["best_price IS NOT NULL", "best_price > 0"]
        params: list = []
        if min_year:
            wheres.append(
                "CAST(COALESCE(SUBSTR(set_release_date,1,4),'0') AS INTEGER) >= ?"
            )
            params.append(min_year)
        if max_year:
            wheres.append(
                "CAST(COALESCE(SUBSTR(set_release_date,1,4),'9999') AS INTEGER) <= ?"
            )
            params.append(max_year)
        if language:
            wheres.append("language = ?")
            params.append(language)
        if owned_ids is not None:
            if not owned_ids:
                return []
            placeholders = ",".join("?" * len(owned_ids))
            wheres.append(f"api_id IN ({placeholders})")
            params.extend(owned_ids)
        where_sql = " AND ".join(wheres)
        sql = f"""
            SELECT api_id, name, set_name, card_number, language,
                   best_price, price_currency, local_image_path,
                   set_local_logo_path, set_release_date, fetched_at, updated_at,
                   CAST(best_price AS REAL) /
                   MAX(1.0, {current_year} - CAST(
                       COALESCE(SUBSTR(set_release_date,1,4),'{current_year}') AS INTEGER
                   )) AS score
            FROM card_catalog
            WHERE {where_sql}
            ORDER BY score DESC
            LIMIT ?
        """
        with self.database.connect() as conn:
            rows = conn.execute(sql, params + [limit]).fetchall()
        return [dict(r) for r in rows]

    # ── Price history ──────────────────────────────────────────────────────

    def record_price_snapshot(
        self, api_id: str, price: float, currency: str = "USD"
    ) -> None:
        """Record today's price for one card. Silently ignored if already exists today."""
        today = dt.date.today().isoformat()
        with self.database.connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO price_history"
                " (api_id, snapshot_date, price, currency) VALUES (?, ?, ?, ?)",
                (api_id, today, price, currency),
            )
            conn.commit()

    def record_price_snapshots_bulk(self, rows: list[dict]) -> None:
        """Record today's price for every row that has a price (max 1× per card/day)."""
        today = dt.date.today().isoformat()
        with self.database.connect() as conn:
            for row in rows:
                api_id = row.get("api_id") or ""
                price = row.get("best_price")
                currency = row.get("price_currency") or "USD"
                if not api_id or price is None:
                    continue
                conn.execute(
                    "INSERT OR IGNORE INTO price_history"
                    " (api_id, snapshot_date, price, currency) VALUES (?, ?, ?, ?)",
                    (api_id, today, price, currency),
                )
            conn.commit()

    def get_price_history(self, api_id: str) -> list[dict[str, Any]]:
        """Return all price snapshots for a card, ordered oldest→newest."""
        with self.database.connect() as conn:
            rows = conn.execute(
                "SELECT snapshot_date, price, currency FROM price_history"
                " WHERE api_id = ? ORDER BY snapshot_date ASC",
                (api_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def count(self) -> int:
        with self.database.connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM card_catalog").fetchone()[0]

    def search_candidates(self, name: str, number: str = "") -> list[CardCandidate]:
        """Search local catalog by name (fuzzy LIKE) or exact card number.

        Returns CardCandidate list sorted by name-similarity score, ready for
        the recognition pipeline without any network call.
        All callers must pass an already-translated English name; this function
        does NOT translate to avoid double-translation bugs.
        """
        primary = name

        rows: list[Any] = []
        with self.database.connect() as conn:
            if number:
                # Exact number match first
                rows = conn.execute(
                    """SELECT api_id, name, set_name, card_number, language,
                              best_price, price_currency, image_url, local_image_path,
                              set_logo_url
                       FROM card_catalog WHERE card_number = ?
                       ORDER BY set_name ASC""",
                    (number,),
                ).fetchall()
            if not rows and primary:
                # Fuzzy name search (LIKE on primary term)
                base = primary.split()[0] if primary else ""
                q = f"%{base}%"
                rows = conn.execute(
                    """SELECT api_id, name, set_name, card_number, language,
                              best_price, price_currency, image_url, local_image_path,
                              set_logo_url
                       FROM card_catalog WHERE name LIKE ?
                       ORDER BY set_name ASC LIMIT 30""",
                    (q,),
                ).fetchall()

        if not rows:
            return []

        q_lower = primary.lower()

        def _score(row) -> float:
            sim = SequenceMatcher(None, q_lower, row["name"].lower()).ratio()
            return sim

        candidates = []
        for row in rows:
            local_path = row["local_image_path"]
            img = local_path if local_path and Path(local_path).exists() else (row["image_url"] or "")
            candidates.append(CardCandidate(
                source="local_catalog",
                name=row["name"],
                set_name=row["set_name"] or "",
                card_number=row["card_number"] or "",
                language=row["language"] or "en",
                confidence=_score(row),
                best_price=row["best_price"],
                price_currency="USD",
                price_source="TCGPlayer" if row["best_price"] else "",
                notes=f"ID: {row['api_id']}",
                image_url=img,
                set_logo_url=row["set_logo_url"] or "",
            ))

        candidates.sort(key=lambda c: c.confidence, reverse=True)
        # Drop results that are not even loosely similar to the query —
        # prevents wildly wrong matches (e.g. Zweilous appearing for "Audino")
        min_sim = 0.35
        candidates = [c for c in candidates if c.confidence >= min_sim]
        return candidates
