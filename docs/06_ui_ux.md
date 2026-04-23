# UI / UX

## Hauptbereiche
- linker Bereich: Bild / Livebild
- mittlerer Bereich: Kandidatenliste
- rechter Bereich: Sammlung / letzte Treffer
- unterer Bereich: Status / Logs / Aktionen

## Kernaktionen
- Bild laden
- Kamera starten
- Scan auslösen
- Kandidat bestätigen
- Export
- Sammlung durchsuchen

## UX-Regeln
- große klickbare Bereiche
- klare Statusmeldungen
- keine versteckten kritischen Aktionen
- gleiche Abläufe für Foto und Kamera

## Album-Funktionen
- **Finish-Dropdown** im Karten-Bearbeitungs-Dialog (11 Optionen): Normal, Holo, Reverse Holo, Full Art, Alt Art, Rainbow / Hyper Rare, Gold, Secret Rare, Promo, Shiny, Etched Holo
- **Album umbenennen**: Doppelklick auf den Albumtitel im Detailview-Header öffnet `QInputDialog`
- **Album-Seite Auto-Fill**: Beim Einfügen einer Karte via `+` oder Drag & Drop wird `album_page` automatisch auf `"{Albumname}, Seite {N}"` gesetzt (immer überschreiben; beim Entfernen unverändert)

## Image-Architektur
- `ui/image_cache.py`: zentrales Image-Modul für alle UI-Module
- QPixmapCache-Limit: 80 MB
- `CardImageDownloadWorker.done` Signal: emittiert absoluten lokalen Pfad oder `""`
