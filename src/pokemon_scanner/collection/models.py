from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class CollectionEntry:
    name: str
    set_name: str
    card_number: str
    language: str
    quantity: int = 1
    last_price: float | None = None
    price_currency: str | None = None
    notes: str = ""
    image_path: str | None = None
