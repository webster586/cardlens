import sqlite3, pathlib
db = sqlite3.connect(str(pathlib.Path("data/pokemon_scanner.sqlite3")))
db.row_factory = sqlite3.Row
print("=== Hippo cards ===")
for r in db.execute("SELECT name,language,set_name FROM card_catalog WHERE name LIKE '%ippo%' LIMIT 20").fetchall():
    print(dict(r))
print("=== Ratt cards ===")
for r in db.execute("SELECT name,language,set_name FROM card_catalog WHERE name LIKE '%atti%' OR name LIKE '%attf%' LIMIT 20").fetchall():
    print(dict(r))
print("=== Languages ===")
for r in db.execute("SELECT language,COUNT(*) n FROM card_catalog GROUP BY language ORDER BY n DESC LIMIT 10").fetchall():
    print(dict(r))
