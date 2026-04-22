from src.pokemon_scanner.core.paths import DATA_DIR, RUNTIME_DIR


def test_runtime_dirs_defined() -> None:
    assert DATA_DIR.name == "data"
    assert RUNTIME_DIR.name == "runtime"
