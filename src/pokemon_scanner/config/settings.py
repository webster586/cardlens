from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from src.pokemon_scanner.core.paths import DATA_DIR, RUNTIME_DIR


@dataclass(slots=True)
class AppSettings:
    app_name: str = "CardLens"
    database_path: str = str(DATA_DIR / "pokemon_scanner.sqlite3")
    export_dir: str = str(Path("exports"))
    default_language: str = "en"
    automode_confidence_threshold: float = 0.95
    enable_automode: bool = False
    enable_mock_recognition: bool = True
    last_camera_index: int = 0
    preferred_language: str = ""
    # Custom OCR name-zone (relative 0–1 fractions of the card image).
    # name_zone_custom=False → use hardcoded defaults in preprocess.py.
    name_zone_custom: bool = False
    name_zone_x1: float = 0.04
    name_zone_y1: float = 0.03
    name_zone_x2: float = 0.60
    name_zone_y2: float = 0.15
    # TCGPlayer API credentials for sealed-product price lookup (ETB, Booster Bundle).
    tcgplayer_public_key: str = ""
    tcgplayer_private_key: str = ""
    # pokemontcg.io API key (optional, raises rate limit to 20k req/day).
    # Enter your own key at https://dev.pokemontcg.io/ — no key means ~1000 req/day.
    pokemontcg_api_key: str = ""
    # Set to True after the user has accepted the first-run disclaimer.
    disclaimer_accepted: bool = False

    @classmethod
    def settings_file(cls) -> Path:
        return RUNTIME_DIR / "settings.json"

    @classmethod
    def load(cls) -> "AppSettings":
        path = cls.settings_file()
        if not path.exists():
            settings = cls()
            settings.save()
            return settings

        raw = json.loads(path.read_text(encoding="utf-8"))
        known = {f.name for f in fields(cls)}
        filtered = {k: v for k, v in raw.items() if k in known}
        return cls(**filtered)

    def save(self) -> None:
        self.settings_file().write_text(
            json.dumps(asdict(self), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
