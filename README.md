# CardLens

Windows-Desktop-App zum Scannen, Identifizieren und Verwalten von Trading-Card-Game-Karten (USB-Kamera oder Fotos).

**Quellcode & Lizenz:** [github.com/webster586/cardlens](https://github.com/webster586/cardlens) — MIT (eigener Code), LGPL v3 (PySide6/Qt)

## Unterstützte TCGs (Markenzeichen)

CardLens hat keine offizielle Verbindung zu den Herausgebern der unterstützten Kartenspiele.
Alle Marken sind Eigentum ihrer jeweiligen Rechteinhaber:

- **Pokémon TCG** — Nintendo / The Pokémon Company International
- **Magic: The Gathering** — Wizards of the Coast LLC (Hasbro, Inc.)
- **Yu-Gi-Oh!** — Konami Digital Entertainment Co., Ltd.
- **Disney Lorcana** — The Walt Disney Company / Ravensburger AG
- **One Piece / Dragon Ball Super / Digimon** — Bandai Co., Ltd.
- **Flesh and Blood** — Legend Story Studios Ltd.
- **Weiss Schwarz** — Bushiroad Inc.
- **KeyForge** — Fantasy Flight Games / Asmodee Group

## Funktionsumfang (aktuell)

- Live-Kamerastream (USB) mit Freeze-Logik
- Foto-Import (Einzelbild)
- EasyOCR-Texterkennung mit Bildvorverarbeitung
- pokemontcg.io API v2 — Kartensuche, Preise (Cardmarket / TCGPlayer)
- TCGPlayer API — Versiegelungspreise (ETB, Booster Bundle)
- Sammlungs-Datenbank (SQLite WAL)
- Catalog-Dialog mit Set-Logos, Karten-Artwork, Top-Performer
- Export nach CSV, JSON und XLSX
- Inno Setup Installer + PyInstaller --onedir Build

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
.\build.ps1            # PyInstaller → dist\CardLens\CardLens.exe
.\build.ps1 -Installer # zusätzlich Inno Setup Installer
```
