from __future__ import annotations

import datetime as dt
import sys
import traceback
from pathlib import Path

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


def _show_crash_dialog(crash_text: str, crash_file: Path) -> None:
    """Try to show a Qt crash dialog. Silently skipped if Qt is unavailable."""
    try:
        from PySide6.QtWidgets import QApplication, QDialog, QVBoxLayout, QHBoxLayout
        from PySide6.QtWidgets import QLabel, QPushButton, QPlainTextEdit, QDialogButtonBox
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QClipboard

        # Reuse existing QApplication or create a minimal one
        app = QApplication.instance()
        _created_app = False
        if app is None:
            app = QApplication(sys.argv)
            _created_app = True

        dlg = QDialog()
        dlg.setWindowTitle("CardLens — Unerwarteter Fehler")
        dlg.setMinimumSize(560, 420)
        dlg.setWindowFlag(Qt.WindowStaysOnTopHint, True)

        lay = QVBoxLayout(dlg)
        lay.setSpacing(10)

        # Header
        header = QLabel(
            "<b style='font-size:15px;color:#f87171;'>CardLens ist abgestürzt.</b><br><br>"
            "Ein unerwarteter Fehler ist aufgetreten. "
            "Die Details wurden in der Crash-Log-Datei gespeichert.<br>"
            f"<span style='color:#64748b;font-size:11px;'>Datei: {crash_file}</span>"
        )
        header.setWordWrap(True)
        header.setTextFormat(Qt.RichText)
        header.setStyleSheet(
            "background:#1e2030;color:#e2e8f0;border-radius:6px;padding:12px;"
        )
        lay.addWidget(header)

        # Error details in collapsible text box
        details = QPlainTextEdit(crash_text)
        details.setReadOnly(True)
        details.setMaximumHeight(200)
        details.setStyleSheet(
            "background:#151726;color:#94a3b8;font-family:Consolas,monospace;font-size:10px;"
            "border:1px solid #334155;border-radius:4px;"
        )
        lay.addWidget(details)

        # Buttons
        btn_row = QHBoxLayout()
        btn_copy = QPushButton("Fehlerdetails kopieren")
        btn_copy.clicked.connect(lambda: QApplication.clipboard().setText(crash_text))
        btn_open = QPushButton("Log-Ordner öffnen")

        def _open_log_dir() -> None:
            import subprocess
            subprocess.Popen(f'explorer "{crash_file.parent}"')

        btn_open.clicked.connect(_open_log_dir)
        btn_close = QPushButton("Schließen")
        btn_close.setDefault(True)
        btn_close.clicked.connect(dlg.accept)
        btn_close.setStyleSheet(
            "background:#dc2626;color:white;font-weight:bold;padding:6px 18px;"
            "border-radius:6px;border:none;"
        )
        btn_row.addWidget(btn_copy)
        btn_row.addWidget(btn_open)
        btn_row.addStretch()
        btn_row.addWidget(btn_close)
        lay.addLayout(btn_row)

        dlg.exec()
    except Exception:
        pass  # dialog failure must never mask the original crash


def install_global_exception_hook() -> None:
    def handle_exception(exc_type, exc_value, exc_traceback):
        # KeyboardInterrupt should not trigger the crash dialog
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return

        # Ensure the crash directory exists — may not be created yet if the app
        # crashes before ensure_runtime_dirs() is called.
        CRASH_DIR.mkdir(parents=True, exist_ok=True)
        ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        crash_file = CRASH_DIR / f"crash_{ts}.log"
        crash_text = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
        try:
            crash_file.write_text(
                f"Unhandled exception\n\n{crash_text}",
                encoding="utf-8",
            )
        except Exception:
            pass  # writing failed — still continue below
        # Print to stderr so developers see it in console
        sys.stderr.write(crash_text)
        _rotate_crash_logs()
        # Show user-friendly GUI dialog
        _show_crash_dialog(crash_text, crash_file)

    sys.excepthook = handle_exception
