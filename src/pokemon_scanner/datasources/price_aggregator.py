from __future__ import annotations

from src.pokemon_scanner.datasources.base import CardCandidate
from src.pokemon_scanner.datasources.ebay import EbayPriceAdapter


class PriceAggregator:
    def __init__(self) -> None:
        self.ebay_adapter = EbayPriceAdapter()

    def enrich(self, candidates: list[CardCandidate]) -> list[CardCandidate]:
        enriched: list[CardCandidate] = []
        for candidate in candidates:
            ebay_price = self.ebay_adapter.search_price(candidate.name)
            price = candidate.best_price
            if price is None and ebay_price.price is not None:
                price = ebay_price.price
            enriched.append(
                CardCandidate(
                    source=candidate.source,
                    name=candidate.name,
                    set_name=candidate.set_name,
                    card_number=candidate.card_number,
                    language=candidate.language,
                    confidence=candidate.confidence,
                    best_price=price,
                    price_currency=ebay_price.currency,
                    notes=candidate.notes,
                    image_url=candidate.image_url,
                    set_logo_url=candidate.set_logo_url,
                )
            )
        return enriched
