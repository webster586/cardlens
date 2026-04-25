"""One-shot patch for main_window.py image-loading block."""
import re

path = r"c:\Users\Coin\Desktop\PokemonCardScanner\pokemon_scanner_repo\src\pokemon_scanner\ui\main_window.py"

with open(path, encoding="utf-8") as f:
    src = f.read()

# ── 1. Replace the old image-loading block ──────────────────────────────────
old_block = re.compile(
    r"        # Load card image \u2014 prefer local file, fall back to HTTP download\n"
    r'        self\.lbl_card_image\.setText\("Lade[^\n]+\n'
    r'        self\.lbl_card_image\.setPixmap\(QPixmap\(\)\)\n'
    r'        img = candidate\.image_url or ""\n'
    r'        if img and Path\(img\)\.exists\(\):[^\n]+\n'
    r"            # Local file \u2014 load directly, no network needed\n"
    r'            px = QPixmap\(img\)\.scaled\([^\n]+\n'
    r'                self\.lbl_card_image\.size\(\), Qt\.KeepAspectRatio, Qt\.SmoothTransformation\n'
    r'            \)\n'
    r'            if px\.isNull\(\):\n'
    r'                self\.lbl_card_image\.setText\("[^\n]+\n'
    r'            else:\n'
    r'                self\.lbl_card_image\.setPixmap\(px\)\n'
    r'                self\.lbl_card_image\.setText\(""\)\n'
    r'        elif img and img\.startswith\("http"\):\n'
    r'            worker = ImageDownloadWorker\(img\)\n'
    r'            self\._image_dl_workers\.append\(worker\)\n'
    r'            worker\.finished\.connect\(self\._on_card_image_loaded\)\n'
    r'            worker\.finished\.connect\(lambda _px, w=worker: self\._image_dl_workers\.remove\(w\) if w in self\._image_dl_workers else None\)\n'
    r'            worker\.start\(\)\n'
    r'        else:\n'
    r'            self\.lbl_card_image\.setText\("Kein Bild"\)',
    re.DOTALL
)

new_block = (
    "        # Load card image \u2014 prefer local catalog file, fall back to HTTP download\n"
    '        self.lbl_card_image.setText("Lade\u2026")\n'
    "        self.lbl_card_image.setPixmap(QPixmap())\n"
    '        img = candidate.image_url or ""\n'
    "        # Try local catalog first (no network)\n"
    "        pix = load_card_pixmap(candidate.api_id, stored_hint=img if not img.startswith(\"http\") else None)\n"
    "        if pix and not pix.isNull():\n"
    "            self.lbl_card_image.setPixmap(\n"
    "                pix.scaled(self.lbl_card_image.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)\n"
    "            )\n"
    '            self.lbl_card_image.setText("")\n'
    '        elif img and img.startswith("http") and candidate.api_id:\n'
    "            worker = CardImageDownloadWorker(candidate.api_id, img)\n"
    "            self._image_dl_workers.append(worker)\n"
    "            worker.done.connect(self._on_card_image_loaded)\n"
    "            worker.done.connect(lambda _p, w=worker: self._image_dl_workers.remove(w) if w in self._image_dl_workers else None)\n"
    "            worker.start()\n"
    "        else:\n"
    '            self.lbl_card_image.setText("Kein Bild")'
)

new_src, n = old_block.subn(new_block, src)
if n == 0:
    print("ERROR: pattern not found — no replacement made")
    raise SystemExit(1)
print(f"Replaced image-loading block ({n} occurrence(s))")

with open(path, "w", encoding="utf-8") as f:
    f.write(new_src)

print("Done.")
