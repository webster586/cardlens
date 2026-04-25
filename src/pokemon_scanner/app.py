from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication

from src.pokemon_scanner.core.paths import ensure_runtime_dirs
from src.pokemon_scanner.core.logging_setup import configure_logging, get_logger
from src.pokemon_scanner.core.crash_handler import install_global_exception_hook
from src.pokemon_scanner.db.database import Database
from src.pokemon_scanner.config.settings import AppSettings
from src.pokemon_scanner.ui.main_window import MainWindow
from src.pokemon_scanner.ui.about_dialog import DisclaimerDialog
from src.pokemon_scanner.ui.styles import (
    get_app_qss,
    set_small, set_xs, set_tiny, set_heading, set_large, set_card_pt, set_mono,
)


def main() -> int:
    ensure_runtime_dirs()
    configure_logging()
    install_global_exception_hook()
    logger = get_logger(__name__)
    logger.info("Starting CardLens")

    settings = AppSettings.load()
    database = Database(settings.database_path)
    database.initialize()

    app = QApplication(sys.argv)

    # Register bundled Montserrat font
    _fonts_dir = Path(__file__).parent / "assets" / "fonts"
    for _ttf in _fonts_dir.glob("Montserrat-*.ttf"):
        QFontDatabase.addApplicationFont(str(_ttf))

    # Initialize all per-category font sizes from persisted settings,
    # then build the QSS (which reads the module-level size variables).
    set_small(settings.ui_font_small)
    set_xs(settings.ui_font_xs)
    set_tiny(settings.ui_font_tiny)
    set_heading(settings.ui_font_heading)
    set_large(settings.ui_font_large)
    set_card_pt(settings.ui_font_card_pt)
    set_mono(settings.ui_font_mono)
    app.setStyleSheet(get_app_qss(settings.ui_font_size))

    # --- First-run disclaimer ---
    if not settings.disclaimer_accepted:
        dlg = DisclaimerDialog(current_api_key=settings.pokemontcg_api_key)
        if not dlg.exec():
            return 0  # user cancelled — do not start
        settings.pokemontcg_api_key = dlg.api_key
        settings.disclaimer_accepted = True
        settings.save()
        logger.info("Disclaimer accepted. API key set: %s", bool(settings.pokemontcg_api_key))

    window = MainWindow(settings=settings, database=database)
    if settings.start_maximized:
        window.showMaximized()
    else:
        window.show()
    return app.exec()
