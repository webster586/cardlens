from pathlib import Path

from src.pokemon_scanner.db.database import Database


def test_database_initialize(tmp_path: Path) -> None:
    db_path = tmp_path / "test.sqlite3"
    db = Database(str(db_path))
    db.initialize()
    assert db_path.exists()
