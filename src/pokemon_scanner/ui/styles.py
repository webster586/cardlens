"""Global application stylesheet — dark mode theme."""

APP_QSS = """
/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   CardLens — Dark Mode (2026)
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */

/* ── Base ─────────────────────────────── */
QWidget {
    background-color: #1e2030;
    color: #e2e8f0;
    font-family: "Segoe UI Variable Text", "Segoe UI", system-ui, sans-serif;
    font-size: 13px;
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
    font-size: 12px;
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
    padding: 5px 14px;
    font-weight: 500;
    min-height: 28px;
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
    font-size: 11px;
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
    font-size: 12px;
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
QProgressBar { background-color: #2d3748; border: 1px solid #334155; border-radius: 4px; text-align: center; color: #e2e8f0; font-size: 10px; }
QProgressBar::chunk { background-color: #5865f2; border-radius: 3px; }

/* ── Labels / Frames ─────────────────── */
QLabel  { background: transparent; }
QFrame  { background-color: transparent; }

/* ── Tool tips ────────────────────────── */
QToolTip { background-color: #252741; color: #e2e8f0; border: 1px solid #5865f2; border-radius: 4px; padding: 4px 8px; font-size: 12px; }

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
    font-size: 12px;
}
"""
