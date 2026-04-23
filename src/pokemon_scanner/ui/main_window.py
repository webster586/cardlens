from __future__ import annotations

import logging
import tempfile
from pathlib import Path

import cv2
import numpy as np
from PySide6.QtCore import QEvent, QFileSystemWatcher, QPoint, QRect, Qt, QSize, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QRubberBand,
    QSlider,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.pokemon_scanner.camera.camera_service import CameraService
from src.pokemon_scanner.collection.service import CollectionService
from src.pokemon_scanner.config.settings import AppSettings
from src.pokemon_scanner.core.logging_setup import get_logger
from src.pokemon_scanner.core.paths import CATALOG_IMAGES_DIR, EXPORT_DIR
from src.pokemon_scanner.datasources.base import CardCandidate
from src.pokemon_scanner.datasources.name_translator import translate_de_to_en_fuzzy
from src.pokemon_scanner.db.catalog_repository import CatalogRepository
from src.pokemon_scanner.db.database import Database
from src.pokemon_scanner.db.repositories import CollectionRepository, OcrCorrectionRepository
from src.pokemon_scanner.export.exporters import export_csv, export_json, export_xlsx
from src.pokemon_scanner.recognition.matcher import CandidateMatcher
from src.pokemon_scanner.recognition.ocr import OcrEngine
from src.pokemon_scanner.recognition.pipeline import RecognitionPipeline
from src.pokemon_scanner.ui.about_dialog import AboutDialog, ApiKeyDialog, DisclaimerDialog
from src.pokemon_scanner.ui.album_scan_dialog import AlbumScanDialog
from src.pokemon_scanner.ui.catalog_dialog import CatalogWidget
from src.pokemon_scanner.ui.image_cache import load_card_pixmap, CardImageDownloadWorker


def _cleanup_scan_photos(repo: "CollectionRepository") -> None:
    """Delete all user-uploaded scan photos from disk and clear the DB column."""
    try:
        rows = repo.list_all()
        paths_to_delete = [
            r["image_path"] for r in rows
            if r.get("image_path") and Path(r["image_path"]).exists()
        ]
        if not paths_to_delete:
            return
        for p in paths_to_delete:
            try:
                Path(p).unlink(missing_ok=True)
            except Exception:
                pass
        # Clear the image_path column in DB for all rows
        with repo.database.connect() as conn:
            conn.execute("UPDATE collection_entries SET image_path = NULL WHERE image_path IS NOT NULL")
            conn.commit()
    except Exception as exc:
        logging.getLogger(__name__).warning("Scan photo cleanup failed: %s", exc)


class CatalogSaveWorker(QThread):
    """Background worker: upserts candidates into catalog and downloads missing images."""

    def __init__(self, repo: "CatalogRepository", candidates: list["CardCandidate"]) -> None:
        super().__init__()
        self._repo = repo
        self._candidates = candidates

    def run(self) -> None:
        try:
            api_ids = self._repo.upsert_candidates(self._candidates)
            # Build url maps from candidates
            url_map: dict[str, str] = {}
            logo_map: dict[str, str] = {}  # set_name -> set_logo_url
            for c in self._candidates:
                if c.notes and c.notes.startswith("ID: "):
                    url_map[c.notes[4:].strip()] = c.image_url
                if c.set_name and c.set_logo_url:
                    logo_map[c.set_name] = c.set_logo_url
            for api_id in api_ids:
                url = url_map.get(api_id, "")
                if url:
                    self._repo.save_local_image(api_id, url)
            # Download set logos (skipped if already cached)
            for set_name, logo_url in logo_map.items():
                self._repo.save_set_logo(set_name, logo_url)
        except Exception as exc:
            logging.getLogger(__name__).warning("CatalogSaveWorker error: %s", exc)


class ManualSearchWorker(QThread):
    finished = Signal(list)  # candidates
    error = Signal(str)

    def __init__(self, pipeline: "RecognitionPipeline", query: str, language: str = "") -> None:
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


class _OcrWarmupWorker(QThread):
    """Pre-loads the EasyOCR model in the background so the first real scan is instant."""

    def run(self) -> None:
        try:
            OcrEngine._get_reader()
        except Exception as exc:
            logging.getLogger(__name__).warning("OCR warmup failed: %s", exc)


class ScanWorker(QThread):
    finished = Signal(list, str, str)  # (candidates, warp_path_or_empty, raw_ocr_text)
    status_update = Signal(str)
    error = Signal(str)

    def __init__(self, pipeline: RecognitionPipeline, image_path: str, language: str = "", zone=None) -> None:
        super().__init__()
        self._pipeline = pipeline
        self._image_path = image_path
        self._language = language
        self._zone = zone

    def run(self) -> None:
        try:
            self.status_update.emit("OCR l\u00e4uft \u2026")
            candidates, warp_path, raw_ocr = self._pipeline.scan_image(
                self._image_path, language=self._language, zone=self._zone
            )
            self.finished.emit(candidates, warp_path, raw_ocr)
        except Exception as exc:
            self.error.emit(str(exc))


class MainWindow(QMainWindow):
    def __init__(self, *, settings: AppSettings, database: Database) -> None:
        super().__init__()
        self.settings = settings
        self.database = database
        self.logger = get_logger(__name__)
        self.collection_service = CollectionService(CollectionRepository(database))
        self.catalog_repo = CatalogRepository(database)
        self.correction_repo = OcrCorrectionRepository(database)
        self.pipeline = RecognitionPipeline(
            database=database,
            pokemontcg_api_key=settings.pokemontcg_api_key,
            correction_repo=self.correction_repo,
        )
        self.camera_service = CameraService()
        self.current_candidates: list[CardCandidate] = []
        self.current_image_path: str = ""
        self._scan_worker: ScanWorker | None = None
        self._manual_search_worker: ManualSearchWorker | None = None
        self._image_dl_workers: list[CardImageDownloadWorker] = []
        self._catalog_save_workers: list[CatalogSaveWorker] = []
        self._active_lang: str = settings.preferred_language
        self._collection_cols_sized: bool = False

        self._camera_timer = QTimer(self)
        self._camera_timer.timeout.connect(self._on_camera_frame)

        # Zoom / pan state (live preview only)
        self._zoom_factor: float = 1.0
        self._pan_x: float = 0.5   # 0=left edge, 1=right edge
        self._pan_y: float = 0.5
        self._drag_last: QPoint | None = None

        # USB / folder watch state
        self._watch_folder: str | None = None
        self._watched_files: set[str] = set()
        self._is_watching: bool = False
        self._watch_timer = QTimer(self)
        self._watch_timer.setInterval(30000)  # fallback poll every 30s
        self._watch_timer.timeout.connect(self._poll_watch_folder)
        self._fs_watcher = QFileSystemWatcher(self)
        self._fs_watcher.directoryChanged.connect(lambda _: self._poll_watch_folder())

        # Region-draw OCR state
        self._region_mode: bool = False
        self._region_start: QPoint | None = None
        self._rubber_band: QRubberBand | None = None

        # OCR overlay: last raw text read by OCR, shown on live camera frame
        self._last_ocr_raw: str = ""
        self._ocr_overlay_cache: QPixmap | None = None
        self._ocr_overlay_cache_key: str = ""

        self.setWindowTitle("CardLens")
        self.resize(1700, 900)
        self._build_ui()
        self._build_menu()
        self._populate_camera_combo()
        self._update_zone_ui()  # restore button style if zone was previously saved

        # Pre-warm EasyOCR so the first real scan doesn't block the UI
        self._ocr_warmup_worker = _OcrWarmupWorker(self)
        self._ocr_warmup_worker.finished.connect(
            lambda: self.status_label.setText("OCR-Modell bereit \u2013 Bereit zum Scannen")
        )
        self.status_label.setText("OCR-Modell wird geladen \u2026")
        self._ocr_warmup_worker.start()

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)

    def _auto_start_camera(self) -> None:
        if not self.camera_service.state.is_running:
            self._start_camera()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_menu(self) -> None:
        """Add the Help menu to the menu bar."""
        menu_bar = self.menuBar()

        help_menu = menu_bar.addMenu("&Hilfe")

        action_api = help_menu.addAction("API-Schlüssel konfigurieren …")
        action_api.triggered.connect(self._open_api_key_dialog)

        help_menu.addSeparator()

        action_about = help_menu.addAction("Über CardLens …")
        action_about.triggered.connect(self._open_about)

        action_disclaimer = help_menu.addAction("Lizenzen & Disclaimer …")
        action_disclaimer.triggered.connect(self._open_disclaimer_readonly)

        help_menu.addSeparator()

        action_reset = help_menu.addAction("Disclaimer zurücksetzen")
        action_reset.setToolTip("Zeigt den Disclaimer beim nächsten Programmstart erneut an")
        action_reset.triggered.connect(self._reset_disclaimer)

    def _open_api_key_dialog(self) -> None:
        dlg = ApiKeyDialog(current_api_key=self.settings.pokemontcg_api_key, parent=self)
        if dlg.exec():
            new_key = dlg.api_key
            self.settings.pokemontcg_api_key = new_key
            self.settings.save()
            self.pipeline.card_adapter._api_key = new_key
            self.status_label.setText("API-Key gespeichert.")

    def _open_about(self) -> None:
        AboutDialog(parent=self).exec()

    def _open_disclaimer_readonly(self) -> None:
        dlg = DisclaimerDialog(current_api_key=self.settings.pokemontcg_api_key, parent=self)
        dlg.exec()

    def _reset_disclaimer(self) -> None:
        self.settings.disclaimer_accepted = False
        self.settings.save()
        self.status_label.setText("Disclaimer zurückgesetzt — wird beim nächsten Start erneut angezeigt.")

    # ── Nav-button style constants ──────────────────────────────────────────
    _NAV_ACTIVE = (
        "background:#252741;color:#fff;border:none;"
        "border-left:3px solid #5865f2;border-radius:0;"
        "padding:10px 16px 10px 13px;"
        "text-align:left;font-size:13px;min-height:44px;"
    )
    _NAV_INACTIVE = (
        "background:transparent;color:#9ca3af;border:none;"
        "border-radius:6px;padding:10px 16px;"
        "text-align:left;font-size:13px;min-height:44px;"
    )

    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── Left sidebar ──────────────────────────────────────────────────
        sidebar = self._build_sidebar()
        outer.addWidget(sidebar)

        # ── Thin separator ────────────────────────────────────────────────
        sep_line = QFrame()
        sep_line.setFrameShape(QFrame.VLine)
        sep_line.setFixedWidth(1)
        sep_line.setStyleSheet("background:#334155;border:none;")
        outer.addWidget(sep_line)

        # ── Content stack ─────────────────────────────────────────────────
        content_wrap = QWidget()
        content_wrap.setStyleSheet("background:#1e2030;")
        content_layout = QVBoxLayout(content_wrap)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        self._stack = QStackedWidget()
        content_layout.addWidget(self._stack, 1)

        self.status_label = QLabel("Bereit")
        self.status_label.setMinimumHeight(24)
        self.status_label.setStyleSheet(
            "color:#94a3b8;font-size:11px;padding:2px 10px;"
            "border-top:1px solid #334155;background:#151726;"
        )
        content_layout.addWidget(self.status_label)

        outer.addWidget(content_wrap, 1)

        # ── Page 0: Katalog ───────────────────────────────────────────────
        self._catalog_widget = CatalogWidget(
            self.catalog_repo,
            self.collection_service.repository,
            settings=self.settings,
        )
        self._stack.addWidget(self._catalog_widget)

        # ── Page 1: Scanner ───────────────────────────────────────────────
        scanner_page = self._build_scanner_page(root)
        self._stack.addWidget(scanner_page)

        # Default: Katalog
        self._stack.setCurrentIndex(0)

    def _build_sidebar(self) -> QFrame:
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(190)
        sidebar.setStyleSheet(
            "QFrame#sidebar { background: #0f1117; }"
            "QFrame#sidebar QPushButton:hover { background: #1e2030; color: #e2e8f0; }"
        )
        lay = QVBoxLayout(sidebar)
        lay.setContentsMargins(8, 16, 8, 16)
        lay.setSpacing(4)

        title = QLabel("CardLens")
        title.setStyleSheet(
            "color:#e2e8f0;font-size:18px;font-weight:bold;"
            "padding:6px 8px 14px 8px;border:none;background:transparent;"
        )
        lay.addWidget(title)

        nav_defs = [
            ("📋  Katalog",       0, 0),
            ("📷  Scanner",       1, -1),
            ("⭐  Sammlung",      0, 1),
            ("🏆  Top-Performer", 0, 2),
        ]
        self._nav_buttons: list[QPushButton] = []
        for i, (label, page_idx, tab_idx) in enumerate(nav_defs):
            btn = QPushButton(label)
            btn.setStyleSheet(self._NAV_ACTIVE if i == 0 else self._NAV_INACTIVE)
            btn.clicked.connect(lambda _=False, p=page_idx, t=tab_idx: self._nav_click(p, t))
            lay.addWidget(btn)
            self._nav_buttons.append(btn)

        lay.addStretch()

        btn_settings = QPushButton("⚙  Einstellungen")
        btn_settings.setStyleSheet(self._NAV_INACTIVE)
        btn_settings.clicked.connect(self._open_api_key_dialog)
        lay.addWidget(btn_settings)

        return sidebar

    def _nav_click(self, page_idx: int, tab_idx: int = -1) -> None:
        self._stack.setCurrentIndex(page_idx)
        if page_idx == 0 and tab_idx >= 0:
            self._catalog_widget.show_page(tab_idx)
        self._set_nav_active_for(page_idx, tab_idx)

    def _set_nav_active(self, btn_idx: int) -> None:
        for i, btn in enumerate(self._nav_buttons):
            btn.setStyleSheet(self._NAV_ACTIVE if i == btn_idx else self._NAV_INACTIVE)

    def _set_nav_active_for(self, page_idx: int, tab_idx: int) -> None:
        mapping = {(0, 0): 0, (1, -1): 1, (0, 1): 2, (0, 2): 3}
        btn_idx = mapping.get((page_idx, tab_idx), 0)
        self._set_nav_active(btn_idx)

    def _build_scanner_page(self, root: QWidget) -> QWidget:
        page = QWidget()
        main_layout = QVBoxLayout(page)

        # --- Input row: camera controls + separator + file load ---
        input_group = QGroupBox("Eingabe")
        input_row = QHBoxLayout(input_group)

        input_row.addWidget(QLabel("Kamera:"))
        self.camera_combo = QComboBox()
        self.camera_combo.setMinimumWidth(160)
        input_row.addWidget(self.camera_combo)

        self.btn_camera_toggle = QPushButton("Kamera starten")
        self.btn_camera_toggle.setMinimumHeight(40)
        input_row.addWidget(self.btn_camera_toggle)

        self.btn_capture_frame = QPushButton("📸 Karte scannen")
        self.btn_capture_frame.setMinimumHeight(48)
        self.btn_capture_frame.setMinimumWidth(160)
        self.btn_capture_frame.setEnabled(False)
        self.btn_capture_frame.setToolTip("Karte einlegen → Knopf drücken → Foto + Scan + Hinzufügen in einem Schritt")
        self.btn_capture_frame.setStyleSheet(
            "QPushButton { font-size: 15px; font-weight: bold; background-color: #2563eb; color: white; border-radius: 6px; border: none; }"
            "QPushButton:disabled { background-color: #cbd5e1; color: #94a3b8; border: none; }"
            "QPushButton:hover:!disabled { background-color: #1d4ed8; }"
            "QPushButton:pressed:!disabled { background-color: #1e40af; }"
        )
        input_row.addWidget(self.btn_capture_frame)

        self.btn_usb_watch = QPushButton("📱 iPhone-Watch")
        self.btn_usb_watch.setMinimumHeight(40)
        self.btn_usb_watch.setToolTip("Ordner überwachen: neues Foto → automatisch scannen")
        self.btn_usb_watch.clicked.connect(self._toggle_usb_watch)
        input_row.addWidget(self.btn_usb_watch)

        sep = QLabel("  \u2014  oder  \u2014")
        sep.setAlignment(Qt.AlignCenter)
        sep.setStyleSheet("color: #888; padding: 0 12px;")
        input_row.addWidget(sep)

        self.btn_load_image = QPushButton("Foto / Bild laden")
        self.btn_load_image.setMinimumHeight(40)
        input_row.addWidget(self.btn_load_image)

        # Language preset buttons
        input_row.addSpacing(20)
        lang_label = QLabel("Sprache:")
        lang_label.setStyleSheet("padding-left: 8px;")
        input_row.addWidget(lang_label)
        self._lang_buttons: dict[str, QPushButton] = {}
        for code, label in [("de", "DE"), ("en", "EN"), ("ja", "JP"), ("zh-Hant", "CHI"), ("ko", "KO"), ("", "Alle")]:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setMinimumHeight(36)
            btn.setMinimumWidth(46)
            btn.clicked.connect(lambda checked, c=code: self._set_language(c))
            input_row.addWidget(btn)
            self._lang_buttons[code] = btn
        self._refresh_lang_buttons()

        input_row.addStretch()
        main_layout.addWidget(input_group)

        # --- Action row: scan + confirm + exports ---
        action_row = QHBoxLayout()
        self.btn_scan = QPushButton("Analyse starten")
        self.btn_confirm = QPushButton("Kandidat best\u00e4tigen")
        self.btn_export_csv = QPushButton("CSV Export")
        self.btn_export_json = QPushButton("JSON Export")
        self.btn_export_xlsx = QPushButton("XLSX Export")

        for button in [
            self.btn_scan,
            self.btn_confirm,
            self.btn_export_csv,
            self.btn_export_json,
            self.btn_export_xlsx,
        ]:
            button.setMinimumHeight(40)
            action_row.addWidget(button)

        self.btn_album_scan = QPushButton("\U0001f4f7  Scan Album")
        self.btn_album_scan.setMinimumHeight(40)
        self.btn_album_scan.setMinimumWidth(120)
        action_row.addWidget(self.btn_album_scan)
        action_row.addStretch()
        main_layout.addLayout(action_row)

        # --- Main grid: preview | candidates | collection ---
        grid = QGridLayout()
        main_layout.addLayout(grid, 1)

        image_group = QGroupBox("Bild / Vorschau")
        image_layout = QVBoxLayout(image_group)

        # Zoom + OCR-zone controls row
        zoom_row = QHBoxLayout()
        zoom_row.setSpacing(4)
        zoom_row.addWidget(QLabel("Zoom:"))
        self._zoom_slider = QSlider(Qt.Horizontal)
        self._zoom_slider.setRange(10, 50)   # 1.0x – 5.0x in 0.1 steps
        self._zoom_slider.setValue(10)
        self._zoom_slider.setFixedWidth(80)
        self._zoom_slider.setToolTip("Zoom 1×–5× (nur im Live-Preview / Kamera-Modus)")
        zoom_row.addWidget(self._zoom_slider)
        self._zoom_label = QLabel("1.0×")
        self._zoom_label.setMinimumWidth(34)
        self._zoom_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        zoom_row.addWidget(self._zoom_label)
        reset_zoom_btn = QPushButton("1:1")
        reset_zoom_btn.setFixedHeight(28)
        reset_zoom_btn.setMinimumWidth(36)
        reset_zoom_btn.setToolTip("Zoom & Pan zurücksetzen")
        reset_zoom_btn.setStyleSheet(
            "QPushButton { font-size: 10px; font-weight: bold; padding: 0 4px; }"
        )
        reset_zoom_btn.clicked.connect(self._reset_zoom)
        zoom_row.addWidget(reset_zoom_btn)
        zoom_row.addStretch(1)
        self._btn_region = QPushButton("OCR-Zone")
        self._btn_region.setFixedHeight(28)
        self._btn_region.setMinimumWidth(76)
        self._btn_region.setCheckable(True)
        self._btn_region.setToolTip("Name-Region ziehen: Rechteck über den Pokémon-Namen ziehen → OCR-Zone speichern")
        self._btn_region.setStyleSheet(
            "QPushButton { font-size: 10px; font-weight: bold; padding: 0 6px; border-radius: 4px; }"
        )
        self._btn_region.clicked.connect(self._toggle_region_mode)
        zoom_row.addWidget(self._btn_region)
        self._btn_clear_zone = QPushButton("Zone \u00d7")
        self._btn_clear_zone.setFixedHeight(28)
        self._btn_clear_zone.setMinimumWidth(60)
        self._btn_clear_zone.setToolTip("Gespeicherte OCR-Zone löschen (zurück zu Standard-Bereich)")
        self._btn_clear_zone.setStyleSheet(
            "QPushButton { font-size: 10px; font-weight: bold; padding: 0 6px;"
            " background: #e74c3c; color: white; border-radius: 4px; border: none; }"
            "QPushButton:hover { background: #c0392b; }"
        )
        self._btn_clear_zone.clicked.connect(self._clear_saved_zone)
        self._btn_clear_zone.setVisible(False)
        zoom_row.addWidget(self._btn_clear_zone)
        image_layout.addLayout(zoom_row)
        self._zoom_slider.valueChanged.connect(self._on_zoom_changed)

        self.image_label = QLabel("Noch kein Bild geladen")
        self.image_label.setAlignment(Qt.AlignCenter)
        self.image_label.setFixedSize(420, 560)
        self.image_label.setScaledContents(False)
        self.image_label.setStyleSheet("border: 1px solid #666; background: #111; color: #ddd;")
        self.image_label.installEventFilter(self)
        image_layout.addWidget(self.image_label)
        grid.addWidget(image_group, 0, 0)

        # Best-match panel above candidate table
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(6)

        # Manual search bar
        search_group = QGroupBox("Manuelle Suche")
        search_row = QHBoxLayout(search_group)
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Kartenname eingeben (z.\u202fB. Pikachu, Glurak, Charizard \u2026)")
        self.search_input.setMinimumHeight(36)
        self.btn_manual_search = QPushButton("Suchen")
        self.btn_manual_search.setMinimumHeight(36)
        self.btn_manual_search.setMinimumWidth(90)
        search_row.addWidget(self.search_input, 1)
        search_row.addWidget(self.btn_manual_search)
        right_layout.addWidget(search_group)

        best_group = QGroupBox("Erkannte Karte")
        best_group.setStyleSheet(
            "QGroupBox { font-weight: bold; font-size: 13px; }"
        )
        best_layout = QGridLayout(best_group)

        self.lbl_best_name = QLabel("–")
        self.lbl_best_name.setStyleSheet(
            "font-size: 18px; font-weight: bold; color: #1a1a1a;"
        )
        self.lbl_best_name.setWordWrap(True)
        best_layout.addWidget(self.lbl_best_name, 0, 0, 1, 2)

        best_layout.addWidget(QLabel("Set:"), 1, 0)
        self.lbl_best_set = QLabel("–")
        best_layout.addWidget(self.lbl_best_set, 1, 1)

        best_layout.addWidget(QLabel("Nummer:"), 2, 0)
        self.lbl_best_number = QLabel("–")
        best_layout.addWidget(self.lbl_best_number, 2, 1)

        best_layout.addWidget(QLabel("Sprache:"), 3, 0)
        self.lbl_best_lang = QLabel("–")
        best_layout.addWidget(self.lbl_best_lang, 3, 1)

        best_layout.addWidget(QLabel("Konfidenz:"), 4, 0)
        self.lbl_best_conf = QLabel("–")
        best_layout.addWidget(self.lbl_best_conf, 4, 1)

        best_layout.addWidget(QLabel("Preis:"), 5, 0)
        self.lbl_best_price = QLabel("–")
        self.lbl_best_price.setStyleSheet("font-size: 15px; font-weight: bold; color: #16a34a;")
        best_layout.addWidget(self.lbl_best_price, 5, 1)

        best_layout.setColumnStretch(1, 1)
        right_layout.addWidget(best_group)

        candidate_group = QGroupBox("Alle Kandidaten")
        candidate_layout = QVBoxLayout(candidate_group)
        self.candidate_table = QTableWidget(0, 7)
        self.candidate_table.setHorizontalHeaderLabels(
            ["Quelle", "Name", "Set", "Nummer", "Sprache", "Konfidenz", "Preis"]
        )
        self.candidate_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.candidate_table.setSelectionMode(QTableWidget.SingleSelection)
        candidate_layout.addWidget(self.candidate_table)
        right_layout.addWidget(candidate_group, 1)

        # Card preview – same size as live camera view, between camera and info panel
        card_preview_group = QGroupBox("Erkannte Karte \u2013 Vorschau")
        card_preview_layout = QVBoxLayout(card_preview_group)
        card_preview_layout.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
        card_preview_layout.setSpacing(4)
        # Set logo strip above the card image
        self.lbl_set_logo = QLabel()
        self.lbl_set_logo.setAlignment(Qt.AlignCenter)
        self.lbl_set_logo.setFixedHeight(40)
        self.lbl_set_logo.setStyleSheet(
            "background: transparent; border: none;"
        )
        card_preview_layout.addWidget(self.lbl_set_logo)
        self.lbl_card_image = QLabel()
        self.lbl_card_image.setAlignment(Qt.AlignCenter)
        self.lbl_card_image.setFixedSize(420, 520)
        self.lbl_card_image.setStyleSheet("border: 1px solid #aaa; background: #222; border-radius: 4px;")
        card_preview_layout.addWidget(self.lbl_card_image)
        grid.addWidget(card_preview_group, 0, 1)
        grid.addWidget(right_panel, 0, 2)

        # Floating toast notification (not in any layout — absolute positioned)
        self._toast_label = QLabel(root)
        self._toast_label.setAlignment(Qt.AlignCenter)
        self._toast_label.setStyleSheet(
            "background-color: #2a7a2a; color: white; border-radius: 6px;"
            " padding: 6px 16px; font-size: 13px; font-weight: bold;"
        )
        self._toast_label.setFixedHeight(36)
        self._toast_label.hide()
        self._toast_timer = QTimer(self)
        self._toast_timer.setSingleShot(True)
        self._toast_timer.timeout.connect(self._toast_label.hide)

        collection_group = QGroupBox("Sammlung")
        collection_layout = QVBoxLayout(collection_group)
        self.collection_table = QTableWidget(0, 8)
        self.collection_table.setHorizontalHeaderLabels(
            ["ID", "Name", "Set", "Nummer", "Sprache", "Menge", "Preis", "W\u00e4hrung"]
        )
        collection_layout.addWidget(self.collection_table)

        # Signals
        self.btn_camera_toggle.clicked.connect(self._toggle_camera)
        self.btn_capture_frame.clicked.connect(self._capture_and_scan)
        self.btn_load_image.clicked.connect(self.load_image)
        self.btn_scan.clicked.connect(self.run_scan)
        self.btn_confirm.clicked.connect(self.confirm_selected_candidate)
        self.btn_export_csv.clicked.connect(self.on_export_csv)
        self.btn_export_json.clicked.connect(self.on_export_json)
        self.btn_export_xlsx.clicked.connect(self.on_export_xlsx)
        self.btn_manual_search.clicked.connect(self._run_manual_search)
        self.search_input.returnPressed.connect(self._run_manual_search)
        self.candidate_table.currentItemChanged.connect(self._on_candidate_row_changed)
        self.candidate_table.cellDoubleClicked.connect(self._on_candidate_double_clicked)
        self.candidate_table.installEventFilter(self)
        self.lbl_card_image.installEventFilter(self)
        self.btn_album_scan.clicked.connect(self._open_album_scan)

        return page

    def _populate_camera_combo(self) -> None:
        self.camera_combo.clear()
        for i in range(4):
            self.camera_combo.addItem(f"Kamera {i}", userData=i)
        # Restore last used camera
        saved = self.settings.last_camera_index
        idx = self.camera_combo.findData(saved)
        if idx >= 0:
            self.camera_combo.setCurrentIndex(idx)
        self.camera_combo.currentIndexChanged.connect(self._on_camera_combo_changed)

    def _on_camera_combo_changed(self) -> None:
        self.settings.last_camera_index = self.camera_combo.currentData() or 0
        self.settings.save()

    def _set_language(self, code: str) -> None:
        self._active_lang = code
        self.settings.preferred_language = code
        self.settings.save()
        self._refresh_lang_buttons()

    def _refresh_lang_buttons(self) -> None:
        for code, btn in self._lang_buttons.items():
            btn.setChecked(code == self._active_lang)
            btn.setStyleSheet(
                "background-color: #3a7bd5; color: white; font-weight: bold;"
                if code == self._active_lang
                else ""
            )

    # ------------------------------------------------------------------
    # Camera controls
    # ------------------------------------------------------------------

    def _toggle_camera(self) -> None:
        if self.camera_service.state.is_running:
            self._stop_camera()
        else:
            self._start_camera()

    def _start_camera(self) -> None:
        idx: int = self.camera_combo.currentData()
        if not self.camera_service.open(idx):
            QMessageBox.warning(
                self, "Kamera",
                f"Kamera {idx} konnte nicht ge\u00f6ffnet werden.\n"
                "Bitte ein anderes Ger\u00e4t w\u00e4hlen.",
            )
            return
        self.btn_camera_toggle.setText("Kamera stoppen")
        self.btn_capture_frame.setEnabled(True)
        self._camera_timer.start(66)  # ~15 fps – reduces main-thread repaint load
        self.status_label.setText(f"Kamera {idx} l\u00e4uft \u2026")
        self.logger.info("Camera %d started", idx)

    def _stop_camera(self) -> None:
        self._camera_timer.stop()
        self.camera_service.close()
        self.btn_camera_toggle.setText("Kamera starten")
        self.btn_capture_frame.setEnabled(False)
        self.image_label.clear()
        self.image_label.setText("Noch kein Bild geladen")
        self.status_label.setText("Kamera gestoppt")
        self.logger.info("Camera stopped")

    _PREVIEW_SIZE = QSize(420, 560)

    def _scale_pixmap(self, pixmap: QPixmap) -> QPixmap:
        return pixmap.scaled(self._PREVIEW_SIZE, Qt.KeepAspectRatio, Qt.SmoothTransformation)

    def _on_zoom_changed(self, value: int) -> None:
        self._zoom_factor = value / 10.0
        self._zoom_label.setText(f"{self._zoom_factor:.1f}\u00d7")
        if self._zoom_factor == 1.0:
            self._pan_x = 0.5
            self._pan_y = 0.5
        can_pan = self._zoom_factor > 1.0 and self.camera_service.state.is_running
        self.image_label.setCursor(Qt.OpenHandCursor if can_pan else Qt.ArrowCursor)

    def _reset_zoom(self) -> None:
        self._zoom_slider.setValue(10)
        self._pan_x = 0.5
        self._pan_y = 0.5

    def _zoom_crop_bgr(self, frame: np.ndarray) -> np.ndarray:
        """Return center-crop of *frame* based on current zoom factor and pan offset."""
        if self._zoom_factor <= 1.0:
            return frame
        h, w = frame.shape[:2]
        crop_w = max(1, int(w / self._zoom_factor))
        crop_h = max(1, int(h / self._zoom_factor))
        x0 = int((w - crop_w) * self._pan_x)
        y0 = int((h - crop_h) * self._pan_y)
        x0 = max(0, min(x0, w - crop_w))
        y0 = max(0, min(y0, h - crop_h))
        return frame[y0:y0 + crop_h, x0:x0 + crop_w]

    def _on_camera_frame(self) -> None:
        frame = self.camera_service.grab_frame()
        if frame is None:
            return
        frame = self._zoom_crop_bgr(frame)
        # Pre-scale in numpy (cv2 is much faster than Qt's SmoothTransformation
        # on large camera frames and reduces main-thread work significantly)
        target_w, target_h = self.image_label.width(), self.image_label.height()
        fh, fw = frame.shape[:2]
        scale = min(target_w / fw, target_h / fh)
        if scale < 0.99:
            frame = cv2.resize(frame, (int(fw * scale), int(fh * scale)),
                               interpolation=cv2.INTER_LINEAR)
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb.shape
        # Use contiguous array to avoid QImage data issues
        rgb = np.ascontiguousarray(rgb)
        qimg = QImage(rgb.data, w, h, ch * w, QImage.Format_RGB888)
        pixmap = QPixmap.fromImage(qimg)
        if self._last_ocr_raw:
            self._draw_ocr_overlay(pixmap)
        self.image_label.setPixmap(pixmap)

    def _draw_ocr_overlay(self, pixmap: QPixmap) -> None:
        """Draw the last OCR raw text + best candidate name as overlay on *pixmap* (in-place)."""
        line1 = f"OCR: {self._last_ocr_raw}"
        line2 = f"\u2192 {self.current_candidates[0].name}" if self.current_candidates else "\u2192 kein Treffer"
        cache_key = f"{line1}\n{line2}"

        if self._ocr_overlay_cache is not None and self._ocr_overlay_cache_key == cache_key:
            painter = QPainter(pixmap)
            painter.drawPixmap(0, 0, self._ocr_overlay_cache)
            painter.end()
            return

        overlay = QPixmap(pixmap.width(), pixmap.height())
        overlay.fill(Qt.transparent)
        painter = QPainter(overlay)
        font = QFont("Consolas", 10)
        font.setBold(True)
        painter.setFont(font)
        fm = painter.fontMetrics()

        pad = 6
        line_h = fm.height()
        box_w = max(fm.horizontalAdvance(line1), fm.horizontalAdvance(line2)) + pad * 2
        box_h = line_h * 2 + pad * 3
        x = 4
        y = pixmap.height() - box_h - 4

        # semi-transparent dark background
        painter.fillRect(x, y, box_w, box_h, QColor(0, 0, 0, 160))
        painter.setPen(QColor(255, 220, 0))
        painter.drawText(x + pad, y + pad + fm.ascent(), line1)
        painter.setPen(QColor(100, 255, 100))
        painter.drawText(x + pad, y + pad + line_h + pad + fm.ascent(), line2)
        painter.end()

        self._ocr_overlay_cache = overlay
        self._ocr_overlay_cache_key = cache_key

        final_painter = QPainter(pixmap)
        final_painter.drawPixmap(0, 0, overlay)
        final_painter.end()

    def _capture_frame(self) -> None:
        frame = self.camera_service.grab_frame()
        if frame is None:
            QMessageBox.warning(self, "Kamera", "Frame konnte nicht aufgenommen werden.")
            return
        frame = self._zoom_crop_bgr(frame)
        tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False, prefix="poke_scan_")
        path = tmp.name
        tmp.close()
        cv2.imwrite(path, frame)
        self._stop_camera()
        self.current_image_path = path
        self.image_label.setPixmap(self._scale_pixmap(QPixmap(path)))
        self.status_label.setText("Foto aufgenommen \u2013 Scan starten")
        self.logger.info("Frame captured (zoom=%.1f\u00d7) to %s", self._zoom_factor, path)

    def _capture_and_scan(self) -> None:
        """One-click: grab frame (camera keeps running) → scan → auto-add best match."""
        if self._scan_worker is not None and self._scan_worker.isRunning():
            return
        frame = self.camera_service.grab_frame()
        if frame is None:
            self.status_label.setText("Kein Frame – Kamera prüfen")
            return
        frame = self._zoom_crop_bgr(frame)
        tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False, prefix="poke_scan_")
        path = tmp.name
        tmp.close()
        cv2.imwrite(path, frame)
        self.current_image_path = path
        # Freeze preview with captured frame; camera timer keeps running in background
        self.image_label.setPixmap(self._scale_pixmap(QPixmap(path)))
        self.run_scan()

    # ------------------------------------------------------------------
    # File load
    # ------------------------------------------------------------------

    def load_image(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Bild laden",
            "",
            "Images (*.png *.jpg *.jpeg *.webp *.bmp)",
        )
        if not file_path:
            return
        if self.camera_service.state.is_running:
            self._stop_camera()
        self.current_image_path = file_path
        pixmap = QPixmap(file_path)
        if pixmap.isNull():
            self.status_label.setText("Bild konnte nicht geladen werden")
            return
        scaled = self._scale_pixmap(pixmap)
        self._draw_zone_overlay_on_pixmap(scaled)
        self.image_label.setPixmap(scaled)
        self.status_label.setText(f"Bild geladen: {Path(file_path).name}")
        self.logger.info("Loaded image %s", file_path)

    # ------------------------------------------------------------------
    # Scan + collection
    # ------------------------------------------------------------------

    def run_scan(self) -> None:
        if not self.current_image_path:
            QMessageBox.information(self, "Hinweis", "Bitte zuerst ein Bild laden oder ein Foto aufnehmen.")
            return
        if self._scan_worker is not None and self._scan_worker.isRunning():
            return
        self.btn_scan.setEnabled(False)
        self.btn_scan.setText("Scanne \u2026")
        self.status_label.setText("Karte wird erkannt \u2026 (erster Start l\u00e4dt OCR-Modell, kann etwas dauern)")
        self._clear_best_match()
        self._last_ocr_raw = ""
        self.candidate_table.setRowCount(0)

        self._scan_worker = ScanWorker(
            self.pipeline, self.current_image_path,
            language=self._active_lang, zone=self._get_saved_zone(),
        )
        self._scan_worker.finished.connect(self._on_scan_finished)
        self._scan_worker.status_update.connect(self.status_label.setText)
        self._scan_worker.error.connect(self._on_scan_error)
        self._scan_worker.start()

    def _on_scan_finished(self, candidates: list[CardCandidate], warp_path: str, raw_ocr: str = "") -> None:
        self.btn_scan.setEnabled(True)
        self.btn_scan.setText("Analyse starten")
        self.current_candidates = candidates
        self._last_ocr_raw = raw_ocr
        # Show warped card preview if detection succeeded
        if warp_path:
            pixmap = QPixmap(warp_path)
            if not pixmap.isNull():
                scaled = self._scale_pixmap(pixmap)
                self._draw_zone_overlay_on_pixmap(scaled)
                self.image_label.setPixmap(scaled)
                self.image_label.setToolTip("Erkannter Kartenausschnitt (entzerrt)")
        if candidates:
            self._fill_candidate_table(candidates)
            warp_note = " (Karte erkannt)" if warp_path else ""
            self.status_label.setText(f"{len(candidates)} Treffer gefunden{warp_note}")
            self._save_to_catalog(candidates)
        else:
            self.status_label.setText("Keine Karte erkannt \u2013 Bild pr\u00fcfen oder erneut scannen")
        self.logger.info("Scan finished: %d candidates for %s", len(candidates), self.current_image_path)

    def _on_scan_error(self, message: str) -> None:
        self.btn_scan.setEnabled(True)
        self.btn_scan.setText("Analyse starten")
        self.status_label.setText(f"Fehler beim Scan: {message}")
        self.logger.error("Scan error: %s", message)

    # ------------------------------------------------------------------
    # Manual search
    # ------------------------------------------------------------------

    def _run_manual_search(self) -> None:
        query = self.search_input.text().strip()
        if not query:
            return
        if self._manual_search_worker is not None and self._manual_search_worker.isRunning():
            return
        self.btn_manual_search.setEnabled(False)
        self.btn_manual_search.setText("Suche \u2026")

        # Detect German→English translation so we can show a hint in the status bar.
        translated = translate_de_to_en_fuzzy(query)
        if translated and translated.lower() != query.lower():
            self._manual_search_translated: str | None = translated
            self.status_label.setText(
                f"Suche nach \u201e{query}\u201c \u2192 {translated} \u2026"
            )
        else:
            self._manual_search_translated = None
            self.status_label.setText(f"Suche nach \u201e{query}\u201c \u2026")

        self._clear_best_match()
        self.candidate_table.setRowCount(0)

        self._manual_search_worker = ManualSearchWorker(self.pipeline, query, language=self._active_lang)
        self._manual_search_worker.finished.connect(self._on_manual_search_finished)
        self._manual_search_worker.error.connect(self._on_manual_search_error)
        self._manual_search_worker.start()

    def _on_manual_search_finished(self, candidates: list[CardCandidate]) -> None:
        self.btn_manual_search.setEnabled(True)
        self.btn_manual_search.setText("Suchen")
        self.current_candidates = candidates
        raw_query = self.search_input.text().strip()
        translated = getattr(self, "_manual_search_translated", None)
        display = f"\u201e{raw_query}\u201c \u2192 {translated}" if translated else f"\u201e{raw_query}\u201c"
        if candidates:
            self._fill_candidate_table(candidates)
            self.status_label.setText(f"{len(candidates)} Treffer f\u00fcr {display}")
            self._save_to_catalog(candidates)
        else:
            self.status_label.setText(f"Keine Treffer f\u00fcr {display} \u2013 anderen Namen versuchen")
        self.logger.info("Manual search finished: %d candidates for %r", len(candidates), raw_query)

    def _on_manual_search_error(self, message: str) -> None:
        self.btn_manual_search.setEnabled(True)
        self.btn_manual_search.setText("Suchen")
        self.status_label.setText(f"Suchfehler: {message}")
        self.logger.error("Manual search error: %s", message)

    def _save_to_catalog(self, candidates: list[CardCandidate]) -> None:
        """Fire-and-forget: save candidates + download images in background."""
        worker = CatalogSaveWorker(self.catalog_repo, candidates)
        self._catalog_save_workers.append(worker)
        worker.finished.connect(
            lambda w=worker: self._catalog_save_workers.remove(w)
            if w in self._catalog_save_workers else None
        )
        worker.start()

    def _open_catalog(self) -> None:
        self._nav_click(0, 0)

    def _open_album_scan(self) -> None:
        dlg = AlbumScanDialog(
            self.pipeline,
            self.collection_service,
            language=self._active_lang,
            correction_repo=self.correction_repo,
            catalog_repo=self.catalog_repo,
            parent=self,
        )
        dlg.exec()

    def confirm_selected_candidate(self) -> None:
        row = self.candidate_table.currentRow()
        if row < 0 or row >= len(self.current_candidates):
            QMessageBox.information(self, "Hinweis", "Bitte einen Kandidaten ausw\u00e4hlen.")
            return
        candidate = self._with_scan_language(self.current_candidates[row])
        self.collection_service.confirm_candidate(candidate, image_path=self.current_image_path)
        # Record price snapshot for history chart
        if candidate.best_price and candidate.notes and candidate.notes.startswith("ID: "):
            _api_id = candidate.notes[4:].strip()
            self.catalog_repo.record_price_snapshot(
                _api_id, candidate.best_price, candidate.price_currency or "USD"
            )
        self.status_label.setText(f"Best\u00e4tigt: {candidate.name} \u2014 im Katalog gespeichert")
        self.logger.info("Candidate confirmed: %s", candidate.name)

    def refresh_collection(self) -> None:
        """Retained for export compatibility. Collection is displayed in Katalog dialog."""
        rows = self.collection_service.list_entries()
        # update collection_table if visible (kept for export)
        if not hasattr(self, 'collection_table'):
            return
        self.collection_table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            values = [
                row.get("id"),
                row.get("name"),
                row.get("set_name"),
                row.get("card_number"),
                row.get("language"),
                row.get("quantity"),
                row.get("last_price"),
                row.get("price_currency"),
            ]
            for col_index, value in enumerate(values):
                self.collection_table.setItem(
                    row_index, col_index,
                    QTableWidgetItem("" if value is None else str(value)),
                )
        if not self._collection_cols_sized:
            self.collection_table.resizeColumnsToContents()
            self._collection_cols_sized = True

    def _price_label(self, candidate: CardCandidate) -> str:
        """Return a formatted price string, prefixed with 'EN Ref:' when the candidate
        language does not match the currently selected scan language."""
        if candidate.best_price is None:
            return "–"
        currency = candidate.price_currency or "USD"
        source_tag = f" ({candidate.price_source})" if candidate.price_source else ""
        price_str = f"{candidate.best_price:.2f} {currency}{source_tag}"
        scan_lang = self._active_lang
        # Only flag as reference when a non-EN language is selected and the card
        # returned is in English (language mismatch).
        if scan_lang and scan_lang not in ("", "en"):
            if not CandidateMatcher.lang_matches(candidate.language or "en", scan_lang):
                return f"EN Ref: {price_str}"
        return price_str

    @staticmethod
    def _conf_color(confidence: float) -> QColor:
        """Return a background color reflecting OCR/match confidence."""
        if confidence >= 0.75:
            return QColor("#dcfce7")  # light green
        if confidence >= 0.50:
            return QColor("#fef9c3")  # light yellow
        return QColor("#fee2e2")      # light red

    def _fill_candidate_table(self, candidates: list[CardCandidate]) -> None:
        candidates = candidates[:15]  # cap at 15 rows — extra candidates add no value in UI
        self.candidate_table.setUpdatesEnabled(False)
        self.candidate_table.blockSignals(True)
        try:
            self.candidate_table.setRowCount(len(candidates))
            for row_index, candidate in enumerate(candidates):
                conf_pct = int(candidate.confidence * 100)
                values = [
                    candidate.source,
                    candidate.name,
                    candidate.set_name,
                    candidate.card_number,
                    candidate.language,
                    f"{conf_pct} %",
                    self._price_label(candidate),
                ]
                conf_bg = self._conf_color(candidate.confidence)
                for col_index, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    if col_index == 5:  # Konfidenz column
                        item.setBackground(conf_bg)
                    self.candidate_table.setItem(row_index, col_index, item)
        finally:
            self.candidate_table.blockSignals(False)
            self.candidate_table.setUpdatesEnabled(True)
        self.candidate_table.resizeColumnsToContents()
        if candidates:
            self.candidate_table.selectRow(0)
            self._update_best_match(candidates[0])
        else:
            self._clear_best_match()

    def _update_best_match(self, candidate: CardCandidate) -> None:
        self.lbl_best_name.setText(candidate.name)
        self.lbl_best_set.setText(candidate.set_name or "–")
        self.lbl_best_number.setText(candidate.card_number or "–")
        self.lbl_best_lang.setText(candidate.language or "–")
        conf_pct = int(candidate.confidence * 100)
        self.lbl_best_conf.setText(f"{conf_pct} %")
        conf_style = (
            "color: #15803d; font-weight: bold;" if candidate.confidence >= 0.75
            else "color: #b45309; font-weight: bold;" if candidate.confidence >= 0.50
            else "color: #dc2626; font-weight: bold;"
        )
        self.lbl_best_conf.setStyleSheet(conf_style)
        self.lbl_best_price.setText(self._price_label(candidate))
        # Show set logo in preview panel
        self.lbl_set_logo.clear()
        if candidate.set_name:
            safe = (
                candidate.set_name
                .replace("/", "_").replace("\\", "_").replace(" ", "_")
            )
            logo_path = CATALOG_IMAGES_DIR / f"logo_{safe}.png"
            if logo_path.exists():
                px = QPixmap(str(logo_path)).scaledToHeight(36, Qt.SmoothTransformation)
                if not px.isNull():
                    self.lbl_set_logo.setPixmap(px)
                else:
                    self.lbl_set_logo.setText(candidate.set_name)
            else:
                self.lbl_set_logo.setText(candidate.set_name)
                self.lbl_set_logo.setStyleSheet(
                    "background: transparent; border: none;"
                    " font-size: 11px; color: #aaa;"
                )
        # Load card image — prefer local catalog file, fall back to HTTP download
        self.lbl_card_image.setText("Lade\u2026")
        self.lbl_card_image.setPixmap(QPixmap())
        img = candidate.image_url or ""
        # Try local catalog first (no network)
        pix = load_card_pixmap(candidate.api_id, stored_hint=img if not img.startswith("http") else None)
        if pix and not pix.isNull():
            self.lbl_card_image.setPixmap(
                pix.scaled(self.lbl_card_image.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )
            self.lbl_card_image.setText("")
        elif img and img.startswith("http") and candidate.api_id:
            worker = CardImageDownloadWorker(candidate.api_id, img)
            self._image_dl_workers.append(worker)
            worker.done.connect(self._on_card_image_loaded)
            worker.done.connect(lambda _p, w=worker: self._image_dl_workers.remove(w) if w in self._image_dl_workers else None)
            worker.start()
        else:
            self.lbl_card_image.setText("Kein Bild")

    def _on_card_image_loaded(self, local_path: str) -> None:
        if not local_path:
            self.lbl_card_image.setText("Bild nicht\nverfügbar")
            return
        pm = QPixmap(local_path)
        if pm.isNull():
            self.lbl_card_image.setText("Bild nicht\nverfügbar")
        else:
            self.lbl_card_image.setPixmap(
                pm.scaled(self.lbl_card_image.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )

    def _on_candidate_row_changed(self) -> None:
        row = self.candidate_table.currentRow()
        if 0 <= row < len(self.current_candidates):
            self._update_best_match(self.current_candidates[row])

    # ------------------------------------------------------------------
    # Double-click → add to collection
    # ------------------------------------------------------------------

    def eventFilter(self, obj: object, event: QEvent) -> bool:
        # Space key on candidate table → confirm selected candidate
        if (hasattr(self, "candidate_table")
                and obj is self.candidate_table
                and event.type() == QEvent.Type.KeyPress
                and event.key() == Qt.Key.Key_Space):
            self.confirm_selected_candidate()
            return True

        if (hasattr(self, "lbl_card_image")
                and obj is self.lbl_card_image
                and event.type() == QEvent.Type.MouseButtonDblClick):
            row = self.candidate_table.currentRow()
            if 0 <= row < len(self.current_candidates):
                self._add_candidate_to_collection(self.current_candidates[row])
            return True

        # Region-draw mode takes priority over pan
        if obj is self.image_label and self._region_mode:
            etype = event.type()
            if etype == QEvent.Type.MouseButtonPress and event.button() == Qt.LeftButton:
                self._region_start = event.pos()
                if self._rubber_band is None:
                    self._rubber_band = QRubberBand(QRubberBand.Rectangle, self.image_label)
                self._rubber_band.setGeometry(QRect(self._region_start, self._region_start))
                self._rubber_band.show()
                return True
            if etype == QEvent.Type.MouseMove and self._region_start is not None:
                self._rubber_band.setGeometry(QRect(self._region_start, event.pos()).normalized())
                return True
            if etype == QEvent.Type.MouseButtonRelease and self._region_start is not None:
                rect = QRect(self._region_start, event.pos()).normalized()
                self._rubber_band.hide()
                self._region_start = None
                self._btn_region.setChecked(False)
                self._region_mode = False
                self.image_label.setCursor(Qt.ArrowCursor)
                if rect.width() > 8 and rect.height() > 8:
                    self._ocr_on_region(rect)
                return True

        # Pan on image_label via mouse drag (only when camera is live and zoom > 1)
        if obj is self.image_label:
            etype = event.type()
            if (etype == QEvent.Type.MouseButtonPress
                    and event.button() == Qt.LeftButton
                    and self.camera_service.state.is_running
                    and self._zoom_factor > 1.0):
                self._drag_last = event.pos()
                self.image_label.setCursor(Qt.ClosedHandCursor)
                return True
            if (etype == QEvent.Type.MouseMove
                    and self._drag_last is not None
                    and self.camera_service.state.is_running):
                delta = event.pos() - self._drag_last
                self._drag_last = event.pos()
                self._pan_x = max(0.0, min(1.0, self._pan_x - delta.x() / self.image_label.width()))
                self._pan_y = max(0.0, min(1.0, self._pan_y - delta.y() / self.image_label.height()))
                return True
            if etype == QEvent.Type.MouseButtonRelease:
                self._drag_last = None
                if self._zoom_factor > 1.0 and self.camera_service.state.is_running:
                    self.image_label.setCursor(Qt.OpenHandCursor)
                else:
                    self.image_label.setCursor(Qt.ArrowCursor)
                return True

        return super().eventFilter(obj, event)

    # ------------------------------------------------------------------
    # Region-draw OCR
    # ------------------------------------------------------------------

    def _toggle_region_mode(self, checked: bool) -> None:
        self._region_mode = checked
        if checked:
            self.image_label.setCursor(Qt.CrossCursor)
            self.status_label.setText("Region ziehen: Rechteck über den Pokémon-Namen aufziehen")
        else:
            self.image_label.setCursor(Qt.ArrowCursor)
            if self._rubber_band:
                self._rubber_band.hide()

    def _ocr_on_region(self, label_rect) -> None:
        """Save the drawn region as the persistent OCR name-zone and re-scan."""
        if not self.current_image_path:
            self.status_label.setText("Kein Bild geladen – zuerst ein Foto aufnehmen")
            return
        pm = self.image_label.pixmap()
        if pm is None or pm.isNull():
            return
        lw, lh = self.image_label.width(), self.image_label.height()
        pw, ph = pm.width(), pm.height()
        ox = (lw - pw) // 2
        oy = (lh - ph) // 2
        if pw == 0 or ph == 0:
            return
        # Normalise to 0–1 fractions of the displayed image
        x1_rel = max(0.0, min(1.0, (label_rect.left() - ox) / pw))
        y1_rel = max(0.0, min(1.0, (label_rect.top() - oy) / ph))
        x2_rel = max(0.0, min(1.0, (label_rect.right() - ox) / pw))
        y2_rel = max(0.0, min(1.0, (label_rect.bottom() - oy) / ph))
        if x2_rel <= x1_rel or y2_rel <= y1_rel:
            return
        # Persist zone to settings
        self.settings.name_zone_x1 = round(x1_rel, 4)
        self.settings.name_zone_y1 = round(y1_rel, 4)
        self.settings.name_zone_x2 = round(x2_rel, 4)
        self.settings.name_zone_y2 = round(y2_rel, 4)
        self.settings.name_zone_custom = True
        self.settings.save()
        self._update_zone_ui()
        self._show_zone_preview()
        self.status_label.setText(
            f"OCR-Zone gespeichert "
            f"(x {x1_rel:.0%}–{x2_rel:.0%} / y {y1_rel:.0%}–{y2_rel:.0%}) – Scan läuft …"
        )
        self.run_scan()

    # ------------------------------------------------------------------
    # Saved OCR zone helpers
    # ------------------------------------------------------------------

    def _get_saved_zone(self) -> tuple[float, float, float, float] | None:
        """Return the saved zone tuple or None when using defaults."""
        if self.settings.name_zone_custom:
            return (
                self.settings.name_zone_x1,
                self.settings.name_zone_y1,
                self.settings.name_zone_x2,
                self.settings.name_zone_y2,
            )
        return None

    def _clear_saved_zone(self) -> None:
        self.settings.name_zone_custom = False
        self.settings.save()
        self._update_zone_ui()
        self._show_zone_preview()
        self.status_label.setText("OCR-Zone zurückgesetzt – Standard-Bereich wird verwendet")

    def _update_zone_ui(self) -> None:
        """Sync OCR-Zone button style and Zone-X visibility to current zone state."""
        if not hasattr(self, "_btn_clear_zone"):
            return
        _btn_base = (
            "QPushButton { font-size: 10px; font-weight: bold;"
            " padding: 0 6px; border-radius: 4px; }"
        )
        if self.settings.name_zone_custom:
            self._btn_region.setStyleSheet(
                "QPushButton { font-size: 10px; font-weight: bold; padding: 0 6px;"
                " border-radius: 4px; background: #2563eb; color: white; border: none; }"
                "QPushButton:hover { background: #1d4ed8; }"
            )
            self._btn_region.setToolTip("OCR-Zone kalibriert – klicken zum Neu-Kalibrieren")
            self._btn_clear_zone.setVisible(True)
        else:
            self._btn_region.setStyleSheet(_btn_base)
            self._btn_region.setToolTip(
                "Name-Region ziehen: Rechteck über den Pokémon-Namen ziehen → OCR-Zone speichern"
            )
            self._btn_clear_zone.setVisible(False)

    def _draw_zone_overlay_on_pixmap(self, pixmap: QPixmap) -> None:
        """Draw the saved OCR zone as a cyan rectangle on *pixmap* (in-place)."""
        if not self.settings.name_zone_custom:
            return
        zone = self._get_saved_zone()
        if zone is None:
            return
        pw, ph = pixmap.width(), pixmap.height()
        rx = int(zone[0] * pw)
        ry = int(zone[1] * ph)
        rw = int((zone[2] - zone[0]) * pw)
        rh = int((zone[3] - zone[1]) * ph)
        if rw <= 0 or rh <= 0:
            return
        painter = QPainter(pixmap)
        pen = QPen(QColor(0, 200, 255), 2)
        painter.setPen(pen)
        painter.fillRect(rx, ry, rw, rh, QColor(0, 200, 255, 35))
        painter.drawRect(rx, ry, rw, rh)
        painter.end()

    def _show_zone_preview(self) -> None:
        """Redraw the current image_label pixmap with (or without) the zone overlay."""
        pm = self.image_label.pixmap()
        if pm is None or pm.isNull():
            return
        # Re-load from file so we have a clean copy without the old overlay
        if self.current_image_path and Path(self.current_image_path).exists():
            base = QPixmap(self.current_image_path)
            if not base.isNull():
                pm = self._scale_pixmap(base)
        else:
            pm = pm.copy()
        self._draw_zone_overlay_on_pixmap(pm)
        self.image_label.setPixmap(pm)

    # ------------------------------------------------------------------
    # USB / folder watch
    # ------------------------------------------------------------------

    def _toggle_usb_watch(self) -> None:
        if self._is_watching:
            self._stop_usb_watch()
        else:
            self._start_usb_watch()

    def _start_usb_watch(self) -> None:
        folder = self._watch_folder
        if not folder:
            folder, _ = QFileDialog.getExistingDirectory(
                self,
                "Ordner wählen (z. B. iPhone DCIM-Import)",
                str(Path.home() / "Pictures"),
            ), None
            if isinstance(folder, tuple):
                folder = folder[0]
            if not folder:
                return
            self._watch_folder = folder

        if not Path(self._watch_folder).exists():
            QMessageBox.warning(self, "Ordner nicht gefunden",
                                f"Ordner nicht erreichbar:\n{self._watch_folder}\n\nBitte anderen Ordner wählen.")
            self._watch_folder = None
            return

        # Snapshot existing files so we only react to NEW ones
        self._watched_files = {
            str(p) for p in Path(self._watch_folder).iterdir()
            if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".heic", ".webp"}
        }
        self._is_watching = True
        self._fs_watcher.addPath(self._watch_folder)
        self._watch_timer.start()
        self.btn_usb_watch.setText("📱 Watch STOP")
        self.btn_usb_watch.setStyleSheet("background-color: #dc2626; color: white; font-weight: bold; border: none; border-radius: 6px;")
        self.status_label.setText(f"Watch aktiv: {self._watch_folder}")
        self.logger.info("USB watch started on %s", self._watch_folder)

    def _stop_usb_watch(self) -> None:
        self._watch_timer.stop()
        for p in self._fs_watcher.directories():
            self._fs_watcher.removePath(p)
        self._is_watching = False
        self.btn_usb_watch.setText("📱 iPhone-Watch")
        self.btn_usb_watch.setStyleSheet("")
        self.status_label.setText("Watch gestoppt")
        self.logger.info("USB watch stopped")

    def _poll_watch_folder(self) -> None:
        if not self._watch_folder or not Path(self._watch_folder).exists():
            return
        try:
            current = {
                str(p) for p in Path(self._watch_folder).iterdir()
                if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".heic", ".webp"}
            }
        except OSError:
            return
        new_files = sorted(current - self._watched_files)
        self._watched_files = current
        if not new_files:
            return
        newest = new_files[-1]
        self.logger.info("USB watch: new file detected %s", newest)
        if self.camera_service.state.is_running:
            self._stop_camera()
        self.current_image_path = newest
        pixmap = QPixmap(newest)
        if pixmap.isNull():
            self.status_label.setText(f"Neues Foto erkannt (Bild nicht lesbar): {Path(newest).name}")
            return
        self.image_label.setPixmap(self._scale_pixmap(pixmap))
        self.status_label.setText(f"Neues Foto: {Path(newest).name} – Scan startet …")
        self.run_scan()

    def _on_candidate_double_clicked(self, row: int, _col: int) -> None:
        if 0 <= row < len(self.current_candidates):
            self._add_candidate_to_collection(self.current_candidates[row])

    # Map UI language button code → language stored in the collection entry.
    # Overrides the API-returned language (which is almost always 'en') so
    # that a physical German/Japanese/Chinese card is correctly labelled.
    _SCAN_LANG_OVERRIDE: dict[str, str] = {
        "de": "de",
        "zh-Hant": "zh-Hans",
        "ja": "ja",
        "ko": "ko",
    }

    def _with_scan_language(self, candidate: "CardCandidate") -> "CardCandidate":
        """Return a copy of *candidate* with language set to the active scan language."""
        from dataclasses import replace as _replace
        override = self._SCAN_LANG_OVERRIDE.get(self._active_lang)
        if override is None:
            return candidate  # "" (Alle) or "en" — keep API value
        return _replace(candidate, language=override)

    def _auto_add_to_collection(self, candidate: "CardCandidate") -> None:
        """Auto-add without confirmation dialogs. Silently increments quantity if duplicate."""
        candidate = self._with_scan_language(candidate)
        self.collection_service.confirm_candidate(candidate, image_path=self.current_image_path)
        if candidate.best_price and candidate.notes and candidate.notes.startswith("ID: "):
            _api_id = candidate.notes[4:].strip()
            self.catalog_repo.record_price_snapshot(
                _api_id, candidate.best_price, candidate.price_currency or "USD"
            )
        self._show_toast(f"✓  {candidate.name} hinzugefügt")
        self.status_label.setText(
            f"✅ {candidate.name} ({candidate.set_name}) – nächste Karte einlegen und Knopf drücken"
        )
        self.logger.info("Auto-added to collection: %s", candidate.name)

    def _add_candidate_to_collection(self, candidate: "CardCandidate") -> None:
        candidate = self._with_scan_language(candidate)
        existing = self.collection_service.find_by_candidate(candidate)
        if existing:
            qty = existing.get("quantity", 1)
            reply = QMessageBox.question(
                self,
                "Bereits vorhanden",
                f"\u201e{candidate.name}\u201c ist bereits {qty}\u00d7 in deiner Sammlung.\n"
                "Trotzdem nochmal hinzuf\u00fcgen?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        # Ask for card condition
        _CONDITIONS = ["M", "NM", "LP", "MP", "HP"]
        cond_dlg = QDialog(self)
        cond_dlg.setWindowTitle("Kartenzustand")
        cond_dlg.setFixedSize(260, 110)
        cond_lay = QVBoxLayout(cond_dlg)
        cond_lay.setContentsMargins(12, 12, 12, 12)
        cond_lay.setSpacing(8)
        cond_lay.addWidget(QLabel("Zustand der physischen Karte:"))
        cond_combo = QComboBox()
        cond_combo.addItems(_CONDITIONS)
        cond_combo.setCurrentText("NM")
        cond_lay.addWidget(cond_combo)
        btn_row = QHBoxLayout()
        ok_btn = QPushButton("Hinzuf\u00fcgen")
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(cond_dlg.accept)
        cancel_btn = QPushButton("Abbrechen")
        cancel_btn.clicked.connect(cond_dlg.reject)
        btn_row.addWidget(ok_btn)
        btn_row.addWidget(cancel_btn)
        cond_lay.addLayout(btn_row)
        if cond_dlg.exec() != QDialog.DialogCode.Accepted:
            return
        chosen_condition = cond_combo.currentText()
        self.collection_service.confirm_candidate(
            candidate, image_path=self.current_image_path, condition=chosen_condition
        )
        # Record price snapshot for history chart
        if candidate.best_price and candidate.notes and candidate.notes.startswith("ID: "):
            _api_id = candidate.notes[4:].strip()
            self.catalog_repo.record_price_snapshot(
                _api_id, candidate.best_price, candidate.price_currency or "USD"
            )
        self._show_toast(f"\u2713  {candidate.name} zur Sammlung hinzugef\u00fcgt")
        self.logger.info("Added to collection via double-click: %s", candidate.name)

    def _show_toast(self, text: str) -> None:
        self._toast_label.setText(text)
        self._toast_label.adjustSize()
        tbl = self.candidate_table
        root = self.centralWidget()
        pos: QPoint = tbl.mapTo(root, QPoint(0, 0))
        toast_w = min(max(320, self._toast_label.sizeHint().width() + 32), tbl.width() - 20)
        toast_x = pos.x() + (tbl.width() - toast_w) // 2
        # Position at the bottom of the candidate table area (overlay style)
        toast_y = pos.y() + tbl.height() - 44
        self._toast_label.setFixedWidth(toast_w)
        self._toast_label.move(toast_x, toast_y)
        self._toast_label.raise_()
        self._toast_label.show()
        self._toast_timer.start(2500)

    def _clear_best_match(self) -> None:
        for lbl in [
            self.lbl_best_name, self.lbl_best_set, self.lbl_best_number,
            self.lbl_best_lang, self.lbl_best_conf, self.lbl_best_price,
        ]:
            lbl.setText("–")
        self.lbl_card_image.setPixmap(QPixmap())
        self.lbl_card_image.setText("")
    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    def _export_rows(self, format_name: str) -> None:
        rows = self.collection_service.list_entries()
        if not rows:
            QMessageBox.information(self, "Hinweis", "Keine Eintr\u00e4ge zum Exportieren vorhanden.")
            return
        EXPORT_DIR.mkdir(parents=True, exist_ok=True)
        if format_name == "csv":
            target = export_csv(rows, EXPORT_DIR / "collection_export.csv")
        elif format_name == "json":
            target = export_json(rows, EXPORT_DIR / "collection_export.json")
        elif format_name == "xlsx":
            target = export_xlsx(rows, EXPORT_DIR / "collection_export.xlsx")
        else:
            raise ValueError(f"Unsupported export format: {format_name}")
        self.status_label.setText(f"Export erstellt: {target.name}")
        self.logger.info("Export created: %s", target)

    def on_export_csv(self) -> None:
        self._export_rows("csv")

    def on_export_json(self) -> None:
        self._export_rows("json")

    def on_export_xlsx(self) -> None:
        self._export_rows("xlsx")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def closeEvent(self, event) -> None:
        if hasattr(self, '_catalog_widget'):
            self._catalog_widget.stop_workers()
        self._stop_camera()
        # Stop all background workers gracefully before exit
        for worker in [self._scan_worker, self._manual_search_worker, self._ocr_warmup_worker]:
            if worker is not None and worker.isRunning():
                worker.quit()
                worker.wait(2000)
        for worker in list(self._image_dl_workers + self._catalog_save_workers):
            if worker.isRunning():
                worker.quit()
                worker.wait(2000)
        _cleanup_scan_photos(self.collection_service.repository)
        super().closeEvent(event)
