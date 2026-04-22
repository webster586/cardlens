# Datenquellen

## Card Metadata
- PokemonTCG API Adapter
- TCGdex Adapter

## Preise
- Preisadapter als austauschbare Provider
- Aggregation pro Karte
- lokaler Cache mit Zeitstempel

## Adapter-Regeln
- Jeder Adapter liefert normierte DTOs
- Fehler eines Adapters brechen den Scan nicht ab
- Preise werden als Snapshot gespeichert
- ungeprüfte Provider bleiben deaktivierbar
