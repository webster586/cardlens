# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for CardLens.
One-dir build (recommended: smaller, faster startup than --onefile for heavy ML deps).
Output: dist/CardLens/CardLens.exe

What is bundled (and what is NOT):
  YES  src/pokemon_scanner/db/schema.sql     — DB init script
  YES  ~/.EasyOCR/model/*                    — OCR models (~330 MB, all languages)
  NO   data/catalog_images/logo_*.png        — downloaded on-demand from pokemontcg.io API
  NO   data/catalog_images/<card images>     — downloaded on-demand
  NO   data/pokemon_scanner.sqlite3          — user gets a fresh empty DB

User-writable data (logs, exports, settings, DB, downloaded card images) is
written to %APPDATA%\\CardLens\\ at runtime — see paths.py.
"""

import glob
from pathlib import Path

ROOT = Path(SPECPATH)
SRC  = ROOT / "src"

# ---------- Data files bundled into the EXE directory ----------

# SQL schema (accessed via Path(__file__).with_name('schema.sql') in database.py)
schema_datas = [
    (str(SRC / "pokemon_scanner" / "db" / "schema.sql"), "src/pokemon_scanner/db"),
]

# EasyOCR models — bundled so the end-user needs no internet.
# Download once by running: python -m easyocr.model_download en
_easyocr_model_dir = Path.home() / ".EasyOCR" / "model"
if _easyocr_model_dir.is_dir():
    model_datas = [
        (str(f), "model")
        for f in _easyocr_model_dir.iterdir()
        if f.is_file()
    ]
    print(f"[spec] Bundling {len(model_datas)} EasyOCR model file(s) from {_easyocr_model_dir}")
else:
    import warnings
    warnings.warn(
        f"EasyOCR models not found at {_easyocr_model_dir}. "
        "Run the app once with internet access to download them, then rebuild."
    )
    model_datas = []

datas = schema_datas + model_datas

# ---------- Hidden imports that PyInstaller misses ----------
hidden = [
    # App internals
    "src.pokemon_scanner",
    "src.pokemon_scanner.app",
    "src.pokemon_scanner.core.paths",
    "src.pokemon_scanner.core.logging_setup",
    "src.pokemon_scanner.core.crash_handler",
    "src.pokemon_scanner.config.settings",
    "src.pokemon_scanner.db.database",
    "src.pokemon_scanner.db.repositories",
    "src.pokemon_scanner.ui.main_window",
    "src.pokemon_scanner.ui.catalog_dialog",
    "src.pokemon_scanner.collection.models",
    "src.pokemon_scanner.collection.service",
    "src.pokemon_scanner.datasources.pokemontcg",
    "src.pokemon_scanner.datasources.ebay",
    "src.pokemon_scanner.datasources.price_aggregator",
    "src.pokemon_scanner.recognition.pipeline",
    "src.pokemon_scanner.recognition.ocr",
    "src.pokemon_scanner.recognition.matcher",
    "src.pokemon_scanner.recognition.preprocess",
    "src.pokemon_scanner.export.exporters",
    "src.pokemon_scanner.camera.camera_service",
    # Third-party
    "easyocr",
    "easyocr.easyocr",
    "easyocr.recognition",
    "easyocr.detection",
    "easyocr.utils",
    "cv2",
    "PIL",
    "PIL.Image",
    "PIL.ImageEnhance",
    "PIL.ImageFilter",
    "torch",
    "torchvision",
    "torchvision.transforms",
    "numpy",
    "requests",
    "urllib3",
    "charset_normalizer",
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "sklearn",
    "scipy",
]

# ---------- Analysis ----------
a = Analysis(
    [str(SRC / "pokemon_scanner" / "main.py")],
    pathex=[str(ROOT), str(SRC)],
    binaries=[],
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=["installer/runtime_hook_easyocr.py"],
    excludes=[
        "matplotlib", "tkinter", "_tkinter", "wx", "PyQt5", "PyQt6",
        "IPython", "jupyter", "notebook",
        "torch.cuda", "torch.distributed", "caffe2",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    noarchive=False,
    optimize=1,
)

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="CardLens",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,           # no console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    icon=None,               # add .ico path here if you have one
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="CardLens",
)
