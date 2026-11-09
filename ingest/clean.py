"""Text cleaning shared by the text-layer and OCR paths."""
from __future__ import annotations

import re
import unicodedata

WATERMARK_RE = re.compile(r"(?i)\bFOR\s+ONLINE\s+USE\s+ONLY\b|\bDO\s+NOT\s+DUPLICATE\b")
# InDesign footer/header slugs: "Science and Technology 3.indd 23   30/07/2021 15:27", "09/12/2023 15:40"
INDD_RE = re.compile(r"(?i)\.indd\b")
STAMP_RE = re.compile(r"^\s*\d{1,2}/\d{1,2}/\d{2,4}(\s+\d{1,2}:\d{2})?\s*$")
PAGE_NO_RE = re.compile(r"^\s*(\d{1,3}|[ivxlc]{1,6})\s*$", re.I)
_SOFT_HYPHEN = "­"
_CTRL = re.compile(r"[\u0000-\u0008\u000b\u000c\u000e-\u001f​‎‏﻿�]")


def is_junk_line(line: str) -> bool:
    s = line.strip()
    if not s:
        return False
    return bool(INDD_RE.search(s) or STAMP_RE.match(s) or WATERMARK_RE.fullmatch(s))


def strip_watermark(text: str) -> str:
    return WATERMARK_RE.sub(" ", text)


def normalize(text: str) -> str:
    """NFC, drop control/zero-width chars, soft hyphens, tabs->space, collapse blanks; keep paragraph breaks."""
    text = unicodedata.normalize("NFC", text).replace(_SOFT_HYPHEN, "")
    text = _CTRL.sub("", text).replace("\t", " ").replace(" ", " ")
    text = re.sub(r"\bChapt er\b", "Chapter", text)          # letter-spaced chapter headings
    text = re.sub(r"[ ]{2,}", " ", text)
    text = re.sub(r"[ ]+\n", "\n", text)
    return text.strip()


def join_lines(lines: list[str]) -> str:
    """Join wrapped lines into a paragraph, repairing 'hyphen-\\nated' words."""
    out = ""
    for ln in (l.strip() for l in lines if l.strip()):
        if out.endswith("-") and ln[:1].islower() and len(out) > 1 and out[-2].isalpha():
            out = out[:-1] + ln
        else:
            out = f"{out} {ln}" if out else ln
    return out


_TAG = re.compile(r"<[^>]+>")


def html_table_to_text(html: str) -> str:
    """OCR tables arrive as HTML; flatten to 'a | b' rows without images."""
    html = re.sub(r"<img[^>]*>", "", html)
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", html, flags=re.S | re.I)
    if not rows:
        return normalize(_TAG.sub(" ", html))
    out = []
    for r in rows:
        cells = [normalize(_TAG.sub(" ", c)) for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", r, flags=re.S | re.I)]
        if any(cells):
            out.append(" | ".join(cells))
    return "\n".join(out)


def is_garbage(text: str) -> bool:
    """True for chunks with no usable content (page-number soup, symbol runs, OCR noise).
    Maths is content: digits and operators count, so number sequences and sums are kept."""
    meaningful = sum(c.isalnum() for c in text)
    letters = sum(c.isalpha() for c in text)
    if meaningful < 15:
        return True
    return meaningful / max(len(text), 1) < 0.4 or (letters == 0 and not re.search(r"\d\s*[+\-×÷=x*/]\s*\d", text))
