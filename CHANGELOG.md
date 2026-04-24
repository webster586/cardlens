# Changelog

## 0.4.5 - 2026-04-24 - Markt UI-Verbesserungen

### Changed
- `ui/market_widget.py`: Marktpreis-Label in Verkauf- und Kauf-Zeilen: Schriftgröße von 12px auf 24px verdoppelt, Farbe auf Weiß (#ffffff) geändert
- `ui/main_window.py`: Markt-Untermenüs (Verkauf, Kauf, Historie) aus linker Sidebar entfernt — Navigation erfolgt über Tab-Buttons im Markt-Widget

## 0.4.4 - 2026-04-23 - Album-Seite Auto-Fill

### Added
- `db/repositories.py`: `CollectionRepository.update_album_page(entry_id, album_page)` — schreibt nur das `album_page`-Feld (kein Read-Modify-Write nötig)
- `db/repositories.py`: `AlbumRepository.get_slot_entry_id(album_id, page_num, slot_index)` — gibt `collection_entry_id` eines Slots zurück
- `ui/album_widget.py`: `_AlbumSlot` und `_AlbumPageGrid` erhalten Parameter `album_name: str`; `_AlbumDetailView._rebuild_spread()` übergibt den aktuellen Albumtitel
- `ui/album_widget.py`: `_on_add_clicked()` — füllt `album_page` nach `set_slot()` automatisch als `"{Albumname}, Seite {N}"`
- `ui/album_widget.py`: `dropEvent()` — liest beide Entry-IDs vor dem Swap und setzt `album_page` für jede beteiligte Karte korrekt

## 0.4.3 - 2026-04-23 - Album-Features & Image-Architektur

### Added
- `ui/image_cache.py` (neu): Unified Image-Modul — `card_image_path()`, `resolve_card_image()`, `load_card_pixmap()`, `CardImageDownloadWorker`; QPixmapCache auf 80 MB gesetzt
- `ui/catalog_dialog.py`: Finish-Dropdown (11 Optionen: Normal, Holo, Reverse Holo, Full Art, Alt Art, Rainbow, Gold, Secret Rare, Promo, Shiny, Etched Holo) ersetzt Holo/Foil-Checkbox
- `ui/album_widget.py`: Album-Umbenennen via Doppelklick auf den Albumtitel im Detailview-Header (`QInputDialog`)

### Changed
- `ui/catalog_dialog.py`, `ui/album_widget.py`, `ui/album_scan_dialog.py`, `ui/main_window.py`: alle Image-Lade-Pfade auf `image_cache.py` umgestellt — kein doppelter Cache-Code mehr
- `db/repositories.py`: Migration ergänzt `finish TEXT DEFAULT ''`; Backfill `is_foil=1` → `finish='holo'`; `update_entry()` verwendet `finish` statt `is_foil`; `split_entry()` kopiert `finish`

### Fixed
- `ui/album_widget.py`: `mouseMoveEvent` nutzte `self._pixmap` statt `self._raw_pixmap` (Drag-Pixmap war leer)

## 0.4.2 - 2026-04-22 - Performance Audit III (17 Punkte)

### Performance
- `recognition/preprocess.py`: `INTER_CUBIC` → `INTER_LINEAR` bei `crop_name_zone()` — 2–3× schneller, kein OCR-Qualitätsverlust
- `recognition/ocr.py`: `import re` aus Class-Body in Modul-Top; `_CHAR_MAP_KEYS` frozenset für O(1)-Prüfung; `_clean()` ruft `translate()` jetzt nur noch auf wenn tatsächlich Block-Zeichen im Text sind
- `recognition/pipeline.py`: `imagehash`/`PIL`-Import und alle `name_translator`-/`CardCandidate`-Importe aus Hot-Paths in Modul-Top verschoben; `_dedup_by_api_id()` neu — alle Scan- und Suche-Rückgabepfade deduplizieren jetzt Kandidaten nach `api_id`
- `datasources/pokemontcg.py`: Fetch-Cache von `dict` auf `OrderedDict`-LRU umgestellt — max 300 Einträge, `move_to_end` bei Cache-Hit, `popitem(last=False)` bei Overflow — O(1) statt O(n) Eviction
- `db/repositories.py`: Modul-level `_schema_checked: set[str]` verhindert wiederholtes `PRAGMA table_info` bei jeder Instanz-Erstellung; `OcrCorrectionRepository` hält Top-500-Korrekturen in `_text_cache` im Speicher (Cache-Invalidierung nach `save_correction()`), `find_best_by_text()` liest damit kein DB-Roundtrip mehr pro Scan
- `db/catalog_repository.py`: `_schema_checked`-Guard analog; neue Spalte `set_release_year INTEGER` wird bei Upsert befüllt; `get_top_performers()` nutzt `set_release_year` direkt statt `CAST(SUBSTR(set_release_date,1,4))` per Zeile; `search()` verwendet Prefix-LIKE (`name%`) statt `%name%` für Name-Suche
- `ui/main_window.py`: `translate_de_to_en_fuzzy`, `CandidateMatcher`, `CATALOG_IMAGES_DIR` aus Hot-Path-Methoden in Modul-Top; `_fill_candidate_table()` mit `setUpdatesEnabled(False)` + `blockSignals(True)` umschlossen und auf max 15 Zeilen begrenzt; OCR-Overlay-Cache-Key ohne Pixmap-Dimensionen (Label ist immer 420×560)
- `ui/album_scan_dialog.py`: `_rotate_image()` hält rotiertes Bild als `_rotated_cv_image: np.ndarray` im Speicher — Disk-Write nur noch einmal pro Winkel über `_get_or_write_rotated_path()`; veraltete doppelte Contour-Filter-Schleife in `_auto_detect()` entfernt

## 0.4.1 - 2026-04-22 - Security & Compliance

### Security
- `ui/catalog_dialog.py`: TCGPlayer-Key-Dialog — `pub_edit` erhält jetzt `EchoMode.Password` (beide Keys jetzt maskiert)
- `ui/catalog_dialog.py`: Klartextwarnung im TCGPlayer-Key-Dialog (`settings.json` unverschlüsselt)
- `core/logging_setup.py`: Log-Level des File-Handlers von `DEBUG` auf `INFO` geändert (rohe OCR-Texte / API-Antworten werden nicht mehr in Log-Dateien geschrieben)

### Documentation
- `README.md`: "Noch nicht enthalten"-Abschnitt entfernt; aktuellen Funktionsumfang, Datenschutz-Hinweise und Build-Anweisungen ergänzt
- `ui/about_dialog.py`: Disclaimer-Text erweitert auf 10 TCG-Marken (Pokémon, MTG, Yu-Gi-Oh!, Lorcana, One Piece, Dragon Ball Super, Digimon, Flesh and Blood, Weiss Schwarz, KeyForge); Button-Text `&&`-Fix

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
