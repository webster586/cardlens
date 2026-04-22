from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class PriceSnapshot:
    source: str
    price: float | None
    currency: str | None = "EUR"


@dataclass(slots=True)
class CardCandidate:
    source: str
    name: str
    set_name: str
    card_number: str
    language: str
    confidence: float
    best_price: float | None
    price_currency: str | None = "EUR"
    notes: str = ""
    image_url: str = ""
    set_logo_url: str = ""
    price_source: str = ""
    # Extended metadata
    rarity: str = ""
    supertype: str = ""
    subtypes: str = ""           # comma-separated list
    hp: str = ""
    types: str = ""              # comma-separated list
    artist: str = ""
    pokedex_numbers: str = ""    # comma-separated list
    regulation_mark: str = ""
    legalities: str = ""         # pipe-separated "key:value" pairs
    set_series: str = ""
    set_total: int = 0
    set_symbol_url: str = ""
    eur_price: float | None = None
    usd_price: float | None = None
