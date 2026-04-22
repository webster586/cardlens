# CardLens

Windows-Desktop-App zum Scannen, Identifizieren und Verwalten von Pokémon-Karten (USB-Kamera oder Fotos).

**Quellcode & Lizenz:** [github.com/webster586/cardlens](https://github.com/webster586/cardlens) — MIT (eigener Code), LGPL v3 (PySide6/Qt)

## Zielbild

Das Projekt ist so strukturiert, dass es schrittweise zu einer ersten nutzbaren App führt:

1. Bilder/Fotos laden
2. Kandidatenliste erzeugen
3. Nutzer bestätigt Treffer
4. Sammlung persistent speichern
5. Preise speichern und exportieren
6. Live-Kamera integrieren
7. OCR + API-Lookups als echte Erkennung ergänzen

## Aktueller Stand des Repos

Dieses Start-Repo enthält:

- vollständige Markdown-Projektdokumentation
- klare Modul- und Ordnerstruktur
- Logging-, Crash- und SQLite-Grundgerüst
- lauffähige Desktop-App als Workflow-Prototyp
- Mock-Recognition und Mock-Preisfluss
- Export nach CSV, JSON und XLSX

## Noch nicht enthalten

- echte OCR
- echte API-Abfragen
- echte Kamera-Streams
- echte Preisadapter
- Holo-/Sleeve-spezifische Bildlogik

## Schnellstart

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m src.pokemon_scanner.main
```

## Empfohlener Arbeitsmodus

1. Erst Foundation stabil halten
2. Dann Foto-Import + Persistenz
3. Dann echte Recognition-Pipeline
4. Dann Kamera
5. Dann Preisadapter
6. Danach Bulk-Optimierung
