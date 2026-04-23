"""Album / Binder UI widgets for the 'Alben' subtab.

Provides:
  AlbenWidget  – top-level container (overview ↔ detail)
  _AlbenOverview – bookshelf with all album spines
  _AlbumDetailView – paginated double-page spread
  _AlbumPageGrid – one binder page (N×M grid of slots)
  _AlbumSlot – single card pocket (drag source + drop target)
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from PySide6.QtCore import Qt, QByteArray, QEvent, QMimeData, Signal
from PySide6.QtGui import (
    QColor, QDrag, QFont, QFontMetrics, QLinearGradient,
    QPainter, QPen, QPixmap, QPixmapCache,
)
from PySide6.QtWidgets import (
    QDialog, QFormLayout, QFrame, QGridLayout, QHBoxLayout,
    QInputDialog, QLabel, QLineEdit, QMenu, QMessageBox,
    QPushButton, QScrollArea, QSizePolicy, QSpinBox,
    QStackedWidget, QVBoxLayout, QWidget,
)

from src.pokemon_scanner.db.repositories import AlbumRepository, CollectionRepository
from src.pokemon_scanner.core.paths import CATALOG_IMAGES_DIR
from src.pokemon_scanner.ui.image_cache import load_card_pixmap, CardImageDownloadWorker

_log = logging.getLogger(__name__)

_SLOT_W = 63
_SLOT_H = 88
_SLOT_GAP = 8
_SPINE_W = 80
_SPINE_H = 340
_MIME_SLOT = "application/x-album-slot"
_PRICE_H = 14  # pixels reserved at slot bottom for price label

# Shared style: dark-blue circle with white "+"
_PLUS_BTN_STYLE = (
    "QPushButton{"
    "background:#1a2050;border:2px solid #5865f2;"
    "border-radius:14px;color:white;font-size:18px;font-weight:bold;"
    "padding:0;min-width:28px;max-width:28px;min-height:28px;max-height:28px;"
    "text-align:center;}"
    "QPushButton:hover{background:#5865f2;border-color:#a0aaff;}"
)


# ─────────────────────────────────────────────────────────────────────────────
# Card picker dialog
# ─────────────────────────────────────────────────────────────────────────────

class _CardPickerDialog(QDialog):
    """Lists owned collection entries so the user can pick one for a slot."""

    def __init__(self, col_repo: CollectionRepository, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Karte auswählen")
        self.setMinimumSize(520, 500)
        self.setStyleSheet("background:#1e2030; color:#e2e8f0;")
        self._col_repo = col_repo
        self._selected_entry_id: int | None = None

        layout = QVBoxLayout(self)
        layout.setSpacing(6)
        layout.setContentsMargins(8, 8, 8, 8)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Name, Set oder Nummer filtern \u2026")
        self._search.setMinimumHeight(32)
        self._search.setStyleSheet(
            "QLineEdit{background:#252741;border:1px solid #334155;"
            "border-radius:4px;padding:0 8px;color:#e2e8f0;}"
        )
        self._search.textChanged.connect(self._on_search_changed)
        layout.addWidget(self._search)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setStyleSheet("QScrollArea{border:none;background:#1e2030;}")
        self._inner = QWidget()
        self._inner.setStyleSheet("background:#1e2030;")
        self._inner_layout = QVBoxLayout(self._inner)
        self._inner_layout.setSpacing(4)
        self._inner_layout.setContentsMargins(4, 4, 4, 4)
        self._inner_layout.addStretch(1)
        self._scroll.setWidget(self._inner)
        layout.addWidget(self._scroll, 1)

        btn_row = QHBoxLayout()
        cancel_btn = QPushButton("Abbrechen")
        cancel_btn.setStyleSheet(
            "QPushButton{background:#252741;color:#e2e8f0;border:1px solid #334155;"
            "border-radius:4px;padding:4px 12px;}"
        )
        cancel_btn.clicked.connect(self.reject)
        btn_row.addStretch()
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

        self._load_entries("")

    def _on_search_changed(self, text: str) -> None:
        self._load_entries(text.strip())

    def _load_entries(self, search: str) -> None:
        with self._col_repo.database.connect() as conn:
            if search:
                t = f"%{search.lower()}%"
                rows = conn.execute(
                    """
                    SELECT c.api_id, c.name, c.set_name, c.card_number, c.local_image_path,
                           COALESCE(SUM(e.quantity), 0) AS owned_qty,
                           MIN(e.id) AS entry_id
                    FROM card_catalog c
                    LEFT JOIN collection_entries e ON e.api_id = c.api_id
                    WHERE LOWER(c.name) LIKE ?
                       OR LOWER(COALESCE(c.set_name,'')) LIKE ?
                       OR LOWER(COALESCE(c.card_number,'')) LIKE ?
                    GROUP BY c.api_id
                    ORDER BY c.set_name, CAST(c.card_number AS INTEGER), c.card_number
                    LIMIT 100
                    """,
                    (t, t, t),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT c.api_id, c.name, c.set_name, c.card_number, c.local_image_path,
                           COALESCE(SUM(e.quantity), 0) AS owned_qty,
                           MIN(e.id) AS entry_id
                    FROM card_catalog c
                    LEFT JOIN collection_entries e ON e.api_id = c.api_id
                    GROUP BY c.api_id
                    ORDER BY c.set_name, CAST(c.card_number AS INTEGER), c.card_number
                    LIMIT 100
                    """,
                ).fetchall()
        self._render([dict(r) for r in rows])

    def _render(self, entries: list[dict]) -> None:
        while self._inner_layout.count() > 1:
            item = self._inner_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        iw, ih = 70, 98
        for entry in entries:
            api_id = entry.get("api_id") or ""
            name = entry.get("name") or "?"
            set_name = entry.get("set_name") or "\u2013"
            card_num = entry.get("card_number") or ""
            owned_qty = int(entry.get("owned_qty") or 0)
            img_path = entry.get("local_image_path")

            row_frame = QFrame()
            row_frame.setMinimumHeight(ih + 12)
            row_frame.setCursor(Qt.PointingHandCursor)
            row_frame.setStyleSheet(
                "QFrame{background:#252741;border:1px solid #2a3045;border-radius:4px;}"
                "QFrame:hover{background:#2a3060;border-color:#5865f2;}"
            )
            row_layout = QHBoxLayout(row_frame)
            row_layout.setContentsMargins(6, 6, 10, 6)
            row_layout.setSpacing(12)

            img_lbl = QLabel()
            img_lbl.setFixedSize(iw, ih)
            img_lbl.setAlignment(Qt.AlignCenter)
            img_lbl.setStyleSheet(
                "background:#16192b;border:1px solid #334155;border-radius:3px;"
            )
            pm = load_card_pixmap(api_id, stored_hint=img_path, w=iw, h=ih)
            if pm:
                img_lbl.setPixmap(pm)
            else:
                img_lbl.setText("?")
                img_lbl.setStyleSheet(
                    "background:#16192b;border:1px solid #334155;border-radius:3px;"
                    "color:#334155;font-size:20px;font-weight:bold;"
                )
            row_layout.addWidget(img_lbl)

            txt = QVBoxLayout()
            txt.setSpacing(2)
            name_lbl = QLabel(f"<b>{name}</b>")
            name_lbl.setStyleSheet(
                "color:#e2e8f0;font-size:12px;background:transparent;border:none;"
            )
            name_lbl.setWordWrap(True)
            set_lbl = QLabel(f"{set_name}  \u00b7  #{card_num}")
            set_lbl.setStyleSheet(
                "color:#94a3b8;font-size:10px;background:transparent;border:none;"
            )
            badge_lbl = QLabel(
                f"\u2713  Im Besitz: \u00d7{owned_qty}" if owned_qty
                else "+ Neu zur Sammlung hinzuf\u00fcgen"
            )
            badge_lbl.setStyleSheet(
                ("color:#4ade80;" if owned_qty else "color:#7c8dbb;")
                + "font-size:10px;background:transparent;border:none;"
            )
            txt.addWidget(name_lbl)
            txt.addWidget(set_lbl)
            txt.addStretch(1)
            txt.addWidget(badge_lbl)
            row_layout.addLayout(txt, 1)

            row_frame.mousePressEvent = (
                lambda _ev, aid=api_id, row=entry: self._pick(aid, row)
            )
            self._inner_layout.insertWidget(self._inner_layout.count() - 1, row_frame)

    def _pick(self, api_id: str, catalog_row: dict) -> None:
        entry_id = self._col_repo.get_or_create_entry_by_api_id(
            api_id=api_id,
            name=catalog_row.get("name") or "?",
            set_name=catalog_row.get("set_name") or "",
            card_number=catalog_row.get("card_number") or "",
            image_path=catalog_row.get("local_image_path"),
        )
        if entry_id is not None:
            self._selected_entry_id = entry_id
            self.accept()

    def selected_entry_id(self) -> int | None:
        return self._selected_entry_id


# ─────────────────────────────────────────────────────────────────────────────
# Single card slot
# ─────────────────────────────────────────────────────────────────────────────

class _AlbumSlot(QFrame):
    """One pocket in a binder page. Supports drag-and-drop rearranging."""

    slot_changed = Signal()

    def __init__(
        self,
        album_id: int,
        page_num: int,
        slot_index: int,
        album_repo: AlbumRepository,
        col_repo: CollectionRepository,
        album_name: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.album_id = album_id
        self.page_num = page_num
        self.slot_index = slot_index
        self._album_repo = album_repo
        self._col_repo = col_repo
        self._album_name = album_name
        self._entry: dict | None = None
        self._raw_pixmap: QPixmap | None = None   # full-res; scaled on-the-fly in paintEvent
        self._market_price: float | None = None
        self._fetch_thread: CardImageDownloadWorker | None = None  # background image download

        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumSize(45, 63)
        self.setAcceptDrops(True)
        self._update_style(False)

        self._is_dragging = False

        self._plus_btn = QPushButton("+", self)
        self._plus_btn.setFixedSize(28, 28)
        self._plus_btn.setStyleSheet(_PLUS_BTN_STYLE)
        self._plus_btn.clicked.connect(self._on_add_clicked)

        self._remove_btn = QPushButton("\u2715", self)
        self._remove_btn.setFixedSize(20, 20)
        self._remove_btn.setStyleSheet(
            "QPushButton{background:#7f1d1d;border:1px solid #ef4444;"
            "border-radius:10px;color:white;font-size:11px;font-weight:bold;"
            "padding:0;min-width:20px;max-width:20px;min-height:20px;max-height:20px;"
            "text-align:center;}"
            "QPushButton:hover{background:#ef4444;}"
        )
        self._remove_btn.setVisible(False)
        self._remove_btn.clicked.connect(self._on_remove_clicked)

        self.setAttribute(Qt.WA_Hover)
        self._reposition_btn()

    def _reposition_btn(self) -> None:
        bw = self._plus_btn.width()
        bh = self._plus_btn.height()
        self._plus_btn.move((self.width() - bw) // 2, (self.height() - bh) // 2)
        rw = self._remove_btn.width()
        self._remove_btn.move(self.width() - rw - 2, 2)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._reposition_btn()
        self.update()

    def _update_style(self, filled: bool) -> None:
        if filled:
            self.setStyleSheet(
                "QFrame{background:#1a1d2e;border:1px solid #2a3045;border-radius:3px;}"
            )
        else:
            self.setStyleSheet(
                "QFrame{background:#16192b;border:1px dashed #334155;border-radius:3px;}"
            )

    def set_entry(self, entry: dict | None) -> None:
        # Cancel any in-flight fetch from the previous entry
        if self._fetch_thread and self._fetch_thread.isRunning():
            self._fetch_thread.done.disconnect()
            self._fetch_thread = None

        self._entry = entry
        self._raw_pixmap = load_card_pixmap(
            entry.get("api_id") if entry else None,
            stored_hint=entry.get("image_path") if entry else None,
        )
        raw_price = entry.get("market_price") if entry else None
        self._market_price = float(raw_price) if raw_price is not None else None
        filled = self._entry is not None
        self._update_style(filled)
        self._plus_btn.setVisible(not filled)
        self._remove_btn.setVisible(False)  # shown on hover only

        # Auto-download missing image if catalog URL is available
        if self._raw_pixmap is None and entry:
            url = entry.get("catalog_image_url") or ""
            api_id = entry.get("api_id") or ""
            if url and api_id:
                self._start_image_fetch(api_id, url)

        self.update()

    def _start_image_fetch(self, api_id: str, url: str) -> None:
        self._fetch_thread = CardImageDownloadWorker(api_id, url, parent=self)
        self._fetch_thread.done.connect(self._on_image_fetched)
        self._fetch_thread.start()

    def _on_image_fetched(self, local_path: str) -> None:
        """Called on the main thread when a background image download finishes."""
        if not local_path or self._entry is None:
            return
        self._raw_pixmap = load_card_pixmap(
            self._entry.get("api_id"),
            stored_hint=local_path,
        )
        if self._raw_pixmap and not self._raw_pixmap.isNull():
            # Persist paths in DB so future loads don't need to re-download
            entry_id = self._entry.get("collection_entry_id")
            api_id = self._entry.get("api_id")
            try:
                with self._col_repo.database.connect() as conn:
                    if entry_id:
                        conn.execute(
                            "UPDATE collection_entries SET image_path=? WHERE id=?",
                            (local_path, entry_id),
                        )
                    if api_id:
                        conn.execute(
                            "UPDATE card_catalog SET local_image_path=? WHERE api_id=?",
                            (local_path, api_id),
                        )
                    conn.commit()
            except Exception as exc:
                _log.warning("Could not persist image_path after download: %s", exc)
            self.update()

    def event(self, ev) -> bool:
        et = ev.type()
        if et == QEvent.HoverEnter:
            if self._entry is not None:
                self._remove_btn.setVisible(True)
        elif et == QEvent.HoverLeave:
            self._remove_btn.setVisible(False)
        return super().event(ev)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        if self._raw_pixmap and not self._raw_pixmap.isNull():
            w = max(4, self.width() - 4)
            h = max(4, self.height() - 4 - _PRICE_H)
            pm = self._raw_pixmap.scaled(w, h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            img_area_h = self.height() - _PRICE_H
            x = (self.width() - pm.width()) // 2
            y = (img_area_h - pm.height()) // 2
            painter.drawPixmap(x, y, pm)
        if self._entry and self._market_price is not None:
            price_str = f"€ {self._market_price:.2f}".replace(".", ",")
            font = QFont("Segoe UI", 6, QFont.Bold)
            painter.setFont(font)
            painter.setPen(QColor("#a0aaff"))
            price_rect = self.rect().adjusted(0, self.height() - _PRICE_H, 0, 0)
            painter.drawText(price_rect, Qt.AlignHCenter | Qt.AlignVCenter, price_str)
        painter.end()

    def _on_add_clicked(self) -> None:
        dlg = _CardPickerDialog(self._col_repo, self)
        if dlg.exec() == QDialog.Accepted:
            eid = dlg.selected_entry_id()
            if eid is not None:
                self._album_repo.set_slot(self.album_id, self.page_num, self.slot_index, eid)
                if self._album_name:
                    self._col_repo.update_album_page(
                        eid, f"{self._album_name}, Seite {self.page_num + 1}"
                    )
                self.slot_changed.emit()

    def _on_remove_clicked(self) -> None:
        self._album_repo.set_slot(self.album_id, self.page_num, self.slot_index, None)
        self.set_entry(None)
        self.slot_changed.emit()

    def _on_edit_clicked(self) -> None:
        if self._entry is None:
            return
        entry_id = self._entry.get("collection_entry_id")
        if not entry_id:
            return
        from src.pokemon_scanner.ui.catalog_dialog import _CardDetailDialog  # lazy – avoids circular
        cat_entry = {
            "local_image_path": self._entry.get("image_path"),
            "api_id": self._entry.get("api_id"),
            "name": self._entry.get("name"),
            "set_name": self._entry.get("set_name"),
            "card_number": self._entry.get("card_number"),
        }
        dlg = _CardDetailDialog(entry_id, self._col_repo, cat_entry, self)
        if dlg.exec() == QDialog.Accepted:
            self.slot_changed.emit()

    def contextMenuEvent(self, event) -> None:
        if self._entry is None:
            return
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu{background:#252741;color:#e2e8f0;border:1px solid #334155;}"
            "QMenu::item:selected{background:#5865f2;}"
        )
        remove_act = menu.addAction("\U0001f5d1  Aus Album entfernen")
        action = menu.exec(event.globalPos())
        if action == remove_act:
            self._on_remove_clicked()

    # ── Drag ──────────────────────────────────────────────────────────────────

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self._entry is not None:
            self._drag_start = event.pos()
            self._is_dragging = False
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if (
            event.button() == Qt.LeftButton
            and self._entry is not None
            and not self._is_dragging
        ):
            self._on_edit_clicked()
        super().mouseReleaseEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if (
            self._entry is not None
            and event.buttons() & Qt.LeftButton
            and hasattr(self, "_drag_start")
            and (event.pos() - self._drag_start).manhattanLength() > 8
        ):
            self._is_dragging = True
            drag = QDrag(self)
            mime = QMimeData()
            data = json.dumps({
                "album_id": self.album_id,
                "page_num": self.page_num,
                "slot_index": self.slot_index,
            }).encode()
            mime.setData(_MIME_SLOT, QByteArray(data))
            if self._raw_pixmap:
                drag.setPixmap(
                    self._raw_pixmap.scaled(40, 56, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                )
            drag.setMimeData(mime)
            drag.exec(Qt.MoveAction)
        else:
            super().mouseMoveEvent(event)

    # ── Drop ──────────────────────────────────────────────────────────────────

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasFormat(_MIME_SLOT):
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        raw = bytes(event.mimeData().data(_MIME_SLOT))
        try:
            src = json.loads(raw)
        except Exception:
            return
        if src.get("album_id") != self.album_id:
            return
        p1, s1 = src["page_num"], src["slot_index"]
        p2, s2 = self.page_num, self.slot_index
        if (p1, s1) == (p2, s2):
            return
        # Capture entry IDs before the swap so we can update album_page correctly
        dragged_eid = self._album_repo.get_slot_entry_id(self.album_id, p1, s1)
        target_eid = self._entry.get("collection_entry_id") if self._entry else None
        self._album_repo.swap_slots(self.album_id, p1, s1, p2, s2)
        if self._album_name:
            if dragged_eid is not None:
                self._col_repo.update_album_page(
                    dragged_eid, f"{self._album_name}, Seite {p2 + 1}"
                )
            if target_eid is not None:
                self._col_repo.update_album_page(
                    target_eid, f"{self._album_name}, Seite {p1 + 1}"
                )
        event.acceptProposedAction()
        self.slot_changed.emit()


# ─────────────────────────────────────────────────────────────────────────────
# Album page grid (cols × rows of slots)
# ─────────────────────────────────────────────────────────────────────────────

class _AlbumPageGrid(QWidget):
    """One binder page rendered as a grid of _AlbumSlot widgets."""

    slot_changed = Signal()

    def __init__(
        self,
        album_id: int,
        page_num: int,
        cols: int,
        rows: int,
        album_repo: AlbumRepository,
        col_repo: CollectionRepository,
        album_name: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._album_id = album_id
        self._page_num = page_num
        self._cols = cols
        self._rows = rows
        self._album_repo = album_repo
        self._col_repo = col_repo
        self._album_name = album_name
        self._slots: list[_AlbumSlot] = []

        grid = QGridLayout(self)
        grid.setSpacing(_SLOT_GAP)
        grid.setContentsMargins(12, 12, 12, 12)

        for c in range(cols):
            grid.setColumnStretch(c, 1)
        for r in range(rows):
            grid.setRowStretch(r, 1)
            for c in range(cols):
                idx = r * cols + c
                slot = _AlbumSlot(album_id, page_num, idx, album_repo, col_repo, self._album_name)
                slot.slot_changed.connect(self._on_slot_changed)
                grid.addWidget(slot, r, c)
                self._slots.append(slot)

        min_w = cols * 45 + (cols - 1) * _SLOT_GAP + 24
        min_h = rows * 63 + (rows - 1) * _SLOT_GAP + 24
        self.setMinimumSize(min_w, min_h)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setStyleSheet("background:#1e2030; border:none;")

    def _on_slot_changed(self) -> None:
        self.reload()
        self.slot_changed.emit()

    def reload(self) -> None:
        slot_data = self._album_repo.get_page_slots_with_entries(self._album_id, self._page_num)
        by_idx = {s["slot_index"]: s for s in slot_data}
        for slot_widget in self._slots:
            entry = by_idx.get(slot_widget.slot_index)
            slot_widget.set_entry(entry)

    def page_value(self) -> float:
        """Sum of market_price for all filled slots on this page."""
        total = 0.0
        for slot in self._slots:
            if slot._market_price is not None:
                total += slot._market_price
        return total


# ─────────────────────────────────────────────────────────────────────────────
# Album detail view
# ─────────────────────────────────────────────────────────────────────────────

class _AlbumDetailView(QWidget):
    """Paginated double-page spread view for one album."""

    back_requested = Signal()

    def __init__(
        self,
        album_id: int,
        album_repo: AlbumRepository,
        col_repo: CollectionRepository,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._album_id = album_id
        self._album_repo = album_repo
        self._col_repo = col_repo
        self._current_spread = 0  # left page index of current double-page
        self._total_pages = 2
        self._cols = 3
        self._rows = 3

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # ── Header ────────────────────────────────────────────────────────────
        hdr = QHBoxLayout()
        self._back_btn = QPushButton("\u25c0  Zur\u00fcck")
        self._back_btn.setFixedHeight(32)
        self._back_btn.setStyleSheet(
            "QPushButton{background:#252741;border:1px solid #334155;"
            "border-radius:4px;color:#e2e8f0;padding:0 12px;}"
            "QPushButton:hover{background:#2a3060;}"
        )
        self._back_btn.clicked.connect(self.back_requested)
        self._title_lbl = QLabel()
        self._title_lbl.setStyleSheet(
            "color:#e2e8f0;font-size:14px;font-weight:bold;border:none;"
        )
        self._title_lbl.setToolTip("Doppelklick zum Umbenennen")
        self._title_lbl.setCursor(Qt.IBeamCursor)
        self._title_lbl.installEventFilter(self)
        self._page_lbl = QLabel()
        self._page_lbl.setStyleSheet("color:#94a3b8;font-size:12px;border:none;")
        hdr.addWidget(self._back_btn)
        hdr.addSpacing(12)
        hdr.addWidget(self._title_lbl)
        hdr.addStretch(1)
        hdr.addWidget(self._page_lbl)
        layout.addLayout(hdr)

        # ── Value / GuV bar ───────────────────────────────────────────────────
        self._value_lbl = QLabel()
        self._value_lbl.setTextFormat(Qt.RichText)
        self._value_lbl.setStyleSheet(
            "color:#94a3b8;font-size:12px;border:none;"
            "background:#1a1d2e;border-radius:4px;padding:3px 10px;"
        )
        self._value_lbl.setAlignment(Qt.AlignCenter)
        self._value_lbl.hide()
        layout.addWidget(self._value_lbl)

        # ── Spread area ───────────────────────────────────────────────────────
        spread_row = QHBoxLayout()
        spread_row.setSpacing(8)
        spread_row.setContentsMargins(0, 0, 0, 0)

        nav_btn_ss = (
            "QPushButton{background:#252741;border:1px solid #334155;"
            "border-radius:4px;color:#94a3b8;font-size:18px;}"
            "QPushButton:hover{background:#2a3060;color:#e2e8f0;}"
            "QPushButton:disabled{color:#2a3045;background:#1a1d2e;border-color:#1a1d2e;}"
        )
        self._prev_btn = QPushButton("\u25c4")
        self._prev_btn.setFixedWidth(36)
        self._prev_btn.setStyleSheet(nav_btn_ss)
        self._prev_btn.clicked.connect(self._prev_spread)
        spread_row.addWidget(self._prev_btn, 0, Qt.AlignVCenter)

        self._pages_widget = QWidget()
        self._pages_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._pages_layout = QHBoxLayout(self._pages_widget)
        self._pages_layout.setSpacing(16)
        self._pages_layout.setContentsMargins(0, 0, 0, 0)
        spread_row.addWidget(self._pages_widget, 1)

        self._next_btn = QPushButton("\u25ba")
        self._next_btn.setFixedWidth(36)
        self._next_btn.setStyleSheet(nav_btn_ss)
        self._next_btn.clicked.connect(self._next_spread)
        spread_row.addWidget(self._next_btn, 0, Qt.AlignVCenter)

        layout.addLayout(spread_row, 1)
        self._page_grids: list[_AlbumPageGrid] = []

    def load_album(self) -> None:
        info = self._album_repo.get_album(self._album_id)
        if not info:
            return
        self._cols = info["cols"]
        self._rows = info["rows"]
        self._title_lbl.setText(info["name"])
        self._refresh_pages()

    def eventFilter(self, obj: object, event: QEvent) -> bool:
        if obj is self._title_lbl and event.type() == QEvent.Type.MouseButtonDblClick:
            self._rename_album()
            return True
        return super().eventFilter(obj, event)

    def _rename_album(self) -> None:
        new_name, ok = QInputDialog.getText(
            self,
            "Album umbenennen",
            "Neuer Name:",
            text=self._title_lbl.text(),
        )
        if ok and new_name.strip():
            self._album_repo.rename_album(self._album_id, new_name.strip())
            self._title_lbl.setText(new_name.strip())

    def _refresh_pages(self) -> None:
        db_count = self._album_repo.get_album_page_count(self._album_id)
        # Always have at least 2 pages (1 spread), always even
        self._total_pages = max(2, db_count + (1 if db_count % 2 != 0 else 0))
        # Potentially expand if last page is fully filled
        self._maybe_expand_pages()
        self._current_spread = min(self._current_spread, self._total_pages - 2)
        if self._current_spread < 0:
            self._current_spread = 0
        self._rebuild_spread()

    def _maybe_expand_pages(self) -> None:
        """Ensure there's always one empty page at the end (dynamic growth)."""
        slots_per_page = self._cols * self._rows
        last_page = self._total_pages - 1
        filled = self._album_repo.get_page_slots_with_entries(self._album_id, last_page)
        if len(filled) >= slots_per_page:
            self._total_pages += 2

    def _rebuild_spread(self) -> None:
        self._page_grids.clear()
        while self._pages_layout.count():
            item = self._pages_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for i, page_num in enumerate([self._current_spread, self._current_spread + 1]):
            frame = QFrame()
            frame.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            frame.setStyleSheet(
                "QFrame{background:#252741;border:1px solid #334155;border-radius:6px;}"
            )
            fl = QVBoxLayout(frame)
            fl.setContentsMargins(4, 4, 4, 4)
            fl.setSpacing(4)
            pg_lbl = QLabel(f"Seite {page_num + 1}")
            pg_lbl.setStyleSheet(
                "color:#94a3b8;font-size:10px;border:none;background:transparent;"
            )
            pg_lbl.setAlignment(Qt.AlignCenter)
            fl.addWidget(pg_lbl)
            grid = _AlbumPageGrid(
                self._album_id, page_num,
                self._cols, self._rows,
                self._album_repo, self._col_repo,
                self._title_lbl.text(),
                frame,
            )
            grid.slot_changed.connect(self._on_slot_changed)
            grid.reload()
            val = grid.page_value()
            if val > 0:
                pg_lbl.setText(
                    f"Seite {page_num + 1}  ·  € {val:.2f}".replace(".", ",")
                )
            fl.addWidget(grid, 1)
            self._pages_layout.addWidget(frame, 1)
            self._page_grids.append(grid)

        total_spreads = (self._total_pages + 1) // 2
        current_spread_idx = self._current_spread // 2 + 1
        self._page_lbl.setText(f"Doppelseite {current_spread_idx} / {total_spreads}")
        self._prev_btn.setEnabled(self._current_spread > 0)
        self._next_btn.setEnabled(self._current_spread + 2 < self._total_pages)
        self._update_value_lbl()

    def _update_value_lbl(self) -> None:
        totals = self._album_repo.get_album_totals(self._album_id)
        market = totals["market"]
        purchase = totals["purchase"]
        if market <= 0.0:
            self._value_lbl.hide()
            return
        market_str = f"€ {market:.2f}".replace(".", ",")
        parts = [f"Gesamtwert: {market_str}"]
        if purchase is not None and purchase > 0.0:
            purchase_str = f"€ {purchase:.2f}".replace(".", ",")
            guv = market - purchase
            sign = "+" if guv >= 0 else ""
            guv_str = f"{sign}€ {guv:.2f}".replace(".", ",")
            pct = (guv / purchase) * 100.0
            pct_str = f"{'+' if pct >= 0 else ''}{pct:.1f} %".replace(".", ",")
            color = "#4ade80" if guv >= 0 else "#f87171"
            parts.append(f"Einkauf: {purchase_str}")
            parts.append(
                f"<span style='color:{color};font-weight:bold;'>GuV: {guv_str} ({pct_str})</span>"
            )
        self._value_lbl.setText("  ·  ".join(parts))
        self._value_lbl.show()

    def _on_slot_changed(self) -> None:
        self._maybe_expand_pages()
        self._rebuild_spread()

    def _prev_spread(self) -> None:
        if self._current_spread >= 2:
            self._current_spread -= 2
            self._rebuild_spread()

    def _next_spread(self) -> None:
        if self._current_spread + 2 < self._total_pages:
            self._current_spread += 2
            self._rebuild_spread()


# ─────────────────────────────────────────────────────────────────────────────
# Album spine (bookshelf item)
# ─────────────────────────────────────────────────────────────────────────────

class _AlbumSpine(QWidget):
    """Visual spine of a binder on the shelf (custom painted)."""

    clicked = Signal(int)  # album_id

    def __init__(
        self,
        album_id: int,
        album_name: str,
        logo_data: list[tuple[str, str | None]],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._album_id = album_id
        self._album_name = album_name
        self._logo_data = logo_data
        self._logos: list[QPixmap] = []
        self._hovered = False

        self.setFixedSize(_SPINE_W, _SPINE_H)
        self.setCursor(Qt.PointingHandCursor)
        sets_text = ", ".join(s for s, _ in logo_data[:5]) or "Keine Sets"
        self.setToolTip(f"\U0001f4d2 {album_name}\n{sets_text}")
        self._load_logos()

    def _load_logos(self) -> None:
        self._logos.clear()
        for _set_name, path in self._logo_data:
            if not path:
                continue
            p = Path(path)
            if not p.exists():
                continue
            pm = QPixmap(str(p))
            if not pm.isNull():
                pm = pm.scaledToWidth(60, Qt.SmoothTransformation)
                self._logos.append(pm)

    def enterEvent(self, event) -> None:
        self._hovered = True
        self.update()

    def leaveEvent(self, event) -> None:
        self._hovered = False
        self.update()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self._album_id)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        grad = QLinearGradient(0, 0, self.width(), 0)
        if self._hovered:
            grad.setColorAt(0, QColor("#1a2050"))
            grad.setColorAt(0.5, QColor("#252741"))
            grad.setColorAt(1, QColor("#1a2050"))
        else:
            grad.setColorAt(0, QColor("#151726"))
            grad.setColorAt(0.5, QColor("#1e2030"))
            grad.setColorAt(1, QColor("#151726"))
        painter.fillRect(self.rect(), grad)

        # Border
        painter.setPen(QPen(QColor("#2a3045" if not self._hovered else "#5865f2"), 1))
        painter.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 4, 4)

        # Set logos stacked from top
        y = 14
        for pm in self._logos[:6]:
            x = (self.width() - pm.width()) // 2
            painter.drawPixmap(x, y, pm)
            y += pm.height() + 6

        if not self._logos and self._logo_data:
            # Text fallback if logos not downloaded yet
            painter.setPen(QColor("#334155"))
            f = QFont()
            f.setPixelSize(9)
            painter.setFont(f)
            sets_text = "\n".join(s for s, _ in self._logo_data[:4])
            painter.drawText(
                self.rect().adjusted(4, 12, -4, -50),
                Qt.AlignHCenter | Qt.AlignTop | Qt.TextWordWrap,
                sets_text,
            )

        # Album name rotated along the spine (bottom to top on left edge)
        painter.save()
        painter.translate(18, self.height() - 12)
        painter.rotate(-90)
        font = QFont()
        font.setPixelSize(12)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor("#e2e8f0"))
        max_w = self.height() - 24
        fm = QFontMetrics(font)
        name_display = fm.elidedText(self._album_name, Qt.ElideRight, max_w)
        painter.drawText(0, 0, name_display)
        painter.restore()

        painter.end()


# ─────────────────────────────────────────────────────────────────────────────
# New album dialog
# ─────────────────────────────────────────────────────────────────────────────

class _NewAlbumDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Neues Album anlegen")
        self.setMinimumWidth(320)
        self.setStyleSheet("background:#1e2030;color:#e2e8f0;")

        layout = QFormLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(16, 16, 16, 16)

        input_ss = (
            "background:#252741;border:1px solid #334155;"
            "border-radius:4px;color:#e2e8f0;min-height:28px;padding:0 6px;"
        )

        self._name = QLineEdit()
        self._name.setPlaceholderText("z.B. Mein erstes Album")
        self._name.setStyleSheet(f"QLineEdit{{{input_ss}}}")
        layout.addRow("Name:", self._name)

        self._cols = QSpinBox()
        self._cols.setRange(1, 6)
        self._cols.setValue(3)
        self._cols.setStyleSheet(f"QSpinBox{{{input_ss}}}")
        layout.addRow("Spalten pro Seite:", self._cols)

        self._rows = QSpinBox()
        self._rows.setRange(1, 6)
        self._rows.setValue(3)
        self._rows.setStyleSheet(f"QSpinBox{{{input_ss}}}")
        layout.addRow("Reihen pro Seite:", self._rows)

        btn_row = QHBoxLayout()
        ok_btn = QPushButton("Anlegen")
        ok_btn.setStyleSheet(
            "QPushButton{background:#5865f2;color:white;border-radius:4px;"
            "padding:4px 16px;font-weight:bold;border:none;}"
            "QPushButton:hover{background:#4752c4;}"
        )
        ok_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("Abbrechen")
        cancel_btn.setStyleSheet(
            "QPushButton{background:#252741;color:#e2e8f0;border:1px solid #334155;"
            "border-radius:4px;padding:4px 12px;}"
        )
        cancel_btn.clicked.connect(self.reject)
        btn_row.addStretch()
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(ok_btn)
        layout.addRow("", btn_row)

    def result_values(self) -> tuple[str, int, int]:
        return self._name.text().strip(), self._cols.value(), self._rows.value()


# ─────────────────────────────────────────────────────────────────────────────
# Album overview (bookshelf)
# ─────────────────────────────────────────────────────────────────────────────

class _AlbenOverview(QWidget):
    """Bookshelf showing all albums as spines. Allows creating/renaming/deleting."""

    open_album = Signal(int)  # album_id

    def __init__(
        self,
        album_repo: AlbumRepository,
        col_repo: CollectionRepository,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._album_repo = album_repo
        self._col_repo = col_repo

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # Toolbar
        toolbar = QHBoxLayout()
        self._new_btn = QPushButton("+  Neues Album")
        self._new_btn.setMinimumHeight(36)
        self._new_btn.setStyleSheet(
            "QPushButton{background:#1a2050;color:white;border:2px solid #5865f2;"
            "border-radius:6px;padding:0 16px;font-weight:bold;font-size:13px;}"
            "QPushButton:hover{background:#5865f2;border-color:#a0aaff;}"
        )
        self._new_btn.clicked.connect(self._create_album)
        toolbar.addWidget(self._new_btn)
        toolbar.addStretch(1)
        hint = QLabel(
            "Klicke auf einen Ordner um ihn zu öffnen  \u2022  "
            "Rechtsklick zum Umbenennen oder Löschen"
        )
        hint.setStyleSheet("color:#334155;font-size:10px;border:none;")
        toolbar.addWidget(hint)
        layout.addLayout(toolbar)

        # Shelf scroll
        self._shelf_scroll = QScrollArea()
        self._shelf_scroll.setWidgetResizable(True)
        self._shelf_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._shelf_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._shelf_scroll.setMinimumHeight(_SPINE_H + 40)
        self._shelf_scroll.setStyleSheet(
            "QScrollArea{border:none;background:#151726;}"
        )
        self._shelf = QWidget()
        self._shelf.setStyleSheet("background:#151726;")
        self._shelf_layout = QHBoxLayout(self._shelf)
        self._shelf_layout.setSpacing(12)
        self._shelf_layout.setContentsMargins(16, 16, 16, 16)
        self._shelf_layout.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._shelf_scroll.setWidget(self._shelf)
        layout.addWidget(self._shelf_scroll, 1)

        self.reload()

    def reload(self) -> None:
        while self._shelf_layout.count():
            item = self._shelf_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        albums = self._album_repo.list_albums()
        if not albums:
            empty_lbl = QLabel(
                "Noch keine Alben vorhanden.\n"
                "Klicke auf '\u2795 Neues Album' um zu starten."
            )
            empty_lbl.setAlignment(Qt.AlignCenter)
            empty_lbl.setStyleSheet(
                "color:#334155;font-size:13px;border:none;background:transparent;"
            )
            self._shelf_layout.addWidget(empty_lbl)
            return

        for alb in albums:
            logos = self._album_repo.get_album_set_logos(alb["id"])
            spine = _AlbumSpine(alb["id"], alb["name"], logos)
            spine.clicked.connect(self.open_album)
            spine.setContextMenuPolicy(Qt.CustomContextMenu)
            spine.customContextMenuRequested.connect(
                lambda pos, aid=alb["id"], sp=spine: self._spine_context(aid, sp, pos)
            )
            self._shelf_layout.addWidget(spine)

        self._shelf_layout.addStretch(1)

    def _create_album(self) -> None:
        dlg = _NewAlbumDialog(self)
        if dlg.exec() == QDialog.Accepted:
            name, cols, rows = dlg.result_values()
            if name:
                self._album_repo.create_album(name, cols, rows)
                self.reload()

    def _spine_context(self, album_id: int, spine: _AlbumSpine, pos) -> None:
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu{background:#252741;color:#e2e8f0;border:1px solid #334155;}"
            "QMenu::item:selected{background:#5865f2;}"
        )
        rename_act = menu.addAction("\u270f  Umbenennen")
        menu.addSeparator()
        delete_act = menu.addAction("\U0001f5d1  Album l\u00f6schen")
        action = menu.exec(spine.mapToGlobal(pos))
        if action == rename_act:
            self._rename_album(album_id)
        elif action == delete_act:
            self._delete_album(album_id)

    def _rename_album(self, album_id: int) -> None:
        alb = self._album_repo.get_album(album_id)
        if not alb:
            return
        new_name, ok = QInputDialog.getText(
            self, "Album umbenennen", "Neuer Name:", text=alb["name"]
        )
        if ok and new_name.strip():
            self._album_repo.rename_album(album_id, new_name.strip())
            self.reload()

    def _delete_album(self, album_id: int) -> None:
        alb = self._album_repo.get_album(album_id)
        name = alb["name"] if alb else "?"
        resp = QMessageBox.question(
            self,
            "Album löschen",
            f"Album '{name}' wirklich löschen?\n"
            "Die Karten selbst bleiben in der Sammlung erhalten.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if resp == QMessageBox.Yes:
            self._album_repo.delete_album(album_id)
            self.reload()


# ─────────────────────────────────────────────────────────────────────────────
# Top-level AlbenWidget
# ─────────────────────────────────────────────────────────────────────────────

class AlbenWidget(QWidget):
    """Top-level widget for the 'Alben' subtab.

    Manages a QStackedWidget with:
      index 0 – _AlbenOverview  (bookshelf)
      index 1 – _AlbumDetailView (paginated album, rebuilt on each open)
    """

    def __init__(
        self,
        album_repo: AlbumRepository,
        col_repo: CollectionRepository,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._album_repo = album_repo
        self._col_repo = col_repo

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._stack = QStackedWidget()
        layout.addWidget(self._stack)

        self._overview = _AlbenOverview(album_repo, col_repo)
        self._overview.open_album.connect(self._open_album)
        self._stack.addWidget(self._overview)  # index 0

        self._detail: _AlbumDetailView | None = None

    def _open_album(self, album_id: int) -> None:
        if self._detail is not None:
            self._stack.removeWidget(self._detail)
            self._detail.deleteLater()
            self._detail = None

        detail = _AlbumDetailView(album_id, self._album_repo, self._col_repo)
        detail.back_requested.connect(self._back_to_overview)
        detail.load_album()
        self._stack.addWidget(detail)
        self._detail = detail
        self._stack.setCurrentWidget(detail)

    def _back_to_overview(self) -> None:
        self._overview.reload()
        self._stack.setCurrentWidget(self._overview)

    def refresh(self) -> None:
        """Called when the Alben subtab becomes active."""
        if self._stack.currentWidget() is self._overview:
            self._overview.reload()
