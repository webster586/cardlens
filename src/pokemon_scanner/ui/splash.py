"""Splash screen shown while the OCR model is loading on startup."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, QRectF
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QSplashScreen


# ── App icon (reused from title_bar logic, larger version) ────────────────

def _make_splash_icon(size: int = 64) -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)

    s = size

    # Card body (blue-purple)
    p.setBrush(QColor("#5865f2"))
    p.setPen(Qt.NoPen)
    card_w = int(s * 0.55)
    card_h = int(s * 0.72)
    p.drawRoundedRect(2, 0, card_w, card_h, 4, 4)

    # Card shine strip
    p.setBrush(QColor(255, 255, 255, 40))
    p.drawRoundedRect(4, 2, int(s * 0.12), card_h - 4, 2, 2)

    # Magnifier circle
    cx = int(s * 0.62)
    cy = int(s * 0.54)
    r = int(s * 0.32)
    pen = QPen(QColor("#e2e8f0"), s * 0.07)
    p.setPen(pen)
    p.setBrush(QColor(30, 32, 48, 200))
    p.drawEllipse(cx - r, cy - r, r * 2, r * 2)

    # Magnifier handle
    pen2 = QPen(QColor("#e2e8f0"), s * 0.07)
    pen2.setCapStyle(Qt.RoundCap)
    p.setPen(pen2)
    hx = int(cx + r * 0.72)
    hy = int(cy + r * 0.72)
    p.drawLine(hx, hy, int(hx + s * 0.16), int(hy + s * 0.16))

    p.end()
    return pm


def _make_splash_pixmap(width: int = 480, height: int = 280) -> QPixmap:
    """Paint the full splash background with logo + title."""
    pm = QPixmap(width, height)
    pm.fill(QColor("#151726"))

    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)

    # Subtle gradient backdrop circle
    cx, cy = width // 2, height // 2 - 10
    for radius, alpha in [(160, 18), (120, 22), (80, 28)]:
        p.setBrush(QColor(88, 101, 242, alpha))
        p.setPen(Qt.NoPen)
        p.drawEllipse(cx - radius, cy - radius, radius * 2, radius * 2)

    # Icon
    icon = _make_splash_icon(72)
    icon_x = cx - 36
    icon_y = cy - 72
    p.drawPixmap(icon_x, icon_y, icon)

    # App title
    font_title = QFont("Montserrat", 26, QFont.Bold)
    font_title.setLetterSpacing(QFont.AbsoluteSpacing, 1.5)
    p.setFont(font_title)
    p.setPen(QColor("#e2e8f0"))
    p.drawText(QRectF(0, icon_y + 82, width, 40), Qt.AlignHCenter | Qt.AlignVCenter, "CardLens")

    # Subtitle
    font_sub = QFont("Segoe UI", 10)
    p.setFont(font_sub)
    p.setPen(QColor("#64748b"))
    p.drawText(
        QRectF(0, icon_y + 120, width, 24),
        Qt.AlignHCenter | Qt.AlignVCenter,
        "TCG Card Scanner & Collection Manager",
    )

    # Loading dots (animated via message updates)
    p.end()
    return pm


class CardLensSplash(QSplashScreen):
    """Frameless splash screen displayed while the OCR model is loading."""

    def __init__(self) -> None:
        super().__init__(_make_splash_pixmap(), Qt.WindowStaysOnTopHint)
        self.setWindowFlag(Qt.FramelessWindowHint, True)
        self._dot_count = 0

        # Animated loading dots timer
        self._dot_timer = QTimer(self)
        self._dot_timer.setInterval(400)
        self._dot_timer.timeout.connect(self._tick_dots)
        self._dot_timer.start()
        self._update_message()

    def _tick_dots(self) -> None:
        self._dot_count = (self._dot_count + 1) % 4
        self._update_message()

    def _update_message(self) -> None:
        dots = "·" * self._dot_count + " " * (3 - self._dot_count)
        self.showMessage(
            f"OCR-Modell wird geladen {dots}",
            Qt.AlignHCenter | Qt.AlignBottom,
            QColor("#64748b"),
        )

    def finish_loading(self) -> None:
        """Call when OCR is ready — stops animation and closes splash."""
        self._dot_timer.stop()
        self.showMessage(
            "Bereit  ✓",
            Qt.AlignHCenter | Qt.AlignBottom,
            QColor("#4ade80"),
        )
        # Short delay so user sees the "Bereit" state before the main window appears
        QTimer.singleShot(600, self.hide)
