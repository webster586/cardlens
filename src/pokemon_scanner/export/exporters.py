from __future__ import annotations

import csv
import json
from pathlib import Path

from openpyxl import Workbook


EXPORT_HEADERS = [
    "id",
    "name",
    "set_name",
    "card_number",
    "language",
    "quantity",
    "last_price",
    "price_currency",
    "notes",
    "image_path",
    "created_at",
    "updated_at",
]


def export_csv(rows: list[dict], destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=EXPORT_HEADERS)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in EXPORT_HEADERS})
    return destination


def export_json(rows: list[dict], destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    return destination


def export_xlsx(rows: list[dict], destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    ws = wb.active
    ws.title = "Collection"
    ws.append(EXPORT_HEADERS)
    for row in rows:
        ws.append([row.get(key) for key in EXPORT_HEADERS])
    wb.save(destination)
    return destination
