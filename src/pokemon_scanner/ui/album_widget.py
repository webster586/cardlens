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
import math
from pathlib import Path

import requests as _requests

from shiboken6 import isValid as _qt_is_valid
from PySide6.QtCore import Qt, QByteArray, QEvent, QMimeData, QThread, QTimer, Signal
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

from src.pokemon_scanner.ui.styles import scale, size_card_pt

from src.pokemon_scanner.db.catalog_repository import CatalogRepository
from src.pokemon_scanner.db.repositories import AlbumRepository, CollectionRepository
from src.pokemon_scanner.core.paths import CATALOG_IMAGES_DIR
from src.pokemon_scanner.datasources.name_translator import find_en_names_for_de_partial
from src.pokemon_scanner.ui.image_cache import load_card_pixmap, CardImageDownloadWorker

_log = logging.getLogger(__name__)


def _crash_trace(msg: str) -> None:
    """Write a line to logs/album_crash_trace.log and fsync immediately.
    Used to pinpoint 100%-reproducible hard crashes that bypass Python exceptions.
    Call BEFORE every potentially dangerous Qt operation."""
    import datetime as _dt, os as _os
    try:
        from src.pokemon_scanner.core.paths import LOG_DIR as _LD
        _LD.mkdir(parents=True, exist_ok=True)
        with open(_LD / "album_crash_trace.log", "a", encoding="utf-8") as _f:
            _f.write(f"{_dt.datetime.now().isoformat()} {msg}\n")
            _f.flush()
            _os.fsync(_f.fileno())
    except Exception:
        pass

_SLOT_W = 63
_SLOT_H = 88
_SLOT_GAP = 8
_SPINE_W = 160
_SPINE_H = 680
_SPINE_BTN_H = 32  # height of refresh button below each spine
_SPINE_CARD_H = _SPINE_H + 4 + _SPINE_BTN_H  # total _SpineCard height (716)
_MIME_SLOT = "application/x-album-slot"
_PRICE_H = 14  # pixels reserved at slot bottom for price label

class _CirclePlusButton(QPushButton):
    """Circular '+' button drawn via paintEvent — perfect circle and centred cross."""

    _SZ = 44
    _ACCENT = QColor("#5865f2")
    _ACCENT_HOVER = QColor("#a0aaff")
    _WHITE = QColor("#ffffff")

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(self._SZ, self._SZ)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hovered = False

    def enterEvent(self, event) -> None:
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event) -> None:  # type: ignore[override]
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        border = 3
        margin = border  # keep ellipse inside widget bounds
        rect_size = self._SZ - 2 * margin
        if self._hovered:
            p.setBrush(self._ACCENT)
            p.setPen(QPen(self._ACCENT_HOVER, border))
            cross_color = self._WHITE
        else:
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(self._ACCENT, border))
            cross_color = self._ACCENT
        p.drawEllipse(margin, margin, rect_size, rect_size)
        # Draw centred '+'
        cross_pen = QPen(cross_color, 2)
        cross_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(cross_pen)
        cx = self._SZ // 2
        cy = self._SZ // 2
        arm = 7
        p.drawLine(cx - arm, cy, cx + arm, cy)
        p.drawLine(cx, cy - arm, cx, cy + arm)
        p.end()


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
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(200)
        self._search_timer.timeout.connect(self._do_search)
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
        self._search_timer.start()  # restarts the 200 ms window

    def _do_search(self) -> None:
        self._load_entries(self._search.text().strip())

    def _load_entries(self, search: str) -> None:
        with self._col_repo.database.connect() as conn:
            if search:
                t = f"%{search.lower()}%"
                # Also search by English names matching the German partial input
                en_names = find_en_names_for_de_partial(search)
                de_clauses = "".join(
                    f"\n                       OR LOWER(c.name) LIKE ?" for _ in en_names
                )
                de_params = tuple(f"%{n}%" for n in en_names)
                rows = conn.execute(
                    f"""
                    SELECT c.api_id, c.name, c.set_name, c.card_number, c.local_image_path,
                           COALESCE(SUM(e.quantity), 0) AS owned_qty,
                           MIN(e.id) AS entry_id
                    FROM card_catalog c
                    LEFT JOIN collection_entries e ON e.api_id = c.api_id
                    WHERE LOWER(c.name) LIKE ?
                       OR LOWER(COALESCE(c.set_name,'')) LIKE ?
                       OR LOWER(COALESCE(c.card_number,'')) LIKE ?{de_clauses}
                    GROUP BY c.api_id
                    ORDER BY c.set_name, CAST(c.card_number AS INTEGER), c.card_number
                    LIMIT 100
                    """,
                    (t, t, t) + de_params,
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
        self._inner.setUpdatesEnabled(False)
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
                    f"color:#334155;font-size:{scale(20)}px;font-weight:bold;"
                )
            row_layout.addWidget(img_lbl)

            txt = QVBoxLayout()
            txt.setSpacing(2)
            name_lbl = QLabel(f"<b>{name}</b>")
            name_lbl.setStyleSheet(
                "color:#e2e8f0;background:transparent;border:none;"
            )
            name_lbl.setWordWrap(True)
            set_lbl = QLabel(f"{set_name}  \u00b7  #{card_num}")
            set_lbl.setStyleSheet(
                f"color:#94a3b8;font-size:{scale(10)}px;background:transparent;border:none;"
            )
            badge_lbl = QLabel(
                f"\u2713  Im Besitz: \u00d7{owned_qty}" if owned_qty
                else "+ Neu zur Sammlung hinzuf\u00fcgen"
            )
            badge_lbl.setStyleSheet(
                ("color:#4ade80;" if owned_qty else "color:#7c8dbb;")
                + f"font-size:{scale(10)}px;background:transparent;border:none;"
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

        self._inner.setUpdatesEnabled(True)

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
    drag_started = Signal()  # emitted immediately before QDrag.exec() blocks
    drag_ended = Signal()    # emitted immediately after QDrag.exec() returns

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

        self._plus_btn = _CirclePlusButton(self)
        self._plus_btn.clicked.connect(self._on_add_clicked)

        self._remove_btn = QPushButton("\u2715", self)
        self._remove_btn.setFixedSize(20, 20)
        self._remove_btn.setStyleSheet(
            "QPushButton{background:#7f1d1d;border:1px solid #ef4444;"
            f"border-radius:10px;color:white;font-size:{scale(11)}px;font-weight:bold;"
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
        # Abandon any in-flight fetch from the previous entry.
        # Do NOT set thread to None while running — that would drop the last
        # Python ref and let GC destroy the QThread mid-run → crash.
        # finished→deleteLater (set in _start_image_fetch) handles cleanup.
        # Guard against RuntimeError when the C++ QThread was already deleted
        # by deleteLater() but the Python wrapper is still alive (dangling ref).
        if self._fetch_thread is not None:
            try:
                if self._fetch_thread.isRunning():
                    self._fetch_thread.done.disconnect()
            except RuntimeError:
                pass
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
            if api_id and url.startswith(("http://", "https://")):
                self._start_image_fetch(api_id, url)

        self.update()

    def teardown(self) -> None:
        """Disconnect the active fetch thread.  Must be called before deleteLater()."""
        if self._fetch_thread is not None:
            try:
                if self._fetch_thread.isRunning():
                    self._fetch_thread.done.disconnect()
            except RuntimeError:
                pass
            self._fetch_thread = None

    def _start_image_fetch(self, api_id: str, url: str) -> None:
        # No parent: Qt must NOT own the thread lifetime via parent-child —
        # if the slot widget is destroyed while downloading, Qt would delete
        # the running QThread → crash.  finished→deleteLater lets the event
        # loop clean up the C++ side once the thread actually finishes.
        self._fetch_thread = CardImageDownloadWorker(api_id, url)
        self._fetch_thread.done.connect(self._on_image_fetched)
        self._fetch_thread.finished.connect(self._fetch_thread.deleteLater)
        self._fetch_thread.start()

    def _on_image_fetched(self, local_path: str) -> None:
        """Called on the main thread when a background image download finishes."""
        # Guard: the slot widget may have been destroyed (deleteLater) before
        # this queued-connection callback fires.  isValid() checks the C++ side.
        if not _qt_is_valid(self):
            return
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
            try:
                self.update()
            except RuntimeError:
                pass  # widget was already deleted before the callback fired

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
        if self._entry:
            if self._market_price is not None:
                price_str = f"\u20ac {self._market_price:.2f}".replace(".", ",")
                price_color = QColor("#a0aaff")
            else:
                price_str = "\u20ac \u2013,\u2013\u2013"
                price_color = QColor("#334155")
            font = QFont("Segoe UI", size_card_pt(), QFont.Bold)
            painter.setFont(font)
            painter.setPen(price_color)
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
            self.drag_started.emit()
            drag.exec(Qt.MoveAction)
            self.drag_ended.emit()
        else:
            super().mouseMoveEvent(event)

    # ── Drop ──────────────────────────────────────────────────────────────────

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasFormat(_MIME_SLOT):
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        _crash_trace(f"DROP_START self=page{self.page_num}/slot{self.slot_index}")
        raw = bytes(event.mimeData().data(_MIME_SLOT))
        try:
            src = json.loads(raw)
        except Exception:
            _crash_trace("DROP_JSON_FAIL")
            return
        if src.get("album_id") != self.album_id:
            return
        p1, s1 = src["page_num"], src["slot_index"]
        p2, s2 = self.page_num, self.slot_index
        if (p1, s1) == (p2, s2):
            return
        _crash_trace(f"DROP_SWAP src=page{p1}/slot{s1} dst=page{p2}/slot{s2}")
        # Capture entry IDs before the swap so we can update album_page correctly
        dragged_eid = self._album_repo.get_slot_entry_id(self.album_id, p1, s1)
        target_eid = self._entry.get("collection_entry_id") if self._entry else None
        self._album_repo.swap_slots(self.album_id, p1, s1, p2, s2)
        _crash_trace("DROP_SWAP_DONE")
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
        _crash_trace("DROP_BEFORE_EMIT")
        self.slot_changed.emit()
        _crash_trace("DROP_AFTER_EMIT_OK")


# ─────────────────────────────────────────────────────────────────────────────
# Album page grid (cols × rows of slots)
# ─────────────────────────────────────────────────────────────────────────────

class _AlbumPageGrid(QWidget):
    """One binder page rendered as a grid of _AlbumSlot widgets."""

    slot_changed = Signal()
    drag_started = Signal()  # forwarded from any child _AlbumSlot
    drag_ended = Signal()    # forwarded from any child _AlbumSlot

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
            for c in range(cols):
                idx = r * cols + c
                slot = _AlbumSlot(album_id, page_num, idx, album_repo, col_repo, self._album_name)
                slot.slot_changed.connect(self._on_slot_changed)
                slot.drag_started.connect(self.drag_started)
                slot.drag_ended.connect(self.drag_ended)
                grid.addWidget(slot, r, c)
                self._slots.append(slot)

        min_w = cols * 45 + (cols - 1) * _SLOT_GAP + 24
        min_h = rows * 63 + (rows - 1) * _SLOT_GAP + 24
        self.setMinimumSize(min_w, min_h)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setStyleSheet("background:#1e2030; border:none;")

    # ── Aspect-ratio enforcement ───────────────────────────────────────────
    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._apply_card_aspect()

    def _apply_card_aspect(self) -> None:
        """Keep every slot at the true Pokémon-card ratio (63 × 88 mm).

        Uses whichever constraint (width or height) yields the smaller slot so
        that all rows are always fully visible and nothing gets clipped.
        """
        grid = self.layout()
        m = grid.contentsMargins()
        spacing = grid.spacing()

        avail_w = self.width() - m.left() - m.right() - spacing * (self._cols - 1)
        avail_h = self.height() - m.top() - m.bottom() - spacing * (self._rows - 1)

        # Candidate size derived from available width
        sw_from_w = max(45, avail_w // self._cols)
        sh_from_w = round(sw_from_w * 88 / 63) + _PRICE_H

        # Candidate size derived from available height
        sh_from_h = max(63 + _PRICE_H, avail_h // self._rows)
        sw_from_h = round((sh_from_h - _PRICE_H) * 63 / 88)

        # Pick the smaller of the two so slots fit in both dimensions
        if sh_from_w <= sh_from_h:
            slot_w, slot_h = sw_from_w, sh_from_w
        else:
            slot_w, slot_h = sw_from_h, sh_from_h

        for slot in self._slots:
            slot.setFixedSize(slot_w, slot_h)

    def _on_slot_changed(self) -> None:
        _crash_trace("PAGE_GRID_ON_SLOT_CHANGED")
        self.reload()
        _crash_trace("PAGE_GRID_AFTER_RELOAD")
        self.slot_changed.emit()
        _crash_trace("PAGE_GRID_AFTER_EMIT")

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
# Navigation arrow button with drag-over support
# ─────────────────────────────────────────────────────────────────────────────

class _NavArrowButton(QPushButton):
    """Navigation arrow (◄ / ►) that also accepts card drops.

    When a card is dragged over the button:
    * The button highlights immediately.
    * After DROP_HOVER_MS ms the ``auto_flip`` signal fires → the spread flips.
    On a direct drop ``page_drop(page_num, slot_index)`` is emitted so the
    caller can move the card to the first free slot of the adjacent spread.
    """

    DROP_HOVER_MS = 800  # ms until auto-flip while hovering during drag

    auto_flip = Signal()
    page_drop = Signal(int, int)  # (src_page_num, src_slot_index)

    _ACCENT = QColor("#5865f2")
    _ACCENT_A = QColor(88, 101, 242, 80)

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setAcceptDrops(True)
        self._drag_over = False
        self._hover_timer = QTimer(self)
        self._hover_timer.setSingleShot(True)
        self._hover_timer.setInterval(self.DROP_HOVER_MS)
        self._hover_timer.timeout.connect(self._on_timer)

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasFormat(_MIME_SLOT) and self.isEnabled():
            event.acceptProposedAction()
            self._drag_over = True
            self._hover_timer.start()
            self.update()
        else:
            super().dragEnterEvent(event)

    def dragLeaveEvent(self, event) -> None:
        self._drag_over = False
        self._hover_timer.stop()
        self.update()
        super().dragLeaveEvent(event)

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasFormat(_MIME_SLOT) and self.isEnabled():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:
        self._drag_over = False
        self._hover_timer.stop()
        self.update()
        if event.mimeData().hasFormat(_MIME_SLOT):
            raw = bytes(event.mimeData().data(_MIME_SLOT))
            try:
                src = json.loads(raw)
            except Exception:
                return
            event.acceptProposedAction()
            self.page_drop.emit(src["page_num"], src["slot_index"])
        else:
            super().dropEvent(event)

    def _on_timer(self) -> None:
        if self._drag_over:
            self.auto_flip.emit()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if self._drag_over:
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setBrush(self._ACCENT_A)
            p.setPen(QPen(self._ACCENT, 2))
            p.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 4, 4)
            p.end()


# ─────────────────────────────────────────────────────────────────────────────
# Album TOC panel (left page of spread 0)
# ─────────────────────────────────────────────────────────────────────────────

class _AlbumTocPanel(QFrame):
    """Table of contents shown as the left-hand page of the first spread."""

    def __init__(
        self,
        album_repo: AlbumRepository,
        album_id: int,
        album_name: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._album_repo = album_repo
        self._album_id = album_id
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setStyleSheet(
            "QFrame{background:#252741;border:1px solid #334155;border-radius:6px;}"
        )

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(8)

        # ── Header ──────────────────────────────────────────────────────────
        hdr = QLabel("Inhaltsangabe")
        hdr.setStyleSheet(
            f"color:#e2e8f0;font-size:{scale(13)}px;font-weight:bold;"
            "border:none;background:transparent;"
        )
        hdr.setAlignment(Qt.AlignCenter)
        outer.addWidget(hdr)

        self._total_lbl = QLabel()
        self._total_lbl.setStyleSheet(
            f"color:#94a3b8;font-size:{scale(11)}px;border:none;background:transparent;"
        )
        self._total_lbl.setAlignment(Qt.AlignCenter)
        outer.addWidget(self._total_lbl)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(
            "QFrame{background:#334155;border:none;max-height:1px;min-height:1px;}"
        )
        outer.addWidget(sep)

        # ── Scrollable page list ─────────────────────────────────────────────
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet(
            "QScrollArea{border:none;background:#252741;}"
            "QScrollBar:vertical{background:#1a1d2e;width:6px;border-radius:3px;}"
            "QScrollBar::handle:vertical{background:#334155;border-radius:3px;}"
        )

        self._list_widget = QWidget()
        self._list_widget.setStyleSheet("background:#252741;")
        self._list_layout = QVBoxLayout(self._list_widget)
        self._list_layout.setSpacing(3)
        self._list_layout.setContentsMargins(2, 2, 2, 2)
        self._list_layout.addStretch(1)
        scroll.setWidget(self._list_widget)
        outer.addWidget(scroll, 1)

        self.refresh()

    # ── helpers ─────────────────────────────────────────────────────────────

    def _make_row(
        self,
        name: str,
        value: str = "",
        *,
        name_color: str = "#94a3b8",
        val_color: str = "#94a3b8",
        name_bold: bool = False,
        name_size: int | None = None,
        indent: int = 0,
        count_text: str = "",
    ) -> QWidget:
        """Return a single list row widget with name + optional count + value."""
        w = QWidget()
        w.setStyleSheet("background:transparent;")
        h = QHBoxLayout(w)
        h.setContentsMargins(indent, 0, 0, 0)
        h.setSpacing(4)

        sz = name_size if name_size is not None else scale(10)
        weight = "bold" if name_bold else "normal"
        name_lbl = QLabel(name)
        name_lbl.setStyleSheet(
            f"color:{name_color};font-size:{sz}px;font-weight:{weight};"
            "border:none;background:transparent;"
        )
        name_lbl.setWordWrap(True)
        h.addWidget(name_lbl, 1)

        if count_text:
            cnt_lbl = QLabel(count_text)
            cnt_lbl.setAlignment(Qt.AlignCenter)
            cnt_lbl.setFixedWidth(60)
            cnt_lbl.setStyleSheet(
                f"color:{name_color};font-size:{sz}px;"
                "border:none;background:transparent;"
            )
            h.addWidget(cnt_lbl, 0)

        val_lbl = QLabel(value if value else "")
        val_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        val_lbl.setFixedWidth(72)
        val_lbl.setStyleSheet(
            f"color:{val_color};font-size:{sz}px;"
            "border:none;background:transparent;"
        )
        h.addWidget(val_lbl, 0)
        return w

    def refresh(self) -> None:
        """Reload totals and per-page rows from the DB."""
        totals = self._album_repo.get_album_totals(self._album_id)
        detail_rows = self._album_repo.get_album_pages_detail(self._album_id)

        market = totals["market"]
        if market > 0:
            self._total_lbl.setText(
                f"Gesamtwert: € {market:.2f}".replace(".", ",")
            )
        else:
            self._total_lbl.setText("Gesamtwert: –")

        # Clear old rows (keep the trailing stretch)
        while self._list_layout.count() > 1:
            item = self._list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # Group cards by page
        pages_data: dict[int, list[dict]] = {}
        for r in detail_rows:
            pages_data.setdefault(r["page_num"], []).append(r)

        for page_num in sorted(pages_data.keys()):
            cards = pages_data[page_num]
            pg_display = page_num + 1
            card_count = len(cards)
            page_total = sum(c["card_value"] for c in cards)
            page_purchase = sum(c["purchase_price"] for c in cards)
            karten = "Karte" if card_count == 1 else "Karten"
            total_str = f"€ {page_total:.2f}".replace(".", ",") if page_total > 0 else "–"

            # G&V
            guv_color = "#4ade80"
            guv_str = ""
            if page_total > 0 or page_purchase > 0:
                guv = page_total - page_purchase
                sign = "+" if guv >= 0 else ""
                guv_str = f"{sign}€ {guv:.2f}".replace(".", ",")
                guv_color = "#4ade80" if guv >= 0 else "#f87171"

            # ── Page header row ──────────────────────────────────────────
            pg_row = self._make_row(
                f"Seite {pg_display}",
                total_str,
                name_color="#e2e8f0",
                val_color="#e2e8f0",
                name_bold=True,
                name_size=scale(11),
                indent=0,
                count_text=f"{card_count} {karten}",
            )
            self._list_layout.insertWidget(self._list_layout.count() - 1, pg_row)

            # G&V row
            if guv_str:
                gv_row = self._make_row(
                    f"G&V: {guv_str}",
                    name_color=guv_color,
                    indent=4,
                )
                self._list_layout.insertWidget(self._list_layout.count() - 1, gv_row)

            # Group cards by set (sorted)
            set_groups: dict[str, list[dict]] = {}
            for c in cards:
                sn = c["set_name"] or "–"
                set_groups.setdefault(sn, []).append(c)

            for set_name in sorted(set_groups.keys()):
                set_cards = sorted(set_groups[set_name], key=lambda c: c["card_name"])
                set_total = sum(c["card_value"] for c in set_cards)
                set_val_str = f"€ {set_total:.2f}".replace(".", ",") if set_total > 0 else "–"

                # ── Set row ──────────────────────────────────────────────
                set_row = self._make_row(
                    set_name,
                    set_val_str,
                    name_color="#7dd3fc",
                    val_color="#7dd3fc",
                    name_bold=True,
                    indent=8,
                )
                self._list_layout.insertWidget(self._list_layout.count() - 1, set_row)

                # ── Card rows ────────────────────────────────────────────
                for card in set_cards:
                    cv = card["card_value"]
                    cv_str = f"€ {cv:.2f}".replace(".", ",") if cv > 0 else "–"
                    card_row = self._make_row(
                        card["card_name"] or "–",
                        cv_str,
                        name_color="#94a3b8",
                        val_color="#94a3b8",
                        indent=16,
                    )
                    self._list_layout.insertWidget(self._list_layout.count() - 1, card_row)

            # Spacer between pages
            spacer = QFrame()
            spacer.setFixedHeight(5)
            spacer.setStyleSheet("background:transparent;border:none;")
            self._list_layout.insertWidget(self._list_layout.count() - 1, spacer)


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
        cat_repo: CatalogRepository,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._album_id = album_id
        self._album_repo = album_repo
        self._col_repo = col_repo
        self._cat_repo = cat_repo
        self._current_spread = 0  # left page index of current double-page
        self._total_pages = 2
        self._cols = 3
        self._rows = 3
        self._price_worker: _AlbumRefreshWorker | None = None
        self._page_labels: list[QLabel] = []
        # Drag-safety state: set True by drag_started signal, False by drag_ended.
        # While True, _rebuild_spread() must NOT be called because the source slot
        # widget is still on the QDrag call stack (Windows OLE nested loop).
        # Instead, callers set _pending_rebuild = True; _on_drag_ended applies it.
        self._drag_active: bool = False
        self._pending_rebuild: bool = False

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
            f"color:#e2e8f0;font-size:{scale(14)}px;font-weight:bold;border:none;"
        )
        self._title_lbl.setToolTip("Doppelklick zum Umbenennen")
        self._title_lbl.setCursor(Qt.IBeamCursor)
        self._title_lbl.installEventFilter(self)
        self._page_lbl = QLabel()
        self._page_lbl.setStyleSheet(f"color:#94a3b8;font-size:{scale(12)}px;border:none;")
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
            f"color:#94a3b8;font-size:{scale(12)}px;border:none;"
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
            f"border-radius:4px;color:#94a3b8;font-size:{scale(18)}px;}}"
            "QPushButton:hover{background:#2a3060;color:#e2e8f0;}"
            "QPushButton:disabled{color:#2a3045;background:#1a1d2e;border-color:#1a1d2e;}"
        )
        self._prev_btn = _NavArrowButton("\u25c4")
        self._prev_btn.setFixedWidth(36)
        self._prev_btn.setStyleSheet(nav_btn_ss)
        self._prev_btn.clicked.connect(self._prev_spread)
        self._prev_btn.auto_flip.connect(self._prev_spread)
        self._prev_btn.page_drop.connect(self._on_drop_to_prev)
        spread_row.addWidget(self._prev_btn, 0, Qt.AlignVCenter)

        self._pages_widget = QWidget()
        self._pages_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._pages_layout = QHBoxLayout(self._pages_widget)
        self._pages_layout.setSpacing(16)
        self._pages_layout.setContentsMargins(0, 0, 0, 0)
        spread_row.addWidget(self._pages_widget, 1)

        self._next_btn = _NavArrowButton("\u25ba")
        self._next_btn.setFixedWidth(36)
        self._next_btn.setStyleSheet(nav_btn_ss)
        self._next_btn.clicked.connect(self._next_spread)
        self._next_btn.auto_flip.connect(self._next_spread)
        self._next_btn.page_drop.connect(self._on_drop_to_next)
        spread_row.addWidget(self._next_btn, 0, Qt.AlignVCenter)

        layout.addLayout(spread_row, 1)
        self._page_grids: list[_AlbumPageGrid] = []
        self._page_labels: list[QLabel] = []
        self._toc_panel: _AlbumTocPanel | None = None

    def load_album(self) -> None:
        info = self._album_repo.get_album(self._album_id)
        if not info:
            return
        self._cols = info["cols"]
        self._rows = info["rows"]
        self._title_lbl.setText(info["name"])
        self.setFocusPolicy(Qt.StrongFocus)
        self.setFocus()
        self._refresh_pages()

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key == Qt.Key.Key_Left:
            self._prev_spread()
        elif key == Qt.Key.Key_Right:
            self._next_spread()
        else:
            super().keyPressEvent(event)

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
        # Preserve the user's current spread position even if DB has fewer pages;
        # also ensure at least one more spread is reachable from where they are.
        self._total_pages = max(self._total_pages, self._current_spread + 4)
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
        _crash_trace("REBUILD_START")
        # Disconnect fetch threads on old slots BEFORE scheduling widget deletion.
        for old_grid in self._page_grids:
            for slot in old_grid._slots:
                slot.teardown()
        _crash_trace("REBUILD_AFTER_TEARDOWN")
        self._page_grids.clear()
        self._page_labels.clear()
        self._toc_panel = None
        while self._pages_layout.count():
            item = self._pages_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        _crash_trace("REBUILD_AFTER_DELETE_LATER")

        # ── Page mapping ─────────────────────────────────────────────────────
        # Spread 0:  [TOC | DB page 0]
        # Spread S>0: [DB page S-1 | DB page S]
        # DB page N → displayed label "Seite N+1"
        if self._current_spread == 0:
            # Left: TOC panel (not a card grid)
            toc = _AlbumTocPanel(
                self._album_repo, self._album_id, self._title_lbl.text()
            )
            self._toc_panel = toc
            self._pages_layout.addWidget(toc, 1)
            card_pages = [0]
        else:
            card_pages = [self._current_spread - 1, self._current_spread]

        for page_num in card_pages:
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
                f"color:#94a3b8;font-size:{scale(10)}px;border:none;background:transparent;"
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
            grid.drag_started.connect(self._on_drag_started)
            grid.drag_ended.connect(self._on_drag_ended)
            grid.reload()
            val = grid.page_value()
            if val > 0:
                pg_lbl.setText(
                    f"Seite {page_num + 1}  ·  € {val:.2f}".replace(".", ",")
                )
            fl.addWidget(grid, 1)
            self._pages_layout.addWidget(frame, 1)
            self._page_grids.append(grid)
            self._page_labels.append(pg_lbl)

        # ── Page counter label ────────────────────────────────────────────────
        if self._current_spread == 0:
            self._page_lbl.setText("Inhaltsangabe")
        else:
            left_num = self._current_spread       # DB page _current_spread-1 → "Seite S"
            right_num = self._current_spread + 1  # DB page _current_spread   → "Seite S+1"
            self._page_lbl.setText(f"Seite {left_num} · {right_num}")

        self._prev_btn.setEnabled(self._current_spread > 0)
        self._next_btn.setEnabled(True)  # album is infinite
        _crash_trace("REBUILD_END")
        self._update_value_lbl()
        self._auto_fetch_missing_prices()

    def _auto_fetch_missing_prices(self) -> None:
        """Background-fetch prices for every album card that has no price yet.

        Queries the DB for all pages (not just the visible spread) so one
        worker run caches the whole album at once.
        """
        if self._price_worker is not None:
            if self._price_worker.isRunning():
                return
            # Worker finished but not yet cleaned up.  Disconnect its signal NOW
            # so a late-firing done() can't call _on_price_refresh_done with a
            # stale self._price_worker reference (which would deleteLater the
            # NEW worker we're about to create → crash).
            try:
                self._price_worker.done.disconnect(self._on_price_refresh_done)
            except RuntimeError:
                pass
            self._price_worker = None
        missing = self._album_repo.get_album_missing_price_api_ids(self._album_id)
        if not missing:
            return
        self._price_worker = _AlbumRefreshWorker(self._cat_repo, missing)
        self._price_worker.done.connect(self._on_price_refresh_done)
        self._price_worker.start()

    def _on_price_refresh_done(self, _msg: str) -> None:
        """Reload slot data and update labels after auto price fetch."""
        if not _qt_is_valid(self):
            return
        for grid, lbl in zip(self._page_grids, self._page_labels):
            grid.reload()
            page_num = grid._page_num
            val = grid.page_value()
            lbl.setText(
                f"Seite {page_num + 1}  ·  € {val:.2f}".replace(".", ",")
                if val > 0 else f"Seite {page_num + 1}"
            )
        self._update_value_lbl()
        if self._price_worker is not None:
            self._price_worker.deleteLater()
            self._price_worker = None

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
        # Also refresh the TOC so its totals and per-page rows stay in sync.
        if self._toc_panel is not None and _qt_is_valid(self._toc_panel):
            self._toc_panel.refresh()

    def _on_drag_started(self) -> None:
        """One of our slot widgets entered QDrag.exec() — mark drag in progress."""
        self._drag_active = True
        _crash_trace("DRAG_ACTIVE_TRUE")

    def _on_drag_ended(self) -> None:
        """QDrag.exec() returned — safe to rebuild spread now if needed."""
        self._drag_active = False
        _crash_trace("DRAG_ACTIVE_FALSE")
        if self._pending_rebuild:
            self._pending_rebuild = False
            _crash_trace("DRAG_ENDED_TRIGGER_REFRESH")
            # singleShot(0) defers past the current mouseMoveEvent dispatch frame
            # before we destroy and recreate slot widgets.
            QTimer.singleShot(0, self._refresh_pages)

    def _live_navigate_to(self, spread: int) -> None:
        """Navigate to a spread IN-PLACE during an active drag — zero widget destruction.

        If the grid count would change (TOC↔cards boundary), bail immediately and
        schedule a full rebuild for after the drag ends instead.
        """
        _crash_trace(f"LIVE_NAVIGATE_TO spread={spread}")

        # TOC spread (0) needs 1 grid; all other spreads need 2 grids.
        # If count changes we can't remap in-place — defer to post-drag rebuild.
        grids_needed = 1 if spread == 0 else 2
        if len(self._page_grids) != grids_needed:
            self._current_spread = spread
            self._pending_rebuild = True
            _crash_trace(f"LIVE_NAVIGATE_TO_BAIL_GRID_COUNT spread={spread}")
            return

        self._current_spread = spread
        self._pending_rebuild = True  # full clean rebuild after drag ends

        for i, grid in enumerate(self._page_grids):
            # New mapping: spread 0 → page 0; spread S>0 → pages S-1, S
            new_page = i if spread == 0 else (spread - 1 + i)
            grid._page_num = new_page
            for slot in grid._slots:
                slot.page_num = new_page
            grid.reload()

        # Update page labels
        for grid, lbl in zip(self._page_grids, self._page_labels):
            page_num = grid._page_num
            val = grid.page_value()
            lbl.setText(
                f"Seite {page_num + 1}  ·  € {val:.2f}".replace(".", ",")
                if val > 0 else f"Seite {page_num + 1}"
            )

        # Update spread counter and nav buttons
        if spread == 0:
            self._page_lbl.setText("Inhaltsangabe")
        else:
            self._page_lbl.setText(f"Seite {spread} · {spread + 1}")
        self._prev_btn.setEnabled(spread > 0)
        self._next_btn.setEnabled(True)  # album is infinite
        _crash_trace(f"LIVE_NAVIGATE_TO_DONE spread={spread}")

    def _on_slot_changed(self) -> None:
        """Same-spread slot changed: reload data IN-PLACE, no widget destruction."""
        _crash_trace("DETAIL_ON_SLOT_CHANGED_RELOAD")
        self._maybe_expand_pages()
        for grid, lbl in zip(self._page_grids, self._page_labels):
            grid.reload()
            page_num = grid._page_num
            val = grid.page_value()
            lbl.setText(
                f"Seite {page_num + 1}  ·  € {val:.2f}".replace(".", ",")
                if val > 0 else f"Seite {page_num + 1}"
            )
        self._update_value_lbl()
        self._auto_fetch_missing_prices()
        _crash_trace("DETAIL_ON_SLOT_CHANGED_RELOAD_DONE")

    def _prev_spread(self) -> None:
        if self._current_spread >= 2:
            if self._drag_active:
                # Navigate in-place (no widget destruction) so the user sees
                # the target spread while still holding the dragged card.
                self._live_navigate_to(self._current_spread - 2)
            else:
                self._current_spread -= 2
                self._rebuild_spread()

    def _next_spread(self) -> None:
        new_spread = self._current_spread + 2
        # Auto-expand so the user can always navigate forward to empty pages.
        # Keep _total_pages at least one spread ahead so the button stays active.
        if new_spread > self._total_pages:
            self._total_pages = new_spread + 2  # new_spread is always even
        if self._drag_active:
            self._live_navigate_to(new_spread)
        else:
            self._current_spread = new_spread
            self._rebuild_spread()

    # ── Cross-page drag-drop ──────────────────────────────────────────────────

    def _on_drop_to_prev(self, src_page: int, src_slot: int) -> None:
        """Card dropped on ◄ → move to first free slot of the previous spread."""
        target = self._current_spread - 2
        if target < 0:
            return
        self._move_card_to_spread(src_page, src_slot, target)

    def _on_drop_to_next(self, src_page: int, src_slot: int) -> None:
        """Card dropped on ► → move to first free slot of the next spread."""
        target = self._current_spread + 2
        if target > self._total_pages:
            return
        self._move_card_to_spread(src_page, src_slot, target)

    def _move_card_to_spread(self, src_page: int, src_slot: int, target_spread: int) -> None:
        """Move the card at (src_page, src_slot) to the first free slot in target_spread."""
        _crash_trace(f"MOVE_CARD src=page{src_page}/slot{src_slot} target_spread={target_spread}")
        slots_per_page = self._cols * self._rows
        entry_id = self._album_repo.get_slot_entry_id(self._album_id, src_page, src_slot)
        if entry_id is None:
            _crash_trace("MOVE_CARD_NO_ENTRY")
            return
        # Determine which DB pages are visible on the target spread (new mapping).
        # Spread 0: [TOC, page 0]; Spread S>0: [page S-1, page S]
        target_pages = [0] if target_spread == 0 else [target_spread - 1, target_spread]
        for page_num in target_pages:
            filled = {
                s["slot_index"]
                for s in self._album_repo.get_page_slots_with_entries(self._album_id, page_num)
            }
            for idx in range(slots_per_page):
                if idx not in filled:
                    # Remove from source, place at target
                    self._album_repo.set_slot(self._album_id, src_page, src_slot, None)
                    self._album_repo.set_slot(self._album_id, page_num, idx, entry_id)
                    album_name = self._title_lbl.text()
                    if album_name:
                        self._col_repo.update_album_page(
                            entry_id, f"{album_name}, Seite {page_num + 1}"
                        )
                    self._current_spread = target_spread
                    # CRITICAL: QTimer.singleShot(0) fires inside QDrag.exec()'s
                    # nested OLE event loop — NOT after exec() returns.  If we
                    # schedule _refresh_pages here, it runs before the drag is
                    # fully cleaned up, deleteLater() fires on the source slot,
                    # and Qt's drag finalizer accesses freed C++ memory → SIGSEGV.
                    #
                    # Instead, set _pending_rebuild = True.  _on_drag_ended() will
                    # call _refresh_pages() via singleShot(0) *after* drag.exec()
                    # has returned to mouseMoveEvent, which is safe.
                    self._pending_rebuild = True
                    _crash_trace("MOVE_CARD_PENDING_REBUILD_SET")
                    return


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
        card_count: int = 0,
        cover_path: str | None = None,
        value_eur: float = 0.0,
        value_usd: float = 0.0,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._album_id = album_id
        self._album_name = album_name
        self._logo_data = logo_data
        self._card_count = card_count
        self._cover_path = cover_path
        self._value_eur = value_eur
        self._value_usd = value_usd
        self._logos: list[QPixmap] = []
        self._cover_pm: QPixmap | None = None
        self._hovered = False
        self._spinning = False

        self.setFixedSize(_SPINE_W, _SPINE_H)
        self.setCursor(Qt.PointingHandCursor)
        sets_text = ", ".join(s for s, _ in logo_data[:5]) or "Keine Sets"
        self.setToolTip(f"\U0001f4d2 {album_name}\n{sets_text}")
        self._load_assets()

    def _load_assets(self) -> None:
        # Cover
        if self._cover_path:
            p = Path(self._cover_path)
            if p.exists():
                pm = QPixmap(str(p))
                if not pm.isNull():
                    self._cover_pm = pm.scaled(
                        _SPINE_W - 20, 180,
                        Qt.KeepAspectRatio, Qt.SmoothTransformation,
                    )
        # Set logos
        self._logos.clear()
        for _set_name, path in self._logo_data:
            if not path:
                continue
            p = Path(path)
            if not p.exists():
                continue
            pm = QPixmap(str(p))
            if not pm.isNull():
                pm = pm.scaledToWidth(min(120, _SPINE_W - 20), Qt.SmoothTransformation)
                self._logos.append(pm)

    def enterEvent(self, event) -> None:
        self._hovered = True
        self.update()

    def leaveEvent(self, event) -> None:
        self._hovered = False
        self.update()

    def set_spinning(self, val: bool) -> None:
        self._spinning = val
        self.update()

    def set_cover_pixmap(self, pm: QPixmap) -> None:
        """Load a fresh cover pixmap (called after async download completes)."""
        if pm.isNull():
            return
        self._cover_pm = pm.scaled(
            _SPINE_W - 20, 180,
            Qt.KeepAspectRatio, Qt.SmoothTransformation,
        )
        self.update()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self._album_id)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # Background gradient
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
        painter.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 6, 6)

        # Left accent bar
        painter.fillRect(0, 0, 6, _SPINE_H, QColor("#5865f2" if self._hovered else "#2a3045"))

        content_x = 12  # left of content area (after accent bar)
        content_w = _SPINE_W - content_x - 8
        y = 14

        # Cover image (top)
        if self._cover_pm and not self._cover_pm.isNull():
            cx = content_x + (content_w - self._cover_pm.width()) // 2
            painter.drawPixmap(cx, y, self._cover_pm)
            y += self._cover_pm.height() + 10

        # Set logos
        for pm in self._logos[:4]:
            lx = content_x + (content_w - pm.width()) // 2
            painter.drawPixmap(lx, y, pm)
            y += pm.height() + 6

        if not self._logos and not self._cover_pm and self._logo_data:
            painter.setPen(QColor("#334155"))
            f = QFont()
            f.setPixelSize(scale(11))
            painter.setFont(f)
            sets_text = "\n".join(s for s, _ in self._logo_data[:5])
            painter.drawText(
                self.rect().adjusted(content_x, y, -8, -80),
                Qt.AlignHCenter | Qt.AlignTop | Qt.TextWordWrap,
                sets_text,
            )

        # Card count (bottom area, above name)
        if self._card_count > 0:
            count_f = QFont()
            count_f.setPixelSize(scale(13))
            painter.setFont(count_f)
            painter.setPen(QColor("#94a3b8"))
            count_rect = self.rect().adjusted(content_x, _SPINE_H - 70, -8, -40)
            painter.drawText(count_rect, Qt.AlignHCenter | Qt.AlignVCenter,
                             f"{self._card_count} Karten")

        # Album value (EUR / USD)
        if self._value_eur > 0.0 or self._value_usd > 0.0:
            if self._value_eur > 0.0 and self._value_usd > 0.0:
                value_text = f"€ {self._value_eur:.2f} / $ {self._value_usd:.2f}"
            elif self._value_eur > 0.0:
                value_text = f"€ {self._value_eur:.2f}"
            else:
                value_text = f"$ {self._value_usd:.2f}"
            val_f = QFont()
            val_f.setPixelSize(scale(11))
            painter.setFont(val_f)
            painter.setPen(QColor("#64d97b"))
            val_rect = self.rect().adjusted(content_x, _SPINE_H - 36, -8, -4)
            painter.drawText(val_rect, Qt.AlignHCenter | Qt.AlignVCenter, value_text)

        # Album name rotated bottom-to-top along left edge
        painter.save()
        painter.translate(16, _SPINE_H - 16)
        painter.rotate(-90)
        font = QFont()
        font.setPixelSize(scale(16))
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor("#e2e8f0"))
        max_w = _SPINE_H - 80
        fm = QFontMetrics(font)
        name_display = fm.elidedText(self._album_name, Qt.ElideRight, max_w)
        painter.drawText(0, 0, name_display)
        painter.restore()

        # Spinner overlay
        if self._spinning:
            overlay = QColor(0, 0, 0, 160)
            painter.fillRect(self.rect(), overlay)
            painter.setPen(QColor("#e2e8f0"))
            spin_f = QFont()
            spin_f.setPixelSize(scale(14))
            painter.setFont(spin_f)
            painter.drawText(
                self.rect(),
                Qt.AlignCenter | Qt.TextWordWrap,
                "⟳  Preise\naktualisieren …",
            )

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

# ─────────────────────────────────────────────────────────────────────────────
# Album overview (bookshelf)
# ─────────────────────────────────────────────────────────────────────────────


def _spine_extract_prices(card: dict) -> tuple[float | None, float | None]:
    """Return (eur_price, usd_price) from Cardmarket + TCGPlayer data."""
    eur = None
    for key in ("averageSellPrice", "trendPrice", "lowPrice"):
        p = card.get("cardmarket", {}).get("prices", {}).get(key)
        if p is not None:
            eur = round(float(p), 2)
            break
    usd = None
    tcg_prices = card.get("tcgplayer", {}).get("prices", {})
    for variant in (
        "normal", "holofoil", "reverseHolofoil",
        "1stEditionHolofoil", "1stEditionNormal",
        "unlimited", "unlimitedHolofoil", "promo",
    ):
        p = tcg_prices.get(variant, {}).get("market")
        if p is not None:
            usd = round(float(p), 2)
            break
    return eur, usd


class _AlbumRefreshWorker(QThread):
    """Fetches fresh prices from pokemontcg.io for all cards in one album."""

    progress = Signal(int, int)   # (current, total)
    done = Signal(str)            # summary message

    def __init__(self, cat_repo: CatalogRepository, api_ids: list[str]) -> None:
        super().__init__()
        self._cat_repo = cat_repo
        self._api_ids = api_ids

    def run(self) -> None:
        total = len(self._api_ids)
        if total == 0:
            self.done.emit("Keine Karten im Album.")
            return
        errors = 0
        fetched = 0
        _CHUNK = 100  # IDs per batch request (stays well within URL-length limits)
        chunks = [self._api_ids[i : i + _CHUNK] for i in range(0, total, _CHUNK)]
        for chunk_idx, chunk in enumerate(chunks):
            self.progress.emit(min(chunk_idx * _CHUNK + 1, total), total)
            try:
                q = " OR ".join(f"id:{aid}" for aid in chunk)
                resp = _requests.get(
                    "https://api.pokemontcg.io/v2/cards",
                    params={"q": q, "pageSize": "250"},
                    timeout=30,
                )
                if resp.ok:
                    for card in resp.json().get("data", []):
                        api_id = card.get("id", "")
                        if not api_id:
                            continue
                        eur, usd = _spine_extract_prices(card)
                        img_url = (
                            card.get("images", {}).get("small")
                            or card.get("images", {}).get("large")
                            or ""
                        )
                        self._cat_repo.update_prices(api_id, eur, usd, image_url=img_url or None)
                        if img_url:
                            self._cat_repo.save_local_image(api_id, img_url)
                        fetched += 1
                else:
                    errors += len(chunk)
                    _log.warning("Album refresh batch HTTP %s", resp.status_code)
            except Exception as exc:
                errors += len(chunk)
                _log.warning("Album refresh batch error: %s", exc)
        msg = f"✓ {fetched}/{total} Preise aktualisiert"
        if errors:
            msg += f" ({errors} Fehler)"
        self.done.emit(msg)


class _SpineCard(QWidget):
    """Container: _AlbumSpine on top + a price-refresh button below."""

    open_requested = Signal(int)  # album_id, forwarded from spine

    def __init__(
        self,
        spine: "_AlbumSpine",
        album_id: int,
        album_repo: AlbumRepository,
        cat_repo: CatalogRepository,
        first_card_info: dict | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._album_id = album_id
        self._album_repo = album_repo
        self._cat_repo = cat_repo
        self._worker: _AlbumRefreshWorker | None = None
        self._img_worker: CardImageDownloadWorker | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 4)
        layout.setSpacing(4)

        self._spine = spine
        spine.clicked.connect(self.open_requested)
        layout.addWidget(spine)

        self._btn = QPushButton("↻  Preise")
        self._btn.setFixedSize(_SPINE_W, _SPINE_BTN_H)
        self._btn.setStyleSheet(
            "QPushButton{background:#1a2050;color:#94a3b8;border:1px solid #2a3045;"
            f"border-radius:4px;font-size:{scale(11)}px;}}"
            "QPushButton:hover{background:#252d5a;color:#e2e8f0;border-color:#5865f2;}"
            "QPushButton:disabled{background:#0f1224;color:#334155;border-color:#1a2050;}"
        )
        self._btn.clicked.connect(self._start_refresh)
        layout.addWidget(self._btn)

        self.setFixedSize(_SPINE_W, _SPINE_CARD_H)

        # Eager cover: if no local image yet, start async download
        if first_card_info and not spine._cover_pm:
            api_id = first_card_info.get("api_id") or ""
            image_url = first_card_info.get("image_url") or ""
            if api_id and image_url:
                self._img_worker = CardImageDownloadWorker(api_id, image_url)
                self._img_worker.done.connect(self._on_cover_downloaded)
                self._img_worker.finished.connect(self._img_worker.deleteLater)
                self._img_worker.start()

    def _on_cover_downloaded(self, path: str) -> None:
        if path:
            pm = QPixmap(path)
            if not pm.isNull():
                self._spine.set_cover_pixmap(pm)
        # deleteLater is already connected to finished — just drop the Python ref
        self._img_worker = None

    def _start_refresh(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        api_ids = self._album_repo.get_album_api_ids(self._album_id)
        if not api_ids:
            self._btn.setText("Keine Karten")
            QTimer.singleShot(2000, self._reset_btn)
            return
        self._btn.setEnabled(False)
        self._btn.setText(f"⟳  Lädt… (0/{len(api_ids)})")
        self._spine.set_spinning(True)
        self._worker = _AlbumRefreshWorker(self._cat_repo, api_ids)
        self._worker.progress.connect(self._on_progress)
        self._worker.done.connect(self._on_done)
        self._worker.start()

    def _on_progress(self, current: int, total: int) -> None:
        self._btn.setText(f"⟳  Lädt… ({current}/{total})")

    def _on_done(self, msg: str) -> None:
        self._spine.set_spinning(False)
        self._btn.setText(msg)
        QTimer.singleShot(4000, self._reset_btn)

    def _reset_btn(self) -> None:
        self._btn.setEnabled(True)
        self._btn.setText("↻  Preise")
        if self._worker is not None:
            self._worker.deleteLater()
            self._worker = None


class _SpineShelf(QWidget):
    """Wrapping shelf that positions spine widgets in rows, top-left aligned."""

    _GAP_H = 16   # horizontal gap between spines
    _GAP_V = 24   # vertical gap between rows
    _MARGIN = 16

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._spines: list[QWidget] = []

    def set_spines(self, spines: list[QWidget]) -> None:
        for s in self._spines:
            s.setParent(None)  # type: ignore[arg-type]
            s.deleteLater()
        self._spines = list(spines)
        for s in self._spines:
            s.setParent(self)
            s.show()
        self._relayout()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._relayout()

    def _relayout(self) -> None:
        if not self._spines:
            self.setMinimumHeight(self._MARGIN * 2)
            return
        m = self._MARGIN
        gh, gv = self._GAP_H, self._GAP_V
        sw = self._spines[0].width() or _SPINE_W
        sh = self._spines[0].height() or _SPINE_CARD_H
        available_w = max(self.width() - 2 * m, sw)
        cols = max(1, (available_w + gh) // (sw + gh))
        x, y, col = m, m, 0
        for spine in self._spines:
            spine.move(x, y)
            col += 1
            if col >= cols:
                col = 0
                x = m
                y += sh + gv
            else:
                x += sw + gh
        rows = math.ceil(len(self._spines) / cols)
        self.setMinimumHeight(m + rows * sh + max(0, rows - 1) * gv + m)


class _AlbenOverview(QWidget):
    """Bookshelf showing all albums as spines. Allows creating/renaming/deleting."""

    open_album = Signal(int)  # album_id

    def __init__(
        self,
        album_repo: AlbumRepository,
        col_repo: CollectionRepository,
        cat_repo: CatalogRepository,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._album_repo = album_repo
        self._col_repo = col_repo
        self._cat_repo = cat_repo

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # Toolbar
        toolbar = QHBoxLayout()
        self._new_btn = QPushButton("+  Neues Album")
        self._new_btn.setMinimumHeight(36)
        self._new_btn.setStyleSheet(
            "QPushButton{background:#1a2050;color:white;border:2px solid #5865f2;"
            f"border-radius:6px;padding:0 16px;font-weight:bold;font-size:{scale(13)}px;}}"
            "QPushButton:hover{background:#5865f2;border-color:#a0aaff;}"
        )
        self._new_btn.clicked.connect(self._create_album)
        toolbar.addWidget(self._new_btn)
        toolbar.addStretch(1)
        hint = QLabel(
            "Klicke auf einen Ordner um ihn zu öffnen  \u2022  "
            "Rechtsklick zum Umbenennen oder Löschen"
        )
        hint.setStyleSheet(f"color:#334155;font-size:{scale(10)}px;border:none;")
        toolbar.addWidget(hint)
        layout.addLayout(toolbar)

        # Shelf scroll (vertical, wrapping grid)
        self._shelf_scroll = QScrollArea()
        self._shelf_scroll.setWidgetResizable(True)
        self._shelf_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._shelf_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._shelf_scroll.setStyleSheet(
            "QScrollArea{border:none;background:#151726;}"
        )
        self._spine_shelf = _SpineShelf()
        self._spine_shelf.setStyleSheet("background:#151726;")
        self._shelf_scroll.setWidget(self._spine_shelf)
        layout.addWidget(self._shelf_scroll, 1)

        self.reload()

    def reload(self) -> None:
        albums = self._album_repo.list_albums()
        if not albums:
            # Show empty placeholder instead of spines
            self._spine_shelf.set_spines([])
            return

        cards: list[QWidget] = []
        for alb in albums:
            logos = self._album_repo.get_album_set_logos(alb["id"])
            card_count = self._album_repo.get_album_card_count(alb["id"])
            cover_path = self._album_repo.get_album_cover_path(alb["id"])
            first_card_info = self._album_repo.get_album_first_card_info(alb["id"])
            value_eur, value_usd = self._album_repo.get_album_value(alb["id"])
            spine = _AlbumSpine(
                alb["id"], alb["name"], logos, card_count, cover_path,
                value_eur=value_eur, value_usd=value_usd,
            )
            spine.setContextMenuPolicy(Qt.CustomContextMenu)
            spine.customContextMenuRequested.connect(
                lambda pos, aid=alb["id"], sp=spine: self._spine_context(aid, sp, pos)
            )
            card = _SpineCard(
                spine, alb["id"], self._album_repo, self._cat_repo,
                first_card_info=first_card_info,
            )
            card.open_requested.connect(self.open_album)
            cards.append(card)
        self._spine_shelf.set_spines(cards)

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
        cat_repo: CatalogRepository,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._album_repo = album_repo
        self._col_repo = col_repo
        self._cat_repo = cat_repo

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._stack = QStackedWidget()
        layout.addWidget(self._stack)

        self._overview = _AlbenOverview(album_repo, col_repo, cat_repo)
        self._overview.open_album.connect(self._open_album)
        self._stack.addWidget(self._overview)  # index 0

        self._detail: _AlbumDetailView | None = None

    def _open_album(self, album_id: int) -> None:
        if self._detail is not None:
            self._stack.removeWidget(self._detail)
            self._detail.deleteLater()
            self._detail = None

        detail = _AlbumDetailView(album_id, self._album_repo, self._col_repo, self._cat_repo)
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
