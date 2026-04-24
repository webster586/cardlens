from __future__ import annotations

import datetime as dt
import sys
import traceback

from src.pokemon_scanner.core.paths import CRASH_DIR

_MAX_CRASH_LOGS = 20  # keep only the last N crash files to avoid unbounded growth


def _rotate_crash_logs() -> None:
    """Delete oldest crash logs if more than _MAX_CRASH_LOGS exist."""
    try:
        logs = sorted(CRASH_DIR.glob("crash_*.log"), key=lambda p: p.stat().st_mtime)
        for old in logs[:-_MAX_CRASH_LOGS]:
            old.unlink(missing_ok=True)
    except Exception:
        pass  # rotation failure must never mask the original crash


def install_global_exception_hook() -> None:
    def handle_exception(exc_type, exc_value, exc_traceback):
        # Ensure the crash directory exists — may not be created yet if the app
        # crashes before ensure_runtime_dirs() is called.
        CRASH_DIR.mkdir(parents=True, exist_ok=True)
        ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        crash_file = CRASH_DIR / f"crash_{ts}.log"
        try:
            with crash_file.open("w", encoding="utf-8") as fh:
                fh.write("Unhandled exception\n\n")
                traceback.print_exception(exc_type, exc_value, exc_traceback, file=fh)
        except Exception:
            pass  # writing failed — still print to stderr below
        traceback.print_exception(exc_type, exc_value, exc_traceback)
        _rotate_crash_logs()

    sys.excepthook = handle_exception
