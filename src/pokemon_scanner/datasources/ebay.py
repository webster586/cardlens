from __future__ import annotations

from src.pokemon_scanner.datasources.base import PriceSnapshot


class EbayPriceAdapter:
    def search_price(self, query: str) -> PriceSnapshot:
        # Placeholder: real eBay integration not yet implemented.
        return PriceSnapshot(source="ebay", price=None, currency="EUR")
