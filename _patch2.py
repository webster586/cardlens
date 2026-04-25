"""Patch main_window.py image-loading block — line-range replacement."""
path = r"c:\Users\Coin\Desktop\PokemonCardScanner\pokemon_scanner_repo\src\pokemon_scanner\ui\main_window.py"

with open(path, encoding="utf-8") as f:
    lines = f.readlines()

# Verify expected content at lines 1173-1194 (0-indexed: 1172-1193)
assert 'prefer local file' in lines[1172], f"Unexpected line 1173: {lines[1172]!r}"
assert 'ImageDownloadWorker' in lines[1187], f"Unexpected line 1188: {lines[1187]!r}"

new_block = [
    '        # Load card image \u2014 prefer local catalog file, fall back to HTTP download\n',
    '        self.lbl_card_image.setText("Lade\\u2026")\n',
    '        self.lbl_card_image.setPixmap(QPixmap())\n',
    '        img = candidate.image_url or ""\n',
    '        # Try local catalog first (no network)\n',
    '        pix = load_card_pixmap(candidate.api_id, stored_hint=img if not img.startswith("http") else None)\n',
    '        if pix and not pix.isNull():\n',
    '            self.lbl_card_image.setPixmap(\n',
    '                pix.scaled(self.lbl_card_image.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)\n',
    '            )\n',
    '            self.lbl_card_image.setText("")\n',
    '        elif img and img.startswith("http") and candidate.api_id:\n',
    '            worker = CardImageDownloadWorker(candidate.api_id, img)\n',
    '            self._image_dl_workers.append(worker)\n',
    '            worker.done.connect(self._on_card_image_loaded)\n',
    '            worker.done.connect(lambda _p, w=worker: self._image_dl_workers.remove(w) if w in self._image_dl_workers else None)\n',
    '            worker.start()\n',
    '        else:\n',
    '            self.lbl_card_image.setText("Kein Bild")\n',
]

# Replace lines 1173-1194 (0-indexed 1172-1193, 22 lines)
lines[1172:1194] = new_block

with open(path, "w", encoding="utf-8") as f:
    f.writelines(lines)

print(f"Done. File now has {len(lines)} lines.")
