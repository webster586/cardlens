"""Unified card image resolution, caching and downloading.

Single source of truth for all card-image access in the app:
  - card_image_path(api_id)            → deterministic local Path
  - resolve_card_image(api_id, hint)   → str path or None (pure filesystem)
  - load_card_pixmap(api_id, ...)      → QPixmap | None (via QPixmapCache)
  - CardImageDownloadWorker            → saves to disk + emits path

QPixmapCache limit is set to 80 MB here so all callers benefit automatically.
"""
from __future__ import annotations

import logging
import re
import urllib.request
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QPixmap, QPixmapCache

from src.pokemon_scanner.core.paths import CATALOG_IMAGES_DIR

_log = logging.getLogger(__name__)

# Raise Qt's default 10 MB limit — a typical card image is ~80–200 KB so
# 80 MB holds ~400–1000 thumbnails comfortably before LRU eviction kicks in.
QPixmapCache.setCacheLimit(80 * 1024)  # kB


def card_image_path(api_id: str) -> Path:
    """Return the deterministic local path for *api_id*, consistent with catalog_repository."""
    safe = re.sub(r"[^\w-]", "_", api_id)
    return CATALOG_IMAGES_DIR / f"{safe}.jpg"


def resolve_card_image(
    api_id: str | None = None,
    stored_hint: str | None = None,
) -> str | None:
    """Return a usable local file path for a card image, or None if absent.

    Resolution order:
    1. ``CATALOG_IMAGES_DIR/{safe_api_id}.jpg``  (deterministic, no DB needed)
    2. ``stored_hint`` if it exists verbatim on disk
    3. ``CATALOG_IMAGES_DIR/{filename-from-hint}`` fallback for relocated files
    """
    if api_id:
        p = card_image_path(api_id)
        if p.exists():
            return str(p)
    if stored_hint:
        p = Path(stored_hint)
        if p.exists():
            return stored_hint
        fallback = CATALOG_IMAGES_DIR / p.name
        if fallback.exists():
            return str(fallback)
    return None


def load_card_pixmap(
    api_id: str | None,
    *,
    stored_hint: str | None = None,
    w: int = 0,
    h: int = 0,
) -> QPixmap | None:
    """Return a (possibly scaled) QPixmap for a card, using QPixmapCache.

    Returns ``None`` when the image is not available locally.
    Use :class:`CardImageDownloadWorker` to fetch missing images asynchronously.

    Parameters
    ----------
    api_id:
        Card API identifier (e.g. ``"swsh7-215"``).  Used as the stable cache
        key and for deterministic path lookup.
    stored_hint:
        Optional DB-stored path, used as a secondary fallback.
    w, h:
        Target dimensions for scaling.  Both 0 means "no scaling (full res)".
        If only h > 0 → ``scaledToHeight``; both > 0 → ``scaled`` keeping ratio.
    """
    path = resolve_card_image(api_id, stored_hint)
    if not path:
        return None

    base_key = api_id if api_id else path
    key = f"card_{base_key}:{w}x{h}"
    pm = QPixmapCache.find(key)
    if pm:
        return pm

    pm = QPixmap(path)
    if pm.isNull():
        return None

    if w > 0 and h > 0:
        pm = pm.scaled(w, h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    elif h > 0:
        pm = pm.scaledToHeight(h, Qt.SmoothTransformation)
    elif w > 0:
        pm = pm.scaledToWidth(w, Qt.SmoothTransformation)

    QPixmapCache.insert(key, pm)
    return pm


class CardImageDownloadWorker(QThread):
    """Download a card image from URL to ``CATALOG_IMAGES_DIR`` on a background thread.

    Emits ``done(local_path)`` on success, or ``done("")`` on failure.

    Replaces:
    - ``_SlotImageFetcher``      in album_widget.py
    - ``_ImageFetchWorker``      in album_scan_dialog.py
    - ``ImageDownloadWorker``    in main_window.py
    """

    done = Signal(str)  # absolute path of saved file, or "" on failure

    def __init__(self, api_id: str, url: str, parent=None) -> None:
        super().__init__(parent)
        self._api_id = api_id
        self._url = url

    def run(self) -> None:
        if not self._url.startswith(("http://", "https://")):
            _log.debug("Skipping non-HTTP image URL for %s", self._api_id)
            self.done.emit("")
            return
        dest = card_image_path(self._api_id)
        try:
            req = urllib.request.Request(self._url, headers={"User-Agent": "CardLens/1.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = resp.read()
            if len(data) > 1024:
                CATALOG_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)
                self.done.emit(str(dest))
            else:
                self.done.emit("")
        except Exception as exc:
            _log.warning("Image fetch failed for %s: %s", self._api_id, exc)
            self.done.emit("")
