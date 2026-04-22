from __future__ import annotations

import datetime as dt
import sys
import traceback

from src.pokemon_scanner.core.paths import CRASH_DIR


def install_global_exception_hook() -> None:
    def handle_exception(exc_type, exc_value, exc_traceback):
        ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        crash_file = CRASH_DIR / f"crash_{ts}.log"
        with crash_file.open("w", encoding="utf-8") as fh:
            fh.write("Unhandled exception\n\n")
            traceback.print_exception(exc_type, exc_value, exc_traceback, file=fh)
        traceback.print_exception(exc_type, exc_value, exc_traceback)

    sys.excepthook = handle_exception
