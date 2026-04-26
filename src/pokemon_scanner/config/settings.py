from __future__ import annotations

import json
import os
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
    enable_mock_recognition: bool = False
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
    # Start the application maximized (fullscreen). Can be toggled in settings.
    start_maximized: bool = True
    # Base font size in pixels (10–18). Adjustable via Help → Debug-Konsole.
    ui_font_size: int = 13
    # Per-category font sizes (pixels, except ui_font_card_pt which is points).
    # All independently adjustable via Debug-Konsole → Darstellung.
    ui_font_small:   int = 11   # Secondary labels, groupbox headers, tooltips
    ui_font_xs:      int = 10   # Progress bar, metadata
    ui_font_tiny:    int = 9    # Hints, micro catalog labels
    ui_font_heading: int = 15   # Section headers, card names in panels
    ui_font_large:   int = 18   # Prices, key display values
    ui_font_card_pt: int = 6    # Card-price paintEvent (point size)
    ui_font_mono:    int = 11   # Log-viewer / console (Consolas)

    @classmethod
    def settings_file(cls) -> Path:
        return RUNTIME_DIR / "settings.json"

    @classmethod
    def load(cls) -> "AppSettings":
        path = cls.settings_file()
        if not path.exists():
            settings = cls()
            settings.save()
        else:
            raw = json.loads(path.read_text(encoding="utf-8"))
            known = {f.name for f in fields(cls)}
            filtered = {k: v for k, v in raw.items() if k in known}
            settings = cls(**filtered)
        # Environment variables take precedence over persisted values.
        # This lets CI/deployment inject keys without touching the JSON file.
        if key := os.environ.get("POKEMONTCG_API_KEY"):
            settings.pokemontcg_api_key = key
        if key := os.environ.get("TCGPLAYER_PUBLIC_KEY"):
            settings.tcgplayer_public_key = key
        if key := os.environ.get("TCGPLAYER_PRIVATE_KEY"):
            settings.tcgplayer_private_key = key
        return settings

    def save(self) -> None:
        self.settings_file().write_text(
            json.dumps(asdict(self), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
