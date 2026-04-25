# CardLens

Windows-Desktop-App zum Scannen, Identifizieren und Verwalten von Trading-Card-Game-Karten (USB-Kamera oder Fotos).

**Quellcode & Lizenz:** [github.com/webster586/cardlens](https://github.com/webster586/cardlens) — MIT (eigener Code), LGPL v3 (PySide6/Qt)

## TCG & Markenhinweis

CardLens unterstützt derzeit **Pokémon TCG**.
Die App hat keine offizielle Verbindung zu Nintendo / The Pokémon Company International.
Alle Marken sind Eigentum ihrer jeweiligen Rechteinhaber.

## Funktionsumfang (aktuell)

- Live-Kamerastream (USB) mit Freeze-Logik
- Foto-Import (Einzelbild)
- EasyOCR-Texterkennung mit Bildvorverarbeitung
- pokemontcg.io API v2 — Kartensuche, Preise (Cardmarket / TCGPlayer)
- TCGPlayer API — Versiegelungspreise (ETB, Booster Bundle)
- Sammlungs-Datenbank (SQLite WAL)
- Catalog-Dialog mit Set-Logos, Karten-Artwork, Top-Performer
- Export nach CSV, JSON und XLSX
- PyInstaller --onedir Build (ZIP-Distribution)

## Datenschutz & Sicherheit

- Alle Nutzerdaten (Sammlung, Logs) werden **lokal** gespeichert — keine Telemetrie, kein Cloud-Sync
- API-Keys (pokemontcg.io, TCGPlayer) werden im Klartext in `%APPDATA%\CardLens\runtime\settings.json` gespeichert
- Log-Dateien: `%APPDATA%\CardLens\logs\app.log` (INFO-Level, max. 3 × 2 MB, rotiert)
- Keine personenbezogenen Daten werden an Dritte übermittelt

## Schnellstart

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m src.pokemon_scanner.main
```

## Build

```powershell
.\build.ps1   # PyInstaller → dist\CardLens\CardLens.exe
```
