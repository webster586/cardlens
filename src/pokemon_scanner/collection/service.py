from __future__ import annotations

import re

from src.pokemon_scanner.db.repositories import CollectionRepository
from src.pokemon_scanner.datasources.base import CardCandidate

_VALID_API_ID = re.compile(r'^[a-z0-9][a-z0-9\-]{0,38}$')


class CollectionService:
    def __init__(self, repository: CollectionRepository) -> None:
        self.repository = repository

    def confirm_candidate(
        self,
        candidate: CardCandidate,
        image_path: str | None = None,
        condition: str = "NM",
        album_page: str = "",
    ) -> None:
        api_id: str | None = None
        if candidate.notes and candidate.notes.startswith("ID: "):
            raw_id = candidate.notes[4:].strip()
            api_id = raw_id if _VALID_API_ID.match(raw_id) else None
        self.repository.upsert_by_identity(
            api_id=api_id,
            name=candidate.name,
            set_name=candidate.set_name,
            card_number=candidate.card_number,
            language=candidate.language,
            last_price=candidate.best_price,
            price_currency=candidate.price_currency,
            notes=candidate.notes,
            image_path=image_path,
            condition=condition,
            album_page=album_page,
        )
        self.repository.create_scan_event(
            image_path=image_path or "",
            selected_candidate_name=candidate.name,
            selected_candidate_set=candidate.set_name,
            selected_candidate_number=candidate.card_number,
            selected_candidate_language=candidate.language,
            confidence=candidate.confidence,
        )

    def list_entries(self) -> list[dict]:
        return self.repository.list_all()

    def find_by_candidate(self, candidate: "CardCandidate") -> "dict | None":
        api_id: str | None = None
        if candidate.notes and candidate.notes.startswith("ID: "):
            api_id = candidate.notes[4:].strip()
        return self.repository.find_by_identity(
            api_id=api_id,
            name=candidate.name,
            set_name=candidate.set_name or "",
            card_number=candidate.card_number or "",
            language=candidate.language or "",
        )
