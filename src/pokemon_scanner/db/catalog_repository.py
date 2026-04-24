from __future__ import annotations

import datetime as dt
import logging
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests as _req

from src.pokemon_scanner.core.paths import CATALOG_IMAGES_DIR
from src.pokemon_scanner.datasources.base import CardCandidate
from src.pokemon_scanner.db.database import Database

# Only download images from trusted pokemontcg.io hosts
_ALLOWED_IMAGE_HOSTS: frozenset[str] = frozenset({
    "images.pokemontcg.io",
    "pokemontcg.io",
    "api.pokemontcg.io",
})
_MAX_DOWNLOAD_BYTES = 5 * 1024 * 1024  # 5 MB hard cap per image


def _is_allowed_url(url: str) -> bool:
    """Return True if *url* points to a whitelisted image host."""
    try:
        host = (urlparse(url).hostname or "").lower()
        return any(host == h or host.endswith("." + h) for h in _ALLOWED_IMAGE_HOSTS)
    except Exception:
        return False


# Module-level persistent HTTP session — avoids re-creating TCP connections for
# each image download when saving many cards from the same host.
_http_session: _req.Session | None = None


def _get_http_session() -> _req.Session:
    global _http_session
    if _http_session is None:
        _http_session = _req.Session()
        _http_session.headers["User-Agent"] = "CardLens/1.0"
    return _http_session

_LOG = logging.getLogger(__name__)


def _extract_api_id(candidate: CardCandidate) -> str | None:
    """Return the pokemontcg.io card ID from candidate.api_id (preferred) or notes field."""
    if candidate.api_id:
        return candidate.api_id
    # Legacy fallback: some callers may not have set api_id but store it in notes
    if candidate.notes and candidate.notes.startswith("ID: "):
        return candidate.notes[4:].strip()
    return None


class CatalogRepository:
    def __init__(self, database: Database) -> None:
        self.database = database
        self._schema_initialized: bool = False
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        """Create table if missing and run column migrations."""
        if self._schema_initialized:
            return
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
        self._schema_initialized = True

    def _migrate(self, conn) -> None:
        """Add columns introduced after initial schema to existing DBs."""
        cols = {row[1] for row in conn.execute("PRAGMA table_info(card_catalog)").fetchall()}
        if "set_logo_url" not in cols:
            conn.execute("ALTER TABLE card_catalog ADD COLUMN set_logo_url TEXT DEFAULT ''")
        if "set_local_logo_path" not in cols:
            conn.execute("ALTER TABLE card_catalog ADD COLUMN set_local_logo_path TEXT")
        if "set_release_date" not in cols:
            conn.execute("ALTER TABLE card_catalog ADD COLUMN set_release_date TEXT")
        # Allowlist maps each migration column to its exact SQL type.  The f-string
        # below is safe because both col_name and col_type come from this literal dict.
        new_cols: dict[str, str] = {
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
            "set_release_year": "INTEGER",
            "tcgplayer_url": "TEXT",
        }
        _safe_types = {"TEXT", "INTEGER", "REAL", "BLOB"}
        for col_name, col_type in new_cols.items():
            # Double-check both names come from the allowlist (defence-in-depth)
            if col_name not in cols and col_name in new_cols and col_type in _safe_types:
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

        # Filter to candidates with a usable api_id
        valid: list[tuple[CardCandidate, str]] = [
            (c, aid) for c in candidates if (aid := _extract_api_id(c))
        ]
        if not valid:
            return []

        api_ids = [aid for _, aid in valid]
        placeholders = ",".join("?" * len(api_ids))

        with self.database.connect() as conn:
            # Single batch lookup — one round-trip instead of N
            existing: dict[str, str | None] = {
                row["api_id"]: row["local_image_path"]
                for row in conn.execute(
                    f"SELECT api_id, local_image_path FROM card_catalog WHERE api_id IN ({placeholders})",
                    api_ids,
                ).fetchall()
            }

            needs_download: list[str] = []
            update_rows: list[tuple] = []
            insert_rows: list[tuple] = []

            for c, api_id in valid:
                release_year: int | None = None
                if c.set_release_date:
                    try:
                        release_year = int(c.set_release_date[:4])
                    except (ValueError, TypeError):
                        pass
                if api_id in existing:
                    update_rows.append((
                        c.best_price, c.price_currency, c.image_url,
                        c.set_logo_url, c.set_symbol_url,
                        c.rarity, c.supertype, c.subtypes, c.hp, c.types,
                        c.artist, c.pokedex_numbers, c.regulation_mark,
                        c.legalities, c.set_series, c.set_total,
                        c.eur_price, c.usd_price, release_year,
                        getattr(c, 'tcgplayer_url', '') or "",
                        now, api_id,
                    ))
                    if not existing[api_id] and c.image_url:
                        needs_download.append(api_id)
                else:
                    insert_rows.append((
                        api_id, c.name, c.set_name, c.card_number, c.language,
                        c.best_price, c.price_currency, c.image_url, None,
                        c.set_logo_url, None,
                        c.rarity, c.supertype, c.subtypes, c.hp, c.types,
                        c.artist, c.pokedex_numbers, c.regulation_mark,
                        c.legalities, c.set_series, c.set_total,
                        c.set_symbol_url, c.eur_price, c.usd_price, release_year,
                        getattr(c, 'tcgplayer_url', '') or "",
                        now, now,
                    ))
                    if c.image_url:
                        needs_download.append(api_id)

            if update_rows:
                conn.executemany(
                    """UPDATE card_catalog
                       SET best_price=?, price_currency=?, image_url=?,
                           set_logo_url=?, set_symbol_url=?,
                           rarity=?, supertype=?, subtypes=?, hp=?, types=?,
                           artist=?, pokedex_numbers=?, regulation_mark=?,
                           legalities=?, set_series=?, set_total=?,
                           eur_price=?, usd_price=?, set_release_year=?,
                           tcgplayer_url=?,
                           updated_at=?
                       WHERE api_id=?""",
                    update_rows,
                )
            if insert_rows:
                conn.executemany(
                    """INSERT OR IGNORE INTO card_catalog
                       (api_id, name, set_name, card_number, language,
                        best_price, price_currency, image_url, local_image_path,
                        set_logo_url, set_local_logo_path,
                        rarity, supertype, subtypes, hp, types,
                        artist, pokedex_numbers, regulation_mark,
                        legalities, set_series, set_total,
                        set_symbol_url, eur_price, usd_price, set_release_year,
                        tcgplayer_url,
                        fetched_at, updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    insert_rows,
                )
            conn.commit()
        return needs_download

    def save_local_image(self, api_id: str, url: str) -> Path | None:
        """Download image from URL, save to catalog_images/{api_id}.jpg, update DB."""
        if not url or not url.startswith("https://"):
            return None
        if not _is_allowed_url(url):
            _LOG.warning("Rejected image download from non-whitelisted host: %s", url)
            return None
        CATALOG_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
        safe_id = api_id.replace("/", "_").replace("\\", "_")
        dest = CATALOG_IMAGES_DIR / f"{safe_id}.jpg"
        if dest.exists() and dest.stat().st_size > 1024:
            self._update_local_path(api_id, dest)
            return dest
        try:
            resp = _get_http_session().get(url, timeout=20, stream=True)
            resp.raise_for_status()
            written = 0
            with open(dest, "wb") as fh:
                for chunk in resp.iter_content(chunk_size=65536):
                    written += len(chunk)
                    if written > _MAX_DOWNLOAD_BYTES:
                        _LOG.warning("Image download exceeded %d bytes, discarded: %s", _MAX_DOWNLOAD_BYTES, api_id)
                        dest.unlink(missing_ok=True)
                        return None
                    fh.write(chunk)
            if dest.stat().st_size < 100:
                dest.unlink(missing_ok=True)
                _LOG.warning("Image too small after download, discarded: %s", api_id)
                return None
            self._update_local_path(api_id, dest)
            _LOG.debug("Catalog image saved: %s (%d bytes)", dest.name, dest.stat().st_size)
            return dest
        except Exception as exc:
            dest.unlink(missing_ok=True)  # remove partial/corrupt file
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
        if not url or not url.startswith("https://"):
            return None
        if not _is_allowed_url(url):
            _LOG.warning("Rejected set symbol download from non-whitelisted host: %s", url)
            return None
        CATALOG_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
        safe_name = set_name.replace("/", "_").replace("\\", "_").replace(" ", "_")
        dest = CATALOG_IMAGES_DIR / f"symbol_{safe_name}.png"
        if not dest.exists() or dest.stat().st_size < 100:
            try:
                resp = _get_http_session().get(url, timeout=15, stream=True)
                resp.raise_for_status()
                written = 0
                with open(dest, "wb") as fh:
                    for chunk in resp.iter_content(chunk_size=65536):
                        written += len(chunk)
                        if written > _MAX_DOWNLOAD_BYTES:
                            dest.unlink(missing_ok=True)
                            _LOG.warning("Set symbol download exceeded size limit: %s", set_name)
                            return None
                        fh.write(chunk)
            except Exception as exc:
                dest.unlink(missing_ok=True)  # remove partial/corrupt file
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
        if not url or not url.startswith("https://"):
            return None
        if not _is_allowed_url(url):
            _LOG.warning("Rejected set logo download from non-whitelisted host: %s", url)
            return None
        CATALOG_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
        safe_name = set_name.replace("/", "_").replace("\\", "_").replace(" ", "_")
        dest = CATALOG_IMAGES_DIR / f"logo_{safe_name}.png"
        if not dest.exists() or dest.stat().st_size < 100:
            try:
                resp = _get_http_session().get(url, timeout=15, stream=True)
                resp.raise_for_status()
                written = 0
                with open(dest, "wb") as fh:
                    for chunk in resp.iter_content(chunk_size=65536):
                        written += len(chunk)
                        if written > _MAX_DOWNLOAD_BYTES:
                            dest.unlink(missing_ok=True)
                            _LOG.warning("Set logo download exceeded size limit: %s", set_name)
                            return None
                        fh.write(chunk)
            except Exception as exc:
                dest.unlink(missing_ok=True)  # remove partial/corrupt file
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
                          tcgplayer_url,
                          fetched_at, updated_at
                   FROM card_catalog ORDER BY set_name ASC, card_number ASC"""
            ).fetchall()
        return [dict(r) for r in rows]

    def search(self, query: str) -> list[dict[str, Any]]:
        from src.pokemon_scanner.core.name_translations import translate_to_en, translate_to_de

        prefix_q = f"{query}%"
        any_q = f"%{query}%"

        # Build extra name terms for DE↔EN cross-search
        q_lower = query.lower().strip()
        en_equiv = translate_to_en(q_lower)   # DE input  → EN name
        de_equiv = translate_to_de(q_lower)   # EN input  → DE name

        # Collect all LIKE patterns to OR together for the name column
        name_likes: list[str] = [prefix_q, any_q]
        if en_equiv:
            name_likes += [f"{en_equiv}%", f"%{en_equiv}%"]
        if de_equiv:
            name_likes += [f"{de_equiv}%", f"%{de_equiv}%"]

        # Deduplicate while preserving order
        seen: set[str] = set()
        unique_name_likes: list[str] = []
        for p in name_likes:
            if p not in seen:
                seen.add(p)
                unique_name_likes.append(p)

        name_clause = " OR ".join(["name LIKE ?"] * len(unique_name_likes))
        sql = f"""SELECT api_id, name, set_name, card_number, language,
                         best_price, price_currency, image_url, local_image_path,
                         set_logo_url, set_local_logo_path, set_release_date,
                         rarity, supertype, subtypes, hp, types,
                         artist, pokedex_numbers, regulation_mark,
                         legalities, set_series, set_total,
                         set_symbol_url, set_symbol_local_path, eur_price, usd_price,
                         tcgplayer_url,
                         fetched_at, updated_at
                  FROM card_catalog
                  WHERE ({name_clause}) OR set_name LIKE ? OR card_number LIKE ?
                  ORDER BY set_name ASC, card_number ASC"""
        params = tuple(unique_name_likes) + (any_q, any_q)
        with self.database.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def get_by_api_id(self, api_id: str) -> "dict | None":
        """Return catalog data for a single card by api_id, or None if not found."""
        with self.database.connect() as conn:
            row = conn.execute(
                "SELECT api_id, name, set_name, card_number, language,"
                " best_price, price_currency, image_url, local_image_path,"
                " set_logo_url, set_local_logo_path, set_release_date,"
                " rarity, supertype, subtypes, hp, types,"
                " artist, pokedex_numbers, regulation_mark,"
                " legalities, set_series, set_total,"
                " set_symbol_url, set_symbol_local_path, eur_price, usd_price,"
                " tcgplayer_url, fetched_at, updated_at"
                " FROM card_catalog WHERE api_id = ?",
                (api_id,),
            ).fetchone()
        return dict(row) if row else None

    def update_release_dates(self, mapping: dict[str, str]) -> None:
        """Set set_release_date and set_release_year for all catalog entries matching each set_name."""
        with self.database.connect() as conn:
            for set_name, release_date in mapping.items():
                # Fill missing set_release_date strings
                conn.execute(
                    "UPDATE card_catalog SET set_release_date=?"
                    " WHERE set_name=? AND (set_release_date IS NULL OR set_release_date='')",
                    (release_date, set_name),
                )
                # Always backfill set_release_year (integer) where NULL — separate
                # query so cards that already had set_release_date also get the year.
                year: int | None = None
                if len(release_date) >= 4 and release_date[:4].isdigit():
                    year = int(release_date[:4])
                if year:
                    conn.execute(
                        "UPDATE card_catalog SET set_release_year=?"
                        " WHERE set_name=? AND set_release_year IS NULL",
                        (year, set_name),
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
            wheres.append("COALESCE(set_release_year, 0) >= ?")
            params.append(min_year)
        if max_year:
            wheres.append("COALESCE(set_release_year, 9999) <= ?")
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
                   MAX(1.0, {current_year} - COALESCE(set_release_year, {current_year})) AS score
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
                api_id=row['api_id'] or '',
                image_url=img,
                set_logo_url=row["set_logo_url"] or "",
            ))

        candidates.sort(key=lambda c: c.confidence, reverse=True)
        # Drop results that are not even loosely similar to the query —
        # prevents wildly wrong matches (e.g. Zweilous appearing for "Audino")
        min_sim = 0.35
        candidates = [c for c in candidates if c.confidence >= min_sim]
        return candidates
