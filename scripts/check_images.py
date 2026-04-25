"""Quick diagnostics for image_url vs local_image_path in card_catalog."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.pokemon_scanner.config.settings import AppSettings
from src.pokemon_scanner.db.database import Database

settings = AppSettings.load()
db = Database(str(settings.database_path))
conn = db.connect()

total = conn.execute("SELECT COUNT(*) FROM card_catalog").fetchone()[0]
http_urls = conn.execute("SELECT COUNT(*) FROM card_catalog WHERE image_url LIKE 'http%'").fetchone()[0]
local_as_url = conn.execute(
    "SELECT COUNT(*) FROM card_catalog WHERE image_url IS NOT NULL AND image_url NOT LIKE 'http%'"
).fetchone()[0]
null_urls = conn.execute("SELECT COUNT(*) FROM card_catalog WHERE image_url IS NULL").fetchone()[0]

print(f"Total cards:              {total}")
print(f"  HTTP image_url:         {http_urls}")
print(f"  Local path as image_url:{local_as_url}")
print(f"  NULL image_url:         {null_urls}")

rows = conn.execute(
    "SELECT api_id, local_image_path FROM card_catalog WHERE local_image_path IS NOT NULL"
).fetchall()
missing = [dict(r) for r in rows if not Path(dict(r)["local_image_path"]).exists()]
present = len(rows) - len(missing)

print(f"\nlocal_image_path set:     {len(rows)}")
print(f"  File present on disk:   {present}")
print(f"  Stale (file missing):   {len(missing)}")
if missing:
    print("  Sample stale entries:")
    for r in missing[:5]:
        print(f"    {r['api_id']} → {r['local_image_path']}")
