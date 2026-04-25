from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

_BG = "#151726"
_FG = "#adb5d0"
_FG_TITLE = "#e2e8f0"
_HOVER_MIN_MAX = "#252b4a"
_HOVER_CLOSE_BG = "#c0392b"
_BTN_W = 46
_BTN_H = 36


def _make_btn(symbol: str, hover_bg: str, hover_fg: str = "#ffffff") -> QPushButton:
    btn = QPushButton(symbol)
    btn.setFixedSize(_BTN_W, _BTN_H)
    btn.setFocusPolicy(Qt.NoFocus)
    btn.setStyleSheet(
        f"""
        QPushButton {{
            background: transparent;
            color: {_FG};
            border: none;
            border-radius: 0;
            font-size: 13px;
        }}
        QPushButton:hover {{
            background: {hover_bg};
            color: {hover_fg};
        }}
        """
    )
    return btn


def _make_icon(size: int = 20) -> QPixmap:
    """Paints a small card + magnifier icon for the title bar."""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)

    # Card body
    p.setBrush(QColor("#5865f2"))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(1, 0, 12, 16, 2, 2)

    # Magnifier circle
    pen = QPen(QColor("#e2e8f0"), 1.5)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    p.drawEllipse(9, 9, 8, 8)

    # Magnifier handle
    pen2 = QPen(QColor("#e2e8f0"), 1.5)
    pen2.setCapStyle(Qt.RoundCap)
    p.setPen(pen2)
    p.drawLine(16, 16, 19, 19)

    p.end()
    return pm


class CustomTitleBar(QWidget):
    """Custom title bar for a frameless MainWindow.

    Provides drag-to-move (via QWindow.startSystemMove — enables Windows Snap),
    double-click to maximize/restore, and styled window control buttons.
    """

    double_clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(_BTN_H)
        self.setObjectName("CustomTitleBar")
        self.setStyleSheet(f"QWidget#CustomTitleBar {{ background: {_BG}; }}")

        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 0, 0, 0)
        lay.setSpacing(0)

        # App icon
        self._icon_lbl = QLabel()
        self._icon_lbl.setPixmap(_make_icon(20))
        self._icon_lbl.setFixedSize(20, 20)
        self._icon_lbl.setStyleSheet("background: transparent; border: none;")
        lay.addWidget(self._icon_lbl)
        lay.addSpacing(8)

        # Title text
        self._title_lbl = QLabel("CardLens  –  Katalog")
        self._title_lbl.setStyleSheet(
            f"color: {_FG_TITLE}; font-size: 13px; font-weight: 600;"
            "background: transparent; border: none;"
        )
        lay.addWidget(self._title_lbl)
        lay.addStretch()

        # Window control buttons
        self._btn_min = _make_btn("─", _HOVER_MIN_MAX)
        self._btn_min.setToolTip("Minimieren")

        self._btn_max = _make_btn("□", _HOVER_MIN_MAX)
        self._btn_max.setToolTip("Maximieren")

        self._btn_close = _make_btn("✕", _HOVER_CLOSE_BG)
        self._btn_close.setToolTip("Schließen")

        lay.addWidget(self._btn_min)
        lay.addWidget(self._btn_max)
        lay.addWidget(self._btn_close)

    # ── Public API ────────────────────────────────────────────────────────

    def set_title(self, title: str) -> None:
        self._title_lbl.setText(title)

    def set_maximized(self, maximized: bool) -> None:
        self._btn_max.setText("❐" if maximized else "□")
        self._btn_max.setToolTip("Wiederherstellen" if maximized else "Maximieren")

    @property
    def btn_min(self) -> QPushButton:
        return self._btn_min

    @property
    def btn_max(self) -> QPushButton:
        return self._btn_max

    @property
    def btn_close(self) -> QPushButton:
        return self._btn_close

    # ── Mouse events ─────────────────────────────────────────────────────

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.LeftButton:
            # Only initiate move when NOT pressing a control button
            if event.position().x() < self._btn_min.x():
                win = self.window().windowHandle()
                if win is not None:
                    win.startSystemMove()
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.LeftButton:
            if event.position().x() < self._btn_min.x():
                self.double_clicked.emit()
        super().mouseDoubleClickEvent(event)
