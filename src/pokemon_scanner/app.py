from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from src.pokemon_scanner.core.paths import ensure_runtime_dirs
from src.pokemon_scanner.core.logging_setup import configure_logging, get_logger
from src.pokemon_scanner.core.crash_handler import install_global_exception_hook
from src.pokemon_scanner.db.database import Database
from src.pokemon_scanner.config.settings import AppSettings
from src.pokemon_scanner.ui.main_window import MainWindow
from src.pokemon_scanner.ui.about_dialog import DisclaimerDialog
from src.pokemon_scanner.ui.styles import APP_QSS


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
    app.setStyleSheet(APP_QSS)

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
