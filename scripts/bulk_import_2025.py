"""
Bulk-Import: Alle Pokémon-Karten ab 2024 von pokemontcg.io → lokale DB + Bilder.

Aufruf (aus pokemon_scanner_repo/):
    .venv\Scripts\python.exe scripts/bulk_import_2025.py

Optionen:
    --no-images     Nur DB-Einträge, kein Bild-Download
    --dry-run       Nur Sets auflisten, nichts schreiben
    --set SV9       Nur ein bestimmtes Set importieren (code oder Teil des Namens)
    --since YEAR    Frühstes Jahr (Standard: 2024)
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# Make src importable when running from repo root
sys.path.insert(0, str(Path(__file__).parent.parent))

import requests

from src.pokemon_scanner.db.catalog_repository import CatalogRepository
from src.pokemon_scanner.db.database import Database
from src.pokemon_scanner.core.paths import DATA_DIR, CATALOG_IMAGES_DIR

_BASE = "https://api.pokemontcg.io/v2"
_TIMEOUT = 30
_PAGE_SIZE = 250  # max allowed by API
_IMAGE_WORKERS = 6
_REQUEST_DELAY = 0.12  # ~8 req/s to stay under rate limit


def _get(url: str, params: dict | None = None, retries: int = 3) -> dict:
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, timeout=_TIMEOUT,
                             headers={"User-Agent": "PokemonScanner/1.0"})
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            if attempt == retries - 1:
                raise
            wait = 2 ** attempt
            print(f"  ⚠  Retry {attempt+1}/{retries} after {wait}s: {exc}")
            time.sleep(wait)
    return {}


def fetch_sets_since(since_year: int = 2024) -> list[dict]:
    """Return all sets with releaseDate >= since_year-01-01, sorted oldest first."""
    data = _get(f"{_BASE}/sets", {"pageSize": 250})
    sets = data.get("data", [])
    cutoff = f"{since_year}-01-01"
    result = [s for s in sets if s.get("releaseDate", "") >= cutoff]
    result.sort(key=lambda s: s.get("releaseDate", ""))
    return result


def fetch_cards_for_set(set_id: str) -> list[dict]:
    """Fetch all cards for a given set ID (handles pagination)."""
    cards: list[dict] = []
    page = 1
    while True:
        data = _get(f"{_BASE}/cards", {
            "q": f"set.id:{set_id}",
            "pageSize": _PAGE_SIZE,
            "page": page,
            "orderBy": "number",
        })
        batch = data.get("data", [])
        cards.extend(batch)
        if len(cards) >= data.get("totalCount", 0):
            break
        page += 1
        time.sleep(_REQUEST_DELAY)
    return cards


def extract_price(card: dict) -> float | None:
    prices = card.get("tcgplayer", {}).get("prices", {})
    for variant in ("normal", "holofoil", "reverseHolofoil", "1stEditionHolofoil"):
        p = prices.get(variant, {}).get("market")
        if p is not None:
            return round(float(p), 2)
    return None


def download_image(api_id: str, url: str, dest: Path) -> bool:
    if dest.exists() and dest.stat().st_size > 1024:
        return False  # already cached
    try:
        r = requests.get(url, headers={"User-Agent": "PokemonScanner/1.0"},
                         timeout=20, stream=True)
        r.raise_for_status()
        dest.write_bytes(r.content)
        return dest.stat().st_size > 100
    except Exception:
        return False


def import_cards(cards: list[dict], repo: CatalogRepository,
                 download_images: bool) -> tuple[int, int]:
    """Insert/update cards into DB. Returns (upserted, images_downloaded)."""
    import sqlite3
    now = dt.datetime.utcnow().isoformat()
    CATALOG_IMAGES_DIR.mkdir(parents=True, exist_ok=True)

    rows_to_upsert = []
    image_tasks: list[tuple[str, str, Path]] = []

    for card in cards:
        api_id = card.get("id", "")
        if not api_id:
            continue
        images = card.get("images", {})
        image_url = images.get("small") or images.get("large") or ""
        set_logo_url = card.get("set", {}).get("images", {}).get("logo", "")
        price = extract_price(card)

        rows_to_upsert.append({
            "api_id": api_id,
            "name": card.get("name", ""),
            "set_name": card.get("set", {}).get("name", ""),
            "card_number": card.get("number", ""),
            "language": card.get("language", "en") or "en",
            "best_price": price,
            "price_currency": "USD",
            "image_url": image_url,
            "set_logo_url": set_logo_url,
            "now": now,
        })

        if download_images and image_url:
            safe_id = api_id.replace("/", "_").replace("\\", "_")
            dest = CATALOG_IMAGES_DIR / f"{safe_id}.jpg"
            image_tasks.append((api_id, image_url, dest))

    # Bulk upsert into DB
    with repo.database.connect() as conn:
        for r in rows_to_upsert:
            conn.execute("""
                INSERT INTO card_catalog
                    (api_id, name, set_name, card_number, language,
                     best_price, price_currency, image_url, local_image_path,
                     set_logo_url, set_local_logo_path, fetched_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,NULL,?,NULL,?,?)
                ON CONFLICT(api_id) DO UPDATE SET
                    best_price=excluded.best_price,
                    price_currency=excluded.price_currency,
                    image_url=excluded.image_url,
                    set_logo_url=excluded.set_logo_url,
                    updated_at=excluded.updated_at
            """, (
                r["api_id"], r["name"], r["set_name"], r["card_number"], r["language"],
                r["best_price"], r["price_currency"], r["image_url"],
                r["set_logo_url"], r["now"], r["now"],
            ))
        conn.commit()

    downloaded = 0
    if download_images and image_tasks:
        with ThreadPoolExecutor(max_workers=_IMAGE_WORKERS) as pool:
            futures = {pool.submit(download_image, aid, url, dest): aid
                       for aid, url, dest in image_tasks}
            for fut in as_completed(futures):
                if fut.result():
                    downloaded += 1
        # Update local_image_path for all successfully downloaded images
        with repo.database.connect() as conn:
            for api_id, _, dest in image_tasks:
                if dest.exists() and dest.stat().st_size > 1024:
                    conn.execute(
                        "UPDATE card_catalog SET local_image_path=? WHERE api_id=?",
                        (str(dest), api_id),
                    )
            conn.commit()

    # Download set logos
    if download_images:
        logo_tasks: list[tuple[str, str]] = []
        seen_sets: set[str] = set()
        for r in rows_to_upsert:
            sn = r.get("set_name") or r.get("name") or ""
            if sn in seen_sets or not r.get("set_logo_url"):
                continue
            seen_sets.add(sn)
            safe = sn.replace("/", "_").replace("\\", "_").replace(" ", "_")
            dest = CATALOG_IMAGES_DIR / f"logo_{safe}.png"
            if not dest.exists() or dest.stat().st_size < 100:
                logo_tasks.append((sn, r["set_logo_url"]))
        if logo_tasks:
            print(f"Lade {len(logo_tasks)} Set-Logos …")
            import datetime as _dt2
            with ThreadPoolExecutor(max_workers=_IMAGE_WORKERS) as pool:
                def _dl_logo(args_: tuple[str, str]) -> tuple[str, str | None]:
                    sn_, url_ = args_
                    safe_ = sn_.replace("/", "_").replace("\\", "_").replace(" ", "_")
                    dest_ = CATALOG_IMAGES_DIR / f"logo_{safe_}.png"
                    try:
                        import requests as _r2
                        rr = _r2.get(url_, headers={"User-Agent": "PokemonScanner/1.0"}, timeout=15)
                        rr.raise_for_status()
                        dest_.write_bytes(rr.content)
                        return sn_, str(dest_)
                    except Exception:
                        return sn_, None
                logo_results = list(pool.map(_dl_logo, logo_tasks))
            now_l = _dt2.datetime.utcnow().isoformat()
            with repo.database.connect() as conn:
                for sn_, path_ in logo_results:
                    if path_:
                        conn.execute(
                            "UPDATE card_catalog SET set_local_logo_path=?, updated_at=? WHERE set_name=?",
                            (path_, now_l, sn_),
                        )
                conn.commit()
            ok_logos = sum(1 for _, p in logo_results if p)
            print(f"{ok_logos}/{len(logo_tasks)} Set-Logos gespeichert.")

    return len(rows_to_upsert), downloaded


def main() -> None:
    parser = argparse.ArgumentParser(description="Bulk-Import Pokémon cards")
    parser.add_argument("--no-images", action="store_true",
                        help="Skip image downloads (only DB data)")
    parser.add_argument("--dry-run", action="store_true",
                        help="List sets only, write nothing")
    parser.add_argument("--set", metavar="CODE",
                        help="Import only this set (partial match on id or name)")
    parser.add_argument("--since", metavar="YEAR", type=int, default=2024,
                        help="Earliest release year to import (default: 2024)")
    args = parser.parse_args()

    download_images = not args.no_images

    print(f"Lade Sets ab {args.since} …")
    sets = fetch_sets_since(args.since)
    if not sets:
        print("Keine Sets gefunden.")
        return

    if args.set:
        needle = args.set.lower()
        sets = [s for s in sets if needle in s["id"].lower() or needle in s["name"].lower()]
        if not sets:
            print(f"Kein Set gefunden das '{args.set}' enthält.")
            return

    print(f"\n{'Set':<12} {'Name':<35} {'Release':<12} {'Karten':>6}")
    print("-" * 70)
    total_cards = 0
    for s in sets:
        count = s.get("total", "?")
        print(f"{s['id']:<12} {s['name']:<35} {s.get('releaseDate','?'):<12} {count:>6}")
        if isinstance(count, int):
            total_cards += count

    print(f"\nGesamt: {len(sets)} Sets, ~{total_cards} Karten")
    if download_images:
        print(f"Bilder werden heruntergeladen nach: {CATALOG_IMAGES_DIR}")
    else:
        print("Bilder werden NICHT heruntergeladen (--no-images aktiv)")

    if args.dry_run:
        print("\n[dry-run] Kein Schreibvorgang.")
        return

    # Init DB
    db = Database(DATA_DIR / "pokemon_scanner.sqlite3")
    repo = CatalogRepository(db)

    grand_total_upserted = 0
    grand_total_images = 0
    start = time.monotonic()

    for i, s in enumerate(sets, 1):
        set_id = s["id"]
        set_name = s["name"]
        print(f"\n[{i}/{len(sets)}] {set_name} ({set_id}) …", end=" ", flush=True)

        cards = fetch_cards_for_set(set_id)
        print(f"{len(cards)} Karten", end=" ", flush=True)

        upserted, images = import_cards(cards, repo, download_images)
        grand_total_upserted += upserted
        grand_total_images += images

        if download_images:
            print(f"→ {upserted} gespeichert, {images} Bilder", flush=True)
        else:
            print(f"→ {upserted} gespeichert", flush=True)

        time.sleep(_REQUEST_DELAY)

    elapsed = time.monotonic() - start
    print(f"\n✓ Fertig in {elapsed:.0f}s — {grand_total_upserted} Karten gespeichert"
          + (f", {grand_total_images} Bilder" if download_images else ""))


if __name__ == "__main__":
    main()
