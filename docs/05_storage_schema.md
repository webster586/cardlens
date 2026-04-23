# Storage Schema

## Zentrale Tabellen
- collection_entries
- price_snapshots
- scan_events
- app_settings
- crash_reports

## Prinzip
- bestätigte Sammlungseinträge getrennt von Roh-Scan-Events
- Preise historisierbar
- Dubletten als quantity verwalten

## collection_entries — relevante Felder
| Feld | Typ | Bedeutung |
|---|---|---|
| `finish` | TEXT | Karten-Veredelung: `''` Normal, `holo`, `reverse`, `full_art`, `alt_art`, `rainbow`, `gold`, `secret`, `promo`, `shiny`, `etched` |
| `is_foil` | INTEGER | Legacy (0/1) — wird mit `finish` synchron gehalten |
| `album_page` | TEXT | Freitext-Ort im Album, z. B. `"Holos, Seite 2"` — wird automatisch befüllt beim Slot-Einfügen/Verschieben |

## Repositories — wichtige Methoden
- `CollectionRepository.update_album_page(entry_id, album_page)` — schreibt nur `album_page`
- `AlbumRepository.get_slot_entry_id(album_id, page_num, slot_index)` — gibt `collection_entry_id` eines Slots zurück
