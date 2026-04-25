"""Debug dialog: system info, live log viewer, and UI font-size control."""
from __future__ import annotations

import logging
import platform
import sys
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSlider,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from src.pokemon_scanner.core.paths import CACHE_DIR, DATA_DIR, EXPORT_DIR, LOG_DIR
from src.pokemon_scanner.ui.styles import (
    get_app_qss,
    set_small, set_xs, set_tiny, set_heading, set_large, set_card_pt, set_mono,
    size_body, size_small, size_xs, size_tiny, size_heading, size_large,
    size_card_pt, size_mono,
)

if TYPE_CHECKING:
    from src.pokemon_scanner.config.settings import AppSettings

try:
    from src.pokemon_scanner.ui.about_dialog import _APP_VERSION  # type: ignore[attr-defined]
except Exception:
    _APP_VERSION = "?"

_LOG_LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR"]


class DebugDialog(QDialog):
    """Debug console: system info, live log tail, log-level switcher, font-size adjuster."""

    def __init__(
        self,
        parent: QWidget | None = None,
        settings: "AppSettings | None" = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self.setWindowTitle("Debug-Konsole")
        self.resize(740, 540)
        self.setMinimumSize(560, 400)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(0)

        tabs = QTabWidget()
        root.addWidget(tabs)

        tabs.addTab(self._make_system_tab(), "System")
        tabs.addTab(self._make_logs_tab(), "Logs")
        tabs.addTab(self._make_ui_tab(), "Darstellung")

        # Auto-refresh timer for log viewer
        self._log_timer = QTimer(self)
        self._log_timer.setInterval(2000)
        self._log_timer.timeout.connect(self._refresh_logs)
        self._log_timer.start()

    # ── System tab ──────────────────────────────────────────────────────────

    def _make_system_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        form.setContentsMargins(20, 20, 20, 20)
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)

        def _ro(value: str) -> QLineEdit:
            le = QLineEdit(value)
            le.setReadOnly(True)
            return le

        form.addRow("App-Version:", _ro(f"CardLens {_APP_VERSION}"))
        form.addRow("Python:", _ro(sys.version.split()[0]))
        form.addRow("Plattform:", _ro(platform.platform(aliased=True, terse=True)))
        form.addRow("Architektur:", _ro(platform.machine()))
        form.addRow("Datenbank:", _ro(str(DATA_DIR / "pokemon_scanner.sqlite3")))
        form.addRow("Log-Verzeichnis:", _ro(str(LOG_DIR)))
        form.addRow("Cache:", _ro(str(CACHE_DIR)))
        form.addRow("Export:", _ro(str(EXPORT_DIR)))
        return w

    # ── Logs tab ─────────────────────────────────────────────────────────────

    def _make_logs_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # Controls row
        ctrl = QHBoxLayout()
        ctrl.addWidget(QLabel("Log-Level:"))

        self._level_combo = QComboBox()
        self._level_combo.addItems(_LOG_LEVELS)
        current = logging.getLevelName(logging.getLogger().level)
        self._level_combo.setCurrentText(current if current in _LOG_LEVELS else "INFO")
        self._level_combo.currentTextChanged.connect(self._on_level_changed)
        ctrl.addWidget(self._level_combo)
        ctrl.addStretch()

        refresh_btn = QPushButton("Aktualisieren")
        refresh_btn.clicked.connect(self._refresh_logs)
        ctrl.addWidget(refresh_btn)
        layout.addLayout(ctrl)

        # Log text area (monospace, read-only) — font controlled via QPlainTextEdit QSS rule
        self._log_text = QPlainTextEdit()
        self._log_text.setReadOnly(True)
        self._log_text.setMaximumBlockCount(600)
        layout.addWidget(self._log_text, stretch=1)

        self._refresh_logs()
        return w

    def _refresh_logs(self) -> None:
        log_file = LOG_DIR / "app.log"
        if not log_file.exists():
            self._log_text.setPlainText("(Noch keine Log-Datei vorhanden)")
            return
        try:
            with open(log_file, encoding="utf-8", errors="replace") as fh:
                lines = fh.readlines()
            tail = "".join(lines[-250:]).rstrip()
            if self._log_text.toPlainText() != tail:
                self._log_text.setPlainText(tail)
                sb = self._log_text.verticalScrollBar()
                sb.setValue(sb.maximum())
        except Exception:
            pass

    def _on_level_changed(self, level_text: str) -> None:
        level = getattr(logging, level_text, logging.INFO)
        root = logging.getLogger()
        root.setLevel(level)
        for handler in root.handlers:
            handler.setLevel(level)

    # ── Darstellung tab ──────────────────────────────────────────────────────

    def _make_ui_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)

        s = self._settings

        def _cat_row(
            label: str,
            current: int,
            lo: int,
            hi: int,
            attr: str,
        ) -> tuple[QSlider, QLabel]:
            """Add one labelled slider row and return (slider, value_label)."""
            hdr = QLabel(label)
            hdr.setStyleSheet("font-weight: 600; color: #e2e8f0;")
            layout.addWidget(hdr)
            row = QHBoxLayout()
            sl = QSlider(Qt.Orientation.Horizontal)
            sl.setRange(lo, hi)
            sl.setValue(current)
            sl.setTickInterval(1)
            sl.setTickPosition(QSlider.TickPosition.TicksBelow)
            vl = QLabel(f"{current} px")
            vl.setMinimumWidth(44)
            vl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            sl.valueChanged.connect(lambda v, lbl=vl, u="px": lbl.setText(f"{v} {u}"))
            row.addWidget(sl)
            row.addWidget(vl)
            layout.addLayout(row)
            setattr(self, attr, sl)
            return sl, vl

        _cat_row(
            "Fließtext  (body)  — Eingaben, Tabs, Buttons",
            s.ui_font_size if s else size_body(), 10, 22, "_sl_body",
        )
        _cat_row(
            "Beschriftungen  (small)  — Labels, GroupBox, Tooltips, Statusleiste",
            s.ui_font_small if s else size_small(), 8, 20, "_sl_small",
        )
        _cat_row(
            "Metadaten  (xs)  — Fortschrittsbalken, Nebeninfos",
            s.ui_font_xs if s else size_xs(), 7, 18, "_sl_xs",
        )
        _cat_row(
            "Mini-Labels  (tiny)  — Hinweise, Katalog-Kleintext",
            s.ui_font_tiny if s else size_tiny(), 6, 16, "_sl_tiny",
        )
        _cat_row(
            "Überschriften  (heading)  — Sektions-Header, Kartennamen in Panels",
            s.ui_font_heading if s else size_heading(), 10, 28, "_sl_heading",
        )
        _cat_row(
            "Preisanzeige  (large)  — Hauptpreise, Highlight-Zahlen",
            s.ui_font_large if s else size_large(), 12, 36, "_sl_large",
        )

        # Card-price uses point size, not pixels — separate label
        hdr_cp = QLabel("Kartenpreis  (card_pt)  — Preis-Beschriftung auf Album-Karte  (Punkte)")
        hdr_cp.setStyleSheet("font-weight: 600; color: #e2e8f0;")
        layout.addWidget(hdr_cp)
        row_cp = QHBoxLayout()
        self._sl_card_pt = QSlider(Qt.Orientation.Horizontal)
        self._sl_card_pt.setRange(4, 14)
        self._sl_card_pt.setValue(s.ui_font_card_pt if s else size_card_pt())
        self._sl_card_pt.setTickInterval(1)
        self._sl_card_pt.setTickPosition(QSlider.TickPosition.TicksBelow)
        vl_cp = QLabel(f"{self._sl_card_pt.value()} pt")
        vl_cp.setMinimumWidth(44)
        vl_cp.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._sl_card_pt.valueChanged.connect(lambda v: vl_cp.setText(f"{v} pt"))
        row_cp.addWidget(self._sl_card_pt)
        row_cp.addWidget(vl_cp)
        layout.addLayout(row_cp)

        _cat_row(
            "Konsole / Log  (mono)  — Consolas-Schrift im Log-Viewer",
            s.ui_font_mono if s else size_mono(), 7, 20, "_sl_mono",
        )

        _btn_ss = (
            "QPushButton { color: #e2e8f0; background-color: #2d3748;"
            " border: 1px solid #4a5568; border-radius: 6px; padding: 6px 14px; }"
            "QPushButton:hover { background-color: #3d4a5c; border-color: #5865f2; }"
            "QPushButton:pressed { background-color: #5865f2; border-color: #5865f2; }"
        )
        btn_row = QHBoxLayout()
        self._apply_btn = QPushButton("Anwenden")
        self._apply_btn.setStyleSheet(_btn_ss)
        self._apply_btn.setMinimumWidth(110)
        self._apply_btn.clicked.connect(self._apply_font_sizes)
        self._reset_btn = QPushButton("Zurücksetzen")
        self._reset_btn.setStyleSheet(_btn_ss)
        self._reset_btn.setMinimumWidth(120)
        self._reset_btn.clicked.connect(self._reset_font_sizes)
        btn_row.addWidget(self._apply_btn)
        btn_row.addWidget(self._reset_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        note = QLabel(
            "Änderungen werden sofort in globalen QSS-Stilen wirksam.\n"
            "Inline-Stile in geöffneten Dialogen werden erst beim nächsten Öffnen übernommen.\n"
            "Alle Werte werden dauerhaft in settings.json gespeichert."
        )
        note.setStyleSheet("color: #94a3b8;")
        note.setWordWrap(True)
        layout.addWidget(note)

        layout.addStretch()
        return w

    def _apply_font_sizes(self) -> None:
        # Apply all 8 independent categories
        set_small(self._sl_small.value())
        set_xs(self._sl_xs.value())
        set_tiny(self._sl_tiny.value())
        set_heading(self._sl_heading.value())
        set_large(self._sl_large.value())
        set_card_pt(self._sl_card_pt.value())
        set_mono(self._sl_mono.value())

        # Apply body last so get_app_qss() uses updated values for all tokens
        body = self._sl_body.value()
        app = QApplication.instance()
        if app is None:
            return
        app.setStyleSheet(get_app_qss(body))

        # Persist all values
        if self._settings is not None:
            self._settings.ui_font_size    = body
            self._settings.ui_font_small   = self._sl_small.value()
            self._settings.ui_font_xs      = self._sl_xs.value()
            self._settings.ui_font_tiny    = self._sl_tiny.value()
            self._settings.ui_font_heading = self._sl_heading.value()
            self._settings.ui_font_large   = self._sl_large.value()
            self._settings.ui_font_card_pt = self._sl_card_pt.value()
            self._settings.ui_font_mono    = self._sl_mono.value()
            self._settings.save()

        # Ask the main window to re-apply inline stylesheets
        p = self.parent()
        if p is not None and hasattr(p, "refresh_font_sizes"):
            p.refresh_font_sizes()

        # Force repaint of all top-level widgets
        for widget in app.topLevelWidgets():
            widget.update()

    def _reset_font_sizes(self) -> None:
        """Reset all sliders to their default values."""
        defaults = {
            "_sl_body": 13, "_sl_small": 11, "_sl_xs": 10, "_sl_tiny": 9,
            "_sl_heading": 15, "_sl_large": 18, "_sl_card_pt": 6, "_sl_mono": 11,
        }
        for attr, val in defaults.items():
            getattr(self, attr).setValue(val)

    # Legacy alias so old call sites still work
    _apply_font_size = _apply_font_sizes

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._log_timer.stop()
        super().closeEvent(event)
