from __future__ import annotations

import re
from pathlib import Path

try:
    import imagehash
    from PIL import Image as _PIL_Image
    _IMAGEHASH_AVAILABLE = True
except ImportError:
    _IMAGEHASH_AVAILABLE = False

from src.pokemon_scanner.datasources.base import CardCandidate
from src.pokemon_scanner.datasources.name_translator import correct_raw_for_search, correct_ocr_pokemon_name
from src.pokemon_scanner.datasources.pokemontcg import PokemonTcgAdapter
from src.pokemon_scanner.recognition.matcher import CandidateMatcher
from src.pokemon_scanner.recognition.ocr import OcrEngine
from src.pokemon_scanner.recognition.preprocess import Preprocessor


# ---------------------------------------------------------------------------
# Keyword overrides: when a known item/trainer keyword appears in the raw OCR
# output, use this English search term directly instead of name translation.
# This handles German item names (e.g. "Flossenfossil") that the name-
# translator doesn't know but the TCG API finds via English keyword search.
# ---------------------------------------------------------------------------
_OCR_KEYWORD_OVERRIDES: dict[str, str] = {
    "fossil": "fossil",
}


def _keyword_override(raw: str) -> str | None:
    """Return a keyword search term when *raw* OCR contains a known item keyword."""
    lower = raw.lower()
    for keyword, search_term in _OCR_KEYWORD_OVERRIDES.items():
        if keyword in lower:
            return search_term
    return None


def _compute_phash(image_path: str) -> str:
    """Return perceptual hash string for *image_path*, or '' on failure."""
    if not _IMAGEHASH_AVAILABLE:
        return ""
    try:
        return str(imagehash.phash(_PIL_Image.open(image_path)))
    except Exception:
        return ""


class RecognitionPipeline:
    def __init__(self, database=None, pokemontcg_api_key: str = "", correction_repo=None) -> None:
        self.preprocessor = Preprocessor()
        self.ocr_engine = OcrEngine()
        self.card_adapter = PokemonTcgAdapter(api_key=pokemontcg_api_key)
        self.matcher = CandidateMatcher()
        self._correction_repo = correction_repo
        # Local catalog — used as primary source to avoid API calls
        self._catalog_repo = None
        if database is not None:
            from src.pokemon_scanner.db.catalog_repository import CatalogRepository
            self._catalog_repo = CatalogRepository(database)

    def _search_local(self, name: str, number: str = "") -> list[CardCandidate]:
        """Search local catalog DB. Returns empty list if catalog unavailable."""
        if self._catalog_repo is None:
            return []
        return self._catalog_repo.search_candidates(name, number)

    def scan_image(self, image_path: str | Path, language: str = "", zone: tuple[float, float, float, float] | None = None) -> tuple[list[CardCandidate], str]:
        """Return (candidates, warp_path_str, raw_ocr_text).

        *warp_path_str* is the path to the perspective-corrected card preview image,
        or an empty string when card detection failed.
        *raw_ocr_text* is the raw text read by OCR before translation.

        Search order: local catalog DB → online API (fallback when local has no hit).
        """
        path = Path(image_path)
        card_img, warp_path = self.preprocessor.detect_card_to_file(path)
        warp_str = str(warp_path) if warp_path else ""

        ocr_result = self.ocr_engine.extract_text(path, card_img=card_img, language=language, zone=zone)
        raw_query = ocr_result.get("name", "").strip()
        raw_query = raw_query[:255]  # guard against excessively long OCR output

        # Translate / OCR-correct the raw name → English for API lookup
        if raw_query:
            keyword_q = _keyword_override(raw_query)
            query = keyword_q or correct_raw_for_search(raw_query)
        else:
            query = ""

        # --- Correction lookup (text-based) ---
        if raw_query and self._correction_repo is not None:
            correction = self._correction_repo.find_best_by_text(raw_query)
            if correction:
                # Build candidate directly from stored data — no network call needed
                direct = CardCandidate(
                    source="ocr_correction",
                    name=correction["correct_name"],
                    set_name=correction.get("correct_set_name", ""),
                    card_number=correction.get("correct_card_number", ""),
                    language=language or "en",
                    confidence=0.95,
                    best_price=None,
                    notes=f"ID: {correction['correct_api_id']}",
                    api_id=correction["correct_api_id"],
                )
                # Supplement with richer local data if available
                local_cands = self._search_local(correction["correct_name"])
                for c in local_cands:
                    if c.api_id == correction["correct_api_id"]:
                        direct = c
                        break
                return [direct], warp_str, raw_query

        if query:
            # 1. Local catalog first
            candidates = self._search_local(query)
            if candidates:
                ranked = self.matcher.rank(candidates, query=query)
                return self._dedup_by_api_id(self.matcher.rerank_by_language(ranked, language)), warp_str, raw_query
            # 2. Online API fallback (already translates internally, but pass EN name for consistency)
            candidates = self.card_adapter.search_cards(query, language=language)
            if candidates:
                ranked = self.matcher.rank(candidates, query=query)
                return self._dedup_by_api_id(self.matcher.rerank_by_language(ranked, language)), warp_str, raw_query

        # --- Correction lookup (pHash-based, when text search found nothing) ---
        if self._correction_repo is not None and warp_str:
            phash = _compute_phash(warp_str)
            if phash:
                correction = self._correction_repo.find_best_by_phash(phash)
                if correction:
                    # Build candidate directly from stored data — no network call needed
                    direct = CardCandidate(
                        source="ocr_correction",
                        name=correction["correct_name"],
                        set_name=correction.get("correct_set_name", ""),
                        card_number=correction.get("correct_card_number", ""),
                        language=language or "en",
                        confidence=0.90,
                        best_price=None,
                        notes=f"ID: {correction['correct_api_id']}",
                        api_id=correction["correct_api_id"],
                    )
                    local_cands = self._search_local(correction["correct_name"])
                    for c in local_cands:
                        if c.api_id == correction["correct_api_id"]:
                            direct = c
                            break
                    return [direct], warp_str, raw_query

        # Fallback: try to identify by card number (e.g. '055/088')
        card_number = self.ocr_engine.extract_number(path, card_img=card_img)
        if card_number:
            swapped = self._swap_confusable_digits(card_number)
            variants = [card_number] + ([swapped] if swapped != card_number else [])
            # Local number search first
            for num in variants:
                candidates = self._search_local("", number=num)
                if candidates:
                    return self._dedup_by_api_id(self.matcher.rank(candidates, query=query)), warp_str, raw_query
            # Online number search fallback
            # Search order: (number+lang) → (swap+lang) → (number) → (swap)
            if language:
                for num in variants:
                    candidates = self.card_adapter.search_by_number(num, language=language)
                    if candidates:
                        return self._dedup_by_api_id(self.matcher.rank(candidates, query=query)), warp_str, raw_query
            for num in variants:
                candidates = self.card_adapter.search_by_number(num, language="")
                if candidates:
                    return self._dedup_by_api_id(self.matcher.rank(candidates, query=query)), warp_str, raw_query

        return [], warp_str, raw_query

    @staticmethod
    def _dedup_by_api_id(candidates: list[CardCandidate]) -> list[CardCandidate]:
        """Keep only the highest-ranked candidate per api_id (preserves order)."""
        seen: set[str] = set()
        result: list[CardCandidate] = []
        for c in candidates:
            key = c.api_id or ""
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            result.append(c)
        return result

    @staticmethod
    def _swap_confusable_digits(number: str) -> str:
        """Swap 6↔8 in the numerator part only (common OCR confusion on dark cards)."""
        parts = number.split("/")
        num = parts[0]
        rest = "/" + parts[1] if len(parts) > 1 else ""
        if "6" in num:
            return num.replace("6", "8") + rest
        if "8" in num:
            return num.replace("8", "6") + rest
        return number

    _NUMBER_RE = re.compile(r'^\d{1,3}/\d{1,3}$|^[A-Z]{1,3}\d{1,4}$')

    def search_by_name(self, query: str, language: str = "") -> list[CardCandidate]:
        """Direct name search without image — used by the manual search field.

        If *query* looks like a card number (e.g. '076/151'), routes to number search.
        Search order: local catalog → online API fallback.
        """
        if self._NUMBER_RE.match(query.strip()):
            candidates = self._search_local("", number=query.strip())
            if not candidates:
                candidates = self.card_adapter.search_by_number(query.strip(), language=language)
        else:
            en_query = correct_ocr_pokemon_name(query) or query
            candidates = self._search_local(en_query)
            if not candidates:
                candidates = self.card_adapter.search_cards(en_query, language=language)
            query = en_query
        ranked = self.matcher.rank(candidates, query=query) if candidates else []
        reranked = self.matcher.rerank_by_language(ranked, language)
        return self._dedup_by_api_id(reranked)
