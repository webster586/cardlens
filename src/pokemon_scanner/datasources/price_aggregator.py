from __future__ import annotations

import dataclasses

from src.pokemon_scanner.datasources.base import CardCandidate
from src.pokemon_scanner.datasources.ebay import EbayPriceAdapter


# NOTE: EbayPriceAdapter is a stub — real eBay integration is not yet implemented.
# PriceAggregator is wired up but intentionally not called from the main pipeline
# until the eBay adapter is functional.
class PriceAggregator:
    def __init__(self) -> None:
        self.ebay_adapter = EbayPriceAdapter()

    def enrich(self, candidates: list[CardCandidate]) -> list[CardCandidate]:
        enriched: list[CardCandidate] = []
        for candidate in candidates:
            ebay_snapshot = self.ebay_adapter.search_price(candidate.name)
            # Only use eBay price when the candidate has no price yet
            if candidate.best_price is None and ebay_snapshot.price is not None:
                price = ebay_snapshot.price
                currency = ebay_snapshot.currency
                price_source = "eBay"
            else:
                price = candidate.best_price
                currency = candidate.price_currency
                price_source = candidate.price_source
            # Preserve every field; only override price-related ones
            enriched.append(dataclasses.replace(
                candidate,
                best_price=price,
                price_currency=currency,
                price_source=price_source,
            ))
        return enriched
