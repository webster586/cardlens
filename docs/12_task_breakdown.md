# Task Breakdown

## Foundation -- erledigt
- App bootstrap, Settings, Logging, SQLite, UI Shell

## Workflow -- erledigt
- Bild laden, Kandidaten rendern, Auswahl bestaetigen
- Sammlung laden / speichern (Catalog-Dialog)

## Export -- erledigt
- CSV, JSON, XLSX

## Recognition -- erledigt
- EasyOCR Integration, Bildvorverarbeitung
- pokemontcg.io API v2, eBay, Price-Aggregator
- OCR-Zone Kalibrierung im Livebild

## Kamera -- erledigt
- USB-Kamera-Livebild, Zoom, Freeze, Scan-Trigger

## UI-Polish -- erledigt
- Set-Logo in Vorschau
- Windows-kompatible Buttons
- Pixmap-Cache, async Datenladen

## Distribution -- erledigt
- PyInstaller Spec (EasyOCR-Modelle + Logos gebundelt)
- paths.py Dual-Mode (dev/frozen)
- build.ps1 + Inno Setup .iss
- RAR-Archiv

## Performance-Optimierung (Audit III) -- erledigt 2026-04-22
- `INTER_CUBIC` → `INTER_LINEAR` (preprocess)
- `re`-Import und Translator-Imports in Modul-Top (ocr, pipeline, main_window)
- `_dedup_by_api_id()` in pipeline — keine doppelten Karten mehr in Kandidatenliste
- OrderedDict-LRU für pokemontcg-Fetch-Cache (max 300, O(1) Eviction)
- PRAGMA-table_info-Guard (`_schema_checked`) in repositories + catalog_repository
- In-Memory-Korrektur-Cache (Top-500) in OcrCorrectionRepository
- `set_release_year`-Spalte + `get_top_performers()`-Query-Fix
- Prefix-LIKE-Suche in `catalog_repository.search()`
- `blockSignals` + `setUpdatesEnabled` in `_fill_candidate_table`; Top-15-Limit
- OCR-Overlay-Cache-Key ohne Pixmap-Dimensionen
- Album-Rotation In-Memory (`_rotated_cv_image`)

## Offen
- Bulk-Queue / Debounce
- Batch-Statistiken
- Installer-Test auf sauberem Rechner
- Mehrsprachige OCR-Verbesserung
