from __future__ import annotations

import difflib

from src.pokemon_scanner.datasources.base import CardCandidate

# Language families: languages that should be treated as equivalent for matching
_LANG_FAMILY: dict[str, frozenset[str]] = {
    "zh-Hant": frozenset({"zh-Hant", "zh-Hans"}),
    "zh-Hans": frozenset({"zh-Hant", "zh-Hans"}),
}


class CandidateMatcher:
    def rank(self, candidates: list[CardCandidate], query: str = "") -> list[CardCandidate]:
        """Rank candidates by a weighted score: OCR confidence + name similarity.

        Without a *query* the sort falls back to pure confidence (legacy).
        """
        if not candidates:
            return []
        if not query:
            return sorted(candidates, key=lambda c: c.confidence, reverse=True)
        q = query.lower().strip()

        def _score(c: CardCandidate) -> float:
            name_sim = difflib.SequenceMatcher(None, q, c.name.lower()).ratio()
            return c.confidence * 0.35 + name_sim * 0.65

        return sorted(candidates, key=_score, reverse=True)

    @staticmethod
    def lang_matches(candidate_lang: str, scan_lang: str) -> bool:
        """Return True when *candidate_lang* belongs to the same language family as *scan_lang*."""
        if not scan_lang:
            return True
        family = _LANG_FAMILY.get(scan_lang, frozenset({scan_lang}))
        return (candidate_lang or "en") in family

    def rerank_by_language(self, candidates: list[CardCandidate], language: str) -> list[CardCandidate]:
        """Move candidates whose language matches *language* to the front, preserving relative order."""
        if not language:
            return candidates
        matching = [c for c in candidates if self.lang_matches(c.language, language)]
        non_matching = [c for c in candidates if not self.lang_matches(c.language, language)]
        return matching + non_matching
