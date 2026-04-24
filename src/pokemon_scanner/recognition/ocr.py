from __future__ import annotations

import logging
import re
import threading
from pathlib import Path

import cv2
import numpy as np

_LOG = logging.getLogger(__name__)

# Characters that appear on Pokemon cards — used as allowlist for EasyOCR
_CARD_CHARS = (
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
    "0123456789"
    "äöüßÄÖÜéèêàáâîïùúûœ"
    "-/ "
)
_NUMBER_CHARS = "0123456789/ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def _to_rgb(img: np.ndarray) -> np.ndarray:
    """Convert BGR/BGRA image to RGB; return grayscale unchanged."""
    if img.ndim == 2:
        return img
    if img.shape[2] == 4:
        return cv2.cvtColor(img, cv2.COLOR_BGRA2RGB)
    return cv2.cvtColor(img, cv2.COLOR_BGR2RGB)


class OcrEngine:
    _readers: dict = {}  # lang_key → easyocr.Reader, lazy-loaded
    _readers_lock: threading.Lock = threading.Lock()  # guards lazy-init of _readers

    # Map internal key → EasyOCR language list
    _LANG_TO_EASYOCR: dict = {
        "de_en": ["de", "en"],
        "ja_en": ["ja", "en"],
        "zh_en": ["ch_sim", "en"],
        "zh_tra_en": ["ch_tra", "en"],
        "ko_en": ["ko", "en"],
    }

    @staticmethod
    def _lang_key(language: str) -> str:
        """Map a UI language code to an internal EasyOCR reader key."""
        return {"ja": "ja_en", "zh-Hant": "zh_en", "zh-Hans": "zh_en", "ko": "ko_en"}.get(language, "de_en")

    # EasyOCR sometimes replaces German umlauts/ß with block chars
    _CHAR_MAP = str.maketrans({
        "\u2584": "\u00dc",  # ▄ → Ü
        "\u2580": "\u00df",  # ▀ → ß
        "\u2588": "\u00dc",  # █ → Ü
        "\u258c": "\u00c4",  # ▌ → Ä
        "\u2590": "\u00d6",  # ▐ → Ö
    })

    # Words that appear on cards but are never the Pokémon name
    _NON_NAME_WORDS = {
        "HP", "ABILITY", "FÄHIGKEIT", "ATTACK", "ATTACKE", "WEAKNESS",
        "RESISTANCE", "RETREAT", "RÜCKZUG", "SCHWÄCHE", "DYNAMAX", "GIGANTAMAX",
        "STAGE", "BASIC", "BASIS", "EVOLVES", "PRIZE", "KNOCKED",
        # German stage/evolution labels
        "ENTWICKLUNG", "MEGA-ENTWICKLUNG", "STADIUM", "LEVEL",
        # Common card section headers that OCR may pick up first
        "TRAINER", "ITEM", "SUPPORTER", "STADIUM",
    }

    # Regex for card number patterns like 055/088 or SV123 (require ≥2 digits for letter prefix)
    _NUMBER_PATTERN = re.compile(r'\b(\d{1,3}/\d{1,3}|[A-Z]{1,3}\d{2,4})\b')

    # Splits CamelCase-merged OCR words, e.g. "UmbreonVax" → "Umbreon Vax".
    # Requires ≥3 chars in the first component and ≥2 in the second to avoid
    # false-splitting legitimate short sequences like "GX" or "EX".
    _CAMEL_SPLIT_RE = re.compile(r'([A-Z][a-z]{2,})([A-Z][a-z])')

    _CHAR_MAP_KEYS = frozenset({
        "\u2584", "\u2580", "\u2588", "\u258c", "\u2590",
    })

    def extract_number(self, image_path: Path, card_img: np.ndarray | None = None) -> str | None:
        """Try to read the card number (e.g. '055/088') from the image.
        Returns the best match as a string, or None.
        """
        base = card_img if card_img is not None else cv2.imread(str(Path(image_path).resolve()))
        if base is None:
            return None
        reader = self._get_reader()
        results = reader.readtext(
            _to_rgb(base),
            detail=1,
            paragraph=False,
            allowlist=_NUMBER_CHARS,
            text_threshold=0.4,
            low_text=0.3,
        )
        best_num: str | None = None
        best_conf: float = 0.0
        for r in results:
            m = self._NUMBER_PATTERN.search(r[1])
            if m and r[2] > best_conf:
                best_conf = r[2]
                best_num = m.group(1)
        _LOG.debug("extract_number: %r (conf=%.2f)", best_num, best_conf)
        return best_num

    def extract_text(self, image_path: Path, card_img: np.ndarray | None = None, language: str = "", zone: tuple[float, float, float, float] | None = None) -> dict[str, str]:
        from src.pokemon_scanner.recognition.preprocess import Preprocessor
        prep = Preprocessor()
        lang_key = self._lang_key(language)
        reader = self._get_reader(lang_key)
        is_cjk = lang_key != "de_en"

        # 1. Try name-zone crop first (uses pre-detected card if supplied)
        name_zone = prep.crop_name_zone(image_path, card_img=card_img, zone=zone)
        if name_zone is not None and name_zone.size > 0:
            _LOG.debug("Name-zone crop shape: %s", name_zone.shape)
            name = self._read_best(reader, name_zone, use_allowlist=not is_cjk)
            # Filter out any non-name stage/label words that crept into the crop
            name = self._filter_non_name_tokens(name)
            if name:
                _LOG.info("OCR name-zone result: %r", name)
                return {"name": name, "set": "", "number": ""}
            _LOG.warning("OCR name-zone returned empty, trying CLAHE+invert on name-zone")
            # Holographic / full-art cards: name zone has glittery background.
            # Try CLAHE-enhanced and inverted variants before giving up on the crop.
            gray_nz = cv2.cvtColor(name_zone, cv2.COLOR_BGR2GRAY) if name_zone.ndim == 3 else name_zone
            _clahe_nz = cv2.createCLAHE(clipLimit=4.0, tileGridSize=(4, 4))
            enh_nz = _clahe_nz.apply(gray_nz)
            _k = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]])
            enh_nz = cv2.filter2D(enh_nz, -1, _k)
            for _variant_nz in (enh_nz, cv2.bitwise_not(enh_nz)):
                _rgb_v = cv2.cvtColor(_variant_nz, cv2.COLOR_GRAY2RGB)
                _name_v = self._read_best(reader, _rgb_v, use_allowlist=not is_cjk)
                _name_v = self._filter_non_name_tokens(_name_v)
                if _name_v:
                    _LOG.info("OCR name-zone CLAHE result: %r", _name_v)
                    return {"name": _name_v, "set": "", "number": ""}
            _LOG.warning("OCR name-zone CLAHE also failed, falling back to top-strip")

        # Use warped card if available so y-positions are reliable, else raw image
        base_img = card_img if card_img is not None else cv2.imread(str(Path(image_path).resolve()))
        if base_img is None:
            _LOG.error("Could not read image: %s", image_path)
            return {"name": "", "set": "", "number": ""}

        h, w = base_img.shape[:2]
        card_detected = card_img is not None

        if card_detected:
            # Card is warped — top 15% + left 70% is reliably the name area
            scan_region = base_img[: int(h * 0.15), : int(w * 0.70)]
            conf_threshold = 0.2
        else:
            # Raw photo — card could be anywhere; scan full image, filter by position later
            scan_region = base_img
            conf_threshold = 0.10

        # 2. Scan the region
        h_sr = scan_region.shape[0]
        _scan_kw: dict = dict(
            detail=1,
            paragraph=False,
            min_size=max(5, int(h_sr * 0.04)),
            text_threshold=0.45,
            low_text=0.35,
        )
        if not is_cjk:
            _scan_kw["allowlist"] = _CARD_CHARS
        results = reader.readtext(_to_rgb(scan_region), **_scan_kw)
        _LOG.debug("Region OCR: %s", [(r[1], round(r[2], 2)) for r in results])

        def _is_valid_name_block(text: str, x: float, y: float, conf: float) -> bool:
            t = text.strip()
            if conf < conf_threshold:
                return False
            if not (2 < len(t) <= 40):
                return False
            if t.replace("/", "").replace("-", "").isdigit():
                return False
            if any(kw in t.upper() for kw in self._NON_NAME_WORDS):
                return False
            if not card_detected:
                # In raw photo, only trust upper 60% of the image (name is near card top)
                if x > w * 0.85 or y > h * 0.60:
                    return False
            return True

        candidates = [
            (r[0][0][1], r[0][0][0], self._clean(r[1].strip()), r[2])  # (y, x, text, conf)
            for r in results
            if _is_valid_name_block(r[1], r[0][0][0], r[0][0][1], r[2])
        ]

        if candidates:
            # Sort by y (topmost), then x — name is always topmost on card
            candidates.sort(key=lambda c: (c[0], c[1]))
            top_y = candidates[0][0]
            # Group all blocks within 20px of the topmost row (handles multi-word names)
            top_row = [t for y, x, t, _ in candidates if y <= top_y + 20]
            joined = self._reorder_name(" ".join(top_row)).strip()
            _LOG.info("OCR top-row result: %r (y=%.0f)", joined, top_y)
            return {"name": joined, "set": "", "number": ""}

        _LOG.warning("OCR found nothing usable in %s", image_path)
        # Last resort: CLAHE on the card image (detected or raw).
        # Always run this — holographic cards need it even after card detection.
        _base_clahe = card_img if card_detected else base_img
        _ch = _base_clahe.shape[0]
        _cw = _base_clahe.shape[1]
        gray_full = cv2.cvtColor(_base_clahe, cv2.COLOR_BGR2GRAY) if _base_clahe.ndim == 3 else _base_clahe
        clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
        enhanced = clahe.apply(gray_full)
        kernel = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]])
        enhanced = cv2.filter2D(enhanced, -1, kernel)
        _clahe_kw: dict = dict(
            detail=1,
            paragraph=False,
            min_size=max(5, int(_ch * 0.02)),
            text_threshold=0.30,
            low_text=0.20,
        )
        if not is_cjk:
            _clahe_kw["allowlist"] = _CARD_CHARS

        def _extract_top_candidate(results_list: list, img_h: int, img_w: int) -> str:
            c2 = [
                (r[0][0][1], r[0][0][0], self._clean(r[1].strip()), r[2])
                for r in results_list
                if r[2] > 0.08
                and 2 < len(r[1].strip()) <= 40
                and not r[1].strip().replace("-", "").isdigit()
                and not any(kw in r[1].upper() for kw in self._NON_NAME_WORDS)
                and r[0][0][0] <= img_w * 0.85
                and r[0][0][1] <= img_h * 0.70
            ]
            if not c2:
                return ""
            c2.sort(key=lambda c: (c[0], c[1]))
            ty = c2[0][0]
            row = [t for y, x, t, _ in c2 if y <= ty + 25]
            return self._reorder_name(" ".join(row)).strip()

        # Try CLAHE-enhanced, then inverted (helps with bright holographic backgrounds)
        _readers_to_try = [reader]
        if is_cjk and lang_key == "zh_en":
            # Also try Traditional Chinese reader as fallback for CHI cards
            _readers_to_try.append(self._get_reader("zh_tra_en"))
        for _rdr in _readers_to_try:
            for _variant in (enhanced, cv2.bitwise_not(enhanced)):
                _rgb2 = cv2.cvtColor(_variant, cv2.COLOR_GRAY2RGB)
                results2 = _rdr.readtext(_rgb2, **_clahe_kw)
                joined2 = _extract_top_candidate(results2, _ch, _cw)
                if joined2:
                    _LOG.info("OCR CLAHE-retry result: %r", joined2)
                    return {"name": joined2, "set": "", "number": ""}
        _LOG.warning("OCR failed completely for %s", image_path)
        return {"name": "", "set": "", "number": ""}

    def _filter_non_name_tokens(self, text: str) -> str:
        """Remove individual tokens that are stage labels / non-name words."""
        tokens = text.split()
        filtered = [t for t in tokens if t.upper() not in self._NON_NAME_WORDS]
        return self._reorder_name(" ".join(filtered)).strip()

    def _clean(self, text: str) -> str:
        if any(c in self._CHAR_MAP_KEYS for c in text):
            return text.translate(self._CHAR_MAP).strip()
        return text.strip()

    # Known card type suffixes that belong at the end of the name
    _SUFFIXES = {"VMAX", "VSTAR", "GX", "EX", "V", "TAG", "TEAM", "LEGEND"}

    def _reorder_name(self, text: str) -> str:
        tokens = text.split()
        name_parts = [t for t in tokens if t.upper() not in self._SUFFIXES]
        suffix_parts = [t for t in tokens if t.upper() in self._SUFFIXES]
        return " ".join(name_parts + suffix_parts).strip()

    def _read_best(self, reader, img: np.ndarray, use_allowlist: bool = True) -> str:
        h = img.shape[0]
        _kw: dict = dict(
            detail=1,
            paragraph=False,
            min_size=max(5, int(h * 0.05)),
            text_threshold=0.45,
            low_text=0.35,
        )
        if use_allowlist:
            _kw["allowlist"] = _CARD_CHARS
        results: list = reader.readtext(_to_rgb(img), **_kw)
        _LOG.debug("Name-zone OCR raw: %s", [(r[1], round(r[2], 2)) for r in results])

        # Compute bounding-box height for each plausible block.
        # The card name is always the LARGEST text on the card — filter to only
        # blocks whose height is ≥ 50 % of the tallest block found.
        items = []
        for r in results:
            bbox = r[0]  # [[x0,y0],[x1,y1],[x2,y2],[x3,y3]]
            text = r[1].strip()
            if r[2] <= 0.15 or len(text) < 2:
                continue
            if text.replace("-", "").replace("/", "").isdigit():
                continue
            ys = [pt[1] for pt in bbox]
            text_h = max(ys) - min(ys)
            x_left = bbox[0][0]
            items.append((text_h, x_left, self._clean(text), r[2]))

        if not items:
            return ""

        max_h = max(item[0] for item in items)
        # Keep only the large-font blocks (= Pokémon name); skip stage labels and
        # small evolution/flavour text that may also appear in the name zone crop.
        # Split CamelCase-merged words before joining — EasyOCR sometimes merges
        # two adjacent text regions into one block (e.g. "UmbreonVax" instead of
        # "Umbreon Vax" when the card's VMAX graphic sits directly next to the name).
        name_items = [(xl, self._CAMEL_SPLIT_RE.sub(r'\1 \2', t)) for th, xl, t, _ in items if th >= max_h * 0.50]
        name_items.sort(key=lambda x: x[0])  # left → right
        joined = self._reorder_name(" ".join(t for _, t in name_items)).strip()
        return joined

    @classmethod
    def _get_reader(cls, lang_key: str = "de_en"):
        # Fast path — no lock needed once the reader is cached
        if lang_key in cls._readers:
            return cls._readers[lang_key]
        with cls._readers_lock:
            # Double-checked: another thread may have loaded it while we waited
            if lang_key not in cls._readers:
                import easyocr
                langs = cls._LANG_TO_EASYOCR.get(lang_key, ["de", "en"])
                _LOG.info("Loading EasyOCR model %s \u2026", langs)
                cls._readers[lang_key] = easyocr.Reader(langs, verbose=False)
                _LOG.info("EasyOCR model %s loaded", langs)
        return cls._readers[lang_key]
