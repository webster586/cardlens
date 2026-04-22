# Architektur

## Layer

1. UI
2. Application Services
3. Recognition Pipeline
4. Data Source Adapters
5. Persistence
6. Diagnostics / Logging

## Hauptfluss

Frame oder Foto
-> Vorverarbeitung
-> OCR-Zonen / Matcher
-> Kandidaten-Ranking
-> manuelle Bestätigung
-> Preisanreicherung
-> Persistenz
-> Export / Sammlung

## Projektentscheidungen
- Python zuerst für schnelle Computer-Vision-Iteration
- SQLite lokal für Persistenz
- Adapter-Schicht für Datenquellen
- Feature-Gates für schrittweise Aktivierung realer Erkennung
