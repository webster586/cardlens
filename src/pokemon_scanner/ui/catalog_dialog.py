from __future__ import annotations
import logging as _logging
from itertools import groupby
from pathlib import Path
from typing import Any
import requests as _requests
from PySide6.QtCore import Qt, QPointF, QSize, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPixmap, QPixmapCache
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPushButton, QScrollArea, QSizePolicy, QSpinBox,
    QTabWidget, QVBoxLayout, QWidget,
)
from src.pokemon_scanner.db.catalog_repository import CatalogRepository
from src.pokemon_scanner.db.repositories import CollectionRepository
from src.pokemon_scanner.core.paths import CATALOG_IMAGES_DIR

_THUMB_W = 200
_THUMB_H = 280
_CARD_W = 216
_CHECK = "\u2714"
# Approximate tile height (margins + thumb + labels)
_TILE_H = _THUMB_H + 150


def _lbl(text: str, style: str = "") -> QLabel:
    """Convenience: centered, word-wrapped QLabel with optional stylesheet."""
    w = QLabel(text)
    w.setWordWrap(True)
    w.setAlignment(Qt.AlignCenter)
    if style:
        w.setStyleSheet(style)
    return w


def _resolve_image_path(stored: str | None) -> str | None:
    """Return a usable file path for *stored*, or None if not found.

    Falls back to CATALOG_IMAGES_DIR/<filename> so the frozen EXE (which has
    catalog_images/ next to it) works even when the DB still contains absolute
    dev-machine paths.
    """
    if not stored:
        return None
    p = Path(stored)
    if p.exists():
        return stored
    fallback = CATALOG_IMAGES_DIR / p.name
    if fallback.exists():
        return str(fallback)
    return None


# Module-level pixmap cache: path + dimensions → already-scaled QPixmap.
# Avoids repeated disk reads + GPU uploads when the same logo/symbol is used
# multiple times per catalog rebuild (header strip + tile grid = 3× per set).
_px_cache: dict[str, QPixmap] = {}


def _cached_pixmap(path: str, height: int, width: int = 0) -> QPixmap:
    """Return a scaled QPixmap, loaded once and cached for the process lifetime."""
    key = f"{path}:{width}x{height}"
    if key not in _px_cache:
        if width > 0:
            _px_cache[key] = QPixmap(path).scaled(
                width, height, Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
        else:
            _px_cache[key] = QPixmap(path).scaledToHeight(height, Qt.SmoothTransformation)
    return _px_cache[key]


class _CardTile(QFrame):
    remove_requested = Signal(int)  # entry id

    def __init__(self, entry: dict, owned_row: dict | None, p: QWidget | None = None) -> None:
        super().__init__(p)
        self.setFixedWidth(_CARD_W)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        self.setFrameShape(QFrame.StyledPanel)
        bg = "#eafaea" if owned_row else "#f9f9f9"
        bd = "#2a9a2a" if owned_row else "#cccccc"
        # All child QLabels get transparent background via descendant rule
        self.setStyleSheet(
            f"QFrame#tile {{ background:{bg}; border:2px solid {bd}; border-radius:6px; }}"
            "QFrame#tile QLabel { border:none; background:transparent; }"
        )
        self.setObjectName("tile")

        vbox = QVBoxLayout(self)
        vbox.setContentsMargins(6, 6, 6, 6)
        vbox.setSpacing(4)

        # Thumbnail label (fixed size, inside a fixed container to allow badge overlay)
        thumb_container = QWidget()
        thumb_container.setFixedSize(_THUMB_W, _THUMB_H)
        thumb_container.setStyleSheet("background:transparent; border:none;")

        img_lbl = QLabel(thumb_container)
        img_lbl.setFixedSize(_THUMB_W, _THUMB_H)
        img_lbl.setAlignment(Qt.AlignCenter)
        img_lbl.setStyleSheet(
            "border:1px solid #aaaaaa; background:#1a1a2e; border-radius:3px;"
        )
        local = _resolve_image_path(entry.get("local_image_path"))
        if local:
            cache_key = f"{local}:{_THUMB_W}x{_THUMB_H}"
            cached = QPixmapCache.find(cache_key)
            if cached is not None and not cached.isNull():
                px = cached
            else:
                px = QPixmap(local).scaled(_THUMB_W, _THUMB_H, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                QPixmapCache.insert(cache_key, px)
            img_lbl.setPixmap(px)
        else:
            img_lbl.setText("?")
            img_lbl.setStyleSheet(img_lbl.styleSheet() + " color:#888888; font-size:24px;")

        if owned_row:
            qty = owned_row.get("quantity", 1) or 1
            badge_text = f"{_CHECK}\u202f{qty}\u00d7" if qty > 1 else _CHECK
            badge = QLabel(badge_text, thumb_container)
            badge.setStyleSheet(
                "background:#1a8a1a; color:white; font-size:11px;"
                " font-weight:bold; border-radius:8px; padding:1px 4px;"
                " border:none;"
            )
            badge.adjustSize()
            badge.move(_THUMB_W - badge.width() - 2, 2)
            badge.raise_()

            # Remove button (×) top-left
            entry_id: int | None = owned_row.get("id")
            rm_btn = QPushButton("\u00d7", thumb_container)
            rm_btn.setFixedSize(20, 20)
            rm_btn.move(2, 2)
            rm_btn.setStyleSheet(
                "QPushButton { background:#c0392b; color:white; font-size:13px;"
                " font-weight:bold; border-radius:10px; border:none; padding:0; }"
                "QPushButton:hover { background:#e74c3c; }"
            )
            if entry_id:
                rm_btn.clicked.connect(lambda _checked, eid=entry_id: self.remove_requested.emit(eid))
            else:
                rm_btn.setEnabled(False)
            rm_btn.raise_()

        vbox.addWidget(thumb_container, 0, Qt.AlignHCenter)

        vbox.addWidget(_lbl(entry.get("name", ""), "font-weight:bold; font-size:11px;"))

        sn = f"{entry.get('set_name') or ''}  #{entry.get('card_number') or ''}".strip()
        vbox.addWidget(_lbl(sn, "font-size:9px; color:#64748b;"))
        # Language badge — colored pill label, works reliably on Windows (no emoji needed)
        _LANG_BADGE: dict[str, tuple[str, str, str]] = {
            # lang_code: (label, bg_color, text_color)
            "en":      ("EN",  "#3c5a99", "#ffffff"),
            "de":      ("DE",  "#000000", "#ffcc00"),
            "ja":      ("JP",  "#bc002d", "#ffffff"),
            "zh-Hans": ("CHI", "#de2910", "#ffde00"),
            "zh-Hant": ("CHI", "#de2910", "#ffde00"),
            "ko":      ("KO",  "#003478", "#ffffff"),
            "fr":      ("FR",  "#002395", "#ffffff"),
            "it":      ("IT",  "#009246", "#ffffff"),
            "es":      ("ES",  "#aa151b", "#f1bf00"),
            "pt":      ("PT",  "#006600", "#ffcc00"),
        }
        # Show the OWNED language (physical card) when owned; else catalog language.
        display_lang = (owned_row.get("language") if owned_row else None) or entry.get("language") or ""
        if display_lang and display_lang in _LANG_BADGE:
            badge_lbl, bg, fg = _LANG_BADGE[display_lang]
            _badge = QLabel(badge_lbl)
            _badge.setAlignment(Qt.AlignCenter)
            _badge.setFixedHeight(16)
            _badge.setStyleSheet(
                f"background:{bg}; color:{fg}; font-size:9px; font-weight:bold;"
                f" border-radius:3px; padding: 0 4px;"
            )
            vbox.addWidget(_badge)
        elif display_lang:
            vbox.addWidget(_lbl(display_lang, "font-size:9px; color:#64748b;"))

        if owned_row and owned_row.get("last_price") is not None:
            sc, cur = owned_row["last_price"], owned_row.get("price_currency") or "USD"
            vbox.addWidget(_lbl(f"Bei Scan: {sc:.2f}\u202f{cur}",
                                "font-size:9px; color:#16a34a; font-weight:bold;"))

        if owned_row:
            _COND_COLOR = {"M": "#16a34a", "NM": "#16a34a", "LP": "#ca8a04", "MP": "#ea580c", "HP": "#dc2626"}
            cond = (owned_row.get("condition") or "NM").upper()
            cond_col = _COND_COLOR.get(cond, "#64748b")
            cond_lbl = QLabel(f"Zustand: {cond}")
            cond_lbl.setAlignment(Qt.AlignCenter)
            cond_lbl.setStyleSheet(
                f"font-size:9px; font-weight:bold; color:white; background:{cond_col};"
                " border-radius:3px; padding:1px 5px; border:none;"
            )
            vbox.addWidget(cond_lbl)

        price = entry.get("best_price")
        cur2 = entry.get("price_currency") or "USD"
        prefix = "Heute: " if owned_row else ""
        vbox.addWidget(_lbl(
            f"{prefix}{price:.2f}\u202f{cur2}" if price else "\u2013",
            "font-size:10px; font-weight:bold; color:#16a34a;"
        ))

        ts = (entry.get("updated_at") or entry.get("fetched_at") or "")[:16].replace("T", " ")
        vbox.addWidget(_lbl(ts, "font-size:8px; color:#94a3b8;"))

        # Rich hover tooltip with extended card metadata
        _tt: list[str] = []
        _st = entry.get("supertype", "")
        _sub = entry.get("subtypes", "")
        if _st:
            _tt.append(f"<b>Typ:</b> {_st}" + (f" ({_sub.replace(',', ', ')})" if _sub else ""))
        if entry.get("hp"):
            _tt.append(f"<b>KP:</b> {entry['hp']}")
        if entry.get("types"):
            _tt.append(f"<b>Element:</b> {entry['types'].replace(',', ', ')}")
        if entry.get("rarity"):
            _tt.append(f"<b>Seltenheit:</b> {entry['rarity']}")
        if entry.get("artist"):
            _tt.append(f"<b>Illustrator:</b> {entry['artist']}")
        if entry.get("pokedex_numbers"):
            _tt.append(f"<b>Pokédex:</b> #{entry['pokedex_numbers']}")
        if entry.get("regulation_mark"):
            _tt.append(f"<b>Regulierung:</b> {entry['regulation_mark']}")
        if entry.get("set_series"):
            _tt.append(f"<b>Serie:</b> {entry['set_series']}")
        if entry.get("eur_price") is not None:
            _tt.append(f"<b>EUR:</b> {entry['eur_price']:.2f} €")
        if entry.get("usd_price") is not None:
            _tt.append(f"<b>USD:</b> {entry['usd_price']:.2f} $")
        if _tt:
            self.setToolTip("<br>".join(_tt))


# ── Year-grouped set tiles ────────────────────────────────────────────────────
_SET_TILE_W = 316
_SET_TILE_H = 212


class _SetHeaderTile(QFrame):
    """Compact set tile for the year-section grid: logo + name, clickable."""
    clicked = Signal(str)  # emits set_name

    def __init__(
        self, set_name: str, logo_path: str | None, owned_count: int,
        set_total: int = 0,
        p: QWidget | None = None,
    ) -> None:
        super().__init__(p)
        self.set_name = set_name
        self.setFixedSize(_SET_TILE_W, _SET_TILE_H)
        self.setCursor(Qt.PointingHandCursor)
        self.setObjectName("settile")
        self._base_ss = (
            "QFrame#settile{background:#f0f4f8;border:2px solid #c0ccdd;border-radius:5px;}"
            "QFrame#settile QLabel{border:none;background:transparent;}"
        )
        self._active_ss = (
            "QFrame#settile{background:#ddeeff;border:2px solid #3a7ecf;border-radius:5px;}"
            "QFrame#settile QLabel{border:none;background:transparent;}"
        )
        self.setStyleSheet(self._base_ss)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(3)
        lgo = QLabel()
        lgo.setAlignment(Qt.AlignCenter)
        lgo.setFixedHeight(92)
        lgo.setStyleSheet("border:none;background:transparent;")
        if logo_path and Path(logo_path).exists():
            px = _cached_pixmap(logo_path, 84)
            if not px.isNull():
                lgo.setPixmap(px)
            else:
                lgo.setText("?")
                lgo.setStyleSheet("border:none;background:transparent;font-size:24px;color:#888;")
        else:
            lgo.setText("?")
            lgo.setStyleSheet("border:none;background:transparent;font-size:24px;color:#888;")
        lay.addWidget(lgo)
        nm = QLabel(set_name)
        nm.setAlignment(Qt.AlignCenter)
        nm.setWordWrap(True)
        nm.setStyleSheet("font-size:10px;color:#64748b;border:none;background:transparent;")
        nm.setMaximumHeight(32)
        lay.addWidget(nm)
        # Sealed product price labels
        self._etb_lbl = QLabel("ETB: –")
        self._etb_lbl.setAlignment(Qt.AlignCenter)
        self._etb_lbl.setWordWrap(False)
        self._etb_lbl.setStyleSheet(
            "font-size:9px;color:#2563eb;border:none;background:transparent;"
        )
        lay.addWidget(self._etb_lbl)
        self._bundle_lbl = QLabel("Bundle: –")
        self._bundle_lbl.setAlignment(Qt.AlignCenter)
        self._bundle_lbl.setWordWrap(False)
        self._bundle_lbl.setStyleSheet(
            "font-size:9px;color:#2563eb;border:none;background:transparent;"
        )
        lay.addWidget(self._bundle_lbl)
        self.setToolTip(set_name)
        if owned_count > 0:
            bdg = QLabel(
                f"\u2714\u202f{owned_count}\u00d7" if owned_count > 1 else "\u2714", self
            )
            bdg.setFixedSize(34, 16)
            bdg.setAlignment(Qt.AlignCenter)
            bdg.setStyleSheet(
                "background:#1a8a1a;color:white;font-size:9px;"
                "font-weight:bold;border-radius:4px;border:none;"
            )
            bdg.move(_SET_TILE_W - 37, 4)
        # Set completion progress bar
        from PySide6.QtWidgets import QProgressBar
        prog = QProgressBar()
        prog.setRange(0, max(set_total, 1))
        prog.setValue(min(owned_count, max(set_total, 1)))
        prog.setFixedHeight(10)
        prog.setTextVisible(False)
        prog.setStyleSheet(
            "QProgressBar{border:1px solid #c0ccdd;border-radius:4px;background:#e8edf4;}"
            "QProgressBar::chunk{background:#2a9a2a;border-radius:3px;}"
        )
        lay.addWidget(prog)
        if set_total > 0:
            pct = int(owned_count / set_total * 100)
            prog_lbl = QLabel(f"{owned_count}\u202f/\u202f{set_total}\u202f({pct}\u202f%)")
        else:
            prog_lbl = QLabel(f"{owned_count}\u202f Karten")
        prog_lbl.setAlignment(Qt.AlignCenter)
        prog_lbl.setStyleSheet(
            "font-size:9px;color:#64748b;border:none;background:transparent;"
        )
        lay.addWidget(prog_lbl)

    def update_sealed_prices(self, prices: dict) -> None:
        """Update ETB/Bundle price labels. prices = {'etb': {'usd': x, 'eur': y}, ...}"""
        def _fmt(key: str) -> str:
            data = prices.get(key)
            if not data:
                return "–"
            parts = []
            if data.get("usd") is not None:
                parts.append(f"${data['usd']:.0f}")
            if data.get("eur") is not None:
                parts.append(f"\u20ac{data['eur']:.0f}")
            return " / ".join(parts) if parts else "–"
        self._etb_lbl.setText(f"ETB: {_fmt('etb')}")
        self._bundle_lbl.setText(f"Bundle: {_fmt('bundle')}")

    def set_active(self, active: bool) -> None:
        self.setStyleSheet(self._active_ss if active else self._base_ss)

    def mousePressEvent(self, event) -> None:
        super().mousePressEvent(event)
        self.clicked.emit(self.set_name)


class _SetTileFlow(QWidget):
    """Responsive flow grid for _SetHeaderTile items with inline card expansion."""
    _SP = 8

    def __init__(self, tiles: list, p: QWidget | None = None) -> None:
        super().__init__(p)
        self._tiles = tiles
        self._by_name: dict[str, _SetHeaderTile] = {t.set_name: t for t in tiles}
        for t in tiles:
            t.setParent(self)
        self._active_set: str | None = None
        self._expand_widget: QWidget | None = None
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._pending_width = 1200
        self._tmr = QTimer(self)
        self._tmr.setSingleShot(True)
        self._tmr.setInterval(60)
        self._tmr.timeout.connect(lambda: self._relayout(self._pending_width))
        self._relayout(1200)

    def set_expand_widget(self, widget: QWidget) -> None:
        """Register the card-expansion widget that appears inline after the active row."""
        self._expand_widget = widget
        widget.setParent(self)
        widget.setVisible(False)

    def _expand_height(self, w: int) -> int:
        """Compute the pixel height of the expand widget when laid out at width w."""
        ew = self._expand_widget
        if ew is None:
            return 0
        lay = ew.layout()
        if lay is None:
            return ew.sizeHint().height()
        m = lay.contentsMargins()
        h = m.top() + m.bottom()
        for i in range(lay.count()):
            child = lay.itemAt(i).widget()
            if child is None:
                continue
            if isinstance(child, _FlowGrid):
                child._relayout(w)
            h += child.height()
            if i < lay.count() - 1:
                h += lay.spacing()
        return max(h, 0)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._pending_width = event.size().width()
        self._tmr.start()

    def _relayout(self, w: int) -> None:
        if not self._tiles:
            self.setFixedHeight(0)
            return
        sp = self._SP
        cols = max(1, (w + sp) // (_SET_TILE_W + sp))
        # Build rows
        rows: list[list[_SetHeaderTile]] = []
        for i in range(0, len(self._tiles), cols):
            rows.append(self._tiles[i:i + cols])
        # Find which row contains the active set
        active_row: int | None = None
        if self._active_set:
            for ri, row in enumerate(rows):
                if any(t.set_name == self._active_set for t in row):
                    active_row = ri
                    break
        # Position tiles and optionally inject expand widget after active row
        y = 0
        for ri, row in enumerate(rows):
            for ci, tile in enumerate(row):
                tile.setGeometry(ci * (_SET_TILE_W + sp), y, _SET_TILE_W, _SET_TILE_H)
            y += _SET_TILE_H + sp
            if ri == active_row and self._expand_widget is not None:
                eh = self._expand_height(w)
                self._expand_widget.setGeometry(0, y, w, eh)
                self._expand_widget.setVisible(True)
                y += eh + sp
        if self._expand_widget is not None and active_row is None:
            self._expand_widget.setVisible(False)
        self.setFixedHeight(max(y, 1))

    def set_active(self, set_name: str, active: bool) -> None:
        if set_name in self._by_name:
            self._by_name[set_name].set_active(active)
        self._active_set = set_name if active else None
        self._relayout(self._pending_width)

    def update_sealed_prices(self, set_name: str, prices: dict) -> None:
        if set_name in self._by_name:
            self._by_name[set_name].update_sealed_prices(prices)


class _YearSection(QWidget):
    """Collapsible year section: set tiles in a grid, click opens that set's cards."""
    remove_requested = Signal(int)

    def __init__(
        self,
        year: str,
        sets_info: list,    # list of (set_name, logo_path, symbol_path, entries_list)
        owned: dict,        # api_id → col_row
        p: QWidget | None = None,
    ) -> None:
        super().__init__(p)
        self._owned = owned
        self._active_set: str | None = None
        self._card_grids: dict[str, _FlowGrid] = {}
        self._sets: dict[str, tuple] = {}
        for set_name, logo_path, symbol_path, entries in sets_info:
            cnt = sum(1 for e in entries if owned.get(e.get("api_id") or ""))
            self._sets[set_name] = (logo_path, symbol_path, entries, cnt)
        owned_sets = sum(1 for _, (_, _, _, cnt) in self._sets.items() if cnt > 0)
        total_sets = len(self._sets)

        vbox = QVBoxLayout(self)
        vbox.setContentsMargins(0, 3, 0, 3)
        vbox.setSpacing(0)

        # Year header bar
        self._hdr = QWidget()
        self._hdr.setFixedHeight(44)
        self._hdr.setCursor(Qt.PointingHandCursor)
        self._hdr.setStyleSheet("background:#2c3e50;border-radius:4px;")
        hl = QHBoxLayout(self._hdr)
        hl.setContentsMargins(12, 0, 12, 0)
        hl.setSpacing(8)
        self._arrow_lbl = QLabel("\u25b6")
        self._arrow_lbl.setFixedWidth(14)
        self._arrow_lbl.setStyleSheet(
            "color:white;font-size:11px;border:none;background:transparent;"
        )
        hl.addWidget(self._arrow_lbl)
        yr_lbl = QLabel(year)
        yr_lbl.setStyleSheet(
            "color:white;font-size:16px;font-weight:bold;border:none;background:transparent;"
        )
        hl.addWidget(yr_lbl)
        hl.addSpacing(10)
        # Set logos + symbols strip — each set shown as [symbol] [logo]
        for set_name, (logo_path, symbol_path, _, _cnt) in self._sets.items():
            if symbol_path and Path(symbol_path).exists():
                sym_lbl = QLabel()
                sym_lbl.setStyleSheet("border:none;background:transparent;")
                sym_lbl.setToolTip(set_name)
                spx = _cached_pixmap(symbol_path, 20)
                if not spx.isNull():
                    sym_lbl.setPixmap(spx)
                    sym_lbl.setFixedSize(spx.width(), 20)
                    hl.addWidget(sym_lbl)
            if logo_path and Path(logo_path).exists():
                logo_lbl = QLabel()
                logo_lbl.setStyleSheet("border:none;background:transparent;")
                logo_lbl.setToolTip(set_name)
                px = _cached_pixmap(logo_path, 26)
                if not px.isNull():
                    logo_lbl.setPixmap(px)
                    logo_lbl.setFixedSize(px.width(), 26)
                    hl.addWidget(logo_lbl)
        hl.addStretch()
        meta_lbl = QLabel(f"{total_sets} Sets  \u00b7  {owned_sets} im Besitz")
        meta_lbl.setStyleSheet(
            "color:#aabbcc;font-size:9px;border:none;background:transparent;"
        )
        hl.addWidget(meta_lbl)
        self._hdr.mousePressEvent = lambda _e: self._toggle()
        vbox.addWidget(self._hdr)

        # Content (collapsed by default)
        self._content = QWidget()
        self._content.setVisible(False)
        cvbox = QVBoxLayout(self._content)
        cvbox.setContentsMargins(4, 6, 4, 4)
        cvbox.setSpacing(6)
        tiles: list[_SetHeaderTile] = []
        for set_name, (logo_path, _, entries, cnt) in self._sets.items():
            set_total = (entries[0].get("set_total") or 0) if entries else 0
            t = _SetHeaderTile(set_name, logo_path, cnt, set_total)
            t.clicked.connect(self._on_set_clicked)
            tiles.append(t)
        self._tile_flow = _SetTileFlow(tiles)
        cvbox.addWidget(self._tile_flow)
        # Expandable card area — injected inline after the active set's row
        self._card_area = QWidget()
        QVBoxLayout(self._card_area).setContentsMargins(0, 4, 0, 0)
        self._tile_flow.set_expand_widget(self._card_area)
        vbox.addWidget(self._content)

    def _toggle(self) -> None:
        exp = not self._content.isVisible()
        self._content.setVisible(exp)
        self._arrow_lbl.setText("\u25bc" if exp else "\u25b6")

    def _on_set_clicked(self, set_name: str) -> None:
        if self._active_set == set_name:
            self._tile_flow.set_active(set_name, False)
            self._active_set = None
        else:
            if self._active_set:
                self._tile_flow.set_active(self._active_set, False)
            self._active_set = set_name
            self._show_cards(set_name)
            self._tile_flow.set_active(set_name, True)

    def _show_cards(self, set_name: str) -> None:
        lay = self._card_area.layout()
        while lay.count():
            item = lay.takeAt(0)
            if item.widget():
                item.widget().setParent(None)
        if set_name not in self._card_grids:
            _, _, entries, _ = self._sets[set_name]
            tiles: list[_CardTile] = []
            for entry in entries:
                owned_row = self._owned.get(entry.get("api_id") or "")
                tile = _CardTile(entry, owned_row)
                if owned_row:
                    tile.remove_requested.connect(self.remove_requested)
                tiles.append(tile)
            self._card_grids[set_name] = _FlowGrid(tiles)
        lay.addWidget(self._card_grids[set_name])

    def tiles_by_name(self) -> dict[str, "_SetHeaderTile"]:
        return dict(self._tile_flow._by_name)


class _SetDivider(QWidget):
    """Full-width horizontal divider with arrow toggle, set logo and set name."""
    clicked = Signal()

    def __init__(self, set_name: str, logo_path: str | None, p: QWidget | None = None) -> None:
        super().__init__(p)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet("background: transparent;")
        hbox = QHBoxLayout(self)
        hbox.setContentsMargins(8, 4, 8, 4)
        hbox.setSpacing(8)

        self._arrow_lbl = QLabel("\u25b6")
        self._arrow_lbl.setFixedWidth(16)
        self._arrow_lbl.setStyleSheet(
            "border: none; background: transparent; font-size: 10px; color: #666;"
        )
        hbox.addWidget(self._arrow_lbl)

        def _line() -> QFrame:
            ln = QFrame()
            ln.setFrameShape(QFrame.HLine)
            ln.setStyleSheet("border: none; border-top: 4px solid #cccccc; margin-top: 14px;")
            ln.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            return ln

        hbox.addWidget(_line(), 1)

        center = QWidget()
        center.setStyleSheet("background: transparent;")
        c_hbox = QHBoxLayout(center)
        c_hbox.setContentsMargins(6, 0, 6, 0)
        c_hbox.setSpacing(6)

        if logo_path and Path(logo_path).exists():
            logo_lbl = QLabel()
            logo_lbl.setStyleSheet("border: none; background: transparent;")
            px = _cached_pixmap(logo_path, 28)
            logo_lbl.setPixmap(px)
            c_hbox.addWidget(logo_lbl)

        name_lbl = QLabel(set_name)
        name_lbl.setStyleSheet(
            "font-size: 11px; font-weight: bold; color: #444444;"
            " border: none; background: transparent;"
        )
        c_hbox.addWidget(name_lbl)

        hbox.addWidget(center)
        hbox.addWidget(_line(), 1)

    def set_expanded(self, expanded: bool) -> None:
        self._arrow_lbl.setText("\u25bc" if expanded else "\u25b6")

    def mousePressEvent(self, event) -> None:
        super().mousePressEvent(event)
        self.clicked.emit()


class _FlowGrid(QWidget):
    """Responsive tile grid: positions tiles with absolute geometry, reflows on resize."""
    _SP = 8

    def __init__(self, tiles: list, p: QWidget | None = None) -> None:
        super().__init__(p)
        self._tiles = tiles
        for t in tiles:
            t.setParent(self)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._pending_width: int = 1200
        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.setInterval(60)
        self._resize_timer.timeout.connect(lambda: self._relayout(self._pending_width))
        # Initial layout with a reasonable guess; real layout fires on first show
        self._relayout(1200)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._pending_width = event.size().width()
        self._resize_timer.start()

    def _relayout(self, w: int) -> None:
        if not self._tiles:
            self.setFixedHeight(0)
            return
        sp = self._SP
        cols = max(1, (w + sp) // (_CARD_W + sp))
        x = y = 0
        col = 0
        row_h = 0
        for tile in self._tiles:
            th = _TILE_H
            tile.setGeometry(x, y, _CARD_W, th)
            row_h = max(row_h, th)
            col += 1
            if col >= cols:
                col = 0
                x = 0
                y += row_h + sp
                row_h = 0
            else:
                x += _CARD_W + sp
        if col > 0:
            y += row_h + sp
        self.setFixedHeight(max(y, 1))


class _CollapsibleSet(QWidget):
    """A set section that starts collapsed; tiles are built lazily on first expand."""
    remove_requested = Signal(int)

    def __init__(
        self,
        set_name: str,
        logo_path: str | None,
        entries: list[dict],
        owned: dict,
        p: QWidget | None = None,
    ) -> None:
        super().__init__(p)
        self._entries = entries
        self._owned = owned
        self._expanded = False
        self._flow: _FlowGrid | None = None

        vbox = QVBoxLayout(self)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(0)

        self._header = _SetDivider(set_name, logo_path)
        self._header.clicked.connect(self._toggle)
        vbox.addWidget(self._header)

        self._content = QWidget()
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        self._content.setVisible(False)
        vbox.addWidget(self._content)

    def expand_for_search(self) -> None:
        """Expand immediately without toggle — used for search results."""
        if not self._expanded:
            self._build_tiles()
            self._content.setVisible(True)
            self._header.set_expanded(True)
            self._expanded = True

    def _toggle(self) -> None:
        if self._expanded:
            self._content.setVisible(False)
            self._header.set_expanded(False)
            self._expanded = False
        else:
            self._build_tiles()
            self._content.setVisible(True)
            self._header.set_expanded(True)
            self._expanded = True

    def _build_tiles(self) -> None:
        if self._flow is not None:
            return  # already built on a previous expand
        tiles: list[_CardTile] = []
        for entry in self._entries:
            owned_row = self._owned.get(entry.get("api_id") or "")
            tile = _CardTile(entry, owned_row)
            if owned_row:
                tile.remove_requested.connect(self.remove_requested)
            tiles.append(tile)
        self._flow = _FlowGrid(tiles)
        self._content_layout.addWidget(self._flow)


def _logo_path_for_set(set_name: str, db_path: str | None = None) -> str | None:
    """Return the local logo path for a set, checking DB value first, then reconstructed path."""
    if db_path and Path(db_path).exists():
        return db_path
    safe = set_name.replace("/", "_").replace("\\", "_").replace(" ", "_")
    candidate = CATALOG_IMAGES_DIR / f"logo_{safe}.png"
    if candidate.exists():
        return str(candidate)
    return None


def _symbol_path_for_set(set_name: str, db_path: str | None = None) -> str | None:
    """Return the local symbol path for a set, checking DB value first, then reconstructed path."""
    if db_path and Path(db_path).exists():
        return db_path
    safe = set_name.replace("/", "_").replace("\\", "_").replace(" ", "_")
    candidate = CATALOG_IMAGES_DIR / f"symbol_{safe}.png"
    if candidate.exists():
        return str(candidate)
    return None


class _SetLogoDownloadWorker(QThread):
    """Downloads missing set logos in the background and updates the DB."""
    done = Signal()

    def __init__(self, repo: CatalogRepository) -> None:
        super().__init__()
        self._repo = repo
        self._log = _logging.getLogger(__name__)

    def run(self) -> None:
        import requests as _r
        import datetime as _dt
        try:
            with self._repo.database.connect() as conn:
                logo_rows = conn.execute(
                    "SELECT DISTINCT set_name, set_logo_url FROM card_catalog"
                    " WHERE set_logo_url IS NOT NULL AND set_logo_url != ''"
                ).fetchall()
                try:
                    sym_rows = conn.execute(
                        "SELECT DISTINCT set_name, set_symbol_url FROM card_catalog"
                        " WHERE set_symbol_url IS NOT NULL AND set_symbol_url != ''"
                    ).fetchall()
                except Exception:
                    sym_rows = []
        except Exception as exc:
            self._log.warning("Logo worker DB read failed: %s", exc)
            return

        CATALOG_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
        updated = 0
        for set_name, url in logo_rows:
            if not url or _logo_path_for_set(set_name) is not None:
                continue
            safe = set_name.replace("/", "_").replace("\\", "_").replace(" ", "_")
            dest = CATALOG_IMAGES_DIR / f"logo_{safe}.png"
            try:
                resp = _r.get(url, headers={"User-Agent": "CardLens/1.0"}, timeout=15)
                resp.raise_for_status()
                dest.write_bytes(resp.content)
                now = _dt.datetime.utcnow().isoformat()
                with self._repo.database.connect() as conn:
                    conn.execute(
                        "UPDATE card_catalog SET set_local_logo_path=?, updated_at=? WHERE set_name=?",
                        (str(dest), now, set_name),
                    )
                    conn.commit()
                updated += 1
            except Exception as exc:
                self._log.debug("Logo download failed for %r: %s", set_name, exc)
        for set_name, url in sym_rows:
            if not url or _symbol_path_for_set(set_name) is not None:
                continue
            safe = set_name.replace("/", "_").replace("\\", "_").replace(" ", "_")
            dest = CATALOG_IMAGES_DIR / f"symbol_{safe}.png"
            try:
                resp = _r.get(url, headers={"User-Agent": "CardLens/1.0"}, timeout=15)
                resp.raise_for_status()
                dest.write_bytes(resp.content)
                now = _dt.datetime.utcnow().isoformat()
                with self._repo.database.connect() as conn:
                    conn.execute(
                        "UPDATE card_catalog SET set_symbol_local_path=?, updated_at=? WHERE set_name=?",
                        (str(dest), now, set_name),
                    )
                    conn.commit()
                updated += 1
            except Exception as exc:
                self._log.debug("Symbol download failed for %r: %s", set_name, exc)
        if updated:
            self._log.info("Downloaded %d set logos/symbols", updated)
        self.done.emit()


def _resolve_image(col: dict, cat: dict) -> str | None:
    """Return the best available local card-art path for a collection row."""
    # 1. Catalog DB already has the path
    p = cat.get("local_image_path")
    if p and Path(p).exists():
        return p
    # 2. Reconstruct from api_id (catalog saves as catalog_images/{safe_id}.jpg)
    api_id = col.get("api_id") or cat.get("api_id") or ""
    if api_id:
        safe = api_id.replace("/", "_").replace("\\", "_")
        candidate = CATALOG_IMAGES_DIR / f"{safe}.jpg"
        if candidate.exists():
            return str(candidate)
    return None


class _SealedPriceWorker(QThread):
    """Fetches ETB and Booster Bundle prices from TCGPlayer API for a list of set names."""
    prices_ready = Signal(str, dict)  # set_name, {'etb': {'usd': x, 'eur': y}, 'bundle': {...}}
    status = Signal(str)
    done = Signal()

    _TCGP_TOKEN_URL = "https://api.tcgplayer.com/token"
    _TCGP_PRODUCTS_URL = "https://api.tcgplayer.com/catalog/products"
    _TCGP_PRICING_URL = "https://api.tcgplayer.com/pricing/product/{}"
    _EXCHANGE_URL = "https://api.exchangerate-api.com/v4/latest/USD"
    _POKEMON_CATEGORY = 3

    def __init__(
        self,
        repo: CatalogRepository,
        set_names: list[str],
        public_key: str,
        private_key: str,
    ) -> None:
        super().__init__()
        self._repo = repo
        self._set_names = set_names
        self._public_key = public_key
        self._private_key = private_key
        self._log = _logging.getLogger(__name__)

    def run(self) -> None:
        if not self._public_key or not self._private_key:
            self.status.emit("Kein TCGPlayer API-Key konfiguriert – Versiegelungspreise nicht geladen.")
            self.done.emit()
            return

        # Authenticate
        try:
            resp = _requests.post(
                self._TCGP_TOKEN_URL,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._public_key,
                    "client_secret": self._private_key,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=15,
            )
            if not resp.ok:
                self.status.emit(f"TCGPlayer Auth fehlgeschlagen (HTTP {resp.status_code}).")
                self.done.emit()
                return
            token = resp.json().get("access_token", "")
            if not token:
                self.status.emit("TCGPlayer Auth: kein Token erhalten.")
                self.done.emit()
                return
        except Exception as exc:
            self.status.emit(f"TCGPlayer Verbindungsfehler: {exc}")
            self.done.emit()
            return

        # EUR exchange rate (free API, no key needed)
        eur_rate = 0.92
        try:
            fx = _requests.get(self._EXCHANGE_URL, timeout=10)
            if fx.ok:
                eur_rate = fx.json().get("rates", {}).get("EUR", 0.92)
        except Exception:
            pass

        headers = {"Authorization": f"Bearer {token}"}
        total = len(self._set_names)

        for i, set_name in enumerate(self._set_names, 1):
            self.status.emit(f"TCGPlayer Versiegelungspreise {i}/{total}: {set_name} …")
            prices: dict = {}
            for product_key, keyword in [("etb", "Elite Trainer Box"), ("bundle", "Booster Bundle")]:
                price_usd = self._fetch_price(headers, set_name, keyword)
                if price_usd is not None:
                    price_eur = round(price_usd * eur_rate, 2)
                    prices[product_key] = {"usd": price_usd, "eur": price_eur}
                    try:
                        self._repo.upsert_sealed_price(set_name, product_key, price_usd, price_eur)
                    except Exception as exc:
                        self._log.warning("upsert_sealed_price failed for %s %s: %s", set_name, product_key, exc)
                else:
                    prices[product_key] = None
            if any(v is not None for v in prices.values()):
                self.prices_ready.emit(set_name, prices)

        self.status.emit("TCGPlayer Versiegelungspreise aktualisiert.")
        self.done.emit()

    def _fetch_price(self, headers: dict, set_name: str, product_type: str) -> float | None:
        try:
            resp = _requests.get(
                self._TCGP_PRODUCTS_URL,
                params={
                    "categoryId": self._POKEMON_CATEGORY,
                    "productTypes": "Sealed Products",
                    "productName": f"{set_name} {product_type}",
                    "limit": 5,
                    "offset": 0,
                },
                headers=headers,
                timeout=15,
            )
            if not resp.ok:
                return None
            products = resp.json().get("results", [])
            if not products:
                return None
            # Prefer a product whose name contains both a set-name substring and the keyword
            sn_words = set_name.lower().split()[:3]
            pt_lower = product_type.lower().split()[0]  # "elite" or "booster"
            best = None
            for p in products:
                name = (p.get("name") or "").lower()
                if pt_lower in name and any(w in name for w in sn_words):
                    best = p
                    break
            if best is None:
                best = products[0]
            product_id = best.get("productId")
            if not product_id:
                return None
            price_resp = _requests.get(
                self._TCGP_PRICING_URL.format(product_id),
                headers=headers,
                timeout=15,
            )
            if not price_resp.ok:
                return None
            for pd in price_resp.json().get("results", []):
                mp = pd.get("marketPrice")
                if mp:
                    return round(float(mp), 2)
            for pd in price_resp.json().get("results", []):
                mp = pd.get("midPrice")
                if mp:
                    return round(float(mp), 2)
            return None
        except Exception as exc:
            self._log.debug("_fetch_price error for %s / %s: %s", set_name, product_type, exc)
            return None


class _BackfillApiIdWorker(QThread):
    """Searches pokemontcg.io for collection entries missing an api_id."""
    status = Signal(str)
    done = Signal()

    def __init__(
        self,
        cat_repo: CatalogRepository,
        col_repo: CollectionRepository,
        missing: list[dict],   # collection rows without api_id
    ) -> None:
        super().__init__()
        self._cat_repo = cat_repo
        self._col_repo = col_repo
        self._missing = missing
        self._log = _logging.getLogger(__name__)

    def run(self) -> None:
        import datetime as _dt
        total = len(self._missing)
        for i, row in enumerate(self._missing, 1):
            name = row.get("name") or ""
            number = row.get("card_number") or ""
            set_name = row.get("set_name") or ""
            self.status.emit(f"Suche API-ID {i}/{total}: {name} #{number}")
            try:
                # Try name + number search
                q = f'name:"{name}" number:{number}' if number else f'name:"{name}"'
                resp = _requests.get(
                    "https://api.pokemontcg.io/v2/cards",
                    params={"q": q, "pageSize": 10, "orderBy": "-set.releaseDate"},
                    timeout=15,
                )
                if not resp.ok:
                    self._log.warning("Backfill HTTP %s for %s", resp.status_code, name)
                    continue
                cards = resp.json().get("data", [])
                # Pick best match: same set name preferred, else first result
                match = None
                for c in cards:
                    if c.get("set", {}).get("name", "").lower() == set_name.lower():
                        match = c
                        break
                if not match and cards:
                    match = cards[0]
                if not match:
                    self._log.warning("No API match for %s #%s", name, number)
                    continue
                api_id = match.get("id", "")
                if not api_id:
                    continue
                # Write api_id to collection
                self._col_repo.set_api_id(row["id"], api_id)
                # Upsert into catalog
                price = self._extract_price(match)
                img_url = (
                    match.get("images", {}).get("small")
                    or match.get("images", {}).get("large")
                    or ""
                )
                set_logo_url = match.get("set", {}).get("images", {}).get("logo", "")
                now = _dt.datetime.utcnow().isoformat()
                with self._cat_repo.database.connect() as conn:
                    conn.execute(
                        """INSERT OR REPLACE INTO card_catalog
                           (api_id,name,set_name,card_number,language,best_price,price_currency,
                            image_url,local_image_path,set_logo_url,set_local_logo_path,fetched_at,updated_at)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (api_id, match.get("name",""),
                         match.get("set",{}).get("name",""),
                         match.get("number",""),
                         match.get("language","en") or "en",
                         price, "USD", img_url, None,
                         set_logo_url, None, now, now)
                    )
                    conn.commit()
                if img_url:
                    self._cat_repo.save_local_image(api_id, img_url)
                self._log.info("Backfilled %s #%s → %s", name, number, api_id)
            except Exception as exc:
                self._log.warning("Backfill error for %s: %s", name, exc)
                self.status.emit(f"Fehler bei {name}: {exc}")
        self.status.emit(f"Backfill fertig ({total} Karten).")
        self.done.emit()

    @staticmethod
    def _extract_price(card: dict) -> float | None:
        prices = card.get("tcgplayer", {}).get("prices", {})
        for v in ("normal", "holofoil", "reverseHolofoil", "1stEditionHolofoil"):
            p = prices.get(v, {}).get("market")
            if p is not None:
                return round(float(p), 2)
        return None


class _MissingImagesWorker(QThread):
    """Downloads card images that are missing in the catalog cache."""
    status = Signal(str)
    done = Signal()

    def __init__(self, repo: CatalogRepository, jobs: list[tuple[str, str]]) -> None:
        """jobs = list of (api_id, image_url)"""
        super().__init__()
        self._repo = repo
        self._jobs = jobs

    def run(self) -> None:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        total = len(self._jobs)
        completed = 0
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {
                pool.submit(self._repo.save_local_image, api_id, url): api_id
                for api_id, url in self._jobs
            }
            for fut in as_completed(futures):
                completed += 1
                self.status.emit(f"Bild laden {completed}/{total}: {futures[fut]}")
                try:
                    fut.result()
                except Exception:
                    pass
        self.status.emit(f"{total} Bild(er) geladen.")
        self.done.emit()


class _RefreshWorker(QThread):
    """Fetches fresh prices + missing images for all collection entries via pokemontcg.io."""
    progress = Signal(int, int)
    status = Signal(str)
    done = Signal()

    def __init__(self, repo: CatalogRepository, api_ids: list[str]) -> None:
        super().__init__()
        self._repo = repo
        self._api_ids = api_ids
        self._log = _logging.getLogger(__name__)

    def run(self) -> None:
        total = len(self._api_ids)
        errors = 0
        for i, api_id in enumerate(self._api_ids):
            self.progress.emit(i + 1, total)
            self.status.emit(f"Preis-Update {i+1}/{total}: {api_id}")
            try:
                resp = _requests.get(
                    f"https://api.pokemontcg.io/v2/cards/{api_id}",
                    timeout=15,
                )
                if resp.ok:
                    card = resp.json().get("data", {})
                    price = self._extract_price(card)
                    img_url = (
                        card.get("images", {}).get("small")
                        or card.get("images", {}).get("large")
                        or ""
                    )
                    self._repo.update_price(api_id, price, "USD", image_url=img_url or None)
                    if img_url:
                        self._repo.save_local_image(api_id, img_url)
                else:
                    errors += 1
                    self._log.warning("Refresh HTTP %s for %s", resp.status_code, api_id)
            except Exception as exc:
                errors += 1
                self._log.warning("Refresh error for %s: %s", api_id, exc)
                self.status.emit(f"Fehler bei {api_id}: {exc}")
        summary = f"Refresh fertig: {total - errors}/{total} OK"
        if errors:
            summary += f", {errors} Fehler (siehe Log)"
        self.status.emit(summary)
        self.done.emit()

    @staticmethod
    def _extract_price(card: dict) -> float | None:
        prices = card.get("tcgplayer", {}).get("prices", {})
        for variant in ("normal", "holofoil", "reverseHolofoil", "1stEditionHolofoil"):
            p = prices.get(variant, {}).get("market")
            if p is not None:
                return round(float(p), 2)
        return None


def _year_for_set(entries: list[dict]) -> str:
    """Extract the release year string from a list of catalog/enriched entries."""
    for e in entries:
        rd = e.get("set_release_date") or ""
        if rd and len(rd) >= 4 and rd[:4].isdigit():
            return rd[:4]
    for e in entries:
        fa = e.get("fetched_at") or ""
        if fa and len(fa) >= 4 and fa[:4].isdigit():
            return fa[:4]
    return "?"


def _group_entries_by_year(
    entries: list[dict],
) -> dict[str, dict[str, list[dict]]]:
    """Return {year: {set_name: [entries]}} preserving groupby order."""
    result: dict[str, dict[str, list[dict]]] = {}
    for set_name, g in groupby(entries, key=lambda e: e.get("set_name") or ""):
        group = list(g)
        year = _year_for_set(group)
        result.setdefault(year, {})[set_name] = group
    return result


class _KatalogDataWorker(QThread):
    """Fetches and preprocesses catalog data off the UI thread."""
    done = Signal(list, dict, dict)  # (unique_entries, year_map, owned)

    def __init__(self, repo, col_repo, query: str = "") -> None:
        super().__init__()
        self._repo = repo
        self._col_repo = col_repo
        self._query = query

    def run(self) -> None:
        try:
            entries = self._repo.search(self._query) if self._query else self._repo.list_all()
            seen: set[str] = set()
            unique: list[dict] = []
            for e in entries:
                aid = e.get("api_id") or ""
                if aid and aid in seen:
                    continue
                seen.add(aid)
                unique.append(e)
            year_map = _group_entries_by_year(unique) if unique else {}
            owned = self._col_repo.get_owned_lookup()
        except Exception:
            unique, year_map, owned = [], {}, {}
        self.done.emit(unique, year_map, owned)


class _SammlungDataWorker(QThread):
    """Fetches and preprocesses collection data off the UI thread."""
    # (removed_dups, col_rows, cat_by_api, year_map_s, samm_logo, owned_lookup)
    done = Signal(int, list, dict, dict, dict, dict)

    def __init__(self, repo, col_repo) -> None:
        super().__init__()
        self._repo = repo
        self._col_repo = col_repo

    def run(self) -> None:
        try:
            removed = self._col_repo.merge_duplicates()
            col_rows = self._col_repo.list_all()
            cat_by_api: dict = {e["api_id"]: e for e in self._repo.list_all() if e.get("api_id")}

            col_sorted = sorted(col_rows, key=lambda r: r.get("set_name") or "")
            samm_by_set: dict[str, list[dict]] = {}
            samm_logo: dict[str, str | None] = {}
            for col in col_sorted:
                sn = col.get("set_name") or ""
                cat = cat_by_api.get(col.get("api_id") or "") or {}
                entry = {
                    "api_id": col.get("api_id") or "",
                    "name": col.get("name") or cat.get("name") or "",
                    "set_name": sn,
                    "card_number": col.get("card_number") or cat.get("card_number") or "",
                    "language": col.get("language") or cat.get("language") or "",
                    "best_price": cat.get("best_price"),
                    "price_currency": cat.get("price_currency") or "USD",
                    "local_image_path": _resolve_image(col, cat),
                    "updated_at": cat.get("updated_at") or "",
                    "fetched_at": cat.get("fetched_at") or "",
                    "set_release_date": cat.get("set_release_date") or "",
                    "rarity": cat.get("rarity") or "",
                    "supertype": cat.get("supertype") or "",
                    "subtypes": cat.get("subtypes") or "",
                    "hp": cat.get("hp") or "",
                    "types": cat.get("types") or "",
                    "artist": cat.get("artist") or "",
                    "pokedex_numbers": cat.get("pokedex_numbers") or "",
                    "regulation_mark": cat.get("regulation_mark") or "",
                    "set_series": cat.get("set_series") or "",
                    "eur_price": cat.get("eur_price"),
                    "usd_price": cat.get("usd_price"),
                    "set_symbol_local_path": cat.get("set_symbol_local_path"),
                }
                samm_by_set.setdefault(sn, []).append(entry)
                if sn not in samm_logo:
                    samm_logo[sn] = _logo_path_for_set(sn, cat.get("set_local_logo_path"))

            owned_lookup: dict = {r["api_id"]: r for r in col_rows if r.get("api_id")}
            year_map_s: dict[str, dict[str, list[dict]]] = {}
            for sn, elist in samm_by_set.items():
                yr = _year_for_set(elist)
                year_map_s.setdefault(yr, {})[sn] = elist
        except Exception:
            removed, col_rows, cat_by_api, year_map_s, samm_logo, owned_lookup = (
                0, [], {}, {}, {}, {}
            )
        self.done.emit(removed, col_rows, cat_by_api, year_map_s, samm_logo, owned_lookup)


class _SetReleaseWorker(QThread):
    """Fetches all set release dates from pokemontcg.io and populates card_catalog."""
    done = Signal()

    def __init__(self, repo) -> None:
        super().__init__()
        self._repo = repo
        self._log = _logging.getLogger(__name__)

    def run(self) -> None:
        try:
            resp = _requests.get(
                "https://api.pokemontcg.io/v2/sets",
                params={
                    "pageSize": 500,
                    "orderBy": "-releaseDate",
                    "select": "id,name,releaseDate",
                },
                headers={"User-Agent": "CardLens/1.0"},
                timeout=20,
            )
            if not resp.ok:
                self._log.warning("Set release dates: HTTP %s", resp.status_code)
                return
            sets = resp.json().get("data", [])
            mapping = {
                s["name"]: s["releaseDate"]
                for s in sets if s.get("name") and s.get("releaseDate")
            }
            if mapping:
                self._repo.update_release_dates(mapping)
                self._log.info("Stored release dates for %d sets", len(mapping))
        except Exception as exc:
            self._log.debug("SetReleaseWorker error: %s", exc)
        self.done.emit()


class _PriceHistoryChart(QWidget):
    """Minimal QPainter line-chart for a card's price history."""
    _H = 130
    _PL, _PR, _PT, _PB = 50, 12, 12, 32

    def __init__(self, history: list[dict], p: QWidget | None = None) -> None:
        super().__init__(p)
        self._history = [r for r in history if r.get("price")]
        self.setFixedHeight(self._H)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def paintEvent(self, _event) -> None:  # noqa: N802
        import datetime as _dtt
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        w = self.width()
        PL, PR, PT, PB = self._PL, self._PR, self._PT, self._PB
        cw = w - PL - PR
        ch = self._H - PT - PB

        if not self._history:
            fnt = QFont()
            fnt.setPointSize(8)
            painter.setFont(fnt)
            painter.setPen(QColor("#aaaaaa"))
            painter.drawText(
                PL, PT, cw, ch, Qt.AlignCenter,
                "Noch keine Verlaufsdaten\n(wird t\u00e4glich gespeichert)",
            )
            return

        prices = [r["price"] for r in self._history]
        dates = [r["snapshot_date"] for r in self._history]
        cur = self._history[-1].get("currency") or "USD"
        sym = "\u20ac" if cur == "EUR" else ("\u00a5" if cur == "JPY" else "$")
        min_p, max_p = min(prices), max(prices)
        p_range = max_p - min_p if max_p != min_p else 1.0
        n = len(prices)

        def _x(i: int) -> float:
            return PL + (i * cw / (n - 1) if n > 1 else cw / 2)

        def _y(price: float) -> float:
            return PT + ch - (price - min_p) / p_range * ch

        # Background + border
        painter.fillRect(PL, PT, cw, ch, QColor("#f8fafc"))
        painter.setPen(QPen(QColor("#dde4ee"), 1))
        painter.drawRect(PL, PT, cw, ch)

        # Horizontal grid lines + Y labels
        fnt = QFont()
        fnt.setPointSize(7)
        painter.setFont(fnt)
        for step in range(4):
            frac = step / 3
            y = int(PT + frac * ch)
            painter.setPen(QPen(QColor("#e0e8f0"), 1))
            painter.drawLine(PL + 1, y, PL + cw - 1, y)
            price_at = max_p - frac * p_range
            painter.setPen(QColor("#888888"))
            painter.drawText(
                0, y - 7, PL - 4, 14,
                Qt.AlignRight | Qt.AlignVCenter,
                f"{sym}{price_at:.1f}",
            )

        # Line
        if n > 1:
            pen_line = QPen(QColor("#3a7ecf"), 2)
            painter.setPen(pen_line)
            painter.setBrush(Qt.NoBrush)
            for i in range(n - 1):
                painter.drawLine(
                    QPointF(_x(i), _y(prices[i])),
                    QPointF(_x(i + 1), _y(prices[i + 1])),
                )

        # Dots + X-axis date labels (first, last, and every ~5th)
        label_idx = {0, n - 1} | {i for i in range(0, n, max(1, n // 5))}
        painter.setPen(QPen(QColor("#1a5ea0"), 1))
        painter.setBrush(QColor("#3a7ecf"))
        for i in range(n):
            x, y = _x(i), _y(prices[i])
            painter.drawEllipse(QPointF(x, y), 3.0, 3.0)
            if i in label_idx:
                try:
                    d = _dtt.date.fromisoformat(dates[i])
                    lbl = d.strftime("%d.%m.%y")
                except Exception:
                    lbl = dates[i][-8:] if len(dates[i]) >= 8 else dates[i]
                painter.setPen(QColor("#555555"))
                painter.drawText(
                    int(x - 22), self._H - PB + 3, 44, 16,
                    Qt.AlignCenter, lbl,
                )
                painter.setPen(QPen(QColor("#1a5ea0"), 1))
                painter.setBrush(QColor("#3a7ecf"))


class _PriceSnapshotWorker(QThread):
    """Saves today's price snapshot for a list of top-performer rows (background)."""
    def __init__(self, repo, rows: list[dict]) -> None:
        super().__init__()
        self._repo = repo
        self._rows = rows

    def run(self) -> None:
        try:
            self._repo.record_price_snapshots_bulk(self._rows)
        except Exception:
            pass


class _BulkDownloadWorker(QThread):
    """Downloads ALL cards from pokemontcg.io page by page and upserts them into card_catalog.

    Only metadata + prices are stored; card images are NOT downloaded here —
    the existing lazy-load / save_local_image mechanism handles that separately.
    """
    progress = Signal(int, int, int)   # page, total_pages, cards_so_far
    status   = Signal(str)
    done     = Signal(int, int)        # total_cards, errors

    _API_URL  = "https://api.pokemontcg.io/v2/cards"
    _PAGE_SIZE = 250

    def __init__(self, repo: "CatalogRepository") -> None:
        super().__init__()
        self._repo = repo
        self._log  = _logging.getLogger(__name__)
        self._abort = False

    def abort(self) -> None:
        self._abort = True

    @staticmethod
    def _price_from_card(card: dict) -> tuple[float | None, str, float | None, float | None]:
        """Extract prices. Returns (best_price, currency, eur_price, usd_price)."""
        eur = None
        for key in ("averageSellPrice", "trendPrice", "lowPrice"):
            p = card.get("cardmarket", {}).get("prices", {}).get(key)
            if p is not None:
                eur = round(float(p), 2)
                break
        usd = None
        for variant in ("normal", "holofoil", "reverseHolofoil", "1stEditionHolofoil"):
            p = card.get("tcgplayer", {}).get("prices", {}).get(variant, {}).get("market")
            if p is not None:
                usd = round(float(p), 2)
                break
        best = eur if eur is not None else usd
        currency = "EUR" if eur is not None else "USD"
        return best, currency, eur, usd

    def run(self) -> None:
        from src.pokemon_scanner.datasources.base import CardCandidate

        total_cards = 0
        errors      = 0
        page        = 1
        total_pages = 1   # updated after first response

        while page <= total_pages and not self._abort:
            self.status.emit(f"Seite {page}/{total_pages} wird geladen …")
            try:
                resp = _requests.get(
                    self._API_URL,
                    params={
                        "pageSize": self._PAGE_SIZE,
                        "page":     page,
                        "orderBy":  "set.releaseDate,number",
                    },
                    headers={"User-Agent": "CardLens/1.0"},
                    timeout=30,
                )
                resp.raise_for_status()
                data = resp.json()
            except Exception as exc:
                errors += 1
                self._log.warning("Bulk download page %d error: %s", page, exc)
                self.status.emit(f"Fehler Seite {page}: {exc}")
                page += 1
                continue

            # Calculate total pages from first response
            total_count = data.get("totalCount", 0)
            if total_count:
                total_pages = (total_count + self._PAGE_SIZE - 1) // self._PAGE_SIZE

            cards = data.get("data", [])
            candidates: list[CardCandidate] = []
            for card in cards:
                price, currency, eur, usd = self._price_from_card(card)
                img = card.get("images", {})
                set_data = card.get("set", {})
                set_images = set_data.get("images", {})
                candidates.append(CardCandidate(
                    source         = "pokemontcg.io",
                    name           = card.get("name", ""),
                    set_name       = set_data.get("name", ""),
                    card_number    = card.get("number", ""),
                    language       = "en",
                    confidence     = 1.0,
                    best_price     = price,
                    price_currency = currency,
                    price_source   = ("Cardmarket" if eur is not None else "TCGPlayer") if price is not None else "",
                    notes          = f"ID: {card.get('id', '')}",
                    api_id         = card.get('id', ''),
                    image_url      = img.get("small") or img.get("large") or "",
                    set_logo_url   = set_images.get("logo") or "",
                    rarity         = card.get("rarity", "") or "",
                    supertype      = card.get("supertype", "") or "",
                    subtypes       = ",".join(card.get("subtypes", []) or []),
                    hp             = card.get("hp", "") or "",
                    types          = ",".join(card.get("types", []) or []),
                    artist         = card.get("artist", "") or "",
                    pokedex_numbers = ",".join(str(n) for n in (card.get("nationalPokedexNumbers", []) or [])),
                    regulation_mark = card.get("regulationMark", "") or "",
                    legalities     = "|".join(f"{k}:{v}" for k, v in (card.get("legalities", {}) or {}).items()),
                    set_series     = set_data.get("series", "") or "",
                    set_total      = set_data.get("total", 0) or 0,
                    set_symbol_url = set_images.get("symbol") or "",
                    eur_price      = eur,
                    usd_price      = usd,
                ))

            try:
                self._repo.upsert_candidates(candidates)
                total_cards += len(candidates)
            except Exception as exc:
                errors += 1
                self._log.warning("Bulk upsert page %d error: %s", page, exc)

            self.progress.emit(page, total_pages, total_cards)
            page += 1

        if self._abort:
            self.status.emit(f"Abgebrochen nach {total_cards} Karten.")
        self.done.emit(total_cards, errors)


class _BulkImageWorker(QThread):
    """Phase 2: downloads card images for all catalog entries that are missing a local image.

    image_size: 'small' | 'large' | 'both'
    Skips cards that already have a valid local_image_path.
    """
    progress = Signal(int, int)   # done, total
    status   = Signal(str)
    done     = Signal(int, int)   # downloaded, errors

    def __init__(self, repo: "CatalogRepository", image_size: str = "small") -> None:
        super().__init__()
        self._repo       = repo
        self._image_size = image_size   # 'small' | 'large' | 'both'
        self._log        = _logging.getLogger(__name__)
        self._abort      = False

    def abort(self) -> None:
        self._abort = True

    def run(self) -> None:
        # Gather all catalog rows that are missing a local image
        all_rows = self._repo.list_all()
        missing  = [r for r in all_rows if not r.get("local_image_path") and r.get("image_url")]
        total    = len(missing)
        if total == 0:
            self.status.emit("Alle Bilder bereits vorhanden.")
            self.done.emit(0, 0)
            return

        self.status.emit(f"Bilder: 0/{total} …")
        downloaded = 0
        errors     = 0

        for i, row in enumerate(missing):
            if self._abort:
                self.status.emit(f"Bild-Download abgebrochen nach {downloaded} Bildern.")
                break

            api_id    = row.get("api_id", "")
            image_url = row.get("image_url", "")
            # Derive large URL from small URL if needed (pokemontcg uses /small → /large)
            large_url = image_url.replace("/small", "/large") if "/small" in image_url else ""

            urls: list[str] = []
            if self._image_size in ("small", "both") and image_url:
                urls.append(image_url)
            if self._image_size in ("large", "both") and large_url:
                urls.append(large_url)
            if not urls:
                urls = [image_url]  # fallback

            saved = False
            for url in urls:
                try:
                    result = self._repo.save_local_image(api_id, url)
                    if result:
                        saved = True
                except Exception as exc:
                    self._log.warning("Image DL error %s: %s", api_id, exc)
            if saved:
                downloaded += 1
            else:
                errors += 1

            self.progress.emit(i + 1, total)
            if (i + 1) % 50 == 0:
                self.status.emit(f"Bilder: {i+1}/{total} …")

        self.done.emit(downloaded, errors)


# \u2500\u2500 Top-Performer \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

class _TopPerformerWorker(QThread):
    """Loads top-performer cards from the local DB in a background thread."""
    done = Signal(list)  # emits list[dict]

    def __init__(
        self,
        repo: CatalogRepository,
        min_year: int,
        max_year: int,
        language: str | None,
        owned_only: bool,
        owned_ids: set,
    ) -> None:
        super().__init__()
        self._repo = repo
        self._min_year = min_year
        self._max_year = max_year
        self._language = language
        self._owned_only = owned_only
        self._owned_ids = owned_ids

    def run(self) -> None:
        ids = self._owned_ids if self._owned_only else None
        rows = self._repo.get_top_performers(
            limit=1000,
            min_year=self._min_year,
            max_year=self._max_year,
            language=self._language,
            owned_ids=ids,
        )
        self.done.emit(rows)


class _TopRow(QFrame):
    """Single clickable row in the Top-Performer table."""
    selected = Signal(dict)
    _H = 52

    def __init__(
        self, rank: int, entry: dict, owned: bool, p: QWidget | None = None
    ) -> None:
        super().__init__(p)
        self._entry = entry
        self.setFixedHeight(self._H)
        self.setCursor(Qt.PointingHandCursor)
        bg = "#edfaed" if owned else ("#f7f9fc" if rank % 2 == 0 else "#ffffff")
        self.setObjectName("toprow")
        self.setStyleSheet(
            f"background:{bg};border:none;border-bottom:1px solid #e0e8f0;"
        )
        hl = QHBoxLayout(self)
        hl.setContentsMargins(6, 2, 6, 2)
        hl.setSpacing(6)

        # Rank
        rk = QLabel(str(rank))
        rk.setFixedWidth(36)
        rk.setAlignment(Qt.AlignCenter)
        rc = "#c9a227" if rank <= 3 else "#7a9bbf"
        rk.setStyleSheet(
            f"font-weight:bold;font-size:12px;color:{rc};"
            "border:none;background:transparent;"
        )
        hl.addWidget(rk)

        # Thumbnail
        img_lbl = QLabel()
        img_lbl.setFixedSize(30, 42)
        img_lbl.setAlignment(Qt.AlignCenter)
        img_lbl.setStyleSheet(
            "border:1px solid #ccc;border-radius:2px;background:#f0f0f0;"
        )
        p_path = _resolve_image_path(entry.get("local_image_path"))
        if p_path:
            px = _cached_pixmap(p_path, 42, 30)
            if not px.isNull():
                img_lbl.setPixmap(px)
        hl.addWidget(img_lbl)

        # Name + number
        name_col = QWidget()
        name_col.setStyleSheet("background:transparent;")
        ncl = QVBoxLayout(name_col)
        ncl.setContentsMargins(0, 0, 0, 0)
        ncl.setSpacing(1)
        nm = QLabel(entry.get("name") or "\u2013")
        nm.setStyleSheet(
            "font-weight:bold;font-size:11px;border:none;background:transparent;"
        )
        nm.setWordWrap(False)
        ncl.addWidget(nm)
        num_lbl = QLabel(f"#{entry.get('card_number') or '?'}")
        num_lbl.setStyleSheet(
            "font-size:9px;color:#777;border:none;background:transparent;"
        )
        ncl.addWidget(num_lbl)
        hl.addWidget(name_col, 1)

        # Set
        set_lbl = QLabel(entry.get("set_name") or "\u2013")
        set_lbl.setFixedWidth(150)
        set_lbl.setStyleSheet(
            "font-size:10px;color:#555;border:none;background:transparent;"
        )
        set_lbl.setWordWrap(False)
        hl.addWidget(set_lbl)

        # Year
        rd = entry.get("set_release_date") or ""
        yr_lbl = QLabel(rd[:4] if len(rd) >= 4 else "\u2013")
        yr_lbl.setFixedWidth(40)
        yr_lbl.setAlignment(Qt.AlignCenter)
        yr_lbl.setStyleSheet(
            "font-size:10px;color:#555;border:none;background:transparent;"
        )
        hl.addWidget(yr_lbl)

        # Language badge
        lang = entry.get("language") or ""
        if lang:
            lang_lbl = QLabel(lang.upper()[:2])
            lang_lbl.setFixedSize(28, 18)
            lang_lbl.setAlignment(Qt.AlignCenter)
            lang_lbl.setStyleSheet(
                "font-size:8px;font-weight:bold;color:white;"
                "background:#3a7ecf;border-radius:3px;border:none;"
            )
            hl.addWidget(lang_lbl)
        else:
            hl.addSpacing(34)

        # Price
        price = entry.get("best_price")
        cur = entry.get("price_currency") or "USD"
        sym = "\u20ac" if cur == "EUR" else ("\u00a5" if cur == "JPY" else "$")
        price_lbl = QLabel(f"{sym}{price:.2f}" if price else "\u2013")
        price_lbl.setFixedWidth(75)
        price_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        price_lbl.setStyleSheet(
            "font-weight:bold;font-size:11px;color:#16a34a;"
            "border:none;background:transparent;"
        )
        hl.addWidget(price_lbl)

        # Ø/Jahr badge — shows average annual price appreciation
        score = entry.get("score")  # = price / age_years
        rd = entry.get("set_release_date") or ""
        release_year = int(rd[:4]) if len(rd) >= 4 and rd[:4].isdigit() else None
        import datetime as _dt
        cur_year = _dt.date.today().year
        age_years = (cur_year - release_year) if release_year else None
        if score and age_years and age_years > 0:
            per_yr_txt = f"+{sym}{score:.1f}/a"
            if score >= 15:
                sc_color = "#186a18"; sc_bg = "#d4f5d4"
            elif score >= 4:
                sc_color = "#7a4a00"; sc_bg = "#fff0cc"
            else:
                sc_color = "#555"; sc_bg = "#eeeeee"
            sc_tip = (
                f"Gesch\u00e4tzter Wertzuwachs \u00f8 {sym}{score:.1f} pro Jahr\n"
                f"Berechnung: Aktueller Preis ({sym}{price:.2f}) \u00f7 "
                f"Kartenalter ({age_years} Jahre seit {release_year})\n"
                "\u26a0 Dies ist eine Sch\u00e4tzung \u2013 kein echter historischer Preisverlauf."
            )
        else:
            per_yr_txt = "\u2013"
            sc_color = "#aaa"; sc_bg = "#f5f5f5"
            sc_tip = "Kein Release-Datum vorhanden."
        sc_lbl = QLabel(per_yr_txt)
        sc_lbl.setFixedWidth(80)
        sc_lbl.setAlignment(Qt.AlignCenter)
        sc_lbl.setToolTip(sc_tip)
        sc_lbl.setStyleSheet(
            f"font-size:9px;font-weight:bold;color:{sc_color};"
            f"background:{sc_bg};border-radius:3px;padding:1px 4px;"
            "border:none;"
        )
        hl.addWidget(sc_lbl)

        # Owned indicator
        own_lbl = QLabel("\u2714" if owned else "")
        own_lbl.setFixedWidth(20)
        own_lbl.setAlignment(Qt.AlignCenter)
        own_lbl.setStyleSheet(
            "font-size:11px;color:#16a34a;border:none;background:transparent;"
        )
        hl.addWidget(own_lbl)

    def mousePressEvent(self, event) -> None:
        super().mousePressEvent(event)
        self.selected.emit(self._entry)


class _TopPerformerWidget(QWidget):
    """Full Top-Performer tab: filter bar + lazy scrollable table."""
    _PAGE_SIZE = 100

    def __init__(
        self,
        catalog_repo: CatalogRepository,
        collection_repo: CollectionRepository,
        p: QWidget | None = None,
    ) -> None:
        super().__init__(p)
        self._repo = catalog_repo
        self._col_repo = collection_repo
        self._worker: _TopPerformerWorker | None = None
        self._snapshot_worker: _PriceSnapshotWorker | None = None
        self._all_rows: list[dict] = []
        self._visible_count = 0
        self._loaded = False
        self._owned_lookup: dict = {}

        vbox = QVBoxLayout(self)
        vbox.setContentsMargins(6, 6, 6, 6)
        vbox.setSpacing(6)

        # Filter bar
        fb = QFrame()
        fb.setStyleSheet(
            "QFrame{background:#f0f4f8;border:1px solid #d0d8e0;border-radius:6px;}"
            "QFrame QLabel{border:none;background:transparent;color:#444;}"
        )
        fb.setFixedHeight(44)
        fl = QHBoxLayout(fb)
        fl.setContentsMargins(12, 0, 12, 0)
        fl.setSpacing(12)
        self._owned_cb = QCheckBox("Nur im Besitz")
        self._owned_cb.setStyleSheet("font-size:11px;")
        fl.addWidget(self._owned_cb)
        fl.addWidget(_lbl("Sprache:", "font-size:11px;"))
        self._lang_combo = QComboBox()
        self._lang_combo.addItems(["Alle", "en", "de", "ja", "zh-Hans", "ko"])
        self._lang_combo.setFixedWidth(85)
        fl.addWidget(self._lang_combo)
        fl.addWidget(_lbl("Jahr von:", "font-size:11px;"))
        self._min_year_sb = QSpinBox()
        self._min_year_sb.setRange(2000, 2030)
        self._min_year_sb.setValue(2016)
        self._min_year_sb.setFixedWidth(70)
        fl.addWidget(self._min_year_sb)
        fl.addWidget(_lbl("bis:", "font-size:11px;"))
        self._max_year_sb = QSpinBox()
        self._max_year_sb.setRange(2000, 2030)
        self._max_year_sb.setValue(2026)
        self._max_year_sb.setFixedWidth(70)
        fl.addWidget(self._max_year_sb)
        fl.addStretch()
        self._load_btn = QPushButton("\u27f3  Laden")
        self._load_btn.setMinimumHeight(32)
        self._load_btn.setFixedWidth(100)
        self._load_btn.clicked.connect(self._start_load)
        fl.addWidget(self._load_btn)
        vbox.addWidget(fb)

        # Disclaimer bar
        disc = QFrame()
        disc.setStyleSheet(
            "QFrame{background:#fffbe6;border:1px solid #f0d060;"
            "border-radius:5px;padding:0px;}"
            "QFrame QLabel{border:none;background:transparent;color:#7a5c00;"
            "font-size:10px;}"
        )
        disc.setFixedHeight(26)
        dl = QHBoxLayout(disc)
        dl.setContentsMargins(10, 0, 10, 0)
        dl.setSpacing(6)
        disc_lbl = QLabel(
            "\u26a0\ufe0f Preise sind Richtwerte \u2013 Qualit\u00e4tsstufe "
            "(PSA/CGC-Grading, Zustand) kann Wert stark ver\u00e4ndern. "
            "\u00d8/Jahr = gesch\u00e4tzter Wertzuwachs pro Jahr (kein echter Preisverlauf)."
        )
        disc_lbl.setWordWrap(False)
        dl.addWidget(disc_lbl)
        vbox.addWidget(disc)

        # Column header bar
        hdr = QFrame()
        hdr.setFixedHeight(28)
        hdr.setStyleSheet(
            "QFrame{background:#2c3e50;border-radius:4px;}"
            "QFrame QLabel{border:none;background:transparent;"
            "color:white;font-weight:bold;font-size:10px;}"
        )
        hl = QHBoxLayout(hdr)
        hl.setContentsMargins(6, 0, 6, 0)
        hl.setSpacing(6)
        for txt, w in [
            ("Rang", 36), ("Bild", 30), ("Name / Nr.", 0),
            ("Set", 150), ("Jahr", 40), ("Lang", 34), ("Preis", 75),
            ("\u00d8/Jahr\u2191", 80), ("\u2714", 20),
        ]:
            lbl_h = QLabel(txt)
            if w:
                lbl_h.setFixedWidth(w)
            else:
                lbl_h.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            lbl_h.setAlignment(
                Qt.AlignLeft | Qt.AlignVCenter
                if txt in ("Name / Nr.", "Set") else Qt.AlignCenter
            )
            hl.addWidget(lbl_h)
        vbox.addWidget(hdr)

        # Scroll area
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._rows_widget = QWidget()
        self._rows_layout = QVBoxLayout(self._rows_widget)
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.setSpacing(0)
        self._rows_layout.addStretch(1)
        self._scroll.setWidget(self._rows_widget)
        vbox.addWidget(self._scroll, 1)

        # Status + more-rows button
        self._status_lbl = QLabel(
            "Noch nicht geladen \u2013 Tab \u00f6ffnen oder \u27f3 klicken."
        )
        self._status_lbl.setAlignment(Qt.AlignCenter)
        self._status_lbl.setStyleSheet("color:#888;font-size:11px;")
        vbox.addWidget(self._status_lbl)
        self._more_btn = QPushButton(f"Weitere {self._PAGE_SIZE} laden \u2026")
        self._more_btn.setVisible(False)
        self._more_btn.clicked.connect(self._append_page)
        vbox.addWidget(self._more_btn)

    def load_if_needed(self) -> None:
        if not self._loaded:
            self._start_load()

    def stop_workers(self) -> None:
        """Quit and wait for all running workers — call before parent is destroyed."""
        for w in (self._worker, self._snapshot_worker):
            if w is not None and w.isRunning():
                w.quit()
                w.wait(2000)

    def _start_load(self) -> None:
        if self._worker and self._worker.isRunning():
            return
        self._load_btn.setEnabled(False)
        self._status_lbl.setText("Lade \u2026")
        self._more_btn.setVisible(False)
        self._clear_rows()
        self._all_rows = []
        self._visible_count = 0
        lang_text = self._lang_combo.currentText()
        language = None if lang_text == "Alle" else lang_text
        owned_only = self._owned_cb.isChecked()
        owned_ids: set = set()
        if owned_only:
            owned_ids = set(self._col_repo.get_owned_lookup().keys())
        self._worker = _TopPerformerWorker(
            self._repo,
            min_year=self._min_year_sb.value(),
            max_year=self._max_year_sb.value(),
            language=language,
            owned_only=owned_only,
            owned_ids=owned_ids,
        )
        self._worker.done.connect(self._on_data_loaded)
        self._worker.start()

    def _on_data_loaded(self, rows: list) -> None:
        self._loaded = True
        self._all_rows = rows
        self._load_btn.setEnabled(True)
        self._owned_lookup = self._col_repo.get_owned_lookup()
        if not rows:
            self._status_lbl.setText(
                "Keine Karten gefunden (noch keine Preisdaten oder Filter zu eng?)"
            )
            return
        self._status_lbl.setText(f"Top {len(rows)} Karten geladen")
        # Save price snapshots in background (1× per card per day)
        self._snapshot_worker = _PriceSnapshotWorker(self._repo, rows)
        self._snapshot_worker.start()
        self._append_page()

    def _append_page(self) -> None:
        start = self._visible_count
        end = min(start + self._PAGE_SIZE, len(self._all_rows))
        # Insert rows before the trailing stretch (last layout item)
        insert_pos = self._rows_layout.count() - 1
        for i in range(start, end):
            entry = self._all_rows[i]
            owned = bool(self._owned_lookup.get(entry.get("api_id") or ""))
            row = _TopRow(i + 1, entry, owned)
            row.selected.connect(self._on_row_selected)
            self._rows_layout.insertWidget(insert_pos + (i - start), row)
        self._visible_count = end
        remaining = len(self._all_rows) - self._visible_count
        if remaining > 0:
            self._more_btn.setText(
                f"Weitere {min(self._PAGE_SIZE, remaining)} laden \u2026"
            )
            self._more_btn.setVisible(True)
        else:
            self._more_btn.setVisible(False)

    def _clear_rows(self) -> None:
        while self._rows_layout.count() > 1:
            item = self._rows_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _on_row_selected(self, entry: dict) -> None:
        api_id = entry.get("api_id") or ""
        owned_row = self._owned_lookup.get(api_id)
        history = self._repo.get_price_history(api_id) if api_id else []
        dlg = QDialog(self)
        dlg.setWindowTitle(entry.get("name") or "Kartendetail")
        dlg.resize(360, 580)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(8)
        tile = _CardTile(entry, owned_row)
        lay.addWidget(tile, 0, Qt.AlignHCenter)
        # Price history chart
        chart_hdr = QLabel("Preisentwicklung")
        chart_hdr.setStyleSheet(
            "font-weight:bold;font-size:10px;color:#334155;"
            "border:none;background:transparent;"
        )
        lay.addWidget(chart_hdr)
        chart = _PriceHistoryChart(history)
        lay.addWidget(chart)
        if not history:
            hint = QLabel(
                "\u26a0 Noch kein Verlauf \u2013 wird ab heute bei jedem "
                "\u00d6ffnen des Top-Performer-Tabs gespeichert."
            )
            hint.setWordWrap(True)
            hint.setStyleSheet("font-size:9px;color:#888;")
            lay.addWidget(hint)
        # Condition editor (only for owned cards)
        if owned_row:
            _CONDS = ["M", "NM", "LP", "MP", "HP"]
            cond_row = QHBoxLayout()
            cond_lbl = QLabel("Zustand:")
            cond_lbl.setStyleSheet("font-size:10px;border:none;background:transparent;")
            cond_combo = QComboBox()
            cond_combo.addItems(_CONDS)
            current_cond = (owned_row.get("condition") or "NM").upper()
            if current_cond in _CONDS:
                cond_combo.setCurrentText(current_cond)
            save_btn = QPushButton("Speichern")
            save_btn.setFixedHeight(28)
            entry_id: int | None = owned_row.get("id")
            def _save_condition(*, _combo=cond_combo, _eid=entry_id, _dlg=dlg) -> None:
                if _eid:
                    self._col_repo.update_condition(_eid, _combo.currentText())
                    # Refresh owned_lookup so badge updates on next open
                    if api_id:
                        updated = self._col_repo.find_by_identity(
                            api_id=api_id, name=entry.get("name", ""),
                            set_name=entry.get("set_name", ""),
                            card_number=entry.get("card_number", ""),
                            language=entry.get("language", ""),
                        )
                        if updated:
                            self._owned_lookup[api_id] = updated
                    _dlg.accept()
            save_btn.clicked.connect(_save_condition)
            cond_row.addWidget(cond_lbl)
            cond_row.addWidget(cond_combo, 1)
            cond_row.addWidget(save_btn)
            lay.addLayout(cond_row)
        close_btn = QPushButton("Schlie\u00dfen")
        close_btn.clicked.connect(dlg.accept)
        lay.addWidget(close_btn)
        dlg.exec()


class CatalogDialog(QDialog):
    def __init__(
        self,
        catalog_repo: CatalogRepository,
        collection_repo: CollectionRepository,
        p: QWidget | None = None,
        settings=None,
    ) -> None:
        super().__init__(p)
        self.setWindowTitle("Katalog & Sammlung")
        self.resize(1200, 750)
        self._repo = catalog_repo
        self._col_repo = collection_repo
        self._settings = settings
        # Increase pixmap cache to 50 MB (default is 10 MB)
        QPixmapCache.setCacheLimit(51_200)
        layout = QVBoxLayout(self)
        layout.setSpacing(6)
        self._tabs = QTabWidget()
        layout.addWidget(self._tabs, 1)

        kat_root = QWidget()
        kat_layout = QVBoxLayout(kat_root)
        kat_layout.setContentsMargins(4, 4, 4, 4)
        sr = QHBoxLayout()
        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("Suchen nach Name, Set, Nummer \u2026")
        self._search_input.setMinimumHeight(34)
        # Debounce: only trigger search 300 ms after the user stops typing
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(300)
        self._search_timer.timeout.connect(
            lambda: self._load_katalog(self._search_input.text().strip())
        )
        self._search_input.textChanged.connect(lambda _: self._search_timer.start())
        self._count_label = QLabel()
        self._count_label.setStyleSheet("color: #555; padding-left: 8px;")
        btn_r = QPushButton("Aktualisieren")
        btn_r.setMinimumHeight(34)
        btn_r.clicked.connect(lambda: self._load_katalog(self._search_input.text().strip()))
        self._kat_price_btn = QPushButton("\U0001f4b0 Preise updaten")
        self._kat_price_btn.setMinimumHeight(34)
        self._kat_price_btn.setToolTip("Preise aller Katalog-Karten via pokemontcg.io aktualisieren")
        self._kat_price_btn.clicked.connect(self._start_catalog_price_update)
        self._kat_bulk_btn = QPushButton("\u2b07 Alle Karten laden")
        self._kat_bulk_btn.setMinimumHeight(34)
        self._kat_bulk_btn.setToolTip(
            "Lädt alle ~18 000 Karten von pokemontcg.io in die lokale Datenbank.\n"
            "Nur beim ersten Start / nach einem Reset nötig."
        )
        self._kat_bulk_btn.clicked.connect(self._start_bulk_download)
        self._kat_img_cb = QCheckBox("\U0001f4f7 Bilder")
        self._kat_img_cb.setToolTip("Auch Kartenbilder herunterladen (Phase 2 nach Metadaten)")
        self._kat_img_cb.setChecked(False)
        self._kat_img_size = QComboBox()
        self._kat_img_size.addItems(["small (~20 KB)", "large (~100 KB)", "beide"])
        self._kat_img_size.setFixedWidth(130)
        self._kat_img_size.setToolTip("Bildgröße für den Download")
        self._kat_img_size.setEnabled(False)
        self._kat_img_cb.toggled.connect(self._kat_img_size.setEnabled)
        btn_keys = QPushButton("\U0001f511 TCGPlayer Key")
        btn_keys.setMinimumHeight(34)
        btn_keys.setToolTip("TCGPlayer API-Keys f\u00fcr ETB/Bundle-Preise eintragen")
        btn_keys.clicked.connect(self._open_api_key_dialog)
        sr.addWidget(QLabel("Suche:"))
        sr.addWidget(self._search_input, 1)
        sr.addWidget(self._count_label)
        sr.addWidget(btn_keys)
        sr.addWidget(self._kat_price_btn)
        sr.addWidget(self._kat_bulk_btn)
        sr.addWidget(self._kat_img_cb)
        sr.addWidget(self._kat_img_size)
        sr.addWidget(btn_r)
        kat_layout.addLayout(sr)
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._grid_widget: QWidget | None = None
        kat_layout.addWidget(self._scroll, 1)
        self._tiles: list[_CardTile] = []
        self._tile_by_setname: dict[str, _SetHeaderTile] = {}
        self._sealed_price_worker: _SealedPriceWorker | None = None
        self._tabs.addTab(kat_root, "\U0001f4d6  Katalog")

        samm_root = QWidget()
        samm_layout = QVBoxLayout(samm_root)
        samm_layout.setContentsMargins(4, 4, 4, 4)
        samm_layout.setSpacing(4)

        # ── Stats bar ───────────────────────────────────────────────────────
        stats_bar = QFrame()
        stats_bar.setStyleSheet(
            "QFrame { background: #f0f4f8; border: 1px solid #d0d8e0;"
            " border-radius: 6px; }"
        )
        stats_bar.setFixedHeight(48)
        sb_lay = QHBoxLayout(stats_bar)
        sb_lay.setContentsMargins(14, 0, 10, 0)
        sb_lay.setSpacing(20)
        self._samm_count_label = QLabel("\U0001f4c4 – Karten")
        self._samm_count_label.setStyleSheet("color: #444; border: none; background: transparent;")
        self._stats_cost = QLabel("Kosten: –")
        self._stats_cost.setStyleSheet("color: #555; border: none; background: transparent;")
        self._stats_value = QLabel("Wert: –")
        self._stats_value.setStyleSheet("color: #555; border: none; background: transparent;")
        self._stats_guv = QLabel("GuV: –")
        self._stats_guv.setStyleSheet("color: #888; border: none; background: transparent;")
        self._refresh_btn = QPushButton("\u21bb  Refresh")
        self._refresh_btn.setMinimumHeight(32)
        self._refresh_btn.setFixedWidth(120)
        self._refresh_btn.clicked.connect(self._start_refresh)
        self._reset_btn = QPushButton("\U0001f5d1  Sammlung leeren")
        self._reset_btn.setMinimumHeight(32)
        self._reset_btn.setToolTip("Alle Eintr\u00e4ge aus der Sammlung l\u00f6schen (Factory-Reset).\nDer Kartenkatalog bleibt erhalten.")
        self._reset_btn.setStyleSheet(
            "QPushButton{color:#b03030;border:1px solid #d08080;"
            "border-radius:4px;padding:0 10px;background:#fff8f8;}"
            "QPushButton:hover{background:#fde8e8;}"
        )
        self._reset_btn.clicked.connect(self._on_factory_reset)
        sb_lay.addWidget(self._samm_count_label)
        sb_lay.addWidget(self._stats_cost)
        sb_lay.addWidget(self._stats_value)
        sb_lay.addWidget(self._stats_guv)
        sb_lay.addStretch(1)
        sb_lay.addWidget(self._refresh_btn)
        sb_lay.addWidget(self._reset_btn)
        samm_layout.addWidget(stats_bar)
        # ────────────────────────────────────────────────────────────────────

        self._samm_scroll = QScrollArea()
        self._samm_scroll.setWidgetResizable(True)
        self._samm_grid_widget: QWidget | None = None
        self._samm_tiles: list[_CardTile] = []
        self._samm_fetch_worker: _MissingImagesWorker | None = None
        self._refresh_worker: _RefreshWorker | None = None
        self._catalog_refresh_worker: _RefreshWorker | None = None
        self._bulk_download_worker: _BulkDownloadWorker | None = None
        self._katalog_data_worker: _KatalogDataWorker | None = None
        self._sammlung_data_worker: _SammlungDataWorker | None = None
        self._bulk_image_worker: _BulkImageWorker | None = None
        self._backfill_worker: _BackfillApiIdWorker | None = None
        self._logo_worker: _SetLogoDownloadWorker | None = None
        self._backfill_attempted_ids: set[int] = set()
        samm_layout.addWidget(self._samm_scroll, 1)
        self._tabs.addTab(samm_root, "\u2b50  Sammlung")

        # ── Top-Performer tab ────────────────────────────────────────────────
        self._top_widget = _TopPerformerWidget(catalog_repo, collection_repo)
        self._tabs.addTab(self._top_widget, "\U0001f3c6  Top-Performer")

        self._tabs.currentChanged.connect(self._on_tab_changed)
        self._load_katalog()

        # Only start background network workers if the catalog already has data.
        # On a fresh install (empty DB) no automatic network access is made.
        self._logo_worker = _SetLogoDownloadWorker(catalog_repo)
        self._logo_worker.done.connect(self._on_logos_downloaded)
        self._set_release_worker: _SetReleaseWorker | None = None
        self._set_release_worker = _SetReleaseWorker(catalog_repo)
        self._set_release_worker.done.connect(self._on_release_dates_loaded)
        if self._repo.count() > 0:
            self._logo_worker.start()
            self._set_release_worker.start()

        # ── Status bar (bottom) ──────────────────────────────────────────────
        self._status_label = QLabel("Bereit.")
        self._status_label.setStyleSheet(
            "color: #555; font-size: 10px; padding: 2px 6px;"
            " border-top: 1px solid #ddd; background: #fafafa;"
        )
        self._status_label.setFixedHeight(22)
        layout.addWidget(self._status_label)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        """Stop all background workers before the dialog is destroyed."""
        workers = [
            self._logo_worker,
            self._set_release_worker,
            self._sealed_price_worker,
            self._backfill_worker,
            self._samm_fetch_worker,
            self._refresh_worker,
            self._catalog_refresh_worker,
            self._bulk_download_worker,
            self._bulk_image_worker,
            self._katalog_data_worker,
            self._sammlung_data_worker,
        ]
        for w in workers:
            if w is not None and w.isRunning():
                w.quit()
                w.wait(2000)
        # Also stop workers inside the Top-Performer tab
        self._top_widget.stop_workers()
        super().closeEvent(event)

    def _owned_map(self) -> dict:
        return self._col_repo.get_owned_lookup()

    def _open_api_key_dialog(self) -> None:
        """Open a small dialog to enter/save TCGPlayer API credentials."""
        from src.pokemon_scanner.config.settings import AppSettings
        settings = self._settings or AppSettings.load()
        dlg = QDialog(self)
        dlg.setWindowTitle("TCGPlayer API-Keys")
        dlg.resize(480, 160)
        lay = QVBoxLayout(dlg)
        lay.setSpacing(8)
        lay.setContentsMargins(12, 12, 12, 12)
        info = QLabel(
            "TCGPlayer Public Key und Private Key f\u00fcr ETB/Bundle-Preise.\n"
            "Erhältlich unter developer.tcgplayer.com nach Anmeldung."
        )
        info.setWordWrap(True)
        info.setStyleSheet("font-size:10px;color:#555;")
        lay.addWidget(info)
        warn = QLabel(
            "\u26a0\ufe0f Die Keys werden im Klartext in "
            "<code>%APPDATA%\\CardLens\\runtime\\settings.json</code> gespeichert.<br>"
            "Teile diese Datei nicht mit anderen Personen."
        )
        warn.setWordWrap(True)
        warn.setTextFormat(Qt.RichText)
        warn.setStyleSheet("font-size:10px; color:#b45309; background:#fef3c7; padding:4px; border-radius:4px;")
        lay.addWidget(warn)
        from PySide6.QtWidgets import QFormLayout
        form = QFormLayout()
        pub_edit = QLineEdit(settings.tcgplayer_public_key or "")
        pub_edit.setPlaceholderText("Public Key")
        pub_edit.setEchoMode(QLineEdit.Password)
        priv_edit = QLineEdit(settings.tcgplayer_private_key or "")
        priv_edit.setPlaceholderText("Private Key")
        priv_edit.setEchoMode(QLineEdit.Password)
        form.addRow("Public Key:", pub_edit)
        form.addRow("Private Key:", priv_edit)
        lay.addLayout(form)
        btn_row = QHBoxLayout()
        btn_save = QPushButton("Speichern")
        btn_cancel = QPushButton("Abbrechen")
        btn_row.addStretch()
        btn_row.addWidget(btn_cancel)
        btn_row.addWidget(btn_save)
        lay.addLayout(btn_row)
        btn_cancel.clicked.connect(dlg.reject)
        def _save():
            settings.tcgplayer_public_key = pub_edit.text().strip()
            settings.tcgplayer_private_key = priv_edit.text().strip()
            settings.save()
            self._settings = settings
            dlg.accept()
            self._start_sealed_price_worker()
        btn_save.clicked.connect(_save)
        dlg.exec()

    def _start_sealed_price_worker(self) -> None:
        """Launch _SealedPriceWorker if keys are set and no worker is running."""
        if self._sealed_price_worker and self._sealed_price_worker.isRunning():
            return
        from src.pokemon_scanner.config.settings import AppSettings
        settings = self._settings or AppSettings.load()
        pub = settings.tcgplayer_public_key or ""
        priv = settings.tcgplayer_private_key or ""
        set_names = list(self._tile_by_setname.keys())
        if not set_names:
            return
        self._sealed_price_worker = _SealedPriceWorker(self._repo, set_names, pub, priv)
        self._sealed_price_worker.prices_ready.connect(self._on_sealed_prices_ready)
        self._sealed_price_worker.status.connect(self._set_status)
        self._sealed_price_worker.start()

    def _on_sealed_prices_ready(self, set_name: str, prices: dict) -> None:
        tile = self._tile_by_setname.get(set_name)
        if tile:
            tile.update_sealed_prices(prices)

    def _on_logos_downloaded(self) -> None:
        """Reload katalog after background logo download so new logos appear."""
        self._load_katalog(self._search_input.text().strip())

    def _on_release_dates_loaded(self) -> None:
        """Reload katalog after set release dates are populated."""
        self._load_katalog(self._search_input.text().strip())

    def _load_katalog(self, query: str = "") -> None:
        # Show loading indicator immediately
        old = self._grid_widget
        self._grid_widget = QWidget()
        vbox = QVBoxLayout(self._grid_widget)
        vbox.setSpacing(0)
        vbox.setContentsMargins(8, 8, 8, 8)
        self._scroll.setWidget(self._grid_widget)
        del old
        from PySide6.QtCore import Qt
        loading = QLabel("Lade Katalog …")
        loading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        loading.setStyleSheet("color: #888; font-size: 13px; padding: 40px;")
        vbox.addWidget(loading)
        vbox.addStretch(1)

        # Abort previous worker if still running
        if self._katalog_data_worker and self._katalog_data_worker.isRunning():
            self._katalog_data_worker.done.disconnect()
            self._katalog_data_worker.quit()
            self._katalog_data_worker.wait(1000)

        self._katalog_data_worker = _KatalogDataWorker(self._repo, self._col_repo, query)
        self._katalog_data_worker.done.connect(
            lambda u, ym, ow, q=query: self._on_katalog_data(u, ym, ow, q)
        )
        self._katalog_data_worker.start()

    def _on_katalog_data(
        self,
        unique: list,
        year_map: dict,
        owned: dict,
        query: str,
    ) -> None:
        old = self._grid_widget
        self._grid_widget = QWidget()
        vbox = QVBoxLayout(self._grid_widget)
        vbox.setSpacing(0)
        vbox.setContentsMargins(8, 8, 8, 8)
        self._scroll.setWidget(self._grid_widget)
        del old

        self._count_label.setText(f"{len(unique)} Karten")
        is_search = bool(query)

        if not unique:
            from PySide6.QtCore import Qt
            hint = QLabel(
                "Katalog ist leer.\n\n"
                "Klicke \"\u2b07 Alle Karten laden\" um den Katalog von pokemontcg.io herunterzuladen,\n"
                "oder füge Karten manuell über den Scanner hinzu."
            )
            hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
            hint.setStyleSheet(
                "color: #888; font-size: 14px; padding: 60px 40px; line-height: 1.6;"
            )
            hint.setWordWrap(True)
            vbox.addWidget(hint)
            vbox.addStretch(1)
            return

        if is_search:
            for set_name, group_iter in groupby(unique, key=lambda e: e.get("set_name") or ""):
                group = list(group_iter)
                db_logo = next(
                    (e["set_local_logo_path"] for e in group if e.get("set_local_logo_path")),
                    None,
                )
                logo_path = _logo_path_for_set(set_name, db_logo)
                section = _CollapsibleSet(set_name or "(Unbekanntes Set)", logo_path, group, owned)
                section.remove_requested.connect(self._on_remove_card)
                section.expand_for_search()
                vbox.addWidget(section)
        else:
            self._tile_by_setname.clear()
            for year in sorted(
                year_map.keys(), reverse=True,
                key=lambda y: y if y.isdigit() else "0",
            ):
                sets_info = []
                for set_name, elist in year_map[year].items():
                    db_logo = next(
                        (e["set_local_logo_path"] for e in elist if e.get("set_local_logo_path")),
                        None,
                    )
                    db_symbol = next(
                        (e["set_symbol_local_path"] for e in elist if e.get("set_symbol_local_path")),
                        None,
                    )
                    sets_info.append((
                        set_name,
                        _logo_path_for_set(set_name, db_logo),
                        _symbol_path_for_set(set_name, db_symbol),
                        elist,
                    ))
                section = _YearSection(year, sets_info, owned)
                section.remove_requested.connect(self._on_remove_card)
                self._tile_by_setname.update(section.tiles_by_name())
                vbox.addWidget(section)

            # Apply cached sealed prices from DB immediately
            if self._tile_by_setname:
                cached_prices = self._repo.get_sealed_prices(list(self._tile_by_setname.keys()))
                for sn, prices in cached_prices.items():
                    tile = self._tile_by_setname.get(sn)
                    if tile:
                        tile.update_sealed_prices(prices)

            # Launch background worker to fetch fresh prices from TCGPlayer
            self._start_sealed_price_worker()

        vbox.addStretch(1)

    def _load_sammlung(self, *, _fetch_images: bool = True) -> None:
        self._samm_tiles.clear()

        # Show loading spinner immediately
        old = self._samm_grid_widget
        self._samm_grid_widget = QWidget()
        vbox = QVBoxLayout(self._samm_grid_widget)
        vbox.setSpacing(0)
        vbox.setContentsMargins(8, 8, 8, 8)
        self._samm_scroll.setWidget(self._samm_grid_widget)

        from PySide6.QtCore import Qt as _Qt
        loading = QLabel("Lade Sammlung …")
        loading.setAlignment(_Qt.AlignmentFlag.AlignCenter)
        loading.setStyleSheet("color: #888; font-size: 13px; padding: 40px;")
        vbox.addWidget(loading)
        vbox.addStretch(1)

        # Abort previous worker if still running
        if self._sammlung_data_worker and self._sammlung_data_worker.isRunning():
            self._sammlung_data_worker.done.disconnect()
            self._sammlung_data_worker.quit()
            self._sammlung_data_worker.wait(1000)

        self._sammlung_data_worker = _SammlungDataWorker(self._repo, self._col_repo)
        self._sammlung_data_worker.done.connect(
            lambda r, cr, ca, ym, sl, ol, fi=_fetch_images:
                self._on_sammlung_data(r, cr, ca, ym, sl, ol, fi)
        )
        self._sammlung_data_worker.start()

    def _on_sammlung_data(
        self,
        removed: int,
        col_rows: list,
        cat_by_api: dict,
        year_map_s: dict,
        samm_logo: dict,
        owned_lookup: dict,
        _fetch_images: bool,
    ) -> None:
        if removed:
            self._set_status(f"{removed} doppelte Einträge zusammengeführt.")

        old = self._samm_grid_widget
        self._samm_grid_widget = QWidget()
        vbox = QVBoxLayout(self._samm_grid_widget)
        vbox.setSpacing(0)
        vbox.setContentsMargins(8, 8, 8, 8)
        self._samm_scroll.setWidget(self._samm_grid_widget)

        self._update_stats_bar(col_rows, cat_by_api)

        for year in sorted(
            year_map_s.keys(), reverse=True,
            key=lambda y: y if y.isdigit() else "0",
        ):
            sets_info = [
                (sn, samm_logo.get(sn), _symbol_path_for_set(sn), elist)
                for sn, elist in year_map_s[year].items()
            ]
            section = _YearSection(year, sets_info, owned_lookup)
            section.remove_requested.connect(self._on_remove_card)
            vbox.addWidget(section)

        vbox.addStretch(1)

        if not _fetch_images:
            return
        # Backfill api_ids for collection entries that have none.
        missing_api = [
            r for r in col_rows
            if not r.get("api_id") and r["id"] not in self._backfill_attempted_ids
        ]
        if missing_api:
            for r in missing_api:
                self._backfill_attempted_ids.add(r["id"])
            self._set_status(f"Suche API-IDs für {len(missing_api)} Karten …")
            self._backfill_worker = _BackfillApiIdWorker(self._repo, self._col_repo, missing_api)
            self._backfill_worker.status.connect(self._set_status)
            self._backfill_worker.done.connect(lambda: self._load_sammlung(_fetch_images=True))
            self._backfill_worker.start()
            return
        # Download missing images only for cards actually in the collection
        col_api_ids = {r["api_id"] for r in col_rows if r.get("api_id")}
        jobs = [
            (api_id, cat.get("image_url", ""))
            for api_id, cat in cat_by_api.items()
            if api_id in col_api_ids
            and cat.get("image_url")
            and not _resolve_image({"api_id": api_id}, cat)
        ]
        if jobs:
            self._set_status(f"Lade {len(jobs)} fehlende Bild(er) …")
            self._samm_fetch_worker = _MissingImagesWorker(self._repo, jobs)
            self._samm_fetch_worker.status.connect(self._set_status)
            self._samm_fetch_worker.done.connect(
                lambda: self._load_sammlung(_fetch_images=False)
            )
            self._samm_fetch_worker.start()

    def _on_remove_card(self, entry_id: int) -> None:
        reply = QMessageBox.question(
            self, "Karte entfernen",
            "Karte aus der Sammlung entfernen?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            self._col_repo.delete_entry(entry_id)
            self._load_sammlung(_fetch_images=False)

    def _set_status(self, msg: str) -> None:
        self._status_label.setText(msg)

    def _update_stats_bar(
        self,
        col_rows: list | None = None,
        cat_by_api: dict | None = None,
    ) -> None:
        if col_rows is None:
            col_rows = self._col_repo.list_all()
        if cat_by_api is None:
            cat_by_api = {e["api_id"]: e for e in self._repo.list_all() if e.get("api_id")}
        total_cards = sum((r.get("quantity") or 1) for r in col_rows)
        cost = sum(
            (r.get("last_price") or 0) * (r.get("quantity") or 1)
            for r in col_rows if r.get("last_price")
        )
        current = sum(
            (cat_by_api[r["api_id"]].get("best_price") or 0) * (r.get("quantity") or 1)
            for r in col_rows
            if r.get("api_id") and r["api_id"] in cat_by_api
            and cat_by_api[r["api_id"]].get("best_price")
        )
        guv = current - cost
        guv_pct = (guv / cost * 100) if cost > 0 else 0
        self._samm_count_label.setText(f"\U0001f4c4 {total_cards} Karten")
        self._stats_cost.setText(f"Kosten: {cost:.2f}\u202fUSD")
        if current > 0:
            guv_color = "#27ae60" if guv >= 0 else "#e74c3c"
            guv_sign = "+" if guv >= 0 else ""
            self._stats_value.setText(f"Wert: {current:.2f}\u202fUSD")
            self._stats_guv.setText(
                f"GuV: {guv_sign}{guv:.2f}\u202fUSD  ({guv_sign}{guv_pct:.1f}%)"
            )
            self._stats_guv.setStyleSheet(
                f"font-weight: bold; color: {guv_color};"
                " border: none; background: transparent;"
            )
        else:
            self._stats_value.setText("Wert: –")
            self._stats_guv.setText("GuV: –")
            self._stats_guv.setStyleSheet("color: #888; border: none; background: transparent;")

    def _on_factory_reset(self) -> None:
        n = len(self._col_repo.list_all())
        if n == 0:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(self, "Sammlung leeren", "Die Sammlung ist bereits leer.")
            return
        from PySide6.QtWidgets import QMessageBox
        reply = QMessageBox.warning(
            self,
            "Sammlung leeren – Sicher?",
            f"Alle {n} Eintr\u00e4ge aus der Sammlung werden unwiderruflich gel\u00f6scht.\n"
            "Der Kartenkatalog bleibt erhalten.\n\n"
            "Fortfahren?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        self._col_repo.clear_collection()
        self._load_sammlung()
        self._set_status("Sammlung geleert.")

    def _start_bulk_download(self) -> None:
        """Download all cards from pokemontcg.io into the local catalog DB."""
        # If image phase is running, second click aborts it
        if self._bulk_image_worker and self._bulk_image_worker.isRunning():
            self._bulk_image_worker.abort()
            self._kat_bulk_btn.setEnabled(False)
            self._kat_bulk_btn.setText("\u2b07 Wird abgebrochen \u2026")
            return
        # If metadata phase is running, second click aborts it
        if self._bulk_download_worker and self._bulk_download_worker.isRunning():
            self._bulk_download_worker.abort()
            self._kat_bulk_btn.setEnabled(False)
            self._kat_bulk_btn.setText("\u2b07 Wird abgebrochen \u2026")
            return

        download_images = self._kat_img_cb.isChecked()
        size_map = {"small (~20 KB)": "small", "large (~100 KB)": "large", "beide": "both"}
        image_size = size_map.get(self._kat_img_size.currentText(), "small")

        # Build confirmation text
        img_note = ""
        if download_images:
            size_label = {"small": "kleine (~20 KB)", "large": "große (~100 KB)", "both": "kleine + große"}[image_size]
            img_note = (
                f"\n\nAnschließend werden {size_label} Bilder heruntergeladen "
                f"(~1–3 GB, kann 30–90 Min. dauern)."
            )
        confirm = QMessageBox.question(
            self,
            "Alle Karten herunterladen",
            f"Alle ~18 000 Karten von pokemontcg.io in die lokale Datenbank laden?\n\n"
            f"Metadaten + Preise: ca. 3–8 Minuten.{img_note}\n\n"
            f"Während des Downloads erneut klicken zum Abbrechen.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        self._kat_bulk_btn.setText("\u2b07 0/? (0 Karten)")
        self._kat_bulk_btn.setToolTip("Klicken zum Abbrechen")
        self._kat_img_cb.setEnabled(False)
        self._kat_img_size.setEnabled(False)
        self._bulk_download_worker = _BulkDownloadWorker(self._repo)
        self._bulk_download_worker.progress.connect(self._on_bulk_progress)
        self._bulk_download_worker.status.connect(self._set_status)
        # Pass image preference via closure so _on_bulk_done knows what to do
        self._bulk_download_worker.done.connect(
            lambda total, errors, _img=download_images, _sz=image_size:
                self._on_bulk_done(total, errors, download_images=_img, image_size=_sz)
        )
        self._bulk_download_worker.start()

    def _on_bulk_progress(self, page: int, total_pages: int, cards: int) -> None:
        self._kat_bulk_btn.setText(f"\u2b07 {page}/{total_pages} ({cards} Karten)")

    def _on_bulk_done(self, total: int, errors: int, *, download_images: bool = False, image_size: str = "small") -> None:
        msg = f"Metadaten: {total} Karten gespeichert."
        if errors:
            msg += f" ({errors} Fehler)"
        self._set_status(msg)
        self._load_katalog(self._search_input.text().strip())

        # Now that data exists, start background workers that were deferred on empty-DB start
        if self._logo_worker and not self._logo_worker.isRunning():
            self._logo_worker.start()
        if self._set_release_worker and not self._set_release_worker.isRunning():
            self._set_release_worker.start()

        if download_images:
            # Phase 2: download images
            self._kat_bulk_btn.setText("\U0001f4f7 Bilder 0/?")
            self._kat_bulk_btn.setToolTip("Klicken zum Abbrechen")
            self._bulk_image_worker = _BulkImageWorker(self._repo, image_size)
            self._bulk_image_worker.progress.connect(
                lambda done, tot: self._kat_bulk_btn.setText(f"\U0001f4f7 Bilder {done}/{tot}")
            )
            self._bulk_image_worker.status.connect(self._set_status)
            self._bulk_image_worker.done.connect(self._on_bulk_images_done)
            self._bulk_image_worker.start()
        else:
            self._kat_bulk_btn.setEnabled(True)
            self._kat_bulk_btn.setText("\u2b07 Alle Karten laden")
            self._kat_bulk_btn.setToolTip(
                "Lädt alle ~18 000 Karten von pokemontcg.io in die lokale Datenbank.\n"
                "Nur beim ersten Start / nach einem Reset nötig."
            )
            self._kat_img_cb.setEnabled(True)
            self._kat_img_size.setEnabled(self._kat_img_cb.isChecked())

    def _on_bulk_images_done(self, downloaded: int, errors: int) -> None:
        self._kat_bulk_btn.setEnabled(True)
        self._kat_bulk_btn.setText("\u2b07 Alle Karten laden")
        self._kat_bulk_btn.setToolTip(
            "Lädt alle ~18 000 Karten von pokemontcg.io in die lokale Datenbank.\n"
            "Nur beim ersten Start / nach einem Reset nötig."
        )
        self._kat_img_cb.setEnabled(True)
        self._kat_img_size.setEnabled(self._kat_img_cb.isChecked())
        msg = f"Bild-Download abgeschlossen: {downloaded} Bilder gespeichert."
        if errors:
            msg += f" ({errors} Fehler – siehe Log)"
        self._set_status(msg)
        self._load_katalog(self._search_input.text().strip())

    def _start_catalog_price_update(self) -> None:
        """Fetch fresh prices for every catalog entry that has an api_id."""
        if self._catalog_refresh_worker and self._catalog_refresh_worker.isRunning():
            self._set_status("Preis-Update läuft bereits …")
            return
        all_entries = self._repo.list_all()
        api_ids = [e["api_id"] for e in all_entries if e.get("api_id")]
        if not api_ids:
            self._set_status("Keine Karten mit API-ID im Katalog gefunden.")
            return
        self._kat_price_btn.setEnabled(False)
        self._kat_price_btn.setText(f"\U0001f4b0 0/{len(api_ids)}")
        self._catalog_refresh_worker = _RefreshWorker(self._repo, api_ids)
        self._catalog_refresh_worker.progress.connect(
            lambda cur, tot: self._kat_price_btn.setText(f"\U0001f4b0 {cur}/{tot}")
        )
        self._catalog_refresh_worker.status.connect(self._set_status)
        self._catalog_refresh_worker.done.connect(self._on_catalog_price_update_done)
        self._catalog_refresh_worker.start()
        self._set_status(f"Starte Preis-Update für {len(api_ids)} Karten …")

    def _on_catalog_price_update_done(self) -> None:
        self._kat_price_btn.setEnabled(True)
        self._kat_price_btn.setText("\U0001f4b0 Preise updaten")
        self._load_katalog(self._search_input.text().strip())

    def _start_refresh(self) -> None:
        if self._backfill_worker and self._backfill_worker.isRunning():
            self._set_status("Bitte warten – API-ID-Suche läuft noch …")
            return
        col_rows = self._col_repo.list_all()
        api_ids = [r["api_id"] for r in col_rows if r.get("api_id")]
        if not api_ids:
            self._set_status("Keine Karten mit API-ID in der Sammlung.")
            return
        self._refresh_btn.setEnabled(False)
        self._refresh_btn.setText("\u21bb  \u2026")
        self._refresh_worker = _RefreshWorker(self._repo, api_ids)
        self._refresh_worker.progress.connect(self._on_refresh_progress)
        self._refresh_worker.status.connect(self._set_status)
        self._refresh_worker.done.connect(self._on_refresh_done)
        self._refresh_worker.start()

    def _on_refresh_progress(self, current: int, total: int) -> None:
        self._refresh_btn.setText(f"\u21bb  {current}/{total}")

    def _on_refresh_done(self) -> None:
        self._refresh_btn.setEnabled(True)
        self._refresh_btn.setText("\u21bb  Refresh")
        self._load_sammlung()

    def _on_search(self, text: str) -> None:
        # Kept for compatibility; actual search is debounced via _search_timer
        self._load_katalog(text.strip())

    def _on_tab_changed(self, index: int) -> None:
        if index == 1:
            self._load_sammlung()
        elif index == 2:
            self._top_widget.load_if_needed()
