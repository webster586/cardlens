"""Batch statistics page — shows aggregate collection metrics, value history, and set completion."""
from __future__ import annotations

import datetime as _dtt
from typing import TYPE_CHECKING

from PySide6.QtCore import QPointF, Qt, QThread, Signal
from PySide6.QtGui import QColor, QFont, QPen
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from src.pokemon_scanner.db.repositories import CollectionRepository

if TYPE_CHECKING:
    from src.pokemon_scanner.db.catalog_repository import CatalogRepository


def _price_str(value: float | None, currency: str = "EUR") -> str:
    if value is None:
        return "\u2013"
    sym = {"USD": "$", "EUR": "\u20ac", "GBP": "\u00a3"}.get(currency, currency)
    return f"{sym}{value:,.2f}"


# ── Background workers ─────────────────────────────────────────────────────────

class _StatsWorker(QThread):
    done = Signal(dict)

    def __init__(self, repo: CollectionRepository) -> None:
        super().__init__()
        self._repo = repo

    def run(self) -> None:
        try:
            stats = self._repo.get_collection_stats()
            try:
                self._repo.record_collection_value_snapshot()
            except Exception:
                pass
            self.done.emit(stats)
        except Exception:
            self.done.emit({})


class _HistoryWorker(QThread):
    done = Signal(list)

    def __init__(self, repo: CollectionRepository) -> None:
        super().__init__()
        self._repo = repo

    def run(self) -> None:
        try:
            self.done.emit(self._repo.get_collection_value_history())
        except Exception:
            self.done.emit([])


class _SetCompletionWorker(QThread):
    done = Signal(list)

    def __init__(self, catalog_repo: "CatalogRepository") -> None:
        super().__init__()
        self._repo = catalog_repo

    def run(self) -> None:
        try:
            self.done.emit(self._repo.get_set_completion())
        except Exception:
            self.done.emit([])


# ── Reusable widgets ───────────────────────────────────────────────────────────

class _StatCard(QFrame):
    """A single metric card (icon + value + label)."""

    def __init__(
        self,
        icon: str,
        label: str,
        value: str,
        accent: str = "#5865f2",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("statcard")
        self.setStyleSheet(
            f"QFrame#statcard{{background:#1a1d2e;border:1px solid #2a3045;"
            f"border-top:3px solid {accent};border-radius:8px;}}"
            "QFrame#statcard QLabel{border:none;background:transparent;}"
        )
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMinimumHeight(110)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(4)

        row = QHBoxLayout()
        icon_lbl = QLabel(icon)
        icon_lbl.setStyleSheet("font-size:22px;")
        row.addWidget(icon_lbl)
        row.addStretch()
        lay.addLayout(row)

        self._val_lbl = QLabel(value)
        self._val_lbl.setStyleSheet(
            f"font-size:26px;font-weight:bold;color:{accent};"
        )
        lay.addWidget(self._val_lbl)

        lbl = QLabel(label)
        lbl.setStyleSheet("font-size:11px;color:#94a3b8;")
        lay.addWidget(lbl)

    def set_value(self, value: str) -> None:
        self._val_lbl.setText(value)


class _ValueHistoryChart(QFrame):
    """Canvas that paints a line chart of total collection value over time."""

    _PL, _PR, _PT, _PB = 58, 12, 10, 28  # padding left/right/top/bottom

    def __init__(self, history: list[dict], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._history: list[dict] = history
        self.setMinimumHeight(180)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setStyleSheet(
            "background:#131625;border:1px solid #2a3045;border-radius:6px;"
        )

    def set_data(self, history: list[dict]) -> None:
        self._history = history
        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        from PySide6.QtGui import QPainter

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        self._paint(painter)
        painter.end()

    def _paint(self, painter) -> None:
        from PySide6.QtCore import QRectF
        from PySide6.QtGui import QLinearGradient, QPainterPath

        w = self.width()
        PL, PR, PT, PB = self._PL, self._PR, self._PT, self._PB
        cw = w - PL - PR
        ch = self.height() - PT - PB

        if not self._history:
            fnt = QFont()
            fnt.setPointSize(8)
            painter.setFont(fnt)
            painter.setPen(QColor("#aaaaaa"))
            painter.drawText(
                PL, PT, cw, ch, Qt.AlignCenter,
                "Noch keine Verlaufsdaten\n(wird t\u00e4glich beim \u00d6ffnen gespeichert)",
            )
            return

        values = [r["total_value"] for r in self._history]
        dates = [r["snapshot_date"] for r in self._history]
        min_v = min(values)
        max_v = max(values)
        v_range = max_v - min_v if max_v != min_v else 1.0
        n = len(values)

        def _x(i: int) -> float:
            return PL + (i * cw / (n - 1) if n > 1 else cw / 2)

        def _y(v: float) -> float:
            return PT + ch - (v - min_v) / v_range * ch

        # Background box
        painter.fillRect(PL, PT, cw, ch, QColor("#1a1d2e"))
        painter.setPen(QPen(QColor("#2a3045"), 1))
        painter.drawRect(PL, PT, cw, ch)

        # Grid + Y labels
        fnt = QFont()
        fnt.setPointSize(7)
        painter.setFont(fnt)
        for step in range(4):
            frac = step / 3
            y = int(PT + frac * ch)
            painter.setPen(QPen(QColor("#252741"), 1))
            painter.drawLine(PL + 1, y, PL + cw - 1, y)
            val_at = max_v - frac * v_range
            painter.setPen(QColor("#94a3b8"))
            painter.drawText(
                0, y - 7, PL - 4, 14,
                Qt.AlignRight | Qt.AlignVCenter,
                f"\u20ac{val_at:,.0f}",
            )

        # Gradient fill under the line
        if n > 1:
            path = QPainterPath()
            path.moveTo(_x(0), _y(values[0]))
            for i in range(1, n):
                path.lineTo(_x(i), _y(values[i]))
            path.lineTo(_x(n - 1), PT + ch)
            path.lineTo(_x(0), PT + ch)
            path.closeSubpath()
            grad = QLinearGradient(0, PT, 0, PT + ch)
            grad.setColorAt(0, QColor(22, 163, 74, 80))
            grad.setColorAt(1, QColor(22, 163, 74, 5))
            painter.fillPath(path, grad)

        # Line
        if n > 1:
            painter.setPen(QPen(QColor("#16a34a"), 2))
            painter.setBrush(Qt.NoBrush)
            for i in range(n - 1):
                painter.drawLine(
                    QPointF(_x(i), _y(values[i])),
                    QPointF(_x(i + 1), _y(values[i + 1])),
                )

        # Dots + X-axis date labels
        label_idx = {0, n - 1} | {i for i in range(0, n, max(1, n // 6))}
        painter.setPen(QPen(QColor("#0d6e34"), 1))
        painter.setBrush(QColor("#16a34a"))
        for i in range(n):
            x, y = _x(i), _y(values[i])
            painter.drawEllipse(QPointF(x, y), 3.0, 3.0)
            if i in label_idx:
                try:
                    d = _dtt.date.fromisoformat(dates[i])
                    lbl = d.strftime("%d.%m.%y")
                except Exception:
                    lbl = dates[i][-8:] if len(dates[i]) >= 8 else dates[i]
                painter.setPen(QColor("#94a3b8"))
                painter.drawText(
                    int(x - 22), self.height() - PB + 3, 44, 16,
                    Qt.AlignCenter, lbl,
                )
                painter.setPen(QPen(QColor("#0d6e34"), 1))
                painter.setBrush(QColor("#16a34a"))


class _SetRow(QFrame):
    """Single row showing one Pokémon TCG set and its collection completion."""

    def __init__(self, data: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        owned = int(data.get("owned_count") or 0)
        catalog = int(data.get("catalog_count") or 0)
        total = int(data.get("set_total") or catalog or 1)
        pct = owned / total * 100 if total > 0 else 0.0
        set_name = data.get("set_name") or "\u2013"
        series = data.get("set_series") or ""
        year = str(data.get("release_year") or "")

        self.setStyleSheet(
            "QFrame{background:#16192b;border:none;"
            "border-bottom:1px solid #2a3045;}"
            "QFrame QLabel{border:none;background:transparent;}"
            "QFrame QProgressBar{"
            "border:1px solid #2a3045;border-radius:3px;"
            "background:#1e2030;height:10px;}"
            "QFrame QProgressBar::chunk{"
            "background:#3a7ecf;border-radius:3px;}"
        )

        hl = QHBoxLayout(self)
        hl.setContentsMargins(8, 6, 8, 6)
        hl.setSpacing(8)

        # Name + series
        name_col = QWidget()
        name_col.setStyleSheet("background:transparent;")
        nl = QVBoxLayout(name_col)
        nl.setContentsMargins(0, 0, 0, 0)
        nl.setSpacing(1)
        nm = QLabel(set_name)
        nm.setStyleSheet(
            "font-weight:bold;font-size:11px;color:#e2e8f0;"
        )
        nm.setWordWrap(False)
        nl.addWidget(nm)
        if series:
            sr = QLabel(series)
            sr.setStyleSheet("font-size:9px;color:#64748b;")
            nl.addWidget(sr)
        hl.addWidget(name_col, 3)

        # Year badge
        yr = QLabel(year)
        yr.setFixedWidth(36)
        yr.setAlignment(Qt.AlignCenter)
        yr.setStyleSheet("font-size:10px;color:#64748b;")
        hl.addWidget(yr)

        # Progress bar + fraction
        bar_col = QWidget()
        bar_col.setStyleSheet("background:transparent;")
        bl = QVBoxLayout(bar_col)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(2)
        bar = QProgressBar()
        bar.setRange(0, max(total, 1))
        bar.setValue(min(owned, total))
        bar.setTextVisible(False)
        bar.setFixedHeight(10)
        bl.addWidget(bar)
        frac_lbl = QLabel(f"{owned}\u202f/\u202f{total}  ({pct:.0f}\u202f%)")
        frac_lbl.setStyleSheet("font-size:9px;color:#94a3b8;")
        frac_lbl.setAlignment(Qt.AlignRight)
        bl.addWidget(frac_lbl)
        hl.addWidget(bar_col, 2)

        # Badge
        if pct >= 100:
            badge_color = "#16a34a"
            badge_text = "\u2713 Komplett"
        elif pct >= 50:
            badge_color = "#f59e0b"
            badge_text = f"{pct:.0f}\u202f%"
        else:
            badge_color = "#64748b"
            badge_text = f"{pct:.0f}\u202f%"
        badge = QLabel(badge_text)
        badge.setFixedWidth(66)
        badge.setAlignment(Qt.AlignCenter)
        badge.setStyleSheet(
            f"font-size:9px;font-weight:bold;color:{badge_color};"
            "padding:2px 4px;border-radius:3px;"
        )
        hl.addWidget(badge)


# ── Main widget ────────────────────────────────────────────────────────────────

class StatsWidget(QWidget):
    """Sidebar page with three tabs: Übersicht · Wert-Verlauf · Set-Vollständigkeit."""

    def __init__(
        self,
        repo: CollectionRepository,
        catalog_repo: "CatalogRepository | None" = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._repo = repo
        self._catalog_repo = catalog_repo
        self._worker: _StatsWorker | None = None
        self._hist_worker: _HistoryWorker | None = None
        self._set_worker: _SetCompletionWorker | None = None
        self._history_loaded = False
        self._sets_loaded = False
        self._build_ui()

    # ── UI construction ────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(8)

        hdr = QHBoxLayout()
        title = QLabel("\U0001f4ca  Sammlungs-Statistiken")
        title.setStyleSheet("font-size:20px;font-weight:bold;color:#e2e8f0;")
        hdr.addWidget(title)
        hdr.addStretch()
        self._btn_refresh = QPushButton("\u21ba Aktualisieren")
        self._btn_refresh.setMinimumSize(120, 32)
        self._btn_refresh.setStyleSheet(
            "QPushButton{background:#374151;color:#e2e8f0;"
            "border:none;border-radius:6px;}"
            "QPushButton:hover{background:#4b5563;}"
        )
        self._btn_refresh.clicked.connect(self._on_refresh)
        hdr.addWidget(self._btn_refresh)
        outer.addLayout(hdr)

        self._tabs = QTabWidget()
        self._tabs.setStyleSheet(
            "QTabWidget::pane{border:1px solid #2a3045;background:#131625;}"
            "QTabBar::tab{background:#1e2030;color:#94a3b8;"
            "padding:6px 16px;border:1px solid #2a3045;"
            "border-bottom:none;border-radius:4px 4px 0 0;}"
            "QTabBar::tab:selected{background:#131625;color:#e2e8f0;}"
            "QTabBar::tab:hover{color:#e2e8f0;}"
        )
        outer.addWidget(self._tabs, 1)

        self._build_overview_tab()
        self._build_history_tab()
        self._build_sets_tab()

        self._tabs.currentChanged.connect(self._on_tab_changed)

        self._status_lbl = QLabel(
            "Noch nicht geladen \u2013 Aktualisieren klicken."
        )
        self._status_lbl.setAlignment(Qt.AlignCenter)
        self._status_lbl.setStyleSheet("color:#64748b;font-size:11px;")
        outer.addWidget(self._status_lbl)

    def _build_overview_tab(self) -> None:
        tab = QWidget()
        tab.setStyleSheet("background:#131625;")
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea{border:none;background:transparent;}")
        inner = QWidget()
        inner.setStyleSheet("background:transparent;")
        self._grid = QGridLayout(inner)
        self._grid.setSpacing(12)
        self._grid.setContentsMargins(8, 8, 8, 8)

        self._cards: list[_StatCard] = []
        defs = [
            ("\U0001f4cb", "Einzigartige Karten",  "\u2013", "#5865f2"),
            ("\U0001f4e6", "Exemplare gesamt",      "\u2013", "#3b82f6"),
            ("\U0001f4b0", "Gesamtwert (Markt)",    "\u2013", "#16a34a"),
            ("\U0001f3ea", "Zum Verkauf",            "\u2013", "#f59e0b"),
            ("\u2705",     "Verkauft",               "\u2013", "#10b981"),
            ("\U0001f4b5", "Verkaufserl\u00f6s",    "\u2013", "#06b6d4"),
            ("\U0001f4c8", "Gesch. Gewinn",          "\u2013", "#8b5cf6"),
        ]
        for i, (icon, label, val, accent) in enumerate(defs):
            card = _StatCard(icon, label, val, accent)
            self._cards.append(card)
            r, c = divmod(i, 3)
            self._grid.addWidget(card, r, c)
        for c in range(3):
            self._grid.setColumnStretch(c, 1)
        filler = QWidget()
        filler.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        filler.setStyleSheet("background:transparent;")
        self._grid.addWidget(filler, (len(defs) // 3) + 1, 0, 1, 3)
        scroll.setWidget(inner)
        vbox = QVBoxLayout(tab)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.addWidget(scroll)
        self._tabs.addTab(tab, "\u00dcbersicht")

    def _build_history_tab(self) -> None:
        tab = QWidget()
        tab.setStyleSheet("background:#131625;")
        vbox = QVBoxLayout(tab)
        vbox.setContentsMargins(12, 12, 12, 12)
        vbox.setSpacing(8)

        info = QLabel(
            "T\u00e4glich beim \u00d6ffnen dieser Seite wird der aktuelle Gesamtwert "
            "gespeichert. Der Chart zeigt die Wertentwicklung deiner Sammlung \u00fcber Zeit."
        )
        info.setWordWrap(True)
        info.setStyleSheet("font-size:10px;color:#64748b;")
        vbox.addWidget(info)

        self._chart = _ValueHistoryChart([])
        vbox.addWidget(self._chart, 1)

        self._hist_status = QLabel("Lade \u2026")
        self._hist_status.setAlignment(Qt.AlignCenter)
        self._hist_status.setStyleSheet("font-size:10px;color:#64748b;")
        vbox.addWidget(self._hist_status)

        self._tabs.addTab(tab, "Wert-Verlauf")

    def _build_sets_tab(self) -> None:
        tab = QWidget()
        tab.setStyleSheet("background:#131625;")
        vbox = QVBoxLayout(tab)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(0)

        if self._catalog_repo is None:
            lbl = QLabel("Katalog nicht verf\u00fcgbar.")
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setStyleSheet("color:#64748b;font-size:11px;")
            vbox.addWidget(lbl)
            self._tabs.addTab(tab, "Set-Vollst\u00e4ndigkeit")
            return

        # Column header
        hdr = QFrame()
        hdr.setFixedHeight(28)
        hdr.setStyleSheet(
            "QFrame{background:#2c3e50;}"
            "QFrame QLabel{border:none;background:transparent;"
            "color:white;font-weight:bold;font-size:10px;}"
        )
        hl = QHBoxLayout(hdr)
        hl.setContentsMargins(8, 0, 8, 0)
        hl.setSpacing(8)
        nm_hdr = QLabel("Set-Name / Serie")
        nm_hdr.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        hl.addWidget(nm_hdr, 3)
        yr_hdr = QLabel("Jahr")
        yr_hdr.setFixedWidth(36)
        yr_hdr.setAlignment(Qt.AlignCenter)
        hl.addWidget(yr_hdr)
        prog_hdr = QLabel("Fortschritt")
        prog_hdr.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        hl.addWidget(prog_hdr, 2)
        spacer_hdr = QLabel("")
        spacer_hdr.setFixedWidth(66)
        hl.addWidget(spacer_hdr)
        vbox.addWidget(hdr)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea{border:none;background:transparent;}")
        self._sets_container = QWidget()
        self._sets_container.setStyleSheet("background:#131625;")
        self._sets_layout = QVBoxLayout(self._sets_container)
        self._sets_layout.setContentsMargins(0, 0, 0, 0)
        self._sets_layout.setSpacing(0)
        self._sets_layout.addStretch(1)
        scroll.setWidget(self._sets_container)
        vbox.addWidget(scroll, 1)

        self._sets_status = QLabel("Noch nicht geladen.")
        self._sets_status.setAlignment(Qt.AlignCenter)
        self._sets_status.setStyleSheet("font-size:10px;color:#64748b;")
        vbox.addWidget(self._sets_status)

        self._tabs.addTab(tab, "Set-Vollst\u00e4ndigkeit")

    # ── Tab switching ──────────────────────────────────────────────────────────

    def _on_tab_changed(self, idx: int) -> None:
        if idx == 1 and not self._history_loaded:
            self._load_history()
        elif idx == 2 and not self._sets_loaded and self._catalog_repo is not None:
            self._load_sets()

    # ── Public API ────────────────────────────────────────────────────────────

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        if self._cards[0]._val_lbl.text() == "\u2013":
            self.load()

    def load(self) -> None:
        """Trigger overview stats reload (called from sidebar nav or refresh button)."""
        if self._worker and self._worker.isRunning():
            return
        self._status_lbl.setText("Lade \u2026")
        self._btn_refresh.setEnabled(False)
        self._worker = _StatsWorker(self._repo)
        self._worker.done.connect(self._on_loaded)
        self._worker.start()

    # ── Private loading ────────────────────────────────────────────────────────

    def _on_refresh(self) -> None:
        """Refresh button: re-load whichever tab is currently active."""
        idx = self._tabs.currentIndex()
        if idx == 1:
            self._history_loaded = False
            self._load_history()
        elif idx == 2:
            self._sets_loaded = False
            self._load_sets()
        else:
            self.load()

    def _load_history(self) -> None:
        if self._hist_worker and self._hist_worker.isRunning():
            return
        self._hist_status.setText("Lade \u2026")
        self._hist_worker = _HistoryWorker(self._repo)
        self._hist_worker.done.connect(self._on_history_loaded)
        self._hist_worker.start()

    def _load_sets(self) -> None:
        if self._catalog_repo is None:
            return
        if self._set_worker and self._set_worker.isRunning():
            return
        self._sets_status.setText("Lade \u2026")
        self._set_worker = _SetCompletionWorker(self._catalog_repo)
        self._set_worker.done.connect(self._on_sets_loaded)
        self._set_worker.start()

    # ── Callbacks ─────────────────────────────────────────────────────────────

    def _on_loaded(self, stats: dict) -> None:
        self._btn_refresh.setEnabled(True)
        if not stats:
            self._status_lbl.setText("Fehler beim Laden der Statistiken.")
            return

        total_cards  = int(stats.get("total_cards", 0) or 0)
        total_qty    = int(stats.get("total_quantity", 0) or 0)
        total_value  = float(stats.get("total_value", 0.0) or 0.0)
        for_sale     = int(stats.get("for_sale_count", 0) or 0)
        sold_count   = int(stats.get("sold_count", 0) or 0)
        sold_revenue = float(stats.get("sold_revenue", 0.0) or 0.0)
        profit       = float(stats.get("estimated_profit", 0.0) or 0.0)

        self._cards[0].set_value(f"{total_cards:,}")
        self._cards[1].set_value(f"{total_qty:,}")
        self._cards[2].set_value(_price_str(total_value))
        self._cards[3].set_value(f"{for_sale:,}")
        self._cards[4].set_value(f"{sold_count:,}")
        self._cards[5].set_value(_price_str(sold_revenue))
        self._cards[6].set_value(_price_str(profit))

        self._status_lbl.setText(
            f"Stand: {total_cards:,} Karten \u00b7 "
            f"Gesamtwert {_price_str(total_value)} \u00b7 "
            f"Gewinn {_price_str(profit)}"
        )

        # If history tab was already loaded, invalidate so it picks up the new snapshot
        if self._history_loaded:
            self._history_loaded = False
            if self._tabs.currentIndex() == 1:
                self._load_history()

    def _on_history_loaded(self, history: list) -> None:
        self._history_loaded = True
        self._chart.set_data(history)
        if history:
            latest = history[-1]
            self._hist_status.setText(
                f"{len(history)} Datenpunkt(e) \u00b7 "
                f"Letzter Wert: \u20ac{latest['total_value']:,.2f} "
                f"({latest['snapshot_date']})"
            )
        else:
            self._hist_status.setText(
                "Noch keine Verlaufsdaten \u2013 "
                "t\u00e4glich beim \u00d6ffnen dieser Seite gespeichert."
            )

    def _on_sets_loaded(self, rows: list) -> None:
        self._sets_loaded = True
        # Clear all rows (keep trailing stretch)
        while self._sets_layout.count() > 1:
            item = self._sets_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not rows:
            self._sets_status.setText(
                "Keine Sets im Katalog \u2013 zuerst Bulk-Download starten."
            )
            return

        insert_pos = self._sets_layout.count() - 1
        for i, data in enumerate(rows):
            row_widget = _SetRow(data)
            self._sets_layout.insertWidget(insert_pos + i, row_widget)

        sets_with_cards = sum(1 for r in rows if int(r.get("owned_count") or 0) > 0)
        owned_total = sum(int(r.get("owned_count") or 0) for r in rows)
        self._sets_status.setText(
            f"{len(rows)} Sets \u00b7 {sets_with_cards} davon angefangen \u00b7 "
            f"{owned_total} Karten in Sets erfasst"
        )
