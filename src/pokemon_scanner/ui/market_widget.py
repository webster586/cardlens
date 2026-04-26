"""market_widget.py — Markt-Reiter mit Kauf / Verkauf / Historie."""
from __future__ import annotations

import datetime as dt
import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, Signal, QThread, QTimer

if TYPE_CHECKING:
    from src.pokemon_scanner.db.catalog_repository import CatalogRepository
from PySide6.QtGui import QPixmap, QPixmapCache
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from src.pokemon_scanner.core.paths import RUNTIME_DIR
from src.pokemon_scanner.db.repositories import CollectionRepository
from src.pokemon_scanner.ui.image_cache import resolve_card_image

_LOG = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _px(path: str | None, w: int, h: int) -> QPixmap | None:
    if not path:
        return None
    key = f"mkt_{path}:{w}x{h}"
    pm = QPixmapCache.find(key)
    if pm and not pm.isNull():
        return pm
    raw = QPixmap(path)
    if raw.isNull():
        return None
    pm = raw.scaled(w, h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    QPixmapCache.insert(key, pm)
    return pm


def _price_str(price: float | None, currency: str | None = "USD") -> str:
    if price is None:
        return "–"
    sym = "€" if currency == "EUR" else ("¥" if currency == "JPY" else "$")
    return f"{sym}{price:.2f}"


# ---------------------------------------------------------------------------
# Background loader
# ---------------------------------------------------------------------------

class _LoadWorker(QThread):
    done = Signal(list)

    def __init__(self, repo: CollectionRepository, sold_only: bool = False) -> None:
        super().__init__()
        self._repo = repo
        self._sold_only = sold_only

    def run(self) -> None:
        try:
            if self._sold_only:
                self.done.emit(self._repo.get_sold_history())
            else:
                self.done.emit(self._repo.list_all_for_market())
        except Exception as exc:
            _LOG.warning("MarktWidget loader error: %s", exc)
            self.done.emit([])


# ---------------------------------------------------------------------------
# Catalog search worker (Kauf-Tab: sucht alle Karten im Katalog)
# ---------------------------------------------------------------------------

class _CatalogSearchWorker(QThread):
    """Sucht im Katalog und überlagert mit Sammlung (Preisalarm, Besitz)."""
    done = Signal(list)

    def __init__(
        self,
        catalog_repo: "CatalogRepository",
        collection_repo: CollectionRepository | None,
        query: str,
    ) -> None:
        super().__init__()
        self._cat = catalog_repo
        self._coll = collection_repo
        self._query = query

    def run(self) -> None:
        try:
            cat_rows = self._cat.search(self._query)
            # Build lookup: api_id → owned quantity
            owned_counts: dict[str, int] = {}
            # Build lookup: api_id → wish_price
            watch_map: dict[str, float] = {}
            if self._coll:
                owned_counts = self._coll.get_owned_counts_by_api_id()
                for we in self._coll.get_watch_entries():
                    watch_map[we["api_id"]] = we["wish_price"]
            merged: list[dict] = []
            for cat in cat_rows:
                api_id = cat.get("api_id") or ""
                price = cat.get("eur_price") or cat.get("best_price")
                currency = "EUR" if cat.get("eur_price") else (cat.get("price_currency") or "EUR")
                owned_count = owned_counts.get(api_id, 0)
                wish_price = watch_map.get(api_id)
                entry: dict = {
                    "api_id":        api_id,
                    "name":          cat.get("name"),
                    "set_name":      cat.get("set_name"),
                    "card_number":   cat.get("card_number"),
                    "language":      cat.get("language"),
                    "last_price":    price,
                    "price_currency": currency,
                    "image_path":    cat.get("local_image_path"),
                    "owned_count":   owned_count,
                    "wish_price":    wish_price,
                }
                merged.append(entry)
            self.done.emit(merged)
        except Exception as exc:
            _LOG.warning("CatalogSearchWorker error: %s", exc)
            self.done.emit([])


# ---------------------------------------------------------------------------
# Sell-dialog
# ---------------------------------------------------------------------------

class _SellDialog(QDialog):
    """Set sale price and confirm listing."""

    def __init__(self, entry: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Karte zum Verkauf anbieten")
        self.setMinimumWidth(340)
        self._price: float | None = None

        lay = QVBoxLayout(self)
        lay.setSpacing(12)

        info = QLabel(
            f"<b>{entry.get('name', '–')}</b><br>"
            f"<small>{entry.get('set_name', '')}  "
            f"#{entry.get('card_number', '')}  |  {entry.get('condition', 'NM')}</small>"
        )
        info.setWordWrap(True)
        lay.addWidget(info)

        form = QFormLayout()
        form.setSpacing(8)

        # Suggested price from market data
        suggested = entry.get("last_price")
        cur = entry.get("price_currency") or "USD"
        sugg_lbl = QLabel(f"{_price_str(suggested, cur)}  (Marktpreis)")
        sugg_lbl.setStyleSheet("color:#94a3b8;font-size:11px;")
        form.addRow("Vorschlag:", sugg_lbl)

        self._spin = QDoubleSpinBox()
        self._spin.setRange(0.01, 99999.99)
        self._spin.setDecimals(2)
        self._spin.setPrefix("€ " if cur == "EUR" else "$ ")
        if suggested:
            self._spin.setValue(round(suggested, 2))
        else:
            self._spin.setValue(1.00)
        form.addRow("Mein Preis:", self._spin)
        lay.addLayout(form)

        btn_row = QHBoxLayout()
        btn_ok = QPushButton("✅  Zum Verkauf anbieten")
        btn_ok.setMinimumHeight(36)
        btn_ok.clicked.connect(self._accept)
        btn_cancel = QPushButton("Abbrechen")
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(btn_ok)
        btn_row.addWidget(btn_cancel)
        lay.addLayout(btn_row)

    def _accept(self) -> None:
        self._price = self._spin.value()
        self.accept()

    def get_price(self) -> float | None:
        return self._price


# ---------------------------------------------------------------------------
# Single row in the Verkauf list
# ---------------------------------------------------------------------------

class _VerkaufRow(QFrame):
    """One collection entry row with sell / sold / remove actions."""

    action_taken = Signal()

    _STATUS_STYLE = {
        None:        ("Nicht inseriert",  "#64748b", "#1e2030"),
        "for_sale":  ("Zum Verkauf",       "#fbbf24", "#2a1a00"),
        "sold":      ("Verkauft ✓",        "#4ade80", "#162820"),
    }

    def __init__(self, entry: dict, repo: CollectionRepository, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._entry = entry
        self._repo = repo
        self._build()

    def _build(self) -> None:
        entry = self._entry
        self.setObjectName("vkrow")
        self.setStyleSheet(
            "QFrame#vkrow{background:#1a1d2e;border:none;border-bottom:1px solid #2a3045;}"
            "QFrame#vkrow QLabel{border:none;background:transparent;}"
            "QFrame#vkrow QPushButton{padding:6px 16px;border-radius:6px;font-size:20px;}"
        )
        self.setFixedHeight(120)

        hl = QHBoxLayout(self)
        hl.setContentsMargins(8, 4, 8, 4)
        hl.setSpacing(8)

        # Thumbnail
        img = QLabel()
        img.setFixedSize(68, 95)
        img.setAlignment(Qt.AlignCenter)
        img.setStyleSheet("border:1px solid #2a3045;border-radius:2px;background:#1a1d2e;")
        p = resolve_card_image(api_id=entry.get("api_id"), stored_hint=entry.get("image_path"))
        if p:
            pm = _px(p, 80, 112)
            if pm:
                img.setPixmap(pm)
        hl.addWidget(img)

        # Name + Set
        name_w = QWidget()
        name_w.setStyleSheet("background:transparent;")
        nl = QVBoxLayout(name_w)
        nl.setContentsMargins(0, 0, 0, 0)
        nl.setSpacing(2)
        nm = QLabel(entry.get("name") or "–")
        nm.setStyleSheet("font-weight:bold;font-size:15px;color:#e2e8f0;")
        nm.setMaximumWidth(400)
        nl.addWidget(nm)
        sub = QLabel(f"{entry.get('set_name') or ''}  #{entry.get('card_number') or '?'}  |  {entry.get('condition') or 'NM'}")
        sub.setStyleSheet("font-size:12px;color:#94a3b8;")
        nl.addWidget(sub)
        hl.addWidget(name_w, 1)

        # Prices
        price_w = QWidget()
        price_w.setFixedWidth(160)
        price_w.setStyleSheet("background:transparent;")
        pl = QVBoxLayout(price_w)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(2)
        mkt_lbl = QLabel(f"Markt: {_price_str(entry.get('last_price'), entry.get('price_currency'))}")
        mkt_lbl.setStyleSheet("font-size:24px;color:#ffffff;")
        pl.addWidget(mkt_lbl)
        sale_p = entry.get("sale_price")
        my_lbl = QLabel(f"Preis: {_price_str(sale_p, entry.get('price_currency'))}" if sale_p else "Preis: –")
        my_lbl.setStyleSheet(f"font-size:14px;font-weight:bold;color:{'#fbbf24' if sale_p else '#64748b'};")
        pl.addWidget(my_lbl)
        hl.addWidget(price_w)

        # Status badge
        status = entry.get("sale_status")
        txt, col, bg2 = self._STATUS_STYLE.get(status, self._STATUS_STYLE[None])
        st_lbl = QLabel(txt)
        st_lbl.setFixedWidth(120)
        st_lbl.setAlignment(Qt.AlignCenter)
        st_lbl.setStyleSheet(
            f"font-size:12px;font-weight:bold;color:{col};background:{bg2};"
            "border-radius:6px;padding:4px 8px;"
        )
        hl.addWidget(st_lbl)

        # Action buttons
        btn_w = QWidget()
        btn_w.setFixedWidth(200)
        btn_w.setStyleSheet("background:transparent;")
        bl = QVBoxLayout(btn_w)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(6)

        if status is None:
            btn = QPushButton("💰 Zum Verkauf")
            btn.setStyleSheet("background:#1d4ed8;color:white;font-size:12px;")
            btn.clicked.connect(self._do_sell)
            bl.addWidget(btn)
        elif status == "for_sale":
            btn_sold = QPushButton("✅ Als verkauft markieren")
            btn_sold.setStyleSheet("background:#16a34a;color:white;font-size:12px;")
            btn_sold.clicked.connect(self._do_mark_sold)
            bl.addWidget(btn_sold)
            btn_rm = QPushButton("✕ Listing entfernen")
            btn_rm.setStyleSheet("background:#7f1d1d;color:white;font-size:12px;")
            btn_rm.clicked.connect(self._do_remove)
            bl.addWidget(btn_rm)
        else:  # sold
            done_lbl = QLabel("Abgeschlossen")
            done_lbl.setStyleSheet("font-size:12px;color:#4ade80;")
            done_lbl.setAlignment(Qt.AlignCenter)
            bl.addWidget(done_lbl)

        hl.addWidget(btn_w)

    def _do_sell(self) -> None:
        dlg = _SellDialog(self._entry, self)
        if dlg.exec() == QDialog.Accepted:
            price = dlg.get_price()
            if price is not None:
                try:
                    self._repo.set_for_sale(self._entry["id"], price)
                    self.action_taken.emit()
                except Exception as exc:
                    QMessageBox.warning(self, "Fehler", str(exc))

    def _do_mark_sold(self) -> None:
        reply = QMessageBox.question(
            self, "Als verkauft markieren",
            f"Karte \"{self._entry.get('name')}\" als verkauft markieren?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            try:
                self._repo.mark_sold(self._entry["id"])
                self.action_taken.emit()
            except Exception as exc:
                QMessageBox.warning(self, "Fehler", str(exc))

    def _do_remove(self) -> None:
        try:
            self._repo.remove_listing(self._entry["id"])
            self.action_taken.emit()
        except Exception as exc:
            QMessageBox.warning(self, "Fehler", str(exc))

    @property
    def entry_id(self) -> int:
        return self._entry["id"]

    _BG = "#1a1d2e"
    _BG_HL = "#1e3a5f"

    def flash_highlight(self) -> None:
        self.setStyleSheet(
            f"QFrame#vkrow{{background:{self._BG_HL};border:none;border-bottom:1px solid #4a70a0;}}"
            "QFrame#vkrow QLabel{border:none;background:transparent;}"
            "QFrame#vkrow QPushButton{padding:6px 16px;border-radius:6px;font-size:20px;}"
        )
        QTimer.singleShot(900, self._restore_bg)

    def _restore_bg(self) -> None:
        self.setStyleSheet(
            f"QFrame#vkrow{{background:{self._BG};border:none;border-bottom:1px solid #2a3045;}}"
            "QFrame#vkrow QLabel{border:none;background:transparent;}"
            "QFrame#vkrow QPushButton{padding:6px 16px;border-radius:6px;font-size:20px;}"
        )


# ---------------------------------------------------------------------------
# Kompakte Zeile in der rechten Verkaufsliste
# ---------------------------------------------------------------------------

class _VkListeRow(QFrame):
    clicked = Signal(int)  # entry_id

    def __init__(self, entry: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._entry_id = entry["id"]
        self._build(entry)

    def _build(self, entry: dict) -> None:
        self.setObjectName("vklisteRow")
        self.setStyleSheet(
            "QFrame#vklisteRow{background:#1a1d2e;border:none;border-bottom:1px solid #2a3045;}"
            "QFrame#vklisteRow:hover{background:#1e2f4a;}"
            "QFrame#vklisteRow QLabel{border:none;background:transparent;}"
        )
        self.setFixedHeight(110)
        self.setCursor(Qt.PointingHandCursor)

        hl = QHBoxLayout(self)
        hl.setContentsMargins(10, 6, 10, 6)
        hl.setSpacing(10)

        img = QLabel()
        img.setFixedSize(68, 95)
        img.setAlignment(Qt.AlignCenter)
        img.setStyleSheet("border:1px solid #2a3045;border-radius:2px;background:#131520;")
        p = resolve_card_image(api_id=entry.get("api_id"), stored_hint=entry.get("image_path"))
        if p:
            pm = _px(p, 80, 112)
            if pm:
                img.setPixmap(pm)
        hl.addWidget(img)

        name_w = QWidget()
        name_w.setStyleSheet("background:transparent;")
        nl = QVBoxLayout(name_w)
        nl.setContentsMargins(0, 0, 0, 0)
        nl.setSpacing(4)
        nm = QLabel(entry.get("name") or "–")
        nm.setStyleSheet("font-weight:bold;font-size:14px;color:#e2e8f0;")
        nl.addWidget(nm)
        sub = QLabel(f"{entry.get('set_name') or ''}  #{entry.get('card_number') or '?'}")
        sub.setStyleSheet("font-size:12px;color:#94a3b8;")
        nl.addWidget(sub)
        standort = entry.get("standort")
        loc_lbl = QLabel(f"📁 {standort}" if standort else "📁 Kein Ordner")
        loc_lbl.setStyleSheet("font-size:11px;color:#64748b;")
        nl.addWidget(loc_lbl)
        hl.addWidget(name_w, 1)

        cur = entry.get("price_currency") or "USD"
        price_w = QWidget()
        price_w.setFixedWidth(150)
        price_w.setStyleSheet("background:transparent;")
        pl = QVBoxLayout(price_w)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(4)
        mkt = QLabel(f"Markt: {_price_str(entry.get('last_price'), cur)}")
        mkt.setStyleSheet("font-size:24px;color:#ffffff;")
        pl.addWidget(mkt)
        sp = entry.get("sale_price")
        vk = QLabel(f"VK: {_price_str(sp, cur)}" if sp else "VK: –")
        vk.setStyleSheet(f"font-size:15px;font-weight:bold;color:{'#fbbf24' if sp else '#64748b'};")
        pl.addWidget(vk)
        hl.addWidget(price_w)

    def mousePressEvent(self, event) -> None:
        self.clicked.emit(self._entry_id)
        super().mousePressEvent(event)


# ---------------------------------------------------------------------------
# eBay CSV-Export Einstellungs-Dialog
# ---------------------------------------------------------------------------

_EBAY_SETTINGS_FILE = RUNTIME_DIR / "ebay_export_settings.json"

_SHIPPING_OPTIONS: list[tuple[str, str, str]] = [
    # (Anzeigename, eBay-Key, Standard-Kosten)
    ("DHL Paket (2,99 €)",          "DE_DHLPaket",                "2.99"),
    ("DHL Warenpost (1,99 €)",       "DE_DHLWarenpost",            "1.99"),
    ("Hermes (3,49 €)",              "DE_Hermes",                  "3.49"),
    ("Deutsche Post Warensendung",   "DE_DeutschePostWarensendung","1.60"),
    ("Abholung (kostenlos)",         "DE_Pickup",                  "0.00"),
]

_DURATION_OPTIONS: list[tuple[str, str]] = [
    ("3 Tage",  "Days_3"),
    ("5 Tage",  "Days_5"),
    ("7 Tage",  "Days_7"),
    ("10 Tage", "Days_10"),
]

_RETURN_DAYS: list[tuple[str, str]] = [
    ("14 Tage", "Days_14"),
    ("30 Tage", "Days_30"),
    ("60 Tage", "Days_60"),
]


class _EbayCsvSettingsDialog(QDialog):
    """Dialog: eBay-Export-Einstellungen vor dem CSV-Export."""

    _DEFAULTS: dict = {
        "export_fmt":        "simple_list",
        "standort":          "",
        "shipping_idx":      0,
        "shipping_cost":     2.99,
        "format":            "Auktion",
        "duration_idx":      3,   # 10 Tage
        "returns":           True,
        "returns_days_idx":  1,   # 30 Tage
    }

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("📦 eBay Export – Einstellungen")
        self.setMinimumWidth(420)
        self.setModal(True)

        saved = self._load()
        root = QVBoxLayout(self)
        root.setSpacing(12)
        root.setContentsMargins(16, 16, 16, 16)

        _grp_style = (
            "QGroupBox{font-weight:bold;color:#93c5fd;border:1px solid #2a3045;"
            "border-radius:5px;margin-top:8px;padding-top:6px;}"
            "QGroupBox::title{subcontrol-origin:margin;left:10px;}"
        )

        # --- Gruppenbox Exportformat ---
        grp_expfmt = QGroupBox("Exportformat")
        grp_expfmt.setStyleSheet(_grp_style)
        form_ef = QFormLayout(grp_expfmt)
        form_ef.setSpacing(8)

        self._export_fmt_cb = QComboBox()
        self._export_fmt_cb.addItems(["Einfache Liste (Privatverkäufer)", "eBay File Exchange (Gewerblich)"])
        _efmt_idx = 0 if saved.get("export_fmt", "simple_list") == "simple_list" else 1
        self._export_fmt_cb.setCurrentIndex(_efmt_idx)
        form_ef.addRow("Format:", self._export_fmt_cb)

        root.addWidget(grp_expfmt)

        # --- Gruppenbox Standort & Versand ---
        grp_versand = QGroupBox("Versand")
        grp_versand.setStyleSheet(_grp_style)
        form_v = QFormLayout(grp_versand)
        form_v.setSpacing(8)

        self._standort = QLineEdit(saved.get("standort", ""))
        self._standort.setPlaceholderText("z. B.  12345 Musterstadt")
        form_v.addRow("Standort (PLZ Stadt):", self._standort)

        self._shipping_cb = QComboBox()
        for label, _, _ in _SHIPPING_OPTIONS:
            self._shipping_cb.addItem(label)
        self._shipping_cb.setCurrentIndex(saved.get("shipping_idx", 0))
        self._shipping_cb.currentIndexChanged.connect(self._on_shipping_changed)
        form_v.addRow("Versandart:", self._shipping_cb)

        self._shipping_cost = QDoubleSpinBox()
        self._shipping_cost.setRange(0.0, 50.0)
        self._shipping_cost.setSingleStep(0.10)
        self._shipping_cost.setDecimals(2)
        self._shipping_cost.setSuffix(" €")
        self._shipping_cost.setValue(float(saved.get("shipping_cost", 2.99)))
        form_v.addRow("Versandkosten:", self._shipping_cost)

        root.addWidget(grp_versand)

        # --- Gruppenbox Angebot ---
        grp_angebot = QGroupBox("Angebot")
        grp_angebot.setStyleSheet(_grp_style)
        form_a = QFormLayout(grp_angebot)
        form_a.setSpacing(8)

        self._format_cb = QComboBox()
        self._format_cb.addItems(["Auktion", "Sofortkauf"])
        idx_fmt = 0 if saved.get("format", "Auktion") == "Auktion" else 1
        self._format_cb.setCurrentIndex(idx_fmt)
        self._format_cb.currentIndexChanged.connect(self._on_format_changed)
        form_a.addRow("Angebotsformat:", self._format_cb)

        self._duration_lbl = QLabel("Laufzeit:")
        self._duration_cb = QComboBox()
        for label, _ in _DURATION_OPTIONS:
            self._duration_cb.addItem(label)
        self._duration_cb.setCurrentIndex(saved.get("duration_idx", 3))
        form_a.addRow(self._duration_lbl, self._duration_cb)

        root.addWidget(grp_angebot)

        # --- Gruppenbox Rückgabe ---
        grp_return = QGroupBox("Rückgabe")
        grp_return.setStyleSheet(_grp_style)
        form_r = QFormLayout(grp_return)
        form_r.setSpacing(8)

        self._returns_chk = QCheckBox("Rückgabe akzeptieren")
        self._returns_chk.setChecked(bool(saved.get("returns", True)))
        self._returns_chk.toggled.connect(self._on_returns_toggled)
        form_r.addRow(self._returns_chk)

        self._returns_days_lbl = QLabel("Rückgabefrist:")
        self._returns_days_cb = QComboBox()
        for label, _ in _RETURN_DAYS:
            self._returns_days_cb.addItem(label)
        self._returns_days_cb.setCurrentIndex(saved.get("returns_days_idx", 1))
        form_r.addRow(self._returns_days_lbl, self._returns_days_cb)

        root.addWidget(grp_return)

        # --- Buttons ---
        btns = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel,
            Qt.Horizontal,
        )
        btns.button(QDialogButtonBox.Ok).setText("✅  Exportieren")
        btns.button(QDialogButtonBox.Cancel).setText("Abbrechen")
        btns.accepted.connect(self._on_accept)
        btns.rejected.connect(self.reject)
        root.addWidget(btns)

        # Initial state
        self._on_format_changed(idx_fmt)
        self._on_returns_toggled(self._returns_chk.isChecked())

    # ------ slots ------

    def _on_shipping_changed(self, idx: int) -> None:
        """Prefill cost from the shipping option's default."""
        if 0 <= idx < len(_SHIPPING_OPTIONS):
            default_cost = float(_SHIPPING_OPTIONS[idx][2])
            self._shipping_cost.setValue(default_cost)

    def _on_format_changed(self, idx: int) -> None:
        visible = idx == 0  # Auktion
        self._duration_lbl.setVisible(visible)
        self._duration_cb.setVisible(visible)

    def _on_returns_toggled(self, checked: bool) -> None:
        self._returns_days_lbl.setVisible(checked)
        self._returns_days_cb.setVisible(checked)

    def _on_accept(self) -> None:
        self._save()
        self.accept()

    # ------ persist ------

    @classmethod
    def _load(cls) -> dict:
        if _EBAY_SETTINGS_FILE.exists():
            try:
                import json as _json
                raw = _json.loads(_EBAY_SETTINGS_FILE.read_text(encoding="utf-8"))
                return {**cls._DEFAULTS, **raw}
            except Exception:
                pass
        return dict(cls._DEFAULTS)

    def _save(self) -> None:
        import json as _json
        data = {
            "export_fmt":       "simple_list" if self._export_fmt_cb.currentIndex() == 0 else "file_exchange",
            "standort":         self._standort.text().strip(),
            "shipping_idx":     self._shipping_cb.currentIndex(),
            "shipping_cost":    self._shipping_cost.value(),
            "format":           self._format_cb.currentText(),
            "duration_idx":     self._duration_cb.currentIndex(),
            "returns":          self._returns_chk.isChecked(),
            "returns_days_idx": self._returns_days_cb.currentIndex(),
        }
        try:
            RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
            _EBAY_SETTINGS_FILE.write_text(
                _json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception:
            pass

    # ------ result accessor ------

    def export_settings(self) -> dict:
        """Return resolved settings for CSV generation."""
        shi_idx = self._shipping_cb.currentIndex()
        _, shipping_key, _ = _SHIPPING_OPTIONS[shi_idx]
        is_auction = self._format_cb.currentIndex() == 0
        _, duration_key = _DURATION_OPTIONS[self._duration_cb.currentIndex()]
        returns = self._returns_chk.isChecked()
        _, returns_days = _RETURN_DAYS[self._returns_days_cb.currentIndex()]
        return {
            "export_fmt":   "simple_list" if self._export_fmt_cb.currentIndex() == 0 else "file_exchange",
            "standort":    self._standort.text().strip() or "Deutschland",
            "shipping_key": shipping_key,
            "shipping_cost": f"{self._shipping_cost.value():.2f}",
            "format":       "Auction" if is_auction else "FixedPriceItem",
            "duration":     duration_key if is_auction else "GTC",
            "returns_accepted": "ReturnsAccepted" if returns else "ReturnsNotAccepted",
            "returns_within":   returns_days if returns else "",
        }


# ---------------------------------------------------------------------------
# Rechte Verkaufsliste-Panel
# ---------------------------------------------------------------------------

class _VerkaufslistePanel(QWidget):
    entry_clicked = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        vbox = QVBoxLayout(self)
        vbox.setContentsMargins(4, 8, 8, 8)
        vbox.setSpacing(6)

        hdr_w = QWidget()
        hdr_w.setStyleSheet(
            "QWidget{background:#1e2030;border-radius:6px;border:1px solid #2a3045;}"
        )
        hdr_w.setFixedHeight(44)
        hdr_hl = QHBoxLayout(hdr_w)
        hdr_hl.setContentsMargins(12, 0, 12, 0)
        hdr_hl.setSpacing(8)
        title = QLabel("💰  Verkaufsliste")
        title.setStyleSheet(
            "font-size:13px;font-weight:bold;color:#fbbf24;"
            "background:transparent;border:none;"
        )
        hdr_hl.addWidget(title)
        hdr_hl.addStretch(1)
        self._csv_btn = QPushButton("📥 CSV-Export")
        self._csv_btn.setStyleSheet(
            "QPushButton{background:#1e3a5f;color:#93c5fd;font-size:11px;"
            "font-weight:bold;border:1px solid #2a3045;border-radius:4px;"
            "padding:4px 10px;}QPushButton:hover{background:#1d4ed8;color:white;}"
        )
        self._csv_btn.clicked.connect(self._do_csv_export)
        hdr_hl.addWidget(self._csv_btn)
        self._sum_lbl = QLabel("")
        self._sum_lbl.setStyleSheet(
            "font-size:13px;font-weight:bold;color:#4ade80;"
            "background:transparent;border:none;"
        )
        hdr_hl.addWidget(self._sum_lbl)
        vbox.addWidget(hdr_w)
        self._entries: list[dict] = []

        hdr = QFrame()
        hdr.setFixedHeight(26)
        hdr.setStyleSheet(
            "QFrame{background:#2c3e50;border-radius:4px;}"
            "QFrame QLabel{border:none;background:transparent;color:white;"
            "font-weight:bold;font-size:10px;}"
        )
        hl = QHBoxLayout(hdr)
        hl.setContentsMargins(8, 0, 8, 0)
        hl.setSpacing(8)
        for txt, w in [("Bild", 68), ("Name / Set", 0), ("Preise", 150)]:
            lbl = QLabel(txt)
            if w:
                lbl.setFixedWidth(w)
            else:
                lbl.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            lbl.setAlignment(Qt.AlignCenter if w else Qt.AlignLeft | Qt.AlignVCenter)
            hl.addWidget(lbl)
        vbox.addWidget(hdr)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._rows_w = QWidget()
        self._rows_l = QVBoxLayout(self._rows_w)
        self._rows_l.setContentsMargins(0, 0, 0, 0)
        self._rows_l.setSpacing(0)
        self._rows_l.addStretch(1)
        self._scroll.setWidget(self._rows_w)
        vbox.addWidget(self._scroll, 1)

        self._count_lbl = QLabel("–")
        self._count_lbl.setAlignment(Qt.AlignCenter)
        self._count_lbl.setStyleSheet("color:#888;font-size:10px;")
        vbox.addWidget(self._count_lbl)

    def update_data(self, entries: list[dict]) -> None:
        self._entries = entries
        while self._rows_l.count() > 1:
            item = self._rows_l.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for i, entry in enumerate(entries):
            row = _VkListeRow(entry)
            row.clicked.connect(self.entry_clicked)
            self._rows_l.insertWidget(i, row)
        n = len(entries)
        self._count_lbl.setText(
            f"{n} Karte{'n' if n != 1 else ''} inseriert" if n else "Keine Karten inseriert"
        )
        total = sum(e.get("sale_price") or 0.0 for e in entries)
        cur = entries[0].get("price_currency") or "USD" if entries else "USD"
        sym = "€" if cur == "EUR" else "$" if cur == "USD" else cur
        self._sum_lbl.setText(f"Gesamt: {sym}{total:.2f}")

    # ------------------------------------------------------------------
    # eBay condition code mapping (File Exchange / ebay.de)
    # ------------------------------------------------------------------
    _EBAY_CONDITION: dict[str, int] = {
        "NM": 2750, "NEAR MINT": 2750,
        "EX": 3000, "EXCELLENT": 3000,
        "GD": 4000, "GOOD": 4000,
        "PL": 5000, "POOR": 5000,
        "LP": 3000, "LIGHTLY PLAYED": 3000,
        "MP": 4000, "MODERATELY PLAYED": 4000,
        "HP": 5000, "HEAVILY PLAYED": 5000,
    }

    @staticmethod
    def _ebay_title(e: dict) -> str:
        """Build an eBay title ≤ 80 chars."""
        cond = (e.get("condition") or "NM").upper()
        parts = [
            "Pokémon",
            e.get("name") or "",
            e.get("card_number") or "",
            e.get("set_name") or "",
            cond,
            "Einzelkarte",
        ]
        title = " ".join(p for p in parts if p)
        return title[:80]

    @staticmethod
    def _ebay_description(e: dict) -> str:
        name    = e.get("name") or ""
        set_n   = e.get("set_name") or ""
        num     = e.get("card_number") or ""
        cond    = e.get("condition") or "NM"
        lang    = e.get("language") or "Englisch"
        return (
            f"<b>{name}</b><br>"
            f"Set: {set_n}<br>"
            f"Karte Nr.: {num}<br>"
            f"Zustand: {cond}<br>"
            f"Sprache: {lang}<br>"
        )

    def _do_csv_export(self) -> None:
        if not self._entries:
            QMessageBox.information(self, "CSV-Export", "Keine Einträge zum Exportieren.")
            return

        # --- Einstellungs-Dialog öffnen ---
        dlg = _EbayCsvSettingsDialog(self)
        if dlg.exec() != QDialog.Accepted:
            return
        cfg = dlg.export_settings()

        is_simple = cfg["export_fmt"] == "simple_list"
        default_fname = "verkaufsliste.csv" if is_simple else "ebay_listing.csv"
        path, _ = QFileDialog.getSaveFileName(
            self, "CSV speichern", default_fname, "CSV-Dateien (*.csv)"
        )
        if not path:
            return

        try:
            if is_simple:
                self._write_simple_list_csv(path)
            else:
                self._write_file_exchange_csv(path, cfg)
        except OSError as exc:
            QMessageBox.critical(self, "Fehler", str(exc))

    def _write_simple_list_csv(self, path: str) -> None:
        """Einfache Verkaufsliste als lesbares CSV – Spickzettel zum manuellen Einstellen."""
        import csv

        headers = [
            "Name",
            "Set",
            "Karten-Nr.",
            "Zustand",
            "Sprache",
            "Preis (€)",
            "Notiz",
            "Ordner/Seite",
        ]
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f, delimiter=";")
            writer.writerow(headers)
            for e in self._entries:
                price = e.get("sale_price") or 0.0
                writer.writerow([
                    e.get("name") or "",
                    e.get("set_name") or "",
                    e.get("card_number") or "",
                    e.get("condition") or "NM",
                    e.get("language") or "Englisch",
                    f"{price:.2f}",
                    "",
                    e.get("standort") or "",
                ])
        n = len(self._entries)
        QMessageBox.information(
            self, "CSV-Export",
            f"{n} Karte{'n' if n != 1 else ''} exportiert.\n\nGespeichert unter:\n{path}"
        )

    def _write_file_exchange_csv(self, path: str, cfg: dict) -> None:
        """eBay File Exchange CSV (ebay.de, SiteID 77) – für Gewerbliche Verkäufer."""
        import csv

        headers = [
            "Action",
            "Title",
            "Category",
            "ConditionID",
            "Format",
            "Duration",
            "StartPrice",
            "Quantity",
            "Description",
            "Location",
            "Country",
            "Currency",
            "SiteID",
            "ShippingType",
            "ShippingService-1:Option",
            "ShippingService-1:Cost",
            "ReturnsAcceptedOption",
            "RefundOption",
            "ReturnsWithinOption",
            "ShippingCostPaidByOption",
        ]
        returns_within = cfg["returns_within"] or "Days_30"
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(headers)
            for e in self._entries:
                cond_key = (e.get("condition") or "NM").upper().strip()
                cond_id  = self._EBAY_CONDITION.get(cond_key, 3000)
                price    = e.get("sale_price") or 0.01
                writer.writerow([
                    "Add",                             # Action
                    self._ebay_title(e),               # Title
                    183454,                            # Category: Pokémon Einzelkarten (ebay.de)
                    cond_id,                           # ConditionID
                    cfg["format"],                     # Format
                    cfg["duration"],                   # Duration
                    f"{price:.2f}",                    # StartPrice
                    1,                                 # Quantity
                    self._ebay_description(e),         # Description
                    cfg["standort"],                   # Location
                    "DE",                              # Country
                    "EUR",                             # Currency
                    77,                                # SiteID (ebay.de)
                    "Flat",                            # ShippingType
                    cfg["shipping_key"],               # ShippingService-1:Option
                    cfg["shipping_cost"],              # ShippingService-1:Cost
                    cfg["returns_accepted"],           # ReturnsAcceptedOption
                    "MoneyBack",                       # RefundOption
                    returns_within,                    # ReturnsWithinOption
                    "Buyer",                           # ShippingCostPaidByOption
                ])
        n = len(self._entries)
        QMessageBox.information(
            self, "eBay File Exchange CSV-Export",
            f"{n} Karte{'n' if n != 1 else ''} exportiert.\n\nGespeichert unter:\n{path}"
        )


# ---------------------------------------------------------------------------
# Verkauf tab
# ---------------------------------------------------------------------------

class _VerkaufWidget(QWidget):
    _STATUS_FILTERS = [
        ("Alle",            None),
        ("Zum Verkauf",     "for_sale"),
        ("Nicht inseriert", "none"),
    ]

    def __init__(self, repo: CollectionRepository, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._repo = repo
        self._all_rows: list[dict] = []
        self._worker: _LoadWorker | None = None
        self._row_map: dict[int, _VerkaufRow] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(4)
        splitter.setStyleSheet("QSplitter::handle{background:#2a3045;}")

        # ── Left: Kartenübersicht ──────────────────────────────────────────
        left_w = QWidget()
        lv = QVBoxLayout(left_w)
        lv.setContentsMargins(8, 8, 4, 8)
        lv.setSpacing(6)

        fb = QFrame()
        fb.setStyleSheet(
            "QFrame{background:#1e2030;border:1px solid #2a3045;border-radius:6px;}"
            "QFrame QLabel{border:none;background:transparent;color:#e2e8f0;}"
        )
        fb.setFixedHeight(44)
        fl = QHBoxLayout(fb)
        fl.setContentsMargins(12, 0, 12, 0)
        fl.setSpacing(10)

        self._search = QLineEdit()
        self._search.setPlaceholderText("🔍  Kartenname …")
        self._search.setFixedWidth(180)
        self._search.textChanged.connect(self._apply_filter)
        fl.addWidget(self._search)

        self._status_combo = QComboBox()
        for label, _ in self._STATUS_FILTERS:
            self._status_combo.addItem(label)
        self._status_combo.currentIndexChanged.connect(self._apply_filter)
        fl.addWidget(self._status_combo)
        fl.addStretch()

        btn_reload = QPushButton("⟳")
        btn_reload.setFixedSize(32, 32)
        btn_reload.setToolTip("Neu laden")
        btn_reload.clicked.connect(self._load)
        fl.addWidget(btn_reload)
        lv.addWidget(fb)

        hdr = QFrame()
        hdr.setFixedHeight(26)
        hdr.setStyleSheet(
            "QFrame{background:#2c3e50;border-radius:4px;}"
            "QFrame QLabel{border:none;background:transparent;color:white;"
            "font-weight:bold;font-size:10px;}"
        )
        hl = QHBoxLayout(hdr)
        hl.setContentsMargins(8, 0, 8, 0)
        hl.setSpacing(8)
        for txt, w in [("Bild", 68), ("Name / Set / Zustand", 0), ("Preise", 160),
                       ("Status", 120), ("Aktion", 200)]:
            lbl = QLabel(txt)
            if w:
                lbl.setFixedWidth(w)
            else:
                lbl.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            lbl.setAlignment(Qt.AlignLeft | Qt.AlignVCenter if w == 0 else Qt.AlignCenter)
            hl.addWidget(lbl)
        lv.addWidget(hdr)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._rows_w = QWidget()
        self._rows_l = QVBoxLayout(self._rows_w)
        self._rows_l.setContentsMargins(0, 0, 0, 0)
        self._rows_l.setSpacing(0)
        self._rows_l.addStretch(1)
        self._scroll.setWidget(self._rows_w)
        lv.addWidget(self._scroll, 1)

        self._status_lbl = QLabel("Noch nicht geladen – ⟳ klicken.")
        self._status_lbl.setAlignment(Qt.AlignCenter)
        self._status_lbl.setStyleSheet("color:#888;font-size:11px;")
        lv.addWidget(self._status_lbl)

        splitter.addWidget(left_w)

        # ── Right: Verkaufsliste ───────────────────────────────────────────
        self._vk_liste = _VerkaufslistePanel()
        self._vk_liste.entry_clicked.connect(self._highlight_row)
        splitter.addWidget(self._vk_liste)

        splitter.setSizes([700, 300])
        outer.addWidget(splitter, 1)

    def load_if_needed(self) -> None:
        if not self._all_rows:
            self._load()

    def _load(self) -> None:
        if self._worker and self._worker.isRunning():
            return
        self._status_lbl.setText("Lade …")
        self._worker = _LoadWorker(self._repo, sold_only=False)
        self._worker.done.connect(self._on_loaded)
        self._worker.start()

    def _on_loaded(self, rows: list) -> None:
        self._all_rows = rows
        for_sale = [r for r in rows if r.get("sale_status") == "for_sale"]
        self._vk_liste.update_data(for_sale)
        self._apply_filter()

    def _apply_filter(self) -> None:
        query = self._search.text().strip().lower()
        idx = self._status_combo.currentIndex()
        _, status_filter = self._STATUS_FILTERS[idx]

        filtered = []
        for r in self._all_rows:
            # Sold items always hidden from Verkaufsübersicht (→ see Historie tab)
            if r.get("sale_status") == "sold":
                continue
            if query and query not in (r.get("name") or "").lower():
                continue
            if status_filter == "none":
                if r.get("sale_status") is not None:
                    continue
            elif status_filter is not None:
                if r.get("sale_status") != status_filter:
                    continue
            filtered.append(r)

        self._rebuild_rows(filtered)

    def _rebuild_rows(self, rows: list[dict]) -> None:
        while self._rows_l.count() > 1:
            item = self._rows_l.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._row_map.clear()
        for i, entry in enumerate(rows):
            row_w = _VerkaufRow(entry, self._repo)
            row_w.action_taken.connect(self._load)
            self._rows_l.insertWidget(i, row_w)
            self._row_map[entry["id"]] = row_w
        n = len(rows)
        self._status_lbl.setText(f"{n} Einträge" if n else "Keine Karten gefunden.")

    def _highlight_row(self, entry_id: int) -> None:
        row = self._row_map.get(entry_id)
        if row:
            self._scroll.ensureWidgetVisible(row)
            row.flash_highlight()

    def stop_workers(self) -> None:
        if self._worker and self._worker.isRunning():
            self._worker.quit()
            self._worker.wait(2000)


# ---------------------------------------------------------------------------
# Edit-Sale-Dialog (Historie)
# ---------------------------------------------------------------------------

class _EditSaleDialog(QDialog):
    """Edit shipping cost, platform and buyer note for a sale history entry."""

    _PLATFORMS = ["–", "eBay", "Kleinanzeigen", "Cardmarket", "TCGPlayer", "Privat", "Sonstige"]

    def __init__(self, entry: dict, repo: CollectionRepository, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._entry = entry
        self._repo = repo
        self.setWindowTitle(f"Verkauf bearbeiten – {entry.get('name') or ''}")
        self.setMinimumWidth(380)

        lay = QFormLayout(self)
        lay.setSpacing(10)
        lay.setContentsMargins(16, 16, 16, 16)

        self._shipping = QDoubleSpinBox()
        self._shipping.setRange(0.0, 50.0)
        self._shipping.setSingleStep(0.10)
        self._shipping.setDecimals(2)
        self._shipping.setSuffix(" €")
        self._shipping.setValue(float(entry.get("shipping_cost") or 0.0))
        lay.addRow("Versandkosten:", self._shipping)

        self._platform = QComboBox()
        self._platform.addItems(self._PLATFORMS)
        cur_plat = entry.get("platform") or "–"
        idx = self._PLATFORMS.index(cur_plat) if cur_plat in self._PLATFORMS else 0
        self._platform.setCurrentIndex(idx)
        lay.addRow("Plattform:", self._platform)

        self._buyer = QLineEdit(entry.get("buyer_note") or "")
        self._buyer.setPlaceholderText("Käufer / Referenz / Notiz …")
        lay.addRow("Käufer / Notiz:", self._buyer)

        btns = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        btns.button(QDialogButtonBox.Save).setText("💾  Speichern")
        btns.button(QDialogButtonBox.Cancel).setText("Abbrechen")
        btns.accepted.connect(self._save)
        btns.rejected.connect(self.reject)
        lay.addRow(btns)

    def _save(self) -> None:
        shipping = self._shipping.value()
        platform = self._platform.currentText()
        if platform == "–":
            platform = None
        buyer = self._buyer.text().strip() or None
        try:
            self._repo.update_sale_history_entry(
                self._entry["id"],
                shipping_cost=shipping if shipping > 0 else None,
                platform=platform,
                buyer_note=buyer,
            )
            self.accept()
        except Exception as exc:
            QMessageBox.warning(self, "Fehler", str(exc))


# ---------------------------------------------------------------------------
# Kauf tab – Wunschpreis
# ---------------------------------------------------------------------------


class _WishPriceDialog(QDialog):
    """Dialog zum Setzen / Löschen eines Wunschpreises."""

    def __init__(self, card_name: str, current: float | None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Wunschpreis – {card_name}")
        self.setModal(True)
        self.setMinimumWidth(320)

        root = QVBoxLayout(self)
        root.setSpacing(12)
        root.setContentsMargins(16, 16, 16, 16)

        info = QLabel(
            "Du wirst benachrichtigt, wenn der Marktpreis\n"
            "den Wunschpreis <b>unterschreitet oder erreicht</b>."
        )
        info.setStyleSheet("color:#94a3b8;font-size:12px;")
        root.addWidget(info)

        self._spin = QDoubleSpinBox()
        self._spin.setRange(0.01, 9999.99)
        self._spin.setSingleStep(0.50)
        self._spin.setDecimals(2)
        self._spin.setSuffix(" €")
        self._spin.setMinimumHeight(36)
        self._spin.setValue(current if current is not None else 5.00)
        root.addWidget(self._spin)

        btns = QHBoxLayout()
        btn_ok = QPushButton("✔ Setzen")
        btn_ok.setStyleSheet(
            "QPushButton{background:#16a34a;color:white;border:none;border-radius:6px;padding:6px 18px;}"
            "QPushButton:hover{background:#15803d;}"
        )
        btn_ok.clicked.connect(self.accept)
        btns.addWidget(btn_ok)

        btn_clear = QPushButton("✕ Löschen")
        btn_clear.setStyleSheet(
            "QPushButton{background:#7f1d1d;color:white;border:none;border-radius:6px;padding:6px 18px;}"
            "QPushButton:hover{background:#991b1b;}"
        )
        btn_clear.clicked.connect(lambda: self.done(2))  # code 2 = clear
        btns.addWidget(btn_clear)
        root.addLayout(btns)

    def get_price(self) -> float:
        return self._spin.value()


class _KaufRow(QFrame):
    wish_changed = Signal(str, object)  # api_id, wish_price | None

    _BG_NORMAL = "#1a1d2e"
    _BG_HIT    = "#0f3320"   # green when price ≤ wish

    def __init__(self, entry: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._entry = entry
        self._build()

    def _build(self) -> None:
        entry = self._entry
        hit = self._is_hit()
        bg = self._BG_HIT if hit else self._BG_NORMAL
        border_color = "#22c55e" if hit else "#2a3045"
        self.setObjectName("kaufrow")
        self.setStyleSheet(
            f"QFrame#kaufrow{{background:{bg};border:none;border-bottom:1px solid {border_color};}}"
            "QFrame#kaufrow QLabel{border:none;background:transparent;}"
        )
        self.setFixedHeight(120)

        hl = QHBoxLayout(self)
        hl.setContentsMargins(10, 6, 10, 6)
        hl.setSpacing(10)

        # ── Thumbnail with owned-count badge ─────────────────────────────
        img_container = QWidget()
        img_container.setFixedSize(68, 95)
        img_container.setStyleSheet("background:transparent;")
        img_layout = QVBoxLayout(img_container)
        img_layout.setContentsMargins(0, 0, 0, 0)
        img_layout.setSpacing(0)

        img = QLabel()
        img.setFixedSize(68, 95)
        img.setAlignment(Qt.AlignCenter)
        img.setStyleSheet("border:1px solid #2a3045;border-radius:2px;background:#131520;")
        p = resolve_card_image(api_id=entry.get("api_id"), stored_hint=entry.get("image_path"))
        if p:
            pm = _px(p, 80, 112)
            if pm:
                img.setPixmap(pm)

        # Owned-count badge (overlay, only if ≥1)
        owned = entry.get("owned_count", 0) or 0
        if owned >= 1:
            badge = QLabel(str(owned))
            badge.setParent(img)
            badge.setFixedSize(22, 22)
            badge.setAlignment(Qt.AlignCenter)
            badge.setStyleSheet(
                "background:#1d4ed8;color:white;font-weight:bold;font-size:11px;"
                "border-radius:11px;border:1px solid #93c5fd;"
            )
            badge.move(44, 0)  # top-right of the 68px image
            badge.raise_()

        hl.addWidget(img)

        # ── Name + Set ────────────────────────────────────────────────────
        name_w = QWidget()
        name_w.setStyleSheet("background:transparent;")
        nl = QVBoxLayout(name_w)
        nl.setContentsMargins(0, 0, 0, 0)
        nl.setSpacing(3)
        nm = QLabel(entry.get("name") or "–")
        nm.setStyleSheet("font-weight:bold;font-size:14px;color:#e2e8f0;")
        nl.addWidget(nm)
        sub = QLabel(
            f"{entry.get('set_name') or ''}  "
            f"#{entry.get('card_number') or '?'}"
        )
        sub.setStyleSheet("font-size:12px;color:#94a3b8;")
        nl.addWidget(sub)
        if hit:
            hit_lbl = QLabel("✅ Wunschpreis erreicht!")
            hit_lbl.setStyleSheet("font-size:12px;font-weight:bold;color:#4ade80;")
            nl.addWidget(hit_lbl)
        hl.addWidget(name_w, 1)

        # ── Price column ─────────────────────────────────────────────────
        cur = entry.get("price_currency") or "EUR"
        price_w = QWidget()
        price_w.setFixedWidth(160)
        price_w.setStyleSheet("background:transparent;")
        pl = QVBoxLayout(price_w)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(2)
        mkt_lbl = QLabel(f"Markt: {_price_str(entry.get('last_price'), cur)}")
        mkt_lbl.setStyleSheet("font-size:22px;color:#ffffff;")
        pl.addWidget(mkt_lbl)
        wish = entry.get("wish_price")
        wish_lbl = QLabel(
            f"Wunsch: {_price_str(wish, cur)}" if wish is not None else "Wunsch: –"
        )
        wish_lbl.setStyleSheet(
            f"font-size:13px;color:{'#4ade80' if hit else '#64748b'};"
        )
        pl.addWidget(wish_lbl)
        hl.addWidget(price_w)

        # ── Bell button (ALL cards) ───────────────────────────────────────
        btn_bell = QPushButton("🔔 Wunschpreis")
        btn_bell.setFixedSize(100, 34)
        btn_bell.setStyleSheet(
            "QPushButton{background:#1e40af;color:white;border:none;border-radius:6px;"
            "font-size:11px;}"
            "QPushButton:hover{background:#1d4ed8;}"
        )
        btn_bell.clicked.connect(self._open_wish_dialog)
        hl.addWidget(btn_bell)

    def _is_hit(self) -> bool:
        mp = self._entry.get("last_price")
        wp = self._entry.get("wish_price")
        return mp is not None and wp is not None and mp <= wp

    def _open_wish_dialog(self) -> None:
        dlg = _WishPriceDialog(
            card_name=self._entry.get("name") or "–",
            current=self._entry.get("wish_price"),
            parent=self,
        )
        result = dlg.exec()
        api_id = self._entry.get("api_id") or ""
        if not api_id:
            return
        if result == QDialog.Accepted:
            self.wish_changed.emit(api_id, dlg.get_price())
        elif result == 2:  # clear
            self.wish_changed.emit(api_id, None)


class _KaufWidget(QWidget):
    """Kauf-Tab: Katalog-Suche mit DE/EN-Übersetzung und Preisalarm-Funktion."""

    def __init__(
        self,
        repo: CollectionRepository | None = None,
        catalog_repo: "CatalogRepository | None" = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._repo = repo
        self._catalog_repo = catalog_repo
        self._worker: _LoadWorker | None = None
        self._cat_worker: _CatalogSearchWorker | None = None
        self._all_rows: list[dict] = []
        self._row_widgets: list[_KaufRow] = []
        self._build_ui()

    def _build_ui(self) -> None:
        vbox = QVBoxLayout(self)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(0)

        outer = QWidget()
        outer_l = QVBoxLayout(outer)
        outer_l.setContentsMargins(8, 8, 8, 6)
        outer_l.setSpacing(6)

        # Toolbar
        fb = QFrame()
        fb.setStyleSheet(
            "QFrame{background:#1e2030;border:1px solid #2a3045;border-radius:6px;}"
            "QFrame QLabel{border:none;background:transparent;color:#e2e8f0;}"
        )
        fb.setFixedHeight(48)
        fl = QHBoxLayout(fb)
        fl.setContentsMargins(12, 4, 12, 4)
        fl.setSpacing(10)

        self._search = QLineEdit()
        self._search.setPlaceholderText("\U0001f50d  Kartenname … (DE/EN, Enter zum Suchen)")
        self._search.setMinimumWidth(300)
        self._search.returnPressed.connect(self._load_search)
        fl.addWidget(self._search)

        self._cb_alerted = QCheckBox("Nur Wunschpreis-Treffer")
        self._cb_alerted.setStyleSheet("color:#e2e8f0;")
        self._cb_alerted.toggled.connect(self._apply_filter)
        fl.addWidget(self._cb_alerted)

        fl.addStretch()

        outer_l.addWidget(fb)

        # Hint label
        hint = QLabel(
            "🔔 Klick auf \"Wunschpreis\", um einen Zielpreis zu setzen. "
            "Zeilen werden grün markiert, wenn der Marktpreis den Wunschpreis erreicht."
        )
        hint.setStyleSheet("color:#64748b;font-size:11px;padding:2px 4px;")
        hint.setWordWrap(True)
        outer_l.addWidget(hint)

        # Scroll area
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._rows_w = QWidget()
        self._rows_l = QVBoxLayout(self._rows_w)
        self._rows_l.setContentsMargins(0, 0, 0, 0)
        self._rows_l.setSpacing(0)
        self._rows_l.addStretch(1)
        self._scroll.setWidget(self._rows_w)
        outer_l.addWidget(self._scroll, 1)

        self._status_lbl = QLabel("Suchbegriff eingeben und Enter dr\u00fccken.")
        self._status_lbl.setAlignment(Qt.AlignCenter)
        self._status_lbl.setStyleSheet("color:#888;font-size:11px;")
        outer_l.addWidget(self._status_lbl)

        vbox.addWidget(outer, 1)

    def load_if_needed(self) -> None:
        if not self._all_rows:
            self._load()

    def _load(self) -> None:
        """Lade alle Sammlungskarten (Fallback ohne Suchbegriff)."""
        if self._repo is None:
            return
        if self._worker and self._worker.isRunning():
            return
        self._status_lbl.setText("Lade \u2026")
        self._worker = _LoadWorker(self._repo, sold_only=False)
        self._worker.done.connect(self._on_loaded)
        self._worker.start()

    def _load_search(self) -> None:
        """Triggered by Enter: sucht im Katalog (DE/EN) oder lädt Sammlung."""
        query = self._search.text().strip()
        if not query:
            # Empty query → show all owned cards
            self._load()
            return
        if self._catalog_repo is not None:
            # Cancel running catalog worker
            if self._cat_worker and self._cat_worker.isRunning():
                self._cat_worker.quit()
                self._cat_worker.wait(1000)
            self._status_lbl.setText("Suche \u2026")
            self._cat_worker = _CatalogSearchWorker(
                self._catalog_repo, self._repo, query
            )
            self._cat_worker.done.connect(self._on_loaded)
            self._cat_worker.start()
        else:
            # No catalog repo: client-side filter on already-loaded collection data
            self._apply_filter()

    def pre_fill(self, name: str) -> None:
        """Pre-fill the search box and trigger a search — called from context menu."""
        self._search.setText(name)
        self._load_search()

    def _on_loaded(self, rows: list) -> None:
        self._all_rows = rows
        self._apply_filter()

    def _apply_filter(self) -> None:
        only_hits = self._cb_alerted.isChecked()
        rows = self._all_rows
        if only_hits:
            rows = [
                r for r in rows
                if r.get("wish_price") is not None
                and r.get("last_price") is not None
                and r["last_price"] <= r["wish_price"]
            ]

        while self._rows_l.count() > 1:
            item = self._rows_l.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        self._row_widgets = []
        for i, entry in enumerate(rows):
            row_w = _KaufRow(entry)
            row_w.wish_changed.connect(self._on_wish_changed)
            self._rows_l.insertWidget(i, row_w)
            self._row_widgets.append(row_w)

        hit_count = sum(
            1 for r in rows
            if r.get("wish_price") is not None
            and r.get("last_price") is not None
            and r["last_price"] <= r["wish_price"]
        )
        n = len(rows)
        if n:
            self._status_lbl.setText(
                f"{n} Karten"
                + (f"  •  {hit_count} Wunschpreis-Treffer" if hit_count else "")
            )
        else:
            self._status_lbl.setText("Keine Einträge.")

    def _on_wish_changed(self, api_id: str, wish_price: object) -> None:
        if self._repo is None:
            return
        try:
            self._repo.set_wish_price(api_id, wish_price)  # type: ignore[arg-type]
        except Exception as exc:
            QMessageBox.warning(self, "Fehler", str(exc))
            return
        # Update local data and refresh
        for r in self._all_rows:
            if r.get("api_id") == api_id:
                r["wish_price"] = wish_price
        self._apply_filter()

    def stop_workers(self) -> None:
        if self._worker and self._worker.isRunning():
            self._worker.quit()
            self._worker.wait(2000)
        if self._cat_worker and self._cat_worker.isRunning():
            self._cat_worker.quit()
            self._cat_worker.wait(2000)


# ---------------------------------------------------------------------------
# Historie tab
# ---------------------------------------------------------------------------

class _HistorieWidget(QWidget):
    def __init__(self, repo: CollectionRepository, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._repo = repo
        self._worker: _LoadWorker | None = None
        self._all_rows: list[dict] = []

        vbox = QVBoxLayout(self)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(0)

        # ── Toolbar (same style as Verkauf) ───────────────────────────────
        outer = QWidget()
        outer_l = QVBoxLayout(outer)
        outer_l.setContentsMargins(8, 8, 8, 6)
        outer_l.setSpacing(6)

        fb = QFrame()
        fb.setStyleSheet(
            "QFrame{background:#1e2030;border:1px solid #2a3045;border-radius:6px;}"
            "QFrame QLabel{border:none;background:transparent;color:#e2e8f0;}"
        )
        fb.setFixedHeight(48)
        fl = QHBoxLayout(fb)
        fl.setContentsMargins(12, 4, 12, 4)
        fl.setSpacing(10)

        self._search = QLineEdit()
        self._search.setPlaceholderText("🔍  Kartenname …")
        self._search.setFixedWidth(200)
        self._search.textChanged.connect(self._apply_filter)
        fl.addWidget(self._search)
        fl.addStretch()

        btn_reload = QPushButton("⟳ Neu laden")
        btn_reload.setMinimumSize(100, 32)
        btn_reload.setToolTip("Neu laden")
        btn_reload.clicked.connect(self._load)
        fl.addWidget(btn_reload)

        btn_csv = QPushButton("⬇ CSV Export")
        btn_csv.setMinimumSize(110, 32)
        btn_csv.setToolTip("Verkaufshistorie als CSV exportieren")
        btn_csv.clicked.connect(self._export_csv)
        fl.addWidget(btn_csv)
        outer_l.addWidget(fb)

        # Column header
        hdr = QFrame()
        hdr.setFixedHeight(26)
        hdr.setStyleSheet(
            "QFrame{background:#2c3e50;border-radius:4px;}"
            "QFrame QLabel{border:none;background:transparent;color:white;"
            "font-weight:bold;font-size:10px;}"
        )
        hl = QHBoxLayout(hdr)
        hl.setContentsMargins(8, 0, 8, 0)
        hl.setSpacing(8)
        for txt, w in [("Datum", 130), ("Bild", 64), ("Name / Set / Zustand", 0),
                       ("Standort", 130), ("Versand", 110), ("Plattform", 140),
                       ("EK", 110), ("VK", 130), ("Netto", 115), ("Gewinn", 120), ("", 52)]:
            lbl = QLabel(txt)
            if w:
                lbl.setFixedWidth(w)
            else:
                lbl.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            lbl.setAlignment(Qt.AlignLeft | Qt.AlignVCenter if w == 0 else Qt.AlignCenter)
            hl.addWidget(lbl)
        outer_l.addWidget(hdr)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._rows_w = QWidget()
        self._rows_l = QVBoxLayout(self._rows_w)
        self._rows_l.setContentsMargins(0, 0, 0, 0)
        self._rows_l.setSpacing(0)
        self._rows_l.addStretch(1)
        self._scroll.setWidget(self._rows_w)
        outer_l.addWidget(self._scroll, 1)

        self._summary_lbl = QLabel("")
        self._summary_lbl.setAlignment(Qt.AlignRight)
        self._summary_lbl.setStyleSheet("color:#94a3b8;font-size:11px;padding:4px 8px;")
        outer_l.addWidget(self._summary_lbl)

        self._status_lbl = QLabel("Noch nicht geladen – ⟳ klicken.")
        self._status_lbl.setAlignment(Qt.AlignCenter)
        self._status_lbl.setStyleSheet("color:#888;font-size:11px;")
        outer_l.addWidget(self._status_lbl)

        vbox.addWidget(outer, 1)

    def load_if_needed(self) -> None:
        if self._rows_l.count() <= 1:
            self._load()

    def _load(self) -> None:
        if self._worker and self._worker.isRunning():
            return
        self._status_lbl.setText("Lade …")
        self._worker = _LoadWorker(self._repo, sold_only=True)
        self._worker.done.connect(self._on_loaded)
        self._worker.start()

    def _on_loaded(self, rows: list) -> None:
        self._all_rows = rows
        self._apply_filter()

    def _apply_filter(self) -> None:
        query = self._search.text().strip().lower()
        rows = self._all_rows
        if query:
            rows = [r for r in rows if query in (r.get("name") or "").lower()]

        while self._rows_l.count() > 1:
            item = self._rows_l.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        total_revenue = 0.0
        total_shipping = 0.0
        total_profit = 0.0
        for i, entry in enumerate(rows):
            row_w = self._make_row(i, entry)
            self._rows_l.insertWidget(i, row_w)
            sp = entry.get("sale_price") or 0.0
            sc = entry.get("shipping_cost") or 0.0
            pp = entry.get("purchase_price") or 0.0
            total_revenue += sp
            total_shipping += sc
            if pp:
                total_profit += sp - sc - pp

        n = len(rows)
        if n:
            net = total_revenue - total_shipping
            self._status_lbl.setText(f"{n} verkaufte Karten")
            self._summary_lbl.setText(
                f"Brutto: {_price_str(total_revenue, 'EUR')}  |  "
                f"Netto (nach Versand): {_price_str(net, 'EUR')}  |  "
                f"Gesch\u00e4tzter Gewinn: {_price_str(total_profit, 'EUR')}"
            )
        else:
            self._status_lbl.setText("Noch keine Karten als verkauft markiert.")
            self._summary_lbl.setText("")

    def _make_row(self, idx: int, entry: dict) -> QFrame:
        row = QFrame()
        row.setObjectName("histrow")
        bg = "#16192b" if idx % 2 == 0 else "#1e2030"
        row.setStyleSheet(
            f"QFrame#histrow{{background:{bg};border:none;border-bottom:1px solid #2a3045;}}"
            "QFrame#histrow QLabel{border:none;background:transparent;}"
        )
        row.setFixedHeight(110)
        hl = QHBoxLayout(row)
        hl.setContentsMargins(8, 4, 8, 4)
        hl.setSpacing(8)

        # Date
        date_str = (entry.get("sale_date") or "")[:10] or "–"
        dt_lbl = QLabel(date_str)
        dt_lbl.setFixedWidth(130)
        dt_lbl.setAlignment(Qt.AlignCenter)
        dt_lbl.setStyleSheet("font-size:20px;color:#cbd5e1;")
        hl.addWidget(dt_lbl)

        # Thumbnail
        img = QLabel()
        img.setFixedSize(60, 84)
        img.setAlignment(Qt.AlignCenter)
        img.setStyleSheet("border:1px solid #2a3045;border-radius:2px;background:#1a1d2e;")
        p = resolve_card_image(api_id=entry.get("api_id"), stored_hint=entry.get("image_path"))
        if p:
            pm = _px(p, 60, 84)
            if pm:
                img.setPixmap(pm)
        hl.addWidget(img)

        # Name + Set + Condition + buyer note
        name_w = QWidget()
        name_w.setStyleSheet("background:transparent;")
        nl = QVBoxLayout(name_w)
        nl.setContentsMargins(0, 0, 0, 0)
        nl.setSpacing(1)
        nm = QLabel(entry.get("name") or "–")
        nm.setStyleSheet("font-weight:bold;font-size:22px;color:#e2e8f0;")
        nl.addWidget(nm)
        set_line = (
            f"{entry.get('set_name') or ''}  "
            f"#{entry.get('card_number') or '?'}  |  "
            f"{entry.get('condition') or 'NM'}"
        )
        sub = QLabel(set_line)
        sub.setStyleSheet("font-size:18px;color:#94a3b8;")
        nl.addWidget(sub)
        if entry.get("buyer_note"):
            buyer_lbl = QLabel(f"👤 {entry['buyer_note']}")
            buyer_lbl.setStyleSheet("font-size:18px;color:#7dd3fc;")
            nl.addWidget(buyer_lbl)
        hl.addWidget(name_w, 1)

        # Standort
        st_lbl = QLabel(entry.get("standort") or "–")
        st_lbl.setFixedWidth(130)
        st_lbl.setAlignment(Qt.AlignCenter)
        st_lbl.setStyleSheet("font-size:18px;color:#94a3b8;")
        st_lbl.setWordWrap(True)
        hl.addWidget(st_lbl)

        # Versandkosten
        sc = entry.get("shipping_cost")
        sc_lbl = QLabel(_price_str(sc, "EUR") if sc is not None else "–")
        sc_lbl.setFixedWidth(110)
        sc_lbl.setAlignment(Qt.AlignCenter)
        sc_lbl.setStyleSheet("font-size:20px;color:#94a3b8;")
        hl.addWidget(sc_lbl)

        # Plattform
        plat_lbl = QLabel(entry.get("platform") or "–")
        plat_lbl.setFixedWidth(140)
        plat_lbl.setAlignment(Qt.AlignCenter)
        plat_lbl.setStyleSheet("font-size:18px;color:#93c5fd;")
        hl.addWidget(plat_lbl)

        # Kaufpreis (EK)
        pp = entry.get("purchase_price")
        buy_lbl = QLabel(_price_str(pp, "EUR"))
        buy_lbl.setFixedWidth(110)
        buy_lbl.setAlignment(Qt.AlignCenter)
        buy_lbl.setStyleSheet("font-size:20px;color:#94a3b8;")
        hl.addWidget(buy_lbl)

        # Verkaufspreis (VK)
        sp = entry.get("sale_price")
        sell_lbl = QLabel(_price_str(sp, "EUR"))
        sell_lbl.setFixedWidth(130)
        sell_lbl.setAlignment(Qt.AlignCenter)
        sell_lbl.setStyleSheet("font-size:22px;font-weight:bold;color:#4ade80;")
        hl.addWidget(sell_lbl)

        # Netto (VK - Versand)
        if sp is not None:
            netto = sp - (sc or 0.0)
            netto_lbl = QLabel(_price_str(netto, "EUR"))
            netto_col = "#4ade80" if netto >= 0 else "#f87171"
        else:
            netto_lbl = QLabel("–")
            netto_col = "#64748b"
        netto_lbl.setFixedWidth(115)
        netto_lbl.setAlignment(Qt.AlignCenter)
        netto_lbl.setStyleSheet(f"font-size:20px;font-weight:bold;color:{netto_col};")
        hl.addWidget(netto_lbl)

        # Gewinn/Verlust (Netto - EK)
        if sp is not None and pp is not None:
            delta = sp - (sc or 0.0) - pp
            sign = "+" if delta >= 0 else ""
            col = "#4ade80" if delta >= 0 else "#f87171"
            profit_lbl = QLabel(f"{sign}{_price_str(delta, 'EUR')}")
        else:
            profit_lbl = QLabel("–")
            col = "#64748b"
        profit_lbl.setFixedWidth(120)
        profit_lbl.setAlignment(Qt.AlignCenter)
        profit_lbl.setStyleSheet(f"font-size:22px;font-weight:bold;color:{col};")
        hl.addWidget(profit_lbl)

        # Edit button
        btn_edit = QPushButton("✏")
        btn_edit.setFixedSize(52, 42)
        btn_edit.setStyleSheet("background:#334155;color:white;font-size:28px;border-radius:4px;")
        btn_edit.setToolTip("Versandkosten / Plattform / Käufernotiz bearbeiten")
        btn_edit.clicked.connect(lambda _checked, e=entry: self._on_edit(e))
        hl.addWidget(btn_edit)

        return row

    def _export_csv(self) -> None:
        import csv as _csv
        rows = self._all_rows
        if not rows:
            QMessageBox.information(self, "Export", "Keine Daten zum Exportieren.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Verkaufshistorie exportieren", "verkaufshistorie.csv",
            "CSV-Dateien (*.csv)"
        )
        if not path:
            return
        headers = [
            "Datum", "Name", "Set", "Nummer", "Zustand", "Sprache",
            "Standort", "Versand (EUR)", "Plattform",
            "EK (EUR)", "VK (EUR)", "Netto (EUR)", "Gewinn (EUR)", "Käufernotiz",
        ]
        with open(path, "w", newline="", encoding="utf-8-sig") as fh:
            writer = _csv.writer(fh)
            writer.writerow(headers)
            for e in rows:
                sp = e.get("sale_price")
                sc_v = e.get("shipping_cost") or 0.0
                pp = e.get("purchase_price")
                netto = (sp - sc_v) if sp is not None else None
                gewinn = (sp - sc_v - pp) if sp is not None and pp is not None else None
                writer.writerow([
                    (e.get("sale_date") or "")[:10],
                    e.get("name") or "",
                    e.get("set_name") or "",
                    e.get("card_number") or "",
                    e.get("condition") or "",
                    e.get("language") or "",
                    e.get("standort") or "",
                    f"{sc_v:.2f}" if sc_v else "",
                    e.get("platform") or "",
                    f"{pp:.2f}" if pp is not None else "",
                    f"{sp:.2f}" if sp is not None else "",
                    f"{netto:.2f}" if netto is not None else "",
                    f"{gewinn:.2f}" if gewinn is not None else "",
                    e.get("buyer_note") or "",
                ])
        QMessageBox.information(
            self, "Export", f"{len(rows)} Einträge exportiert nach:\n{path}"
        )

    def _on_edit(self, entry: dict) -> None:
        dlg = _EditSaleDialog(entry, self._repo, self)
        if dlg.exec() == QDialog.Accepted:
            self._load()

    def stop_workers(self) -> None:
        if self._worker and self._worker.isRunning():
            self._worker.quit()
            self._worker.wait(2000)


# ---------------------------------------------------------------------------
# Main MarktWidget
# ---------------------------------------------------------------------------

class MarktWidget(QWidget):
    """Markt-Hauptseite mit Tabs: Kauf / Verkauf / Historie."""

    # Tab indices
    TAB_KAUF = 0
    TAB_VERKAUF = 1
    TAB_HISTORIE = 2

    def __init__(
        self,
        repo: CollectionRepository,
        catalog_repo: "CatalogRepository | None" = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._repo = repo

        vbox = QVBoxLayout(self)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(0)

        self._tabs = QTabWidget()
        self._tabs.setStyleSheet(
            "QTabBar::tab{padding:8px 16px;font-size:13px;}"
        )
        vbox.addWidget(self._tabs, 1)

        self._kauf_w = _KaufWidget(self._repo, catalog_repo=catalog_repo)
        self._verkauf_w = _VerkaufWidget(self._repo)
        self._historie_w = _HistorieWidget(self._repo)

        self._tabs.addTab(self._kauf_w, "\U0001f6d2  Kauf")
        self._tabs.addTab(self._verkauf_w, "\U0001f4b0  Verkauf")
        self._tabs.addTab(self._historie_w, "\U0001f4dc  Historie")

        self._tabs.currentChanged.connect(self._on_tab_changed)

    def show_tab(self, idx: int) -> None:
        self._tabs.setCurrentIndex(idx)
        self._on_tab_changed(idx)

    def search_card(self, name: str) -> None:
        """Navigate to Kauf tab and pre-fill search with the given card name."""
        self.show_tab(self.TAB_KAUF)
        self._kauf_w.pre_fill(name)

    def _on_tab_changed(self, idx: int) -> None:
        if idx == self.TAB_KAUF:
            self._kauf_w.load_if_needed()
        elif idx == self.TAB_VERKAUF:
            self._verkauf_w.load_if_needed()
        elif idx == self.TAB_HISTORIE:
            self._historie_w.load_if_needed()

    def stop_workers(self) -> None:
        self._kauf_w.stop_workers()
        self._verkauf_w.stop_workers()
        self._historie_w.stop_workers()
