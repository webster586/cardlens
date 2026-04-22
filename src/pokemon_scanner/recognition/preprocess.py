from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from src.pokemon_scanner.core.paths import RUNTIME_DIR


class Preprocessor:
    def prepare(self, image_path: Path) -> Path:
        return image_path

    def detect_card_to_file(self, image_path: Path) -> tuple[np.ndarray | None, Path | None]:
        """Detect the card in *image_path*, warp it, save to runtime/ and return (card_img, warp_path).

        Returns (None, None) when no 4-corner contour is found.
        """
        img = cv2.imread(str(image_path))
        if img is None:
            return None, None
        card = self._detect_card(img)
        if card is None:
            return None, None
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        warp_path = RUNTIME_DIR / "warp_preview.jpg"
        cv2.imwrite(str(warp_path), card)
        return card, warp_path

    def crop_name_zone(
        self,
        image_path: Path,
        card_img: np.ndarray | None = None,
        zone: tuple[float, float, float, float] | None = None,
    ) -> np.ndarray | None:
        """Return a scaled crop of the name zone.

        If *card_img* is provided (already detected/warped), it is used directly
        instead of running card detection again.
        *zone* is a (x1, y1, x2, y2) tuple of 0–1 fractions; when omitted the
        default Pokémon-card name area is used.
        """
        if card_img is not None:
            base = card_img
        else:
            img = cv2.imread(str(image_path))
            if img is None:
                return None
            card = self._detect_card(img)
            base = card if card is not None else img
        h, w = base.shape[:2]
        if zone is not None:
            x1, y1, x2, y2 = (
                int(w * zone[0]), int(h * zone[1]),
                int(w * zone[2]), int(h * zone[3]),
            )
        else:
            # Pokémon card: name is top ~3–14%, left ~4–60%
            # HP value sits at ~62–75% width — keep x2 at 60% to exclude it
            y1, y2 = int(h * 0.03), int(h * 0.15)
            x1, x2 = int(w * 0.04), int(w * 0.60)
        crop = base[y1:y2, x1:x2]
        # Scale up for better OCR accuracy
        scale = max(1, int(200 / (y2 - y1)))
        return cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)

    def _detect_card(self, img: np.ndarray) -> np.ndarray | None:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        img_area = img.shape[0] * img.shape[1]

        # Try multiple preprocessing approaches for robustness
        edge_images: list[np.ndarray] = []

        # 1. Standard Canny
        blurred = cv2.GaussianBlur(gray, (5, 5), 0)
        edge_images.append(cv2.Canny(blurred, 40, 150))

        # 2. Looser Canny (helps with shadows / low contrast)
        edge_images.append(cv2.Canny(blurred, 20, 80))

        # 3. Adaptive threshold → edges via morphological gradient
        adap = cv2.adaptiveThreshold(blurred, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                     cv2.THRESH_BINARY_INV, 11, 2)
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
        edge_images.append(cv2.dilate(adap, kernel, iterations=1))

        for edges in edge_images:
            # Close small gaps in edges to improve contour connectivity
            closed = cv2.morphologyEx(edges, cv2.MORPH_CLOSE,
                                      cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5)))
            contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            if not contours:
                continue
            for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:8]:
                if cv2.contourArea(contour) < img_area * 0.05:
                    break
                peri = cv2.arcLength(contour, True)
                # Try multiple epsilon values — aggressive photos need higher epsilon
                for eps in (0.02, 0.03, 0.04, 0.01):
                    approx = cv2.approxPolyDP(contour, eps * peri, True)
                    if len(approx) == 4:
                        warped = self._four_point_transform(img, approx.reshape(4, 2))
                        if warped is not None:
                            h, w = warped.shape[:2]
                            # Sanity check: card aspect ratio ~0.68–0.78 (portrait)
                            ratio = w / h if h > 0 else 0
                            if 0.55 <= ratio <= 0.90:
                                return warped
        return None

    def _four_point_transform(self, img: np.ndarray, pts: np.ndarray) -> np.ndarray | None:
        rect = self._order_points(pts)
        (tl, tr, br, bl) = rect
        widthA = float(np.sqrt(((br[0] - bl[0]) ** 2) + ((br[1] - bl[1]) ** 2)))
        widthB = float(np.sqrt(((tr[0] - tl[0]) ** 2) + ((tr[1] - tl[1]) ** 2)))
        maxWidth = max(int(widthA), int(widthB))
        heightA = float(np.sqrt(((tr[0] - br[0]) ** 2) + ((tr[1] - br[1]) ** 2)))
        heightB = float(np.sqrt(((tl[0] - bl[0]) ** 2) + ((tl[1] - bl[1]) ** 2)))
        maxHeight = max(int(heightA), int(heightB))
        if maxWidth < 10 or maxHeight < 10:
            return None
        dst = np.array([[0, 0], [maxWidth - 1, 0], [maxWidth - 1, maxHeight - 1], [0, maxHeight - 1]], dtype="float32")
        M = cv2.getPerspectiveTransform(rect.astype("float32"), dst)
        return cv2.warpPerspective(img, M, (maxWidth, maxHeight))

    def _order_points(self, pts: np.ndarray) -> np.ndarray:
        rect = np.zeros((4, 2), dtype="float32")
        s = pts.sum(axis=1)
        rect[0] = pts[np.argmin(s)]
        rect[2] = pts[np.argmax(s)]
        diff = np.diff(pts, axis=1)
        rect[1] = pts[np.argmin(diff)]
        rect[3] = pts[np.argmax(diff)]
        return rect
