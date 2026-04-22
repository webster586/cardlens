# Repo-Struktur

```
pokemon_scanner_repo/
  src/pokemon_scanner/
    app.py, main.py
    camera/         -- Kamera-Service (USB-Livebild)
    collection/     -- Sammlung: Models + Service
    config/         -- Settings
    core/           -- Logging, Crash-Handler, paths.py
    datasources/    -- pokemontcg.io, eBay, Price-Aggregator
    db/             -- SQLite: database.py, repositories.py, schema.sql
    export/         -- CSV, JSON, XLSX Exporter
    recognition/    -- OCR, Preprocess, Matcher, Pipeline
    ui/             -- main_window.py, catalog_dialog.py, styles.py
  installer/
    runtime_hook_easyocr.py  -- PyInstaller Runtime-Hook
    pokemon_scanner.iss      -- Inno Setup 6 Installer-Skript
  tests/
  scripts/         -- bootstrap_env.ps1, run_dev.py
  docs/
  data/
    catalog_images/  -- logo_*.png (85 Set-Logos)
  dist/            -- PyInstaller Output (nach build.ps1)
  build.ps1        -- Build-Script
  pokemon_scanner.spec  -- PyInstaller Spec
```

## Pfad-Konvention
- Dev-Modus: alle Pfade relativ zum Repo-Root (`core/paths.py`)
- Frozen (EXE): Nutzer-Daten unter `%APPDATA%\CardLens\`

## Regel
Jede neue groessere Capability bekommt einen eigenen Modulordner.
