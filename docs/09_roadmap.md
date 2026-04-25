# Roadmap

## Phase 1 Foundation -- DONE
- Struktur, Logging, Settings, SQLite, UI-Shell

## Phase 2 Foto-Workflow -- DONE
- Bildimport, Mock Scan, Kandidatenbestaetigung, Persistenz

## Phase 3 Echte Recognition -- DONE
- Bildvorverarbeitung, EasyOCR, Kandidaten-Ranking
- pokemontcg.io API v2 Adapter, eBay-Adapter, Price-Aggregator

## Phase 4 Kamera -- DONE
- USB-Kamera-Livebild, Freeze bei Kartenwechsel, Scan-Trigger
- OCR-Zone Kalibrierung, Zoom-Slider

## Phase 5 UI-Polish -- DONE
- Catalog-Dialog (Sammlung, Top-Performer, Kartendetails)
- Set-Logo in Karten-Vorschau
- Pixmap-Cache, async Datenladen
- Windows-kompatible Buttons (kein Emoji)

## Phase 6 Distribution -- DONE
- PyInstaller --onedir Build mit EasyOCR-Modellen + Logos
- paths.py Dual-Mode (dev / frozen APPDATA)
- build.ps1
- RAR-Archiv fuer direkte Weitergabe

## Phase 7 Bulk-Optimierung -- TEILWEISE DONE
- Performance-Optimierung bei grosser Sammlung (Performance Audit III — 17 Punkte umgesetzt)
  - LRU-Cache, In-Memory-Korrektur-Lookup, PRAGMA-Guard, set_release_year-Spalte
  - INTER_LINEAR, blockSignals, Kandidaten-Dedup, OCR-Overlay-Cache-Key
  - Album-Rotation In-Memory
- Queueing, Debounce, Statistiken — offen
- Batch-Scan mehrerer Karten — offen

## Phase 8 Album & Sammlung UX -- DONE
- Unified Image-Architektur (`image_cache.py`) für alle UI-Module
- Finish-Dropdown (11 Optionen) in Kartenbearbeitungs-Dialog
- Album umbenennen via Doppelklick
- Album-Seite Auto-Fill beim Einfügen und Verschieben von Karten
