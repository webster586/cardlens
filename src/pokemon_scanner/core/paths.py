from __future__ import annotations

import os
import sys
from pathlib import Path


if getattr(sys, "frozen", False):
    # PyInstaller bundle (--onedir): _MEIPASS is the dist folder next to the EXE.
    # User-writable data lives in %APPDATA%\CardLens\ so it survives
    # reinstalls and works even when the app is installed under Program Files.
    _MEIPASS_DIR: Path | None = Path(getattr(sys, "_MEIPASS"))
    _BASE = Path(os.environ.get("APPDATA", str(Path.home()))) / "CardLens"
else:
    _MEIPASS_DIR = None
    _BASE = Path(__file__).resolve().parents[3]

# Public path constants — identical names in dev and frozen mode so every
# importer works without changes.
PROJECT_ROOT = _BASE
LOG_DIR = _BASE / "logs"
CRASH_DIR = _BASE / "crashes"
CACHE_DIR = _BASE / "cache"
EXPORT_DIR = _BASE / "exports"
RUNTIME_DIR = _BASE / "runtime"
DATA_DIR = _BASE / "data"
CATALOG_IMAGES_DIR = DATA_DIR / "catalog_images"


def ensure_runtime_dirs() -> None:
    for path in [LOG_DIR, CRASH_DIR, CACHE_DIR, EXPORT_DIR, RUNTIME_DIR, DATA_DIR, CATALOG_IMAGES_DIR]:
        path.mkdir(parents=True, exist_ok=True)
