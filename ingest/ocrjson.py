"""Read PaddleOCR-VL per-page JSON (parsed-output/json/*_res.json) into the same Block model as the text layer."""
from __future__ import annotations

import glob
import json
import os
import re

from . import clean
from .model import Block, BookText, PageInfo

SKIP = {"image", "header_image", "footer_image", "seal", "chart", "header", "footer", "number", "aside_text",
        "formula_number"}
HEADING = {"doc_title": 1, "paragraph_title": 2}


def _page_no(path: str) -> int:
    m = re.search(r"_(\d+)_res\.json$", path)
    return int(m.group(1)) if m else -1


def read_book(json_dir: str, page_map: dict | None = None) -> BookText:
    """page_map maps the OCR run's 0-based page index to the original PDF page (1-based) when only a subset of
    the pages was OCRed (mixed books)."""
    book = BookText(method="ocr")
    files = sorted(glob.glob(os.path.join(json_dir, "*_res.json")), key=_page_no)
    for f in files:
        j = json.load(open(f, encoding="utf-8"))
        idx = int(j.get("page_index", _page_no(f)))
        page = int(page_map[str(idx)]) if page_map and str(idx) in page_map else idx + 1
        printed, chars = "", 0
        blocks = []
        for b in sorted(j.get("parsing_res_list", []), key=lambda x: x.get("block_order") if x.get("block_order") is not None else 10**6):
            label, content = b.get("block_label", ""), b.get("block_content", "") or ""
            if label == "number":
                if clean.PAGE_NO_RE.match(content.strip()):
                    printed = content.strip()
                continue
            if label in SKIP:
                continue
            if label == "table":
                text = clean.html_table_to_text(content)
            else:
                text = clean.normalize(clean.strip_watermark(content))
            if not text or clean.is_junk_line(text):
                continue
            if label in HEADING:
                kind, level = "heading", HEADING[label]
            elif label == "table":
                kind, level = "table", 0
            elif label in ("figure_title", "vision_footnote"):
                kind, level = "caption", 0
            else:
                kind, level = "para", 0
            chars += len(re.sub(r"\s", "", text))
            blocks.append(Block(page=page, kind=kind, text=text, level=level))
        for b in blocks:
            b.printed_page = printed
        book.blocks += blocks
        book.pages.append(PageInfo(page, chars, 0.0, False, printed))
    return book
