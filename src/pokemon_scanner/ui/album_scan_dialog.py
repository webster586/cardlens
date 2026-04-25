from __future__ import annotations

import logging
import shutil
import tempfile
from pathlib import Path

import cv2
import numpy as np
from PIL import Image as _PilImage
from PIL import ExifTags as _ExifTags
from PySide6.QtCore import Qt, QEvent, QThread, Signal, QTimer, QRect
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRubberBand,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from src.pokemon_scanner.collection.service import CollectionService
from src.pokemon_scanner.core.paths import CATALOG_IMAGES_DIR, RUNTIME_DIR
from src.pokemon_scanner.datasources.base import CardCandidate
from src.pokemon_scanner.db.catalog_repository import CatalogRepository
from src.pokemon_scanner.db.repositories import AlbumPageRepository, OcrCorrectionRepository
from src.pokemon_scanner.recognition.pipeline import RecognitionPipeline
from src.pokemon_scanner.ui.image_cache import load_card_pixmap, CardImageDownloadWorker


def _compute_phash(image_path: str) -> str:
    """Return perceptual hash string for *image_path*, or '' on failure."""
    try:
        import imagehash
        return str(imagehash.phash(_PilImage.open(image_path)))
    except Exception:
        return ""

_CONDITIONS = ["M", "NM", "LP", "MP", "HP"]
_LOG = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Rotation helpers
# ---------------------------------------------------------------------------

def _exif_rotation_angle(image_path: str) -> int:
    """Return CW rotation degrees (0/90/180/270) implied by EXIF Orientation tag."""
    try:
        img = _PilImage.open(image_path)
        exif = img.getexif()
        if exif is None:
            return 0
        orient_tag = next(
            (k for k, v in _ExifTags.TAGS.items() if v == "Orientation"), None
        )
        if orient_tag is None:
            return 0
        orientation = exif.get(orient_tag, 1)
        return {1: 0, 3: 180, 6: 90, 8: 270}.get(orientation, 0)
    except Exception:
        return 0


def _rotate_cv2(img: np.ndarray, angle: int) -> np.ndarray:
    """Rotate *img* CW by *angle* degrees (0/90/180/270)."""
    if angle == 90:
        return cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)
    if angle == 180:
        return cv2.rotate(img, cv2.ROTATE_180)
    if angle == 270:
        return cv2.rotate(img, cv2.ROTATE_90_COUNTERCLOCKWISE)
    return img


TILE_W = 350
TILE_H = 275
THUMB_H = 263


def _load_card_pixmap(candidate: CardCandidate, w: int, h: int) -> QPixmap | None:
    """Return a scaled QPixmap from local catalog, or None if not available."""
    api_id = candidate.api_id or (
        candidate.notes[4:].strip()
        if candidate.notes and candidate.notes.startswith("ID: ")
        else ""
    )
    return load_card_pixmap(api_id, w=w, h=h) if api_id else None


# ---------------------------------------------------------------------------
# Workers
# ---------------------------------------------------------------------------

class GridDetectionWorker(QThread):
    """Detects card grid in an album photo via OpenCV contours."""

    detected = Signal(list, int, int)  # (cells as list of (x,y,w,h), rows, cols)
    failed = Signal()

    def __init__(self, image_path: str, manual_rows: int = 0, manual_cols: int = 0) -> None:
        super().__init__()
        self._path = image_path
        self._manual_rows = manual_rows
        self._manual_cols = manual_cols

    def run(self) -> None:
        try:
            img = cv2.imread(self._path)
            if img is None:
                self.failed.emit()
                return
            h, w = img.shape[:2]

            if self._manual_rows > 0 and self._manual_cols > 0:
                cells = self._manual_grid(w, h, self._manual_rows, self._manual_cols)
                self.detected.emit(cells, self._manual_rows, self._manual_cols)
                return

            cells, rows, cols = self._auto_detect(img)
            if cells:
                self.detected.emit(cells, rows, cols)
            else:
                # Fallback to 3×3
                cells = self._manual_grid(w, h, 3, 3)
                self.detected.emit(cells, 3, 3)
        except Exception as exc:
            _LOG.warning("GridDetectionWorker error: %s", exc)
            self.failed.emit()

    @staticmethod
    def _manual_grid(w: int, h: int, rows: int, cols: int) -> list:
        cw = w // cols
        ch = h // rows
        return [
            (c * cw, r * ch, cw, ch)
            for r in range(rows)
            for c in range(cols)
        ]

    @staticmethod
    def _auto_detect(img: np.ndarray) -> tuple:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        blurred = cv2.GaussianBlur(gray, (5, 5), 0)
        edges = cv2.Canny(blurred, 30, 100)
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
        dilated = cv2.dilate(edges, kernel, iterations=2)

        contours, _ = cv2.findContours(dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        img_area = img.shape[0] * img.shape[1]
        min_area = img_area * 0.01
        max_area = img_area * 0.35  # single card should not cover >35% of full photo
        candidates = []
        for cnt in contours:
            x, y, w, h = cv2.boundingRect(cnt)
            area = w * h
            if min_area < area < max_area:
                ar = w / h if h > 0 else 0
                if 0.45 < ar < 1.1:
                    candidates.append((x, y, w, h))

        if len(candidates) < 2:
            return [], 0, 0

        # Cluster into rows by y-center
        candidates.sort(key=lambda c: c[1])
        tolerance = img.shape[0] * 0.08
        rows_groups: list = []
        for cell in candidates:
            cy = cell[1] + cell[3] // 2
            placed = False
            for grp in rows_groups:
                avg_y = sum(g[1] + g[3] // 2 for g in grp) / len(grp)
                if abs(cy - avg_y) < tolerance:
                    grp.append(cell)
                    placed = True
                    break
            if not placed:
                rows_groups.append([cell])

        for grp in rows_groups:
            grp.sort(key=lambda c: c[0])

        n_rows = len(rows_groups)
        n_cols = max(len(g) for g in rows_groups)

        all_w = [c[2] for g in rows_groups for c in g]
        all_h = [c[3] for g in rows_groups for c in g]
        med_w = int(np.median(all_w))
        med_h = int(np.median(all_h))

        cells = []
        for grp in rows_groups:
            for (x, y, _, _) in grp:
                cells.append((x, y, med_w, med_h))

        return cells, n_rows, n_cols


class AlbumOcrWorker(QThread):
    """Runs OCR on every album cell. Emits cell_done(index, candidates) per cell."""

    cell_started = Signal(int)       # index of cell about to be processed
    cell_done = Signal(int, list, str, str)  # idx, candidates, warp_path, raw_ocr_text
    progress = Signal(int, int)   # (done, total)
    finished = Signal()

    def __init__(
        self,
        pipeline: RecognitionPipeline,
        image_path: str,
        cells: list,
        language: str = "",
        zone: tuple[float, float, float, float] | None = None,
    ) -> None:
        super().__init__()
        self._pipeline = pipeline
        self._image_path = image_path
        self._cells = cells
        self._language = language
        self._zone = zone
        # Private temp dir for this scan session — avoids collisions when
        # multiple AlbumScanDialogs are open or a dialog is reopened quickly.
        self._session_dir: str | None = None

    def run(self) -> None:
        img = cv2.imread(self._image_path)
        if img is None:
            self.finished.emit()
            return
        # Create a fresh temp dir for this scan session
        session_dir = tempfile.mkdtemp(prefix="cardlens_album_")
        self._session_dir = session_dir
        total = len(self._cells)
        for idx, cell in enumerate(self._cells):
            if self.isInterruptionRequested():
                break
            self.cell_started.emit(idx)
            x, y, w, h = cell
            try:
                crop = img[y:y + h, x:x + w]
                if crop.size == 0:
                    self.cell_done.emit(idx, [], "", "")
                    self.progress.emit(idx + 1, total)
                    continue
                # Cell-specific warp path inside the session dir — no global collisions
                cell_warp = str(Path(session_dir) / f"album_warp_{idx}.jpg")
                cv2.imwrite(cell_warp, crop)
                with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
                    tmp_path = tmp.name
                try:
                    cv2.imwrite(tmp_path, crop)
                    candidates, warp_path_obj, raw_ocr_text = self._pipeline.scan_image(
                        tmp_path, language=self._language, zone=self._zone
                    )
                finally:
                    Path(tmp_path).unlink(missing_ok=True)
                # Copy warp to cell-specific file
                if warp_path_obj and Path(str(warp_path_obj)).exists():
                    try:
                        shutil.copy2(str(warp_path_obj), cell_warp)
                    except Exception:
                        pass
                self.cell_done.emit(idx, candidates, cell_warp, raw_ocr_text)
            except Exception as exc:
                _LOG.warning("AlbumOcrWorker cell %d error: %s", idx, exc)
                self.cell_done.emit(idx, [], "", "")
            self.progress.emit(idx + 1, total)
        # session_dir intentionally kept alive — AlbumPageWidget.cleanup() deletes it
        # once the UI is done using the emitted cell warp paths.
        self.finished.emit()


class AlbumSearchWorker(QThread):
    finished = Signal(list)
    error = Signal(str)

    def __init__(self, pipeline: RecognitionPipeline, query: str, language: str = "") -> None:
        super().__init__()
        self._pipeline = pipeline
        self._query = query
        self._language = language

    def run(self) -> None:
        try:
            candidates = self._pipeline.search_by_name(self._query, language=self._language)
            self.finished.emit(candidates)
        except Exception as exc:
            self.error.emit(str(exc))


# ---------------------------------------------------------------------------
# TileWidget
# ---------------------------------------------------------------------------

class TileWidget(QFrame):
    """One album slot: thumbnail + name + condition combo + include checkbox."""

    clicked = Signal(int)
    candidate_changed = Signal(int)   # emitted when ◀▶ changes the active candidate

    def __init__(self, index: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.index = index
        self._candidates: list[CardCandidate] = []
        self._candidate_idx: int = 0
        self._selected_candidate: CardCandidate | None = None
        self._warp_path: str = ""

        self.setFixedSize(TILE_W + 14, THUMB_H + 130)
        self.setFrameShape(QFrame.Box)
        self.setFrameShadow(QFrame.Raised)
        self.setCursor(Qt.PointingHandCursor)

        layout = QVBoxLayout(self)
        layout.setSpacing(2)
        layout.setContentsMargins(4, 4, 4, 4)

        self._thumb = QLabel()
        self._thumb.setFixedSize(TILE_W, THUMB_H)
        self._thumb.setAlignment(Qt.AlignCenter)
        self._thumb.setStyleSheet("background: #1e293b; border-radius: 3px;")
        self._thumb.setText("?")
        layout.addWidget(self._thumb)

        self._name_lbl = QLabel("—")
        self._name_lbl.setAlignment(Qt.AlignCenter)
        self._name_lbl.setWordWrap(True)
        self._name_lbl.setMaximumHeight(48)
        font = QFont()
        font.setPointSize(10)
        self._name_lbl.setFont(font)
        layout.addWidget(self._name_lbl)

        bottom = QHBoxLayout()
        bottom.setSpacing(3)
        self._condition_combo = QComboBox()
        self._condition_combo.addItems(_CONDITIONS)
        self._condition_combo.setCurrentText("NM")
        self._condition_combo.setFixedHeight(28)
        self._condition_combo.setFixedWidth(100)
        f2 = QFont()
        f2.setPointSize(9)
        self._condition_combo.setFont(f2)
        bottom.addWidget(self._condition_combo)

        self._include_chk = QCheckBox()
        self._include_chk.setToolTip("In Sammlung aufnehmen")
        self._include_chk.setChecked(False)
        bottom.addWidget(self._include_chk)
        bottom.addStretch()
        layout.addLayout(bottom)

        # Candidate navigation row (always shown, disabled when < 2 candidates)
        nav = QHBoxLayout()
        nav.setSpacing(2)
        self._btn_prev = QPushButton("◀")
        self._btn_prev.setFixedSize(40, 28)
        self._btn_prev.setEnabled(False)
        self._btn_prev.clicked.connect(self._prev_candidate)
        nav.addWidget(self._btn_prev)
        self._nav_lbl = QLabel("")
        self._nav_lbl.setAlignment(Qt.AlignCenter)
        f3 = QFont()
        f3.setPointSize(10)
        self._nav_lbl.setFont(f3)
        nav.addWidget(self._nav_lbl, 1)
        self._btn_next = QPushButton("▶")
        self._btn_next.setFixedSize(40, 28)
        self._btn_next.setEnabled(False)
        self._btn_next.clicked.connect(self._next_candidate)
        nav.addWidget(self._btn_next)
        layout.addLayout(nav)

        self._set_empty_style()

    def _set_empty_style(self) -> None:
        self.setStyleSheet(
            "QFrame { border: 1px dashed #475569; border-radius: 4px; background: #0f172a; }"
        )

    def _set_filled_style(self) -> None:
        self.setStyleSheet(
            "QFrame { border: 1px solid #3b82f6; border-radius: 4px; background: #1e293b; }"
        )

    def set_loading(self) -> None:
        self._thumb.setText("⏳")
        self._name_lbl.setText("Analyse…")
        self._nav_lbl.setText("")
        self._btn_prev.setEnabled(False)
        self._btn_next.setEnabled(False)
        self._set_empty_style()

    def set_candidates(self, candidates: list, warp_path: str = "") -> None:
        self._candidates = candidates
        self._candidate_idx = 0
        self._warp_path = warp_path
        n = len(candidates)
        if n > 1:
            self._nav_lbl.setText(f"1/{n}")
            self._btn_prev.setEnabled(False)
            self._btn_next.setEnabled(True)
        elif n == 1:
            self._nav_lbl.setText("")
            self._btn_prev.setEnabled(False)
            self._btn_next.setEnabled(False)
        else:
            self._nav_lbl.setText("")
            self._btn_prev.setEnabled(False)
            self._btn_next.setEnabled(False)
        if candidates:
            self._selected_candidate = candidates[0]
            self._name_lbl.setText(candidates[0].name)
            self._include_chk.setChecked(True)
            self._set_filled_style()
            self._try_load_thumb(candidates[0])
        else:
            self._selected_candidate = None
            self._name_lbl.setText("—")
            self._thumb.setText("?")
            self._include_chk.setChecked(False)
            self._set_empty_style()

    def set_selected_candidate(self, candidate: CardCandidate) -> None:
        self._selected_candidate = candidate
        self._name_lbl.setText(candidate.name)
        self._include_chk.setChecked(True)
        self._set_filled_style()
        self._try_load_thumb(candidate)

    def get_warp_path(self) -> str:
        return self._warp_path

    def _prev_candidate(self) -> None:
        if self._candidate_idx > 0:
            self._candidate_idx -= 1
            self._apply_candidate_idx()

    def _next_candidate(self) -> None:
        if self._candidate_idx < len(self._candidates) - 1:
            self._candidate_idx += 1
            self._apply_candidate_idx()

    def _apply_candidate_idx(self) -> None:
        n = len(self._candidates)
        c = self._candidates[self._candidate_idx]
        self._selected_candidate = c
        self._name_lbl.setText(c.name)
        self._include_chk.setChecked(True)
        self._set_filled_style()
        self._try_load_thumb(c)
        self._nav_lbl.setText(f"{self._candidate_idx + 1}/{n}")
        self._btn_prev.setEnabled(self._candidate_idx > 0)
        self._btn_next.setEnabled(self._candidate_idx < n - 1)
        self.candidate_changed.emit(self.index)

    def _try_load_thumb(self, candidate: CardCandidate) -> None:
        pix = _load_card_pixmap(candidate, TILE_W, THUMB_H)
        if pix:
            self._thumb.setPixmap(pix)
        else:
            self._thumb.setText(candidate.name[:12] if candidate.name else "?")

    def get_selected(self) -> CardCandidate | None:
        return self._selected_candidate

    def get_condition(self) -> str:
        return self._condition_combo.currentText()

    def is_included(self) -> bool:
        return self._include_chk.isChecked() and self._selected_candidate is not None

    def mousePressEvent(self, event) -> None:
        self.clicked.emit(self.index)


# ---------------------------------------------------------------------------
# Image fetch worker + result row widget
# ---------------------------------------------------------------------------

class _ImageFetchWorker(CardImageDownloadWorker):
    """Alias kept for backwards compatibility within this module."""
    pass


_RESULT_IMG_W = 158
_RESULT_IMG_H = 220


class _CardResultRow(QFrame):
    selected = Signal(object)  # CardCandidate

    def __init__(self, candidate: CardCandidate, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._candidate = candidate
        self._worker: _ImageFetchWorker | None = None
        self.setFrameShape(QFrame.NoFrame)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet(
            "_CardResultRow { border-radius: 4px; }"
            "_CardResultRow:hover { background: #1e3a5f; }"
        )
        self.setMinimumHeight(_RESULT_IMG_H + 8)

        row = QHBoxLayout(self)
        row.setContentsMargins(4, 4, 4, 4)
        row.setSpacing(8)

        self._img_lbl = QLabel()
        self._img_lbl.setFixedSize(_RESULT_IMG_W, _RESULT_IMG_H)
        self._img_lbl.setAlignment(Qt.AlignCenter)
        self._img_lbl.setStyleSheet("background: #1e293b; border-radius: 3px;")
        self._img_lbl.setText("…")
        row.addWidget(self._img_lbl)

        info = QVBoxLayout()
        info.setSpacing(2)
        name_lbl = QLabel(candidate.name or "—")
        bold = QFont()
        bold.setBold(True)
        bold.setPointSize(9)
        name_lbl.setFont(bold)
        name_lbl.setWordWrap(True)
        info.addWidget(name_lbl)
        set_str = candidate.set_name or ""
        num_str = f" #{candidate.card_number}" if candidate.card_number else ""
        set_lbl = QLabel(f"{set_str}{num_str}")
        set_lbl.setStyleSheet("color: #94a3b8; font-size: 10px;")
        info.addWidget(set_lbl)
        if candidate.best_price:
            price_lbl = QLabel(f"€{candidate.best_price:.2f}")
            price_lbl.setStyleSheet("color: #22c55e; font-size: 10px;")
            info.addWidget(price_lbl)
        info.addStretch()
        row.addLayout(info, 1)

        # Try local catalog image first (no network needed)
        pix = _load_card_pixmap(candidate, _RESULT_IMG_W, _RESULT_IMG_H)
        if pix:
            self._img_lbl.setPixmap(pix)
            self._img_lbl.setText("")
        elif candidate.image_url:
            _api_id = candidate.api_id or (
                candidate.notes[4:].strip()
                if candidate.notes and candidate.notes.startswith("ID: ")
                else ""
            )
            if _api_id:
                self._worker = CardImageDownloadWorker(_api_id, candidate.image_url)
                self._worker.done.connect(self._on_image)
                self._worker.finished.connect(self._worker.deleteLater)
                self._worker.start()
            else:
                self._img_lbl.setText("?")
        else:
            self._img_lbl.setText("?")

    def _on_image(self, local_path: str) -> None:
        if local_path:
            pix = _load_card_pixmap(self._candidate, _RESULT_IMG_W, _RESULT_IMG_H)
            if pix and not pix.isNull():
                self._img_lbl.setPixmap(pix)
                self._img_lbl.setText("")
                return
        self._img_lbl.setText("?")

    def mousePressEvent(self, event) -> None:
        self.selected.emit(self._candidate)


# ---------------------------------------------------------------------------
# TileSearchDialog
# ---------------------------------------------------------------------------

class TileSearchDialog(QDialog):
    """Opened when user clicks a tile: search for a card and select it."""

    card_selected = Signal(object)  # CardCandidate

    def __init__(
        self,
        pipeline: RecognitionPipeline,
        initial_name: str,
        language: str,
        parent: QWidget | None = None,
        ocr_raw: str = "",
        warp_path: str = "",
        original_api_id: str | None = None,
        correction_repo: OcrCorrectionRepository | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Karte suchen")
        self.setMinimumWidth(560)
        self.setMinimumHeight(560)
        self.resize(580, 620)
        self._pipeline = pipeline
        self._language = language
        self._candidates: list[CardCandidate] = []
        self._worker: AlbumSearchWorker | None = None
        self._ocr_raw = ocr_raw
        self._warp_path = warp_path
        self._original_api_id = original_api_id
        self._correction_repo = correction_repo

        layout = QVBoxLayout(self)

        search_row = QHBoxLayout()
        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("Name oder Nummer eingeben…")
        self._search_edit.setText(initial_name)
        self._search_edit.returnPressed.connect(self._do_search)
        search_row.addWidget(self._search_edit)
        btn_search = QPushButton("Suchen")
        btn_search.setMinimumHeight(32)
        btn_search.clicked.connect(self._do_search)
        search_row.addWidget(btn_search)
        layout.addLayout(search_row)

        self._status_lbl = QLabel("")
        self._status_lbl.setStyleSheet("color: #94a3b8; font-size: 11px;")
        layout.addWidget(self._status_lbl)

        self._results_widget = QWidget()
        self._results_layout = QVBoxLayout(self._results_widget)
        self._results_layout.setSpacing(2)
        self._results_layout.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self._results_widget)
        scroll.setMinimumHeight(380)
        layout.addWidget(scroll)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_cancel = QPushButton("Abbrechen")
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(btn_cancel)
        layout.addLayout(btn_row)

        if initial_name.strip():
            QTimer.singleShot(80, self._do_search)

    def _do_search(self) -> None:
        query = self._search_edit.text().strip()
        if not query:
            return
        if self._worker and self._worker.isRunning():
            return
        self._status_lbl.setText("Suche läuft…")
        while self._results_layout.count():
            item = self._results_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._worker = AlbumSearchWorker(self._pipeline, query, self._language)
        self._worker.finished.connect(self._on_results)
        self._worker.error.connect(lambda e: self._status_lbl.setText(f"Fehler: {e}"))
        self._worker.start()

    def _on_results(self, candidates: list) -> None:
        self._candidates = candidates
        self._status_lbl.setText(
            f"{len(candidates)} Treffer" if candidates else "Keine Treffer"
        )
        for c in candidates[:30]:
            row = _CardResultRow(c, self._results_widget)
            row.selected.connect(self._select_candidate)
            self._results_layout.addWidget(row)
        self._results_layout.addStretch()

    def _select_candidate(self, candidate: CardCandidate) -> None:
        # Save correction when OCR was wrong or empty and user picks a different card
        if (
            self._correction_repo is not None
            and candidate.api_id
            and (self._ocr_raw or self._warp_path)
            and candidate.api_id != self._original_api_id
        ):
            phash = _compute_phash(self._warp_path) if self._warp_path else ""
            self._correction_repo.save_correction(
                ocr_raw=self._ocr_raw,
                correct_api_id=candidate.api_id,
                correct_name=candidate.name,
                image_phash=phash,
                correct_set_name=candidate.set_name,
                correct_card_number=candidate.card_number,
            )
            _LOG.info(
                "OCR correction saved: %r -> %s (%s)",
                self._ocr_raw, candidate.name, candidate.api_id,
            )
        self.card_selected.emit(candidate)
        self.accept()


# ---------------------------------------------------------------------------
# AlbumPageWidget — one photo page
# ---------------------------------------------------------------------------

class AlbumPageWidget(QWidget):
    """Left: photo + grid controls. Right: scrollable tile grid."""

    name_changed = Signal(str)  # emitted when the user edits the page name

    def __init__(
        self,
        image_path: str,
        pipeline: RecognitionPipeline,
        language: str,
        album_page_repo: AlbumPageRepository | None = None,
        correction_repo: OcrCorrectionRepository | None = None,
        catalog_repo: CatalogRepository | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._image_path = image_path
        self._pipeline = pipeline
        self._language = language
        self._album_page_repo = album_page_repo
        self._correction_repo = correction_repo
        self._catalog_repo = catalog_repo
        self._cells: list = []
        self._tiles: list[TileWidget] = []
        self._grid_worker: GridDetectionWorker | None = None
        self._ocr_worker: AlbumOcrWorker | None = None
        self._ocr_running = False
        self._original_pixmap: QPixmap | None = None
        self._detected_rows = 3
        self._detected_cols = 3
        self._ocr_active_cell: int = -1
        self._exif_angle: int = 0        # fixed from EXIF at load
        self._user_angle: int = 0        # user ↻ clicks (multiples of 90)
        self._exif_corrected_path: str = image_path  # EXIF-corrected base
        self._active_image_path: str = image_path  # currently displayed (may have user rotation too)
        self._rotated_tmp_path: str | None = None
        self._rotated_cv_image: np.ndarray | None = None  # in-memory rotated image (lazy write)
        self._single_card_idx: int = 0
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.timeout.connect(self._persist_name)
        # OCR zone: (x1, y1, x2, y2) as fractions of each cell (0–1); None = default
        self._ocr_zone: tuple[float, float, float, float] | None = None
        self._ocr_zone_draw_mode: bool = False
        self._drag_start: object = None   # QPoint when draw starts
        self._rubber_band: QRubberBand | None = None
        # Per-cell OCR data for learning
        self._ocr_texts: dict[int, str] = {}
        self._original_candidates: dict[int, list] = {}

        self._build_ui()
        self._load_image_preview()
        self._run_grid_detection(0, 0)

    def _build_ui(self) -> None:
        splitter = QSplitter(Qt.Horizontal)
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(splitter)

        # ---- LEFT ----
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(8, 8, 8, 8)
        left_layout.setSpacing(6)

        # ---- Compact controls row (top) ----
        ctrl_row = QHBoxLayout()
        ctrl_row.setSpacing(4)

        ctrl_row.addWidget(QLabel("Seite:"))
        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("z.B. Ordner Holo Seite 4")
        self._name_edit.setMinimumHeight(28)
        self._name_edit.setFixedWidth(150)
        if self._album_page_repo:
            saved = self._album_page_repo.find_name(self._image_path)
            if saved:
                self._name_edit.setText(saved)
        self._name_edit.textChanged.connect(self._on_name_changed)
        ctrl_row.addWidget(self._name_edit)

        ctrl_row.addWidget(QLabel("Z:"))
        self._rows_spin = QSpinBox()
        self._rows_spin.setRange(1, 20)
        self._rows_spin.setValue(3)
        self._rows_spin.setFixedWidth(46)
        self._rows_spin.setMinimumHeight(28)
        ctrl_row.addWidget(self._rows_spin)
        ctrl_row.addWidget(QLabel("S:"))
        self._cols_spin = QSpinBox()
        self._cols_spin.setRange(1, 20)
        self._cols_spin.setValue(3)
        self._cols_spin.setFixedWidth(46)
        self._cols_spin.setMinimumHeight(28)
        ctrl_row.addWidget(self._cols_spin)

        btn_apply = QPushButton("Anwenden")
        btn_apply.setMinimumHeight(28)
        btn_apply.setToolTip("Raster manuell anwenden")
        btn_apply.clicked.connect(self._apply_manual_grid)
        ctrl_row.addWidget(btn_apply)

        btn_auto = QPushButton("Auto")
        btn_auto.setMinimumHeight(28)
        btn_auto.setToolTip("Raster automatisch erkennen")
        btn_auto.clicked.connect(lambda: self._run_grid_detection(0, 0))
        ctrl_row.addWidget(btn_auto)

        btn_rotate = QPushButton("Drehen")
        btn_rotate.setMinimumHeight(28)
        btn_rotate.setToolTip("Bild 90 Grad im Uhrzeigersinn drehen")
        btn_rotate.clicked.connect(self._rotate_image)
        ctrl_row.addWidget(btn_rotate)

        self._btn_ocr_zone = QPushButton("OCR-Zone")
        self._btn_ocr_zone.setMinimumHeight(28)
        self._btn_ocr_zone.setCheckable(True)
        self._btn_ocr_zone.setToolTip("Rechteck im Vorschaubild aufziehen → OCR-Name-Zone für alle Slots setzen")
        self._btn_ocr_zone.clicked.connect(self._toggle_ocr_zone_mode)
        ctrl_row.addWidget(self._btn_ocr_zone)

        self._btn_analyse = QPushButton("Analyse")
        self._btn_analyse.setMinimumHeight(28)
        self._btn_analyse.setEnabled(False)
        self._btn_analyse.clicked.connect(self._start_ocr)
        ctrl_row.addWidget(self._btn_analyse)

        self._btn_stop = QPushButton("Stop")
        self._btn_stop.setMinimumHeight(28)
        self._btn_stop.setToolTip("Analyse stoppen")
        self._btn_stop.setEnabled(False)
        self._btn_stop.clicked.connect(self._stop_ocr)
        ctrl_row.addWidget(self._btn_stop)

        ctrl_row.addStretch(1)
        left_layout.addLayout(ctrl_row)

        self._progress_bar = QProgressBar()
        self._progress_bar.setVisible(False)
        self._progress_bar.setTextVisible(True)
        self._progress_bar.setMaximumHeight(12)
        left_layout.addWidget(self._progress_bar)

        # Status label hidden — text mirrored to _btn_analyse tooltip
        self._status_lbl = QLabel("Raster wird erkannt…")

        # Preview fills all remaining vertical space
        self._preview_lbl = QLabel()
        self._preview_lbl.setAlignment(Qt.AlignCenter)
        self._preview_lbl.setMinimumWidth(200)
        self._preview_lbl.setMinimumHeight(100)
        self._preview_lbl.setStyleSheet("background: #0f172a; border-radius: 4px;")
        self._preview_lbl.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._preview_lbl.setCursor(Qt.PointingHandCursor)
        self._preview_lbl.setMouseTracking(True)
        self._preview_lbl.installEventFilter(self)
        left_layout.addWidget(self._preview_lbl, 1)

        splitter.addWidget(left)
        splitter.setStretchFactor(0, 2)

        # ---- RIGHT ----
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(8, 8, 8, 8)
        right_layout.setSpacing(4)

        hdr = QHBoxLayout()
        hdr.addWidget(QLabel("Erkannte Karten:"))
        hdr.addStretch()
        self._select_all_btn = QPushButton("Alle ✓")
        self._select_all_btn.setFixedHeight(26)
        self._select_all_btn.setFixedWidth(64)
        self._select_all_btn.setToolTip("Alle erkannten Kacheln markieren")
        self._select_all_btn.clicked.connect(self._select_all)
        hdr.addWidget(self._select_all_btn)
        self._deselect_all_btn = QPushButton("Alle ✗")
        self._deselect_all_btn.setFixedHeight(26)
        self._deselect_all_btn.setFixedWidth(64)
        self._deselect_all_btn.setToolTip("Alle Markierungen aufheben")
        self._deselect_all_btn.clicked.connect(self._deselect_all)
        hdr.addWidget(self._deselect_all_btn)
        right_layout.addLayout(hdr)

        # Internal tile grid (not shown, used for data storage only)
        self._tile_scroll = QScrollArea()
        self._tile_scroll.setWidgetResizable(True)
        self._tiles_container = QWidget()
        self._tiles_grid_layout = QGridLayout(self._tiles_container)
        self._tiles_grid_layout.setSpacing(4)
        self._tile_scroll.setWidget(self._tiles_container)

        # -- Single-card view --
        single_page = QWidget()
        sp_layout = QVBoxLayout(single_page)
        sp_layout.setContentsMargins(4, 4, 4, 4)
        sp_layout.setSpacing(6)

        self._single_warp_lbl = QLabel()
        self._single_warp_lbl.setAlignment(Qt.AlignCenter)
        self._single_warp_lbl.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        self._single_warp_lbl.setCursor(Qt.PointingHandCursor)
        self._single_warp_lbl.setToolTip("Klicken zum Suchen / ändern")
        self._single_warp_lbl.installEventFilter(self)
        sp_layout.addWidget(self._single_warp_lbl, 1)

        self._single_name_lbl = QLabel("—")
        self._single_name_lbl.setAlignment(Qt.AlignCenter)
        bold = QFont()
        bold.setBold(True)
        bold.setPointSize(10)
        self._single_name_lbl.setFont(bold)
        sp_layout.addWidget(self._single_name_lbl)

        self._single_set_lbl = QLabel("")
        self._single_set_lbl.setAlignment(Qt.AlignCenter)
        self._single_set_lbl.setStyleSheet("color: #94a3b8; font-size: 10px;")
        sp_layout.addWidget(self._single_set_lbl)

        cond_row = QHBoxLayout()
        cond_row.addStretch()
        cond_row.addWidget(QLabel("Zustand:"))
        self._single_condition_combo = QComboBox()
        self._single_condition_combo.addItems(_CONDITIONS)
        self._single_condition_combo.setCurrentText("NM")
        self._single_condition_combo.setFixedWidth(72)
        self._single_condition_combo.currentTextChanged.connect(self._sync_condition_from_single)
        cond_row.addWidget(self._single_condition_combo)
        cond_row.addStretch()
        sp_layout.addLayout(cond_row)

        nav_row = QHBoxLayout()
        self._single_prev_btn = QPushButton("◀")
        self._single_prev_btn.setFixedSize(36, 32)
        self._single_prev_btn.clicked.connect(lambda: self._navigate_single(-1))
        nav_row.addWidget(self._single_prev_btn)
        self._single_pos_lbl = QLabel("")
        self._single_pos_lbl.setAlignment(Qt.AlignCenter)
        nav_row.addWidget(self._single_pos_lbl, 1)
        self._single_next_btn = QPushButton("▶")
        self._single_next_btn.setFixedSize(36, 32)
        self._single_next_btn.clicked.connect(lambda: self._navigate_single(1))
        nav_row.addWidget(self._single_next_btn)
        sp_layout.addLayout(nav_row)

        right_layout.addWidget(single_page, 1)

        splitter.addWidget(right)
        splitter.setStretchFactor(1, 3)

    def _load_image_preview(self) -> None:
        """Load image with EXIF-based auto-rotation, then update active path."""
        try:
            self._exif_angle = _exif_rotation_angle(self._image_path)
        except Exception:
            self._exif_angle = 0
        self._user_angle = 0
        if self._exif_angle != 0:
            self._exif_corrected_path = self._write_rotated_from(self._image_path, self._exif_angle, "exif")
        else:
            self._exif_corrected_path = self._image_path
        self._active_image_path = self._exif_corrected_path
        pix = QPixmap(self._active_image_path)
        if not pix.isNull():
            self._original_pixmap = pix
            self._refresh_preview(pix)

    def _write_rotated_from(self, src: str, angle: int, tag: str = "") -> str:
        """Rotate *src* by *angle* CW degrees, save to runtime tmp, return path."""
        img = cv2.imread(src)
        if img is None:
            return src
        rotated = _rotate_cv2(img, angle)
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        stem = Path(self._image_path).stem
        label = f"{tag}_{angle}" if tag else str(angle)
        tmp = str(RUNTIME_DIR / f"album_rot_{stem}_{label}.jpg")
        cv2.imwrite(tmp, rotated)
        return tmp

    def _write_rotated(self, src: str, angle: int) -> str:
        """Legacy wrapper kept for grid-overlay compatibility."""
        return self._write_rotated_from(src, angle)

    def _rotate_image(self) -> None:
        """Rotate the working image 90° CW on top of EXIF correction."""
        self._user_angle = (self._user_angle + 90) % 360
        if self._user_angle == 0:
            self._active_image_path = self._exif_corrected_path
            self._rotated_cv_image = None
            pix = QPixmap(self._exif_corrected_path)
        else:
            # Load base image only once; accumulate rotation in-memory
            base = cv2.imread(self._exif_corrected_path)
            if base is not None:
                self._rotated_cv_image = _rotate_cv2(base, self._user_angle)
                # Flush to disk lazily — write path for grid worker to read
                self._active_image_path = self._get_or_write_rotated_path()
            pix = QPixmap(self._active_image_path)
        if not pix.isNull():
            self._original_pixmap = pix
            self._refresh_preview(pix)
        # Reset tiles and re-detect grid on rotated image
        self._cells = []
        self._tiles = []
        self._run_grid_detection(0, 0)

    def _get_or_write_rotated_path(self) -> str:
        """Write _rotated_cv_image to disk (once per rotation angle) and return the path."""
        if self._rotated_cv_image is None:
            return self._exif_corrected_path
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        stem = Path(self._image_path).stem
        tmp = str(RUNTIME_DIR / f"album_rot_{stem}_user_{self._user_angle}.jpg")
        cv2.imwrite(tmp, self._rotated_cv_image)
        return tmp

    def _refresh_preview(self, pix: QPixmap) -> None:
        lbl_w = max(self._preview_lbl.width(), 200)
        lbl_h = max(self._preview_lbl.height(), 100)
        scaled = pix.scaled(lbl_w, lbl_h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self._preview_lbl.setPixmap(scaled)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._original_pixmap and not self._original_pixmap.isNull():
            self._refresh_preview(self._original_pixmap)

    def _run_grid_detection(self, manual_rows: int, manual_cols: int) -> None:
        if self._grid_worker and self._grid_worker.isRunning():
            return
        self._status_lbl.setText("Raster wird erkannt…")
        self._btn_analyse.setEnabled(False)
        self._grid_worker = GridDetectionWorker(self._active_image_path, manual_rows, manual_cols)
        self._grid_worker.detected.connect(self._on_grid_detected)
        def _on_failed():
            msg = "Auto-Erkennung fehlgeschlagen — Raster manuell eingeben"
            self._status_lbl.setText(msg)
            self._btn_analyse.setToolTip(msg)
        self._grid_worker.failed.connect(_on_failed)
        self._grid_worker.start()

    def _apply_manual_grid(self) -> None:
        self._run_grid_detection(self._rows_spin.value(), self._cols_spin.value())

    def _on_grid_detected(self, cells: list, rows: int, cols: int) -> None:
        self._cells = [tuple(c) for c in cells]
        self._detected_rows = rows
        self._detected_cols = cols
        self._rows_spin.setValue(rows)
        self._cols_spin.setValue(cols)
        msg = f"Raster: {rows} × {cols}  ({len(cells)} Kacheln)"
        self._status_lbl.setText(msg)
        self._btn_analyse.setToolTip(msg)
        self._btn_analyse.setEnabled(True)
        self._build_tiles(rows, cols)
        self._draw_grid_overlay()

    def _draw_grid_overlay(self) -> None:
        if not self._original_pixmap or self._original_pixmap.isNull():
            return
        pix = self._original_pixmap.copy()
        img = cv2.imread(self._active_image_path)
        if img is None:
            return
        ih, iw = img.shape[:2]
        pw, ph = pix.width(), pix.height()
        sx = pw / iw
        sy = ph / ih
        painter = QPainter(pix)
        cell_pen_w = max(2, pw // 400)
        for cell_idx, (x, y, w, h) in enumerate(self._cells):
            cx = int(x * sx); cy = int(y * sy)
            cw = int(w * sx); ch = int(h * sy)
            # Cell border: cyan if OCR-active, yellow if selected slot, else blue
            if cell_idx == self._ocr_active_cell:
                painter.setPen(QPen(QColor("#22d3ee"), cell_pen_w + 2))
                painter.drawRect(cx, cy, cw, ch)
            elif cell_idx == self._single_card_idx:
                painter.setPen(QPen(QColor("#facc15"), cell_pen_w + 2))
                painter.drawRect(cx, cy, cw, ch)
                # Draw slot number badge in top-left corner
                badge_size = max(18, cell_pen_w * 8)
                painter.setBrush(QColor("#facc15"))
                painter.setPen(Qt.NoPen)
                painter.drawRect(cx, cy, badge_size, badge_size)
                num_font = painter.font()
                num_font.setPixelSize(max(10, badge_size - 4))
                num_font.setBold(True)
                painter.setFont(num_font)
                painter.setPen(QColor("#0f172a"))
                painter.drawText(cx, cy, badge_size, badge_size, Qt.AlignCenter, str(cell_idx + 1))
                painter.setPen(Qt.NoPen)
                painter.setBrush(Qt.NoBrush)
            else:
                painter.setPen(QPen(QColor("#3b82f6"), cell_pen_w))
                painter.drawRect(cx, cy, cw, ch)
            # Name-zone overlay — use custom zone if set, else default
            if self._ocr_zone is not None:
                x1f, y1f, x2f, y2f = self._ocr_zone
                nz_x = int((x + w * x1f) * sx)
                nz_y = int((y + h * y1f) * sy)
                nz_w = int(w * (x2f - x1f) * sx)
                nz_h = int(h * (y2f - y1f) * sy)
            else:
                nz_x = int((x + w * 0.04) * sx)
                nz_y = int((y + h * 0.03) * sy)
                nz_w = int(w * 0.56 * sx)
                nz_h = int(h * 0.12 * sy)
            if nz_w > 4 and nz_h > 2:
                painter.setPen(QPen(QColor("#fbbf24"), max(1, cell_pen_w - 1)))
                painter.drawRect(nz_x, nz_y, nz_w, nz_h)
        painter.end()
        self._refresh_preview(pix)

    def _build_tiles(self, rows: int, cols: int) -> None:
        # Swap the container widget so Qt cleans up old tiles properly
        new_container = QWidget()
        new_grid = QGridLayout(new_container)
        new_grid.setSpacing(4)
        self._tile_scroll.setWidget(new_container)
        self._tiles_container = new_container
        self._tiles_grid_layout = new_grid
        self._tiles.clear()

        n = min(len(self._cells), rows * cols)
        for idx in range(n):
            tile = TileWidget(idx)
            tile.clicked.connect(self._on_tile_clicked)
            r, c = divmod(idx, cols)
            new_grid.addWidget(tile, r, c)
            self._tiles.append(tile)

    def _start_ocr(self) -> None:
        if self._ocr_running or not self._cells:
            return
        self._ocr_running = True
        self._btn_analyse.setEnabled(False)
        self._btn_stop.setEnabled(True)
        self._progress_bar.setVisible(True)
        self._progress_bar.setRange(0, len(self._cells))
        self._progress_bar.setValue(0)
        self._status_lbl.setText("OCR läuft…")
        for tile in self._tiles:
            tile.set_loading()
        self._ocr_worker = AlbumOcrWorker(
            self._pipeline, self._active_image_path, self._cells, self._language,
            zone=self._ocr_zone,
        )
        self._ocr_worker.cell_started.connect(self._on_cell_started)
        self._ocr_worker.cell_done.connect(self._on_cell_done)
        self._ocr_worker.progress.connect(
            lambda done, total: self._progress_bar.setValue(done)
        )
        self._ocr_worker.finished.connect(self._on_ocr_finished)
        self._ocr_worker.start()

    def _on_cell_started(self, idx: int) -> None:
        self._ocr_active_cell = idx
        self._draw_grid_overlay()

    def _on_cell_done(self, idx: int, candidates: list, warp_path: str, raw_ocr_text: str) -> None:
        if 0 <= idx < len(self._tiles):
            self._ocr_texts[idx] = raw_ocr_text
            self._original_candidates[idx] = list(candidates)
            self._tiles[idx].set_candidates(candidates, warp_path)
            self._tiles[idx].candidate_changed.connect(lambda i: self._update_single_card_view())
            if idx == self._single_card_idx:
                self._update_single_card_view()
            # Cache found candidates into local catalog (background, fire-and-forget)
            if self._catalog_repo and candidates:
                from src.pokemon_scanner.ui.main_window import CatalogSaveWorker
                worker = CatalogSaveWorker(self._catalog_repo, list(candidates))
                # Keep reference so GC doesn't kill it; remove on finish
                if not hasattr(self, '_catalog_workers'):
                    self._catalog_workers: list = []
                self._catalog_workers.append(worker)
                worker.finished.connect(
                    lambda w=worker: self._catalog_workers.remove(w)
                    if hasattr(self, '_catalog_workers') and w in self._catalog_workers else None
                )
                worker.start()

    def _stop_ocr(self) -> None:
        if self._ocr_worker and self._ocr_worker.isRunning():
            self._ocr_worker.requestInterruption()
        self._btn_stop.setEnabled(False)
        self._btn_analyse.setEnabled(True)
        self._status_lbl.setText("Analyse wird gestoppt…")

    def _on_ocr_finished(self) -> None:
        self._ocr_running = False
        self._ocr_active_cell = -1
        self._draw_grid_overlay()
        self._btn_stop.setEnabled(False)
        self._btn_analyse.setEnabled(True)
        self._progress_bar.setVisible(False)
        confirmed = sum(1 for t in self._tiles if t.get_selected() is not None)
        self._status_lbl.setText(
            f"Analyse abgeschlossen: {confirmed}/{len(self._tiles)} erkannt"
        )
        self._update_single_card_view()

    def _on_name_changed(self, text: str) -> None:
        self.name_changed.emit(text)
        # Debounce DB write by 600 ms
        self._save_timer.start(600)

    def _persist_name(self) -> None:
        if self._album_page_repo:
            self._album_page_repo.save(self._image_path, self._name_edit.text().strip())

    def get_page_name(self) -> str:
        return self._name_edit.text().strip()

    def _on_tile_clicked(self, idx: int) -> None:
        tile = self._tiles[idx]
        current_name = tile.get_selected().name if tile.get_selected() else ""
        dlg = TileSearchDialog(self._pipeline, current_name, self._language, self)
        dlg.card_selected.connect(lambda c, t=tile: t.set_selected_candidate(c))
        dlg.exec()
        if idx == self._single_card_idx:
            self._update_single_card_view()

    def _navigate_single(self, delta: int) -> None:
        n = len(self._tiles)
        if n == 0:
            return
        self._single_card_idx = max(0, min(self._single_card_idx + delta, n - 1))
        self._update_single_card_view()

    def _update_single_card_view(self) -> None:
        n = len(self._tiles)
        if n == 0:
            self._single_warp_lbl.setText("Keine Kacheln")
            self._single_name_lbl.setText("—")
            self._single_set_lbl.setText("")
            self._single_pos_lbl.setText("")
            self._single_prev_btn.setEnabled(False)
            self._single_next_btn.setEnabled(False)
            return
        idx = max(0, min(self._single_card_idx, n - 1))
        self._single_card_idx = idx
        tile = self._tiles[idx]
        self._single_pos_lbl.setText(f"{idx + 1} / {n}")
        self._single_prev_btn.setEnabled(idx > 0)
        self._single_next_btn.setEnabled(idx < n - 1)
        # Image: prefer catalog image of selected candidate, fallback to warp scan
        # Use a fixed max display size so the label doesn't expand with dark bars
        MAX_W, MAX_H = 350, 490
        cand = tile.get_selected()
        pix: QPixmap | None = None
        if cand:
            pix = _load_card_pixmap(cand, MAX_W, MAX_H)
        if pix is None:
            warp = tile.get_warp_path()
            if warp and Path(warp).exists():
                raw = QPixmap(warp)
                if not raw.isNull():
                    pix = raw.scaled(MAX_W, MAX_H, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        # Third fallback: crop the slot directly from the original image
        if pix is None and self._original_pixmap and not self._original_pixmap.isNull():
            if idx < len(self._cells):
                cx, cy, cw, ch = self._cells[idx]
                img = cv2.imread(self._active_image_path)
                if img is not None:
                    ih, iw = img.shape[:2]
                    pw2 = self._original_pixmap.width()
                    ph2 = self._original_pixmap.height()
                    sx2 = pw2 / iw; sy2 = ph2 / ih
                    crop_rect = QRect(
                        int(cx * sx2), int(cy * sy2),
                        int(cw * sx2), int(ch * sy2)
                    )
                    cropped = self._original_pixmap.copy(crop_rect)
                    if not cropped.isNull():
                        pix = cropped.scaled(MAX_W, MAX_H, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        if pix is not None:
            self._single_warp_lbl.setPixmap(pix)
            self._single_warp_lbl.setText("")
        else:
            self._single_warp_lbl.clear()
            self._single_warp_lbl.setText("⏳ Warte auf Analyse…")
        # Name + set
        if cand:
            self._single_name_lbl.setText(cand.name)
            self._single_set_lbl.setText(cand.set_name or "")
            self._single_condition_combo.blockSignals(True)
            self._single_condition_combo.setCurrentText(tile.get_condition())
            self._single_condition_combo.blockSignals(False)
        else:
            self._single_name_lbl.setText("— nicht erkannt")
            self._single_set_lbl.setText("")

    def _sync_condition_from_single(self, text: str) -> None:
        """Mirror condition change from single view back to the active tile."""
        if 0 <= self._single_card_idx < len(self._tiles):
            self._tiles[self._single_card_idx]._condition_combo.setCurrentText(text)

    def eventFilter(self, obj, event) -> bool:
        if (
            hasattr(self, "_single_warp_lbl")
            and obj is self._single_warp_lbl
            and event.type() == QEvent.Type.MouseButtonPress
        ):
            self._open_search_from_single()
            return True
        if hasattr(self, "_preview_lbl") and obj is self._preview_lbl:
            if self._ocr_zone_draw_mode:
                return self._handle_draw_event(event)
            elif event.type() == QEvent.Type.MouseButtonPress:
                self._on_preview_click(event)
                return True
        return super().eventFilter(obj, event)

    def _handle_draw_event(self, event) -> bool:
        """Handle rubber-band drawing of OCR zone on _preview_lbl."""
        from PySide6.QtCore import QRect as _QRect, QPoint as _QPoint
        from PySide6.QtWidgets import QRubberBand as _QRubberBand
        if event.type() == QEvent.Type.MouseButtonPress:
            self._drag_start = event.pos()
            if self._rubber_band is None:
                self._rubber_band = QRubberBand(QRubberBand.Shape.Rectangle, self._preview_lbl)
            self._rubber_band.setGeometry(_QRect(self._drag_start, self._drag_start))
            self._rubber_band.show()
            return True
        if event.type() == QEvent.Type.MouseMove and self._drag_start is not None:
            if self._rubber_band:
                self._rubber_band.setGeometry(
                    _QRect(self._drag_start, event.pos()).normalized()
                )
            return True
        if event.type() == QEvent.Type.MouseButtonRelease and self._drag_start is not None:
            if self._rubber_band:
                self._rubber_band.hide()
            end = event.pos()
            self._finalize_ocr_zone(self._drag_start, end)
            self._drag_start = None
            # Exit draw mode
            self._ocr_zone_draw_mode = False
            self._btn_ocr_zone.setChecked(False)
            self._preview_lbl.setCursor(Qt.PointingHandCursor)
            return True
        return False

    def _toggle_ocr_zone_mode(self, checked: bool) -> None:
        self._ocr_zone_draw_mode = checked
        if checked:
            from PySide6.QtCore import Qt as _Qt
            self._preview_lbl.setCursor(_Qt.CrossCursor)
        else:
            self._preview_lbl.setCursor(Qt.PointingHandCursor)
            if self._rubber_band:
                self._rubber_band.hide()
            self._drag_start = None

    def _finalize_ocr_zone(self, p1, p2) -> None:
        """Convert drawn rect (label coords) to cell-relative fractions and store as OCR zone."""
        if not self._cells or not self._original_pixmap:
            return
        pm = self._preview_lbl.pixmap()
        if pm is None or pm.isNull():
            return
        lbl_w = self._preview_lbl.width()
        lbl_h = self._preview_lbl.height()
        pm_w = pm.width(); pm_h = pm.height()
        off_x = (lbl_w - pm_w) // 2
        off_y = (lbl_h - pm_h) // 2
        pix_w = self._original_pixmap.width()
        pix_h = self._original_pixmap.height()
        # Clamp to pixmap area
        rx = max(0, min(p1.x(), p2.x()) - off_x)
        ry = max(0, min(p1.y(), p2.y()) - off_y)
        rx2 = max(0, max(p1.x(), p2.x()) - off_x)
        ry2 = max(0, max(p1.y(), p2.y()) - off_y)
        if rx2 <= rx or ry2 <= ry:
            return
        # Map to image coordinates
        img_x1 = rx * pix_w / pm_w
        img_y1 = ry * pix_h / pm_h
        img_x2 = rx2 * pix_w / pm_w
        img_y2 = ry2 * pix_h / pm_h
        # Find the cell whose center is closest to the rect center
        cx = (img_x1 + img_x2) / 2
        cy = (img_y1 + img_y2) / 2
        best_cell = min(
            self._cells,
            key=lambda c: (c[0] + c[2] / 2 - cx) ** 2 + (c[1] + c[3] / 2 - cy) ** 2
        )
        cell_x, cell_y, cell_w, cell_h = best_cell
        if cell_w == 0 or cell_h == 0:
            return
        x1f = max(0.0, (img_x1 - cell_x) / cell_w)
        y1f = max(0.0, (img_y1 - cell_y) / cell_h)
        x2f = min(1.0, (img_x2 - cell_x) / cell_w)
        y2f = min(1.0, (img_y2 - cell_y) / cell_h)
        if x2f <= x1f or y2f <= y1f:
            return
        self._ocr_zone = (x1f, y1f, x2f, y2f)
        self._draw_grid_overlay()

    def _on_preview_click(self, event) -> None:
        """Switch active slot by clicking on a cell in the preview image."""
        if not self._cells or not self._original_pixmap:
            return
        # Map label coords → original image coords
        lbl_w = self._preview_lbl.width()
        lbl_h = self._preview_lbl.height()
        pix_w = self._original_pixmap.width()
        pix_h = self._original_pixmap.height()
        if lbl_w == 0 or lbl_h == 0:
            return
        # The pixmap is displayed at the label's current pixmap size (centered)
        pm = self._preview_lbl.pixmap()
        if pm is None or pm.isNull():
            return
        pm_w = pm.width(); pm_h = pm.height()
        # Offset of pixmap inside label (centered)
        off_x = (lbl_w - pm_w) // 2
        off_y = (lbl_h - pm_h) // 2
        rel_x = event.pos().x() - off_x
        rel_y = event.pos().y() - off_y
        if rel_x < 0 or rel_y < 0 or rel_x >= pm_w or rel_y >= pm_h:
            return
        # Scale to original image coordinates
        img_x = rel_x * pix_w / pm_w
        img_y = rel_y * pix_h / pm_h
        # Find which cell was clicked
        for idx, (cx, cy, cw, ch) in enumerate(self._cells):
            if cx <= img_x <= cx + cw and cy <= img_y <= cy + ch:
                self._single_card_idx = idx
                self._draw_grid_overlay()
                self._update_single_card_view()
                break

    def _open_search_from_single(self) -> None:
        if not self._tiles or self._single_card_idx >= len(self._tiles):
            return
        tile = self._tiles[self._single_card_idx]
        idx = self._single_card_idx
        current_name = tile.get_selected().name if tile.get_selected() else ""
        ocr_raw = self._ocr_texts.get(idx, "")
        warp_path = tile.get_warp_path()
        orig_cands = self._original_candidates.get(idx, [])
        original_api_id = orig_cands[0].api_id if orig_cands else None
        dlg = TileSearchDialog(
            self._pipeline, current_name, self._language, self,
            ocr_raw=ocr_raw,
            warp_path=warp_path,
            original_api_id=original_api_id,
            correction_repo=self._correction_repo,
        )

        def _on_selected(c: CardCandidate) -> None:
            tile.set_selected_candidate(c)
            self._update_single_card_view()

        dlg.card_selected.connect(_on_selected)
        dlg.exec()

    def _select_all(self) -> None:
        for t in self._tiles:
            if t.get_selected() is not None:
                t._include_chk.setChecked(True)

    def _deselect_all(self) -> None:
        for t in self._tiles:
            t._include_chk.setChecked(False)

    def get_selected_tiles(self) -> list:
        """Return [(candidate, condition), …] for all checked tiles."""
        return [
            (t.get_selected(), t.get_condition())
            for t in self._tiles
            if t.is_included()
        ]

    def cleanup(self) -> None:
        """Stop all running workers and delete this page's temporary files."""
        for worker in (self._ocr_worker, self._grid_worker):
            if worker and worker.isRunning():
                worker.requestInterruption()
                worker.quit()
                worker.wait(2000)
        # Delete the per-session warp image directory created by AlbumOcrWorker
        if self._ocr_worker is not None and self._ocr_worker._session_dir:
            try:
                shutil.rmtree(self._ocr_worker._session_dir, ignore_errors=True)
            except Exception:
                pass
        # Delete rotation temp files (only those inside RUNTIME_DIR)
        for path in (self._exif_corrected_path, self._rotated_tmp_path, self._active_image_path):
            if not path or path == self._image_path:
                continue
            try:
                p = Path(path)
                if RUNTIME_DIR in p.parents:
                    p.unlink(missing_ok=True)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# AlbumScanDialog — main dialog
# ---------------------------------------------------------------------------

class AlbumScanDialog(QDialog):
    """Tab-based album scan: one tab per photo, bulk-add to collection."""

    def __init__(
        self,
        pipeline: RecognitionPipeline,
        collection_service: CollectionService,
        language: str = "",
        parent: QWidget | None = None,
        correction_repo: OcrCorrectionRepository | None = None,
        catalog_repo: CatalogRepository | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("📚  Scan Album")
        self.setMinimumSize(1050, 680)
        self._pipeline = pipeline
        self._collection_service = collection_service
        self._language = language
        self._has_real_tabs = False
        self._correction_repo = correction_repo
        self._catalog_repo = catalog_repo
        self._album_page_repo = AlbumPageRepository(collection_service.repository.database)

        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        if app:
            app.aboutToQuit.connect(self._cleanup_all_pages)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        top_row = QHBoxLayout()
        btn_add = QPushButton("📂  Foto(s) hinzufügen")
        btn_add.setMinimumHeight(36)
        btn_add.clicked.connect(self._add_photos)
        top_row.addWidget(btn_add)
        top_row.addStretch()
        layout.addLayout(top_row)

        self._tabs = QTabWidget()
        self._tabs.setTabsClosable(True)
        self._tabs.tabCloseRequested.connect(self._close_tab)
        layout.addWidget(self._tabs, 1)

        self._placeholder = QLabel(
            "Kein Foto geladen.\n\nFoto hinzufügen → Raster prüfen → Analyse starten → Kacheln prüfen → Zur Sammlung hinzufügen"
        )
        self._placeholder.setAlignment(Qt.AlignCenter)
        self._placeholder.setStyleSheet("color: #94a3b8; font-size: 13px;")
        self._tabs.addTab(self._placeholder, "—")

        bottom_row = QHBoxLayout()
        bottom_row.addStretch()
        self._bulk_status_lbl = QLabel("")
        self._bulk_status_lbl.setStyleSheet("color: #22c55e; font-size: 11px;")
        bottom_row.addWidget(self._bulk_status_lbl)
        btn_bulk = QPushButton("✅  Markierte zur Sammlung hinzufügen")
        btn_bulk.setMinimumHeight(38)
        btn_bulk.clicked.connect(self._bulk_add)
        bottom_row.addWidget(btn_bulk)
        btn_close = QPushButton("Schließen")
        btn_close.setMinimumHeight(38)
        btn_close.clicked.connect(self.accept)
        bottom_row.addWidget(btn_close)
        layout.addLayout(bottom_row)

    def closeEvent(self, event) -> None:
        self._cleanup_all_pages()
        super().closeEvent(event)

    def _cleanup_all_pages(self) -> None:
        for i in range(self._tabs.count()):
            page = self._tabs.widget(i)
            if isinstance(page, AlbumPageWidget):
                page.cleanup()

    def _add_photos(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Album-Fotos auswählen",
            "",
            "Bilder (*.jpg *.jpeg *.png *.bmp *.webp)",
        )
        for path in paths:
            self._add_tab(path)

    def _add_tab(self, image_path: str) -> None:
        if not self._has_real_tabs:
            self._tabs.clear()
            self._has_real_tabs = True
        page = AlbumPageWidget(
            image_path, self._pipeline, self._language,
            self._album_page_repo, self._correction_repo, self._catalog_repo,
        )
        name = Path(image_path).name
        label = name if len(name) <= 22 else name[:19] + "…"
        idx = self._tabs.addTab(page, label)
        # Update tab label live as user types the page name
        page.name_changed.connect(
            lambda text, i=idx: self._tabs.setTabText(
                self._tabs.indexOf(page),
                text if text.strip() else Path(image_path).name[:22]
            )
        )
        self._tabs.setCurrentWidget(page)

    def _close_tab(self, idx: int) -> None:
        page = self._tabs.widget(idx)
        image_path: str | None = None
        if isinstance(page, AlbumPageWidget):
            image_path = page._image_path
            msg = QMessageBox(self)
            msg.setWindowTitle("Bild entfernen")
            msg.setText(f"Soll die Datei auch vom Datentraeger geloescht werden?\n\n{image_path}")
            btn_list_only = msg.addButton("Nur aus Liste entfernen", QMessageBox.ButtonRole.ActionRole)
            btn_delete = msg.addButton("Datei loeschen", QMessageBox.ButtonRole.DestructiveRole)
            btn_cancel = msg.addButton("Abbrechen", QMessageBox.ButtonRole.RejectRole)
            msg.setDefaultButton(btn_list_only)
            msg.exec()
            clicked = msg.clickedButton()
            if clicked is btn_cancel:
                return
            page.cleanup()
            if clicked is btn_delete and image_path:
                try:
                    Path(image_path).unlink(missing_ok=True)
                except Exception:
                    pass
        self._tabs.removeTab(idx)
        if self._tabs.count() == 0:
            self._placeholder = QLabel(
                "Kein Foto geladen.\n\nFoto hinzufügen → Raster prüfen → Analyse starten → Kacheln prüfen → Zur Sammlung hinzufügen"
            )
            self._placeholder.setAlignment(Qt.AlignCenter)
            self._placeholder.setStyleSheet("color: #94a3b8; font-size: 13px;")
            self._tabs.addTab(self._placeholder, "—")
            self._has_real_tabs = False

    def _bulk_add(self) -> None:
        total_added = 0
        for i in range(self._tabs.count()):
            page = self._tabs.widget(i)
            if not isinstance(page, AlbumPageWidget):
                continue
            album_page = page.get_page_name()
            for candidate, condition in page.get_selected_tiles():
                try:
                    self._collection_service.confirm_candidate(
                        candidate, condition=condition, album_page=album_page
                    )
                    total_added += 1
                except Exception as exc:
                    _LOG.warning("bulk_add error: %s", exc)
        if total_added > 0:
            self._bulk_status_lbl.setText(f"{total_added} Karte(n) hinzugefügt")
            QMessageBox.information(
                self,
                "Sammlung aktualisiert",
                f"{total_added} Karte(n) wurden zur Sammlung hinzugefügt.",
            )
        else:
            self._bulk_status_lbl.setText("Keine markierten Kacheln gefunden")


# ---------------------------------------------------------------------------
# Embeddable widget (used as QStackedWidget page in the main window)
# ---------------------------------------------------------------------------

class AlbumScanWidget(QWidget):
    """Same content as AlbumScanDialog but embedded as a QWidget page."""

    back_requested = Signal()

    def __init__(
        self,
        pipeline: RecognitionPipeline,
        collection_service: CollectionService,
        language: str = "",
        parent: QWidget | None = None,
        correction_repo: OcrCorrectionRepository | None = None,
        catalog_repo: CatalogRepository | None = None,
    ) -> None:
        super().__init__(parent)
        self._pipeline = pipeline
        self._collection_service = collection_service
        self._language = language
        self._has_real_tabs = False
        self._correction_repo = correction_repo
        self._catalog_repo = catalog_repo
        self._album_page_repo = AlbumPageRepository(collection_service.repository.database)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        top_row = QHBoxLayout()
        btn_back = QPushButton("← Zurück")
        btn_back.setMinimumHeight(36)
        btn_back.clicked.connect(self.back_requested)
        top_row.addWidget(btn_back)
        btn_add = QPushButton("📂  Foto(s) hinzufügen")
        btn_add.setMinimumHeight(36)
        btn_add.clicked.connect(self._add_photos)
        top_row.addWidget(btn_add)
        top_row.addStretch()
        layout.addLayout(top_row)

        self._tabs = QTabWidget()
        self._tabs.setTabsClosable(True)
        self._tabs.tabCloseRequested.connect(self._close_tab)
        layout.addWidget(self._tabs, 1)

        self._placeholder = QLabel(
            "Kein Foto geladen.\n\nFoto hinzufügen → Raster prüfen → Analyse starten → Kacheln prüfen → Zur Sammlung hinzufügen"
        )
        self._placeholder.setAlignment(Qt.AlignCenter)
        self._placeholder.setStyleSheet("color: #94a3b8; font-size: 13px;")
        self._tabs.addTab(self._placeholder, "—")

        bottom_row = QHBoxLayout()
        bottom_row.addStretch()
        self._bulk_status_lbl = QLabel("")
        self._bulk_status_lbl.setStyleSheet("color: #22c55e; font-size: 11px;")
        bottom_row.addWidget(self._bulk_status_lbl)
        btn_bulk = QPushButton("✅  Markierte zur Sammlung hinzufügen")
        btn_bulk.setMinimumHeight(38)
        btn_bulk.clicked.connect(self._bulk_add)
        bottom_row.addWidget(btn_bulk)
        layout.addLayout(bottom_row)

    def update_language(self, language: str) -> None:
        """Update OCR language (called by main window when language changes)."""
        self._language = language

    def _cleanup_all_pages(self) -> None:
        for i in range(self._tabs.count()):
            page = self._tabs.widget(i)
            if isinstance(page, AlbumPageWidget):
                page.cleanup()

    def _add_photos(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Album-Fotos auswählen",
            "",
            "Bilder (*.jpg *.jpeg *.png *.bmp *.webp)",
        )
        for path in paths:
            self._add_tab(path)

    def _add_tab(self, image_path: str) -> None:
        if not self._has_real_tabs:
            self._tabs.clear()
            self._has_real_tabs = True
        page = AlbumPageWidget(
            image_path, self._pipeline, self._language,
            self._album_page_repo, self._correction_repo, self._catalog_repo,
        )
        name = Path(image_path).name
        label = name if len(name) <= 22 else name[:19] + "…"
        idx = self._tabs.addTab(page, label)
        page.name_changed.connect(
            lambda text, i=idx: self._tabs.setTabText(
                self._tabs.indexOf(page),
                text if text.strip() else Path(image_path).name[:22],
            )
        )
        self._tabs.setCurrentWidget(page)

    def _close_tab(self, idx: int) -> None:
        page = self._tabs.widget(idx)
        image_path: str | None = None
        if isinstance(page, AlbumPageWidget):
            image_path = page._image_path
            msg = QMessageBox(self)
            msg.setWindowTitle("Bild entfernen")
            msg.setText(
                f"Soll die Datei auch vom Datentraeger geloescht werden?\n\n{image_path}"
            )
            btn_list_only = msg.addButton("Nur aus Liste entfernen", QMessageBox.ButtonRole.ActionRole)
            btn_delete = msg.addButton("Datei loeschen", QMessageBox.ButtonRole.DestructiveRole)
            btn_cancel = msg.addButton("Abbrechen", QMessageBox.ButtonRole.RejectRole)
            msg.setDefaultButton(btn_list_only)
            msg.exec()
            clicked = msg.clickedButton()
            if clicked is btn_cancel:
                return
            page.cleanup()
            if clicked is btn_delete and image_path:
                try:
                    Path(image_path).unlink(missing_ok=True)
                except Exception:
                    pass
        self._tabs.removeTab(idx)
        if self._tabs.count() == 0:
            self._placeholder = QLabel(
                "Kein Foto geladen.\n\nFoto hinzufügen → Raster prüfen → Analyse starten → Kacheln prüfen → Zur Sammlung hinzufügen"
            )
            self._placeholder.setAlignment(Qt.AlignCenter)
            self._placeholder.setStyleSheet("color: #94a3b8; font-size: 13px;")
            self._tabs.addTab(self._placeholder, "—")
            self._has_real_tabs = False

    def _bulk_add(self) -> None:
        total_added = 0
        for i in range(self._tabs.count()):
            page = self._tabs.widget(i)
            if not isinstance(page, AlbumPageWidget):
                continue
            album_page = page.get_page_name()
            for candidate, condition in page.get_selected_tiles():
                try:
                    self._collection_service.confirm_candidate(
                        candidate, condition=condition, album_page=album_page
                    )
                    total_added += 1
                except Exception as exc:
                    _LOG.warning("bulk_add error: %s", exc)
        if total_added > 0:
            self._bulk_status_lbl.setText(f"{total_added} Karte(n) hinzugefügt")
            QMessageBox.information(
                self,
                "Sammlung aktualisiert",
                f"{total_added} Karte(n) wurden zur Sammlung hinzugefügt.",
            )
        else:
            self._bulk_status_lbl.setText("Keine markierten Kacheln gefunden")
