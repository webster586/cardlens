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
- build.ps1 + Inno Setup .iss Skript
- RAR-Archiv fuer direkte Weitergabe

## Phase 7 Bulk-Optimierung -- OFFEN
- Queueing, Debounce, Statistiken
- Batch-Scan mehrerer Karten
- Performance-Optimierung bei grosser Sammlung
