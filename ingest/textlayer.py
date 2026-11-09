"""Extract structured blocks from a PDF's embedded text layer, without the 'FOR ONLINE USE ONLY' watermark.

Watermark facts (verified across the corpus): the diagonal copy is rotated 45 deg (matrix b/c != 0) and the
top-of-page copy is non-embedded Helvetica (no 'ABCDEF+' subset prefix). Body text is axis-aligned and embedded.
"""
from __future__ import annotations

import re
from collections import Counter

from . import clean
from .model import Block, BookText, PageInfo

MIN_TEXT_CHARS = 50          # below this a page is treated as having no text layer
OCR_IMAGE_COVER = 0.5        # ...and if an image covers this much of the page it is a scan -> needs OCR
_WM_ONLY = re.compile(r"(FOR ONLINE USE ONLY|DO NOT DUPLICATE|\s)+")


def _is_rotated(ch) -> bool:
    m = ch["matrix"]
    return abs(m[1]) > 0.01 or abs(m[2]) > 0.01


_WM_STREAM = re.compile(r"FORONLINEUSEONLY|DONOTDUPLICATE")


def clean_page_chars(page):
    """Page filtered to real content: drop overprinted duplicates, rotated glyphs, and the horizontal watermark.
    The horizontal copy is found in content-stream order (its glyphs are contiguous there even when it overlaps a
    heading on the page), not by position."""
    page = page.dedupe_chars(tolerance=1)      # faux-bold overprint: 'SSCCIIEENNCCEE' -> 'SCIENCE'
    chars = page.chars
    letters, owner = [], []                  # one entry per output letter; ligatures expand to several
    for c in chars:
        for ch in c["text"].upper():
            if not ch.isspace():
                letters.append(ch)
                owner.append(c)
    drop = set()
    for m in _WM_STREAM.finditer("".join(letters)):
        drop.update(id(owner[k]) for k in range(m.start(), m.end()))
    return page.filter(lambda o: o.get("object_type") != "char" or (not _is_rotated(o) and id(o) not in drop))


def image_cover(page) -> float:
    area = (page.width * page.height) or 1
    total = 0.0
    for im in page.images:
        w = max(0.0, min(im["x1"], page.width) - max(im["x0"], 0))
        h = max(0.0, min(im["bottom"], page.height) - max(im["top"], 0))
        total += w * h
    return min(1.0, total / area)


def _line_info(line) -> tuple[str, float, bool]:
    chars = [c for c in line["chars"] if c["text"].strip()]
    if not chars:
        return "", 0.0, False
    sizes = Counter(round(c["size"], 1) for c in chars)
    size = sizes.most_common(1)[0][0]
    bold = sum("bold" in c["fontname"].lower() for c in chars) / len(chars) > 0.6
    return line["text"], size, bold


def extract_book(pdf_path: str, page_numbers: list[int] | None = None) -> BookText:
    """Blocks (paragraphs/headings) for every page, plus per-page text/OCR diagnostics.
    Heading levels are assigned later by `assign_heading_levels` once the body size is known."""
    import pdfplumber

    book = BookText(method="text-layer")
    raw_lines: list[tuple[int, str, float, bool, float, float]] = []   # page,text,size,bold,top,gap_before
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            if page_numbers and i not in page_numbers:
                continue
            try:
                cp = clean_page_chars(page)
                lines = cp.extract_text_lines(layout=False, strip=True, return_chars=True, x_tolerance_ratio=0.15, y_tolerance=3)
            except Exception as e:      # bad page: flag it for OCR, keep the book, and say so in the manifest
                book.errors.append(f"p{i}: {type(e).__name__}: {e}"[:160])
                book.pages.append(PageInfo(i, 0, image_cover(page), True))
                continue
            kept, printed = [], ""
            edge_h = page.height
            for ln in lines:
                text, size, bold = _line_info(ln)
                text = clean.normalize(clean.strip_watermark(text))
                if not text or clean.is_junk_line(text):
                    continue
                if clean.PAGE_NO_RE.match(text) and (ln["top"] < 60 or ln["bottom"] > page.height - 70):
                    printed = text.strip()
                    continue
                kept.append((text, size, bold, ln["top"], ln["bottom"], ln["top"] < 70 or ln["bottom"] > edge_h - 70))
            chars = sum(len(re.sub(r"\s", "", t[0])) for t in kept)
            cover = image_cover(page)
            book.pages.append(PageInfo(i, chars, cover, chars < MIN_TEXT_CHARS and cover >= OCR_IMAGE_COVER, printed))
            prev_bottom = None
            for text, size, bold, top, bottom, edge in kept:
                gap = (top - prev_bottom) if prev_bottom is not None else 999.0
                raw_lines.append((i, text, size, bold, top, gap, edge))
                prev_bottom = bottom
    raw_lines = _drop_running(raw_lines, len(book.pages))
    book.blocks = _group_blocks(raw_lines, {p.page: p.printed_page for p in book.pages})
    assign_heading_levels(book.blocks)
    return book


def _drop_running(lines, n_pages):
    """Drop page-edge lines that repeat on many pages (running titles, 'TAASISI YA ELIMU TANZANIA 2021')."""
    key = lambda t: re.sub(r"\d+", "#", t.lower()).strip()
    seen: dict[str, set] = {}
    for page, text, *_rest, edge in lines:
        if edge:
            seen.setdefault(key(text), set()).add(page)
    bad = {k for k, v in seen.items() if len(v) >= max(4, 0.06 * n_pages)}
    return [l for l in lines if not (l[-1] and key(l[1]) in bad)]


def _group_blocks(lines, printed) -> list[Block]:
    """Merge consecutive lines into paragraphs; a style change or a big vertical gap starts a new block."""
    sizes = Counter(round(l[2]) for l in lines if len(l[1]) > 25)
    body = sizes.most_common(1)[0][0] if sizes else 12
    blocks: list[Block] = []
    cur: list[str] = []
    cur_meta = None
    gap_break = max(body * 0.9, 8)

    def flush():
        nonlocal cur, cur_meta
        if cur:
            page, kind, size = cur_meta
            text = clean.join_lines(cur)
            if text:
                blocks.append(Block(page=page, kind=kind, text=text, level=0,
                                    printed_page=printed.get(page, ""), size=size))
        cur, cur_meta = [], None

    for page, text, size, bold, top, gap, _edge in lines:
        is_head = (size >= body * 1.2 or (bold and len(text) < 60 and not text.endswith((".", ",", ";", ":")))) and len(text) < 120
        kind = "heading" if is_head else "para"
        new_block = (cur_meta is None or page != cur_meta[0] or kind != cur_meta[1]
                     or (kind == "heading" and abs(size - cur_meta[2]) > 0.6)
                     or (kind == "para" and gap > gap_break))
        if new_block:
            flush()
            cur_meta = (page, kind, size)
        cur.append(text)
    flush()
    return blocks


def assign_heading_levels(blocks: list[Block]) -> None:
    """Rank distinct heading sizes: biggest = level 1, next = 2, then 3 (cap)."""
    sizes = sorted({round(b.size) for b in blocks if b.kind == "heading"}, reverse=True)
    for b in blocks:
        if b.kind == "heading":
            b.level = min(sizes.index(round(b.size)) + 1, 3) if sizes else 2
