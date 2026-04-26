# Changelog

## 0.6.3 - 2026-04-27 - Album Kartenslots & TOC Inhaltsübersicht

### Added
- `db/repositories.py`: neue Methode `AlbumRepository.get_album_pages_detail(album_id)` — liefert eine Zeile pro belegtem Slot (page_num, set_name, card_name, card_value, purchase_price), sortiert nach Seite / Set / Name; wird vom TOC-Panel genutzt
- `datasources/name_translator.py`: `_ROMANIZATION_ALIASES`-Dict für Pokémon mit Runic/CJK-Namen (Baojian → Chien-Pao, Wochien → Wo-Chien, Chiyu → Chi-Yu, Chiling → Ting-Lu); `find_en_names_for_de_partial()` prüft Aliases vor regulärer Fuzzy-Suche

### Changed
- `ui/album_widget.py`: `_AlbumTocPanel.refresh()` komplett neu als Listenansicht — pro Seite: Header-Zeile (weiß/fett, „Seite N" | „K Karten" | Seitenwert), G&V-Zeile (grün/rot), dann pro Set: Set-Name-Zeile (blau/fett, 8 px Einzug) + je Karten-Zeile (grau, 16 px Einzug); nutzt `get_album_pages_detail()`
- `ui/album_widget.py`: Kartenslots im Album passen sich jetzt dem echten Pokémon-Karten-Seitenverhältnis an (63 × 88 mm, ≈ 1:1,40) — `_AlbumPageGrid._apply_card_aspect()` berechnet via `resizeEvent` die größtmögliche Slotgröße die in Breite **und** Höhe passt; Kartenbild skaliert mit `Qt.KeepAspectRatio` (Letterbox mit dunklem Rand)

## 0.6.2 - 2026-04-26 - Album Drag-Drop & Preis-Batch

### Fixed
- `ui/album_widget.py`: Harter Absturz bei jedem Karten-Drop im Album — Windows OLE Nested Event Loop in `QDrag.exec()` zerstörte den Quell-Slot-Widget, während der C++-OLE-Thread noch lief; Fix: `drag_started`/`drag_ended`-Signalkette (`_AlbumSlot` → `_AlbumPageGrid` → `_AlbumDetailView`), `_rebuild_spread()` wird während eines aktiven Drags nie aufgerufen; `_on_slot_changed()` nutzt jetzt `grid.reload()` statt `_rebuild_spread()`
- `ui/album_widget.py`: Karte wurde nach Auto-Flip auf neue Doppelseite nicht visuell übernommen — neue Methode `_live_navigate_to()` remappt bestehende Grids/Slots in-place während des Drags, ohne Widgets zu zerstören; `_pending_rebuild = True` sorgt für vollständigen Rebuild nach Drop-Ende

### Changed
- `db/repositories.py`: neue Methode `AlbumRepository.get_album_missing_price_api_ids(album_id)` — SQL-Query über alle Album-Seiten, liefert alle api_ids ohne Preis (kein UI-Loop über visible Slots mehr)
- `ui/album_widget.py`: `_auto_fetch_missing_prices()` fragt jetzt das gesamte Album aller Seiten ab — ein Worker-Start fetcht alle fehlenden Preise auf einmal; Worker wird nicht neu gestartet wenn bereits einer läuft
- `ui/album_widget.py`: `_AlbumRefreshWorker.run()` nutzt Batch-Requests (`GET /v2/cards?q=id:X OR id:Y`, 100 IDs/Batch) statt pro-Karte-Anfragen — 300-Karten-Album: 3 statt 300 HTTP-Calls

## 0.6.1 - 2026-04-25 - Bild-Stabilität & Preis-Korrektheit

### Fixed
- `db/catalog_repository.py`: `images.scrydex.com` zur `_ALLOWED_IMAGE_HOSTS`-Whitelist hinzugefügt — Karten aus JP/Fan-Sets (z. B. "Ascended Heroes", "Mega Evolution") zeigten dauerhaft `?`-Platzhalter, weil ihre Bild-URLs auf scrydex.com zeigten und geblockt wurden; 4 fehlende Dateien werden nun beim nächsten Start nachgeladen
- `datasources/pokemontcg.py`: EUR-Preis-Extraktion priorisiert jetzt `trendPrice` vor `averageSellPrice`; `averageSellPrice` mittelt historische Verkäufe (inkl. alter/graded) → viel zu hohe Preise bei verbreiteten Karten (z. B. Bulbasaur 151 = 59 € statt ~0,20 €); `trendPrice` spiegelt aktuellen Markt korrekt wider
- `ui/album_widget.py`, `ui/album_scan_dialog.py`: `CardImageDownloadWorker` wurde mit `parent=self` erzeugt — bei Widget-Destroy löschte Qt den noch laufenden C++-Thread → `"QThread: Destroyed while thread is still running"` + Exit-Code 1; Fix: `parent` entfernt, stattdessen `finished.connect(deleteLater)` für sicheres async Cleanup

### Changed
- `db/catalog_repository.py` (Data): EUR-Preise für 98 Katalogeinträge aus DB gelöscht, damit sie beim nächsten Scan mit korrekter `trendPrice`-Logik neu abgerufen werden

## 0.6.0 - 2026-04-24 - Wert-Verlauf & Set-Vollständigkeit

### Added
- `db/repositories.py`: neue Tabelle `collection_value_snapshots` (id, snapshot_date UNIQUE, total_value, unique_cards, total_qty, created_at); Migration beim App-Start
- `db/repositories.py`: `CollectionRepository.record_collection_value_snapshot()` — speichert max. 1× pro Tag den aktuellen Gesamtwert der Sammlung
- `db/repositories.py`: `CollectionRepository.get_collection_value_history()` — gibt alle Snapshots älteste→neueste zurück
- `db/catalog_repository.py`: `CatalogRepository.get_set_completion()` — JOIN von `card_catalog` × `collection_entries`; liefert je Set: owned_count, catalog_count, set_total, set_series, release_year
- `ui/stats_widget.py`: 3-Tab-Layout (Übersicht · Wert-Verlauf · Set-Vollständigkeit)
  - **Übersicht**: bisherige 7 Metriken-Karten unverändert
  - **Wert-Verlauf**: `_ValueHistoryChart` — Linien-Chart mit Gradientfill; täglich auto-snapshot beim Öffnen der Seite; X-Achse mit Datumslabels
  - **Set-Vollständigkeit**: scrollbare Liste aller Sets aus dem Katalog mit QProgressBar, Bruchzahl (besessen / gesamt) und Komplett-Badge; sortiert neueste zuerst
- `ui/main_window.py`: `StatsWidget` bekommt `catalog_repo=self.catalog_repo` übergeben


### Added
- `ui/stats_widget.py` (neu): Sammlungs-Statistiken-Seite mit 7 Metriken-Karten (einzigartige Karten, Exemplare, Gesamtwert, Zum Verkauf, Verkauft, Erlös, geschätzter Gewinn); eigener Sidebar-Eintrag "📊 Statistiken"
- `db/repositories.py`: `CollectionRepository.get_collection_stats()` — aggregiert Karten, Menge, Marktwert, Verkaufserlös und Gewinn in einer DB-Abfrage
- `db/repositories.py`: Migration ergänzt Spalte `price_alert REAL` in `collection_entries`
- `db/repositories.py`: `CollectionRepository.set_price_alert(entry_id, threshold)` — setzt oder löscht Preisalarm-Schwellwert
- `ui/market_widget.py`: Kauf-Tab vollständig implementiert: scrollbare Liste aller Sammlungskarten mit 🔔-Button pro Zeile; Alarm-Dialog (SpinBox €) zum Setzen des Zielpreises; Zeilen werden grün markiert und "ALARM: Preis unter Zielwert!" angezeigt, wenn `last_price ≤ price_alert`; Filter "Nur Alarme" + Suche
### Changed
- `ui/main_window.py`: Scan-Debounce via `QTimer` (400 ms, singleShot) in `_queue_scan()` — verhindert Pipeline-Überlastung bei schnellen Folder-Watch-Events; neuestes Bild wird stets gesetzt, ältere verworfen
- `ui/main_window.py`: `_on_scan_finished()` und `_on_scan_error()` prüfen nach Abschluss auf ausstehende `_pending_scan_path` und starten den nächsten Scan automatisch
- `ui/market_widget.py`: `list_all_for_market()` liefert nun auch `price_alert` im Ergebnisdict
- `ui/market_widget.py`: Kauf-Tab lädt Daten lazy via `load_if_needed()` beim ersten Tab-Wechsel

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
- `ui/about_dialog.py`: Disclaimer-Text aktualisiert; Button-Text `&&`-Fix

## 0.4.0 - 2026-04-22 - Build Pipeline
### Added
- `build.ps1`: PyInstaller-Build-Script
- `installer/runtime_hook_easyocr.py`: Runtime-Hook setzt `EASYOCR_MODULE_PATH` im frozen Build
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
