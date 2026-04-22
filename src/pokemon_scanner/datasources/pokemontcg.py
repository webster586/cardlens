from __future__ import annotations

import logging
import time
from collections import OrderedDict

import requests

from src.pokemon_scanner.datasources.base import CardCandidate
from src.pokemon_scanner.datasources.name_translator import translate_to_en

_LOG = logging.getLogger(__name__)
_BASE_URL = "https://api.pokemontcg.io/v2/cards"
_TIMEOUT = 20
_CACHE_TTL = 120.0  # seconds — identical queries within 2 min reuse results
_CACHE_MAX = 300    # max entries; LRU evicts oldest on overflow
# {query_string: (timestamp, results)} — OrderedDict for O(1) LRU eviction
_fetch_cache: OrderedDict[str, tuple[float, list[CardCandidate]]] = OrderedDict()


class PokemonTcgAdapter:
    # Suffixes that should not be used alone as a wildcard search term
    _SUFFIXES = {"VMAX", "VSTAR", "GX", "EX", "V", "TAG", "TEAM", "LEGEND"}

    def __init__(self, api_key: str = "") -> None:
        self._api_key = api_key

    def _headers(self) -> dict[str, str]:
        if self._api_key:
            return {"X-Api-Key": self._api_key}
        return {}

    def search_cards(self, query: str, language: str = "") -> list[CardCandidate]:
        if not query:
            return []
        # Remove only OCR block-char artefacts; keep Unicode umlauts
        unicode_clean = self._fix_block_chars(query).strip()
        ascii_clean = self._sanitize(unicode_clean)
        if not unicode_clean:
            return []

        # Resolve non-EN translation upfront (pokemontcg.io indexes cards by English name)
        words = ascii_clean.split() if ascii_clean else unicode_clean.split()
        en_name = translate_to_en(unicode_clean) or translate_to_en(words[0] if words else "")
        # Use English name as primary search term when available; keep original as fallback
        primary = en_name if (en_name and en_name.lower() != unicode_clean.lower()) else unicode_clean
        primary_ascii = self._sanitize(primary)
        _LOG.info("PokemonTCG search: %r → primary=%r (raw OCR: %r)", unicode_clean, primary, query)

        lang_suffix = f" language:{language}" if language else ""

        # 1. Exact English name (most reliable — pokemontcg.io indexes by English)
        candidates = self._fetch(f'name:"{primary}"{lang_suffix}')
        if candidates:
            return candidates

        # 2. ASCII-transliterated primary name
        if primary_ascii and primary_ascii != primary:
            candidates = self._fetch(f'name:"{primary_ascii}"{lang_suffix}')
            if candidates:
                return candidates

        # 3. Wildcard on first non-suffix base word of English name
        primary_words = primary_ascii.split() if primary_ascii else primary.split()
        base_word = next((w for w in primary_words if w.upper() not in self._SUFFIXES), primary_words[0])
        base_ascii = base_word.encode("ascii", "ignore").decode("ascii")
        if base_ascii and not base_ascii.replace("/", "").isdigit() and "/" not in base_ascii:
            candidates = self._fetch(f"name:*{base_ascii}*{lang_suffix}")
        if candidates:
            return candidates

        # 4. If language filter yielded nothing, retry without it
        if language:
            _LOG.info("No results for language=%r, retrying without language filter", language)
            return self.search_cards(query, language="")

        return candidates

    @staticmethod
    def _fix_block_chars(text: str) -> str:
        """Replace EasyOCR block-character artefacts with proper Unicode characters."""
        replacements = {
            "\u2584": "\u00dc",  # ▄ → Ü
            "\u2580": "\u00df",  # ▀ → ß
            "\u2588": "\u00dc",  # █ → Ü
            "\u258c": "\u00c4",  # ▌ → Ä
            "\u2590": "\u00d6",  # ▐ → Ö
        }
        result = text
        for char, replacement in replacements.items():
            result = result.replace(char, replacement)
        return result

    @staticmethod
    def _sanitize(text: str) -> str:
        """Transliterate Unicode umlauts to ASCII and strip remaining non-ASCII."""
        replacements = {
            "\u00fc": "ue", "\u00f6": "oe", "\u00e4": "ae", "\u00df": "ss",
            "\u00dc": "Ue", "\u00d6": "Oe", "\u00c4": "Ae",
        }
        result = text
        for char, replacement in replacements.items():
            result = result.replace(char, replacement)
        result = result.encode("ascii", "ignore").decode("ascii").strip()
        return result

    def search_by_number(self, number: str, language: str = "") -> list[CardCandidate]:
        """Search by card number (e.g. '055/088'). Returns cards matching that collector number."""
        _LOG.info("PokemonTCG search by number: %r (language=%r)", number, language)
        num_part = number.split("/")[0].lstrip("0") or number.split("/")[0]
        lang_suffix = f" language:{language}" if language else ""
        return self._fetch(f"number:{num_part}{lang_suffix}")

    def _fetch(self, q: str) -> list[CardCandidate]:
        # --- cache lookup ---
        now = time.monotonic()
        cached = _fetch_cache.get(q)
        if cached is not None and now - cached[0] < _CACHE_TTL:
            _LOG.debug("Cache hit for %r", q)
            _fetch_cache.move_to_end(q)  # LRU: mark as recently used
            return list(cached[1])

        try:
            resp = requests.get(
                _BASE_URL,
                params={"q": q, "pageSize": 6, "orderBy": "-set.releaseDate"},
                headers=self._headers(),
                timeout=_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as exc:
            _LOG.warning("PokemonTCG API error: %s", exc)
            return []

        candidates: list[CardCandidate] = []
        for card in data.get("data", []):
            eur = self._extract_eur_price(card)
            usd = self._extract_price(card)
            best_price = eur if eur is not None else usd
            price_currency = "EUR" if eur is not None else "USD"
            images = card.get("images", {})
            image_url = images.get("small") or images.get("large") or ""
            set_data = card.get("set", {})
            set_images = set_data.get("images", {})
            candidates.append(
                CardCandidate(
                    source="pokemontcg.io",
                    name=card.get("name", ""),
                    set_name=set_data.get("name", ""),
                    card_number=card.get("number", ""),
                    language=card.get("language", "en") or "en",
                    confidence=0.85,
                    best_price=best_price,
                    price_currency=price_currency,
                    price_source=("Cardmarket" if eur is not None else "TCGPlayer") if best_price is not None else "",
                    notes=f"ID: {card.get('id', '')}",
                    api_id=card.get('id', ''),
                    image_url=image_url,
                    set_logo_url=set_images.get("logo") or "",
                    rarity=card.get("rarity", "") or "",
                    supertype=card.get("supertype", "") or "",
                    subtypes=",".join(card.get("subtypes", []) or []),
                    hp=card.get("hp", "") or "",
                    types=",".join(card.get("types", []) or []),
                    artist=card.get("artist", "") or "",
                    pokedex_numbers=",".join(str(n) for n in (card.get("nationalPokedexNumbers", []) or [])),
                    regulation_mark=card.get("regulationMark", "") or "",
                    legalities="|".join(f"{k}:{v}" for k, v in (card.get("legalities", {}) or {}).items()),
                    set_series=set_data.get("series", "") or "",
                    set_total=set_data.get("total", 0) or 0,
                    set_symbol_url=set_images.get("symbol") or "",
                    eur_price=eur,
                    usd_price=usd,
                )
            )
        # LRU eviction: discard oldest entry when cache is at max capacity
        if len(_fetch_cache) >= _CACHE_MAX:
            _fetch_cache.popitem(last=False)  # remove least-recently-used
        _fetch_cache[q] = (time.monotonic(), candidates)
        return candidates

    @staticmethod
    def _extract_eur_price(card: dict) -> float | None:
        """Extract EUR price from Cardmarket."""
        prices = card.get("cardmarket", {}).get("prices", {})
        for key in ("averageSellPrice", "trendPrice", "lowPrice"):
            p = prices.get(key)
            if p is not None:
                return round(float(p), 2)
        return None

    def _extract_price(self, card: dict) -> float | None:
        prices = card.get("tcgplayer", {}).get("prices", {})
        for variant in ("normal", "holofoil", "reverseHolofoil", "1stEditionHolofoil"):
            p = prices.get(variant, {}).get("market")
            if p is not None:
                return round(float(p), 2)
        return None
