# Changelog

## 0.4.0 - 2026-04-22 - Build & Installer Pipeline
### Added
- `build.ps1`: PyInstaller-Build-Script mit optionalem `-Installer`-Flag fuer Inno Setup
- `installer/runtime_hook_easyocr.py`: Runtime-Hook setzt `EASYOCR_MODULE_PATH` im frozen Build
- `installer/pokemon_scanner.iss`: Inno Setup 6 Skript (kein UAC, Deinstall behaelt User-Daten)
- `pokemon_scanner.spec`: PyInstaller --onedir Spec; buendelt EasyOCR-Modelle (~330 MB) und 85 Set-Logos (~5 MB)
### Changed
- `core/paths.py`: Dual-Mode-Pfade (dev: Repo-Root, frozen: %APPDATA%\CardLens); `_seed_bundled_logos()` kopiert Logos bei erstem Start
- `db/repositories.py`: Index `ix_ce_api_id` fuer schnellere API-ID-Lookups
### Notes
- Dist-Ordner: `dist/CardLens/` (~1049 MB), RAR-Archiv: `dist/CardLens.rar` (~527 MB)

## 0.3.0 - UI-Polishing
### Changed
- `ui/main_window.py`: Zoom-Buttons Emoji -> Text ("OCR-Zone", "Zone x", "1:1") fuer Windows-Kompatibilitaet
- `ui/main_window.py`: Set-Logo-Anzeige im Karten-Vorschau-Panel (`lbl_set_logo` ueber Kartenvorschau)
- `ui/main_window.py`: Zoom-Slider kompakter (80px fest), kein Tick-Marks
- `ui/catalog_dialog.py`: Pixmap-Cache, async `_SammlungDataWorker` (QThread)
### Fixed
- Emoji-Buttons (`🎯`, `🗑️`) wurden auf Windows als leere Quadrate gerendert

## 0.2.0 - Recognition Pipeline
### Added
- EasyOCR-Integration fuer Texterkennung auf Kartenfotos
- Bildvorverarbeitung (preprocess.py), OCR-Wrapper (ocr.py), Kandidaten-Matcher (matcher.py)
- Kamera-Service (camera_service.py) mit USB-Livebild und Freeze-Logik
- pokemontcg.io API v2 Adapter, eBay-Adapter, Price-Aggregator
- Catalog-Dialog mit Sammlung, Top-Performer, Kartendetails

## 0.1.0 - Initial scaffold
### Added
- Projektstruktur angelegt
- Dokumentationspaket angelegt
- Logging- und Crash-Ordner vorgesehen
- SQLite-Grundlage angelegt
- Lauffaehiger Desktop-Workflow-Prototyp
- Mock-Recognition eingebaut
- Export nach CSV/JSON/XLSX eingebaut
