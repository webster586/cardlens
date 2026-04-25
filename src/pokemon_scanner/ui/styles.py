"""Global application stylesheet — dark mode theme."""
from __future__ import annotations

# ── Independent per-category font sizes ───────────────────────────────────
# Each category can be adjusted separately via Debug-Konsole → Darstellung.
# Defaults are chosen for a 96 dpi screen.

# Body: main text, inputs, tabs, buttons
_SIZE_BODY:    int = 13
# Small: secondary labels, groupbox headers, tooltips, status bar, table headers
_SIZE_SMALL:   int = 11
# Extra-Small: progress bar, metadata, minor info lines
_SIZE_XS:      int = 10
# Tiny: hints, micro catalog labels, card-detail timestamps
_SIZE_TINY:    int = 9
# Heading: section headers, sidebar nav buttons, card names in panels
_SIZE_HEADING: int = 15
# Large: prices, key display values, bold number highlights
_SIZE_LARGE:   int = 18
# Card-price point size (QFont in album paintEvent – points, not pixels)
_SIZE_CARD_PT: int = 6
# Monospace: log-viewer / console (Consolas / Courier New)
_SIZE_MONO:    int = 11

# Legacy alias kept for callers that still use _CURRENT_BASE / set_base()
_CURRENT_BASE: int = _SIZE_BODY


# ── Setters ────────────────────────────────────────────────────────────────

def set_base(base: int) -> None:
    """Update body (base) font size. Also kept as legacy entry-point."""
    global _SIZE_BODY, _CURRENT_BASE
    _SIZE_BODY = _CURRENT_BASE = max(10, min(24, base))


def set_small(v: int) -> None:
    global _SIZE_SMALL
    _SIZE_SMALL = max(8, min(22, v))


def set_xs(v: int) -> None:
    global _SIZE_XS
    _SIZE_XS = max(7, min(20, v))


def set_tiny(v: int) -> None:
    global _SIZE_TINY
    _SIZE_TINY = max(6, min(18, v))


def set_heading(v: int) -> None:
    global _SIZE_HEADING
    _SIZE_HEADING = max(10, min(30, v))


def set_large(v: int) -> None:
    global _SIZE_LARGE
    _SIZE_LARGE = max(12, min(40, v))


def set_card_pt(v: int) -> None:
    global _SIZE_CARD_PT
    _SIZE_CARD_PT = max(4, min(16, v))


def set_mono(v: int) -> None:
    global _SIZE_MONO
    _SIZE_MONO = max(7, min(22, v))


# ── Getters ────────────────────────────────────────────────────────────────

def size_body()    -> int: return _SIZE_BODY
def size_small()   -> int: return _SIZE_SMALL
def size_xs()      -> int: return _SIZE_XS
def size_tiny()    -> int: return _SIZE_TINY
def size_heading() -> int: return _SIZE_HEADING
def size_large()   -> int: return _SIZE_LARGE
def size_card_pt() -> int: return _SIZE_CARD_PT
def size_mono()    -> int: return _SIZE_MONO


def scale(logical: int, *, base: int | None = None) -> int:
    """Proportionally scale *logical* (designed for base-13) to the current body base.

    Example: scale(11) at base 16 → round(11 * 16/13) == 14
    """
    b = base if base is not None else _SIZE_BODY
    return max(7, round(logical * b / 13))


# Typography scale tokens (replaced at runtime by get_app_qss()):
#   §BASE§     = _SIZE_BODY     (default 13 px)  — body, inputs, tabs, buttons
#   §SM§       = _SIZE_SMALL    (default 11 px)  — labels, groupbox, tooltips, status
#   §XS§       = _SIZE_XS       (default 10 px)  — progress bar, metadata
#   §MONO§     = _SIZE_MONO     (default 11 px)  — log viewer / console

_APP_QSS_TMPL = """
/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   CardLens — Dark Mode (2026)
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */

/* ── Base ─────────────────────────────── */
QWidget {
    background-color: #1e2030;
    color: #e2e8f0;
    font-family: "Montserrat", "Segoe UI Variable Text", "Segoe UI", sans-serif;
    font-size: §BASE§px;
}
QMainWindow { background-color: #151726; }
QDialog     { background-color: #1e2030; }

/* ── Group boxes ──────────────────────── */
QGroupBox {
    background-color: #252741;
    border: 1px solid #334155;
    border-radius: 8px;
    margin-top: 14px;
    padding: 10px 8px 8px 8px;
    font-weight: 600;
    font-size: §SM§px;
    color: #94a3b8;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 0 8px;
    left: 10px;
    top: -1px;
    color: #6b8ef5;
}

/* ── Buttons ──────────────────────────── */
QPushButton {
    background-color: #2d3748;
    color: #e2e8f0;
    border: 1px solid #4a5568;
    border-radius: 6px;
    padding: §PAD§px 14px;
    font-weight: 500;
}
QPushButton:hover {
    background-color: #3d4a5c;
    border-color: #5865f2;
    color: #ffffff;
}
QPushButton:pressed {
    background-color: #5865f2;
    border-color: #5865f2;
    color: #ffffff;
}
QPushButton:disabled {
    background-color: #1e2030;
    color: #4a5568;
    border-color: #334155;
}
QPushButton:checked {
    background-color: #5865f2;
    color: #ffffff;
    border-color: #5865f2;
}
QPushButton:checked:hover {
    background-color: #4752d0;
    border-color: #4752d0;
}

/* ── Line edits / SpinBox ─────────────── */
QLineEdit, QSpinBox {
    background-color: #2d3748;
    color: #e2e8f0;
    border: 1px solid #4a5568;
    border-radius: 6px;
    padding: 5px 9px;
    selection-background-color: #5865f2;
    selection-color: #ffffff;
}
QLineEdit:focus, QSpinBox:focus { border-color: #5865f2; }
QLineEdit:disabled, QSpinBox:disabled { background-color: #1e2030; color: #4a5568; }
QSpinBox::up-button, QSpinBox::down-button {
    background-color: #2d3748;
    border: none;
    border-left: 1px solid #4a5568;
    width: 16px;
}
QSpinBox::up-button:hover, QSpinBox::down-button:hover { background-color: #3d4a5c; }

/* ── ComboBox ─────────────────────────── */
QComboBox {
    background-color: #2d3748;
    color: #e2e8f0;
    border: 1px solid #4a5568;
    border-radius: 6px;
    padding: 5px 9px;
    min-height: 28px;
}
QComboBox:hover, QComboBox:focus { border-color: #5865f2; }
QComboBox::drop-down { border: none; width: 20px; }
QComboBox QAbstractItemView {
    background-color: #2d3748;
    color: #e2e8f0;
    border: 1px solid #5865f2;
    selection-background-color: #5865f2;
    selection-color: white;
    outline: none;
}

/* ── Tables ───────────────────────────── */
QTableWidget, QTableView {
    background-color: #1a1b2e;
    alternate-background-color: #1e2030;
    color: #e2e8f0;
    gridline-color: #2d3748;
    border: 1px solid #334155;
    border-radius: 6px;
    selection-background-color: #5865f2;
    selection-color: white;
}
QTableWidget::item, QTableView::item { padding: 4px 8px; }
QTableWidget::item:selected, QTableView::item:selected { background-color: #5865f2; color: white; }
QHeaderView { background-color: #2d3748; }
QHeaderView::section {
    background-color: #2d3748;
    color: #9ca3af;
    border: none;
    border-right: 1px solid #334155;
    border-bottom: 1px solid #334155;
    padding: 6px 10px;
    font-weight: 600;
    font-size: §SM§px;
}

/* ── Scroll bars ──────────────────────── */
QScrollArea { background-color: transparent; border: none; }
QScrollBar:vertical { background: #1a1b2e; width: 8px; border-radius: 4px; margin: 0; }
QScrollBar::handle:vertical { background: #4a5568; border-radius: 4px; min-height: 30px; }
QScrollBar::handle:vertical:hover { background: #5865f2; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }
QScrollBar:horizontal { background: #1a1b2e; height: 8px; border-radius: 4px; margin: 0; }
QScrollBar::handle:horizontal { background: #4a5568; border-radius: 4px; min-width: 30px; }
QScrollBar::handle:horizontal:hover { background: #5865f2; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background: none; }

/* ── Tab widget ───────────────────────── */
QTabWidget::pane { border: 1px solid #334155; border-radius: 6px; background: #1e2030; }
QTabBar::tab {
    background: #252741;
    color: #9ca3af;
    padding: 8px 20px;
    border: 1px solid #334155;
    border-bottom: none;
    border-radius: 6px 6px 0 0;
    font-size: §BASE§px;
    font-weight: 500;
}
QTabBar::tab:selected { background: #5865f2; color: white; border-color: #5865f2; }
QTabBar::tab:hover:!selected { background: #3d4a5c; color: white; }

/* ── Checkbox ─────────────────────────── */
QCheckBox { color: #e2e8f0; spacing: 6px; background: transparent; }
QCheckBox::indicator { width: 16px; height: 16px; border: 1px solid #4a5568; border-radius: 4px; background: #2d3748; }
QCheckBox::indicator:checked { background: #5865f2; border-color: #5865f2; }
QCheckBox::indicator:hover { border-color: #5865f2; }

/* ── Slider ───────────────────────────── */
QSlider::groove:horizontal { background: #2d3748; height: 4px; border-radius: 2px; }
QSlider::handle:horizontal { background: #5865f2; width: 14px; height: 14px; border-radius: 7px; margin: -5px 0; }
QSlider::handle:horizontal:hover { background: #4752d0; }
QSlider::sub-page:horizontal { background: #5865f2; border-radius: 2px; }

/* ── Progress bar ─────────────────────── */
QProgressBar { background-color: #2d3748; border: 1px solid #334155; border-radius: 4px; text-align: center; color: #e2e8f0; font-size: §XS§px; }
QProgressBar::chunk { background-color: #5865f2; border-radius: 3px; }

/* ── Labels / Frames ─────────────────── */
QLabel  { background: transparent; }
QFrame  { background-color: transparent; }

/* ── Tool tips ────────────────────────── */
QToolTip { background-color: #252741; color: #e2e8f0; border: 1px solid #5865f2; border-radius: 4px; padding: 4px 8px; font-size: §SM§px; }

/* ── Message boxes ────────────────────── */
QMessageBox { background-color: #1e2030; }
QMessageBox QLabel { color: #e2e8f0; background: transparent; }
QMessageBox QPushButton { min-width: 80px; }

/* ── Menu bar / menus ─────────────────── */
QMenuBar { background-color: #151726; color: #e2e8f0; border-bottom: 1px solid #334155; }
QMenuBar::item:selected { background: #5865f2; border-radius: 4px; }
QMenu { background: #252741; border: 1px solid #334155; border-radius: 6px; color: #e2e8f0; }
QMenu::item:selected { background: #5865f2; color: white; }
QMenu::separator { background: #334155; height: 1px; margin: 4px 0; }

/* ── Status bar ───────────────────────── */
QStatusBar {
    background-color: #151726;
    color: #94a3b8;
    border-top: 1px solid #334155;
    font-size: §SM§px;
}

/* ── Log viewer / console ─────────────── */
QPlainTextEdit {
    font-family: "Consolas", "Courier New", monospace;
    font-size: §MONO§px;
    background-color: #0d0f1a;
    color: #94a3b8;
}
"""


def get_app_qss(base: int = 13) -> str:
    """Return the application QSS with all current independent font-size categories.

    *base* sets the body size; all other categories keep their independently stored
    values unless they have been explicitly changed via set_small() / set_xs() etc.
    Pass base=-1 to re-apply current values without changing the body size.
    """
    if base >= 0:
        set_base(base)
    pad = max(4, _SIZE_BODY - 5)
    return (
        _APP_QSS_TMPL
        .replace("\u00a7BASE\u00a7px", f"{_SIZE_BODY}px")
        .replace("\u00a7SM\u00a7px",   f"{_SIZE_SMALL}px")
        .replace("\u00a7XS\u00a7px",   f"{_SIZE_XS}px")
        .replace("\u00a7MONO\u00a7px", f"{_SIZE_MONO}px")
        .replace("\u00a7PAD\u00a7px",  f"{pad}px")
    )


APP_QSS = get_app_qss()
