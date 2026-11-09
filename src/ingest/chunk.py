"""Structure-aware chunking: sections never cross chapter/section headings, paragraphs are kept whole, small
sections are merged, oversize paragraphs are split on sentence boundaries, and every chunk carries its heading
path plus a context-prefixed `embed_text`."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

from . import clean
from .model import Block

TARGET_CHARS = 1400        # ~350-400 tokens for English/Kiswahili
MAX_CHARS = 2000
MIN_CHARS = 350
OVERLAP_CHARS = 200        # last paragraph of the previous chunk is repeated if at most this long

_FRONT = re.compile(r"(?i)^(yaliyomo|contents?|table of contents|shukurani|acknowledg\w*|utangulizi|introduction|"
                    r"dibaji|foreword|preface|neno la|dedication)\b")
_BACK = re.compile(r"(?i)^(marejeleo|references?|bibliography|other books|vitabu vingine|orodha ya vitabu)\b")
_BOILER = re.compile(r"(?i)ISBN|haki zote zimehifadhiwa|all rights reserved|taasisi ya elimu tanzania \(tet\) inatambua|"
                     r"cheti cha ithibati|accreditation|iliyochapishwa na|published by|mchapishaji|©|\(c\) ?\d{4}")
_CHAPTER = re.compile(r"(?i)^(chapter|chapt\s?er|sura|unit|lesson|somo|mada|kitengo|topic|part|sehemu|theme|module|"
                      r"unit\s*\d|lesson\s*\d)\b")
_SENT = re.compile(r"(?<=[.!?])\s+(?=[A-ZÀ-Ý0-9\"“(])")


@dataclass
class Chunk:
    text: str
    page_start: int
    page_end: int
    section_path: list[str]
    section_type: str = "content"   # content | front_matter | references
    kinds: set = field(default_factory=set)
    printed_page_start: str = ""
    printed_page_end: str = ""


def _split_long(text: str) -> list[str]:
    if len(text) <= MAX_CHARS:
        return [text]
    out, cur = [], ""
    for s in _SENT.split(text):
        if cur and len(cur) + len(s) + 1 > TARGET_CHARS:
            out.append(cur)
            cur = s
        else:
            cur = f"{cur} {s}".strip()
    if cur:
        out.append(cur)
    # a "sentence" can itself exceed MAX (OCR without punctuation): hard split on whitespace
    final = []
    for piece in out:
        while len(piece) > MAX_CHARS:
            cut = piece.rfind(" ", 0, TARGET_CHARS) or TARGET_CHARS
            final.append(piece[:cut])
            piece = piece[cut:].strip()
        final.append(piece)
    return final


def chunk_blocks(blocks: list[Block]) -> list[Chunk]:
    chunks: list[Chunk] = []
    stack: list[tuple[int, str]] = []          # (level, title)
    cur: list[tuple[str, Block]] = []          # (piece, block)
    cur_len = 0
    # Until the first chapter-like heading, everything is title page / acknowledgements / TOC / preface.
    has_chapters = any(b.kind == "heading" and _CHAPTER.match(b.text.strip()) for b in blocks)
    section_type = "front_matter" if has_chapters else "content"
    seen_content_heading = False

    def path():
        return [t for _, t in stack]

    def flush(carry=True):
        nonlocal cur, cur_len
        if not cur:
            return
        body = "\n\n".join(t for t, _ in cur)
        pages = [b.page for _, b in cur]
        pp = [b.printed_page for _, b in cur if b.printed_page]
        chunks.append(Chunk(body, min(pages), max(pages), path(), section_type, {b.kind for _, b in cur},
                            pp[0] if pp else "", pp[-1] if pp else ""))
        last = cur[-1]
        cur, cur_len = ([last], len(last[0])) if carry and len(last[0]) <= OVERLAP_CHARS and last[1].kind == "para" else ([], 0)
        if cur and len(chunks[-1].text) < MIN_CHARS:
            cur, cur_len = [], 0

    for b in blocks:
        if b.kind == "heading":
            title = b.text.strip()
            if sum(c.isalpha() for c in title) < 3:       # '7', '(a)', OCR title fragments
                continue
            flush(carry=False)
            if _CHAPTER.match(title):               # a chapter heading restarts the path (drops the book title)
                stack.clear()
                stack.append((0, title))
            else:
                while stack and stack[-1][0] >= max(b.level, 1):
                    stack.pop()
                stack.append((max(b.level, 1), title))
            if _BACK.match(title):
                section_type = "references"
            elif _CHAPTER.match(title):
                section_type, seen_content_heading = "content", True
            elif _FRONT.match(title) and not seen_content_heading:
                section_type = "front_matter"
            elif seen_content_heading or not has_chapters:
                section_type = "content"
            continue
        for piece in _split_long(b.text):
            if cur and cur_len + len(piece) + 2 > MAX_CHARS or (cur and cur_len >= TARGET_CHARS):
                flush()
            cur.append((piece, Block(b.page, b.kind, piece, printed_page=b.printed_page)))
            cur_len += len(piece) + 2
    flush(carry=False)
    return _merge_small(chunks)


def _common(a: list[str], b: list[str]) -> list[str]:
    out = []
    for x, y in zip(a, b):
        if x != y:
            break
        out.append(x)
    return out


def _merge_small(chunks: list[Chunk]) -> list[Chunk]:
    """Pack neighbouring sections of one chapter into chunks up to TARGET_CHARS (runts up to MAX_CHARS).
    When the sub-heading differs, its title is written into the text so it is not lost."""
    out: list[Chunk] = []
    for c in chunks:
        fits = len(out[-1].text) + len(c.text) + 2 <= (MAX_CHARS if len(c.text) < MIN_CHARS else TARGET_CHARS) if out else False
        if (out and fits and out[-1].section_type == c.section_type
                and out[-1].section_path[:1] == c.section_path[:1]):
            p = out[-1]
            if c.section_path != p.section_path and c.section_path:
                p.text += f"\n\n{c.section_path[-1]}\n{c.text}"
                p.section_path = _common(p.section_path, c.section_path)
            else:
                p.text += "\n\n" + c.text
            p.page_end = max(p.page_end, c.page_end)
            p.kinds |= c.kinds
            p.printed_page_end = c.printed_page_end or p.printed_page_end
        else:
            out.append(c)
    return out


def classify_chunk(c: Chunk) -> str:
    """'' when the chunk should be embedded, else the reason it is excluded."""
    if len(re.findall(r"[.…]{5,}\s*\d", c.text)) >= 2:       # table-of-contents leader dots
        return "toc"
    if clean.is_garbage(c.text):
        return "garbage"
    if c.section_type == "front_matter":
        return "front_matter"
    if c.section_type == "references":
        return "references"
    if c.page_start <= 12 and _BOILER.search(c.text):
        return "boilerplate"
    return ""


FIGURE_RE = re.compile(r"(?i)\b(figure|fig\.|picture|kielelezo|picha|mchoro)\s*\d")


def has_figure(text: str, kinds=()) -> bool:
    """Figure referenced or captioned in this chunk (text-layer books have no caption blocks, so look at the text)."""
    return "caption" in kinds or bool(FIGURE_RE.search(text))


def content_hash(text: str) -> str:
    return hashlib.sha1(re.sub(r"\W+", "", text.lower()).encode()).hexdigest()[:16]
