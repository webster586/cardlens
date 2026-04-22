"""Global application stylesheet — modern flat light theme."""

APP_QSS = """
/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   Pokemon Scanner — Modern UI (2026 edition)
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */

/* ── Base ─────────────────────────────── */
QWidget {
    background-color: #f8fafc;
    color: #1e293b;
    font-family: "Segoe UI Variable Text", "Segoe UI", system-ui, sans-serif;
    font-size: 13px;
}
QMainWindow { background-color: #f1f5f9; }
QDialog     { background-color: #f8fafc; }

/* ── Group boxes ──────────────────────── */
QGroupBox {
    background-color: #ffffff;
    border: 1px solid #e2e8f0;
    border-radius: 8px;
    margin-top: 14px;
    padding: 10px 8px 8px 8px;
    font-weight: 600;
    font-size: 12px;
    color: #334155;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 0 8px;
    left: 10px;
    top: -1px;
    color: #2563eb;
}

/* ── Buttons ──────────────────────────── */
QPushButton {
    background-color: #ffffff;
    color: #374151;
    border: 1px solid #d1d5db;
    border-radius: 6px;
    padding: 5px 14px;
    font-weight: 500;
    min-height: 28px;
}
QPushButton:hover {
    background-color: #eff6ff;
    border-color: #93c5fd;
    color: #1d4ed8;
}
QPushButton:pressed {
    background-color: #dbeafe;
    border-color: #3b82f6;
}
QPushButton:disabled {
    background-color: #f9fafb;
    color: #9ca3af;
    border-color: #e5e7eb;
}
QPushButton:checked {
    background-color: #2563eb;
    color: #ffffff;
    border-color: #2563eb;
}
QPushButton:checked:hover {
    background-color: #1d4ed8;
    border-color: #1d4ed8;
}

/* ── Line edits ───────────────────────── */
QLineEdit, QSpinBox {
    background-color: #ffffff;
    color: #1e293b;
    border: 1px solid #cbd5e1;
    border-radius: 6px;
    padding: 5px 9px;
    selection-background-color: #bfdbfe;
    selection-color: #1e3a8a;
}
QLineEdit:focus, QSpinBox:focus {
    border-color: #3b82f6;
}
QLineEdit:disabled, QSpinBox:disabled {
    background-color: #f1f5f9;
    color: #94a3b8;
}
QSpinBox::up-button, QSpinBox::down-button {
    background-color: #f8fafc;
    border: none;
    border-left: 1px solid #e2e8f0;
    width: 16px;
}
QSpinBox::up-button:hover, QSpinBox::down-button:hover {
    background-color: #e0f2fe;
}

/* ── ComboBox ─────────────────────────── */
QComboBox {
    background-color: #ffffff;
    color: #1e293b;
    border: 1px solid #cbd5e1;
    border-radius: 6px;
    padding: 4px 9px;
    min-height: 26px;
}
QComboBox:hover  { border-color: #93c5fd; }
QComboBox:focus  { border-color: #3b82f6; }
QComboBox::drop-down {
    border: none;
    width: 22px;
    padding-right: 4px;
}
QComboBox QAbstractItemView {
    background-color: #ffffff;
    color: #1e293b;
    border: 1px solid #cbd5e1;
    border-radius: 4px;
    selection-background-color: #dbeafe;
    selection-color: #1e3a8a;
    padding: 2px;
    outline: none;
}

/* ── Tab widget ───────────────────────── */
QTabWidget::pane {
    background-color: #ffffff;
    border: 1px solid #e2e8f0;
    border-radius: 0 8px 8px 8px;
    top: -1px;
}
QTabBar {
    background-color: transparent;
}
QTabBar::tab {
    background-color: #f1f5f9;
    color: #64748b;
    padding: 7px 22px;
    border: 1px solid #e2e8f0;
    border-bottom: none;
    border-top-left-radius: 7px;
    border-top-right-radius: 7px;
    margin-right: 2px;
    font-weight: 500;
}
QTabBar::tab:selected {
    background-color: #ffffff;
    color: #2563eb;
    border-bottom: 2px solid #2563eb;
    font-weight: 600;
}
QTabBar::tab:hover:!selected {
    background-color: #e8f0fe;
    color: #1d4ed8;
}

/* ── Table ────────────────────────────── */
QTableWidget, QTableView {
    background-color: #ffffff;
    alternate-background-color: #f8fafc;
    color: #1e293b;
    gridline-color: #f1f5f9;
    border: 1px solid #e2e8f0;
    border-radius: 8px;
    selection-background-color: #dbeafe;
    selection-color: #1e3a8a;
}
QTableWidget::item, QTableView::item {
    padding: 4px 8px;
}
QTableWidget::item:selected, QTableView::item:selected {
    background-color: #dbeafe;
    color: #1e3a8a;
}
QHeaderView {
    background-color: #f8fafc;
}
QHeaderView::section {
    background-color: #f8fafc;
    color: #475569;
    border: none;
    border-right: 1px solid #e2e8f0;
    border-bottom: 2px solid #e2e8f0;
    padding: 6px 10px;
    font-weight: 600;
    font-size: 12px;
}
QHeaderView::section:first {
    border-top-left-radius: 7px;
}

/* ── Scroll bars ──────────────────────── */
QScrollBar:vertical {
    background-color: transparent;
    width: 8px;
    margin: 0;
}
QScrollBar::handle:vertical {
    background-color: #cbd5e1;
    border-radius: 4px;
    min-height: 24px;
}
QScrollBar::handle:vertical:hover {
    background-color: #94a3b8;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }
QScrollBar:horizontal {
    background-color: transparent;
    height: 8px;
    margin: 0;
}
QScrollBar::handle:horizontal {
    background-color: #cbd5e1;
    border-radius: 4px;
    min-width: 24px;
}
QScrollBar::handle:horizontal:hover {
    background-color: #94a3b8;
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background: none; }

/* ── Checkbox ─────────────────────────── */
QCheckBox {
    spacing: 6px;
    color: #374151;
    background: transparent;
}
QCheckBox::indicator {
    width: 16px;
    height: 16px;
    border: 1.5px solid #9ca3af;
    border-radius: 4px;
    background-color: #ffffff;
}
QCheckBox::indicator:checked {
    background-color: #2563eb;
    border-color: #2563eb;
}
QCheckBox::indicator:hover {
    border-color: #3b82f6;
}

/* ── Slider ───────────────────────────── */
QSlider::groove:horizontal {
    background-color: #e2e8f0;
    height: 4px;
    border-radius: 2px;
}
QSlider::handle:horizontal {
    background-color: #3b82f6;
    width: 16px;
    height: 16px;
    margin: -6px 0;
    border-radius: 8px;
    border: 2px solid #ffffff;
}
QSlider::handle:horizontal:hover {
    background-color: #2563eb;
}
QSlider::sub-page:horizontal {
    background-color: #93c5fd;
    border-radius: 2px;
}

/* ── Scroll area ──────────────────────── */
QScrollArea {
    background-color: transparent;
    border: none;
}

/* ── Labels ───────────────────────────── */
QLabel { background: transparent; }

/* ── Tooltips ─────────────────────────── */
QToolTip {
    background-color: #1e293b;
    color: #f8fafc;
    border: none;
    border-radius: 6px;
    padding: 5px 10px;
    font-size: 12px;
}

/* ── Message boxes ────────────────────── */
QMessageBox { background-color: #f8fafc; }
QMessageBox QLabel { color: #1e293b; background: transparent; }
QMessageBox QPushButton { min-width: 80px; }

/* ── Status bar ───────────────────────── */
QStatusBar {
    background-color: #f1f5f9;
    color: #64748b;
    border-top: 1px solid #e2e8f0;
    font-size: 12px;
}
"""
