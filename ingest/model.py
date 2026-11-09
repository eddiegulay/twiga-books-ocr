from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Block:
    """One structural unit of a page, in reading order."""
    page: int                 # 1-based PDF page index
    kind: str                 # heading | para | table | caption
    text: str
    level: int = 0            # heading level (1 = chapter, 2 = section...), 0 for non-headings
    printed_page: str = ""
    size: float = 0.0         # dominant font size (text-layer only), used to rank headings


@dataclass
class PageInfo:
    page: int
    text_chars: int           # characters left after watermark removal
    image_cover: float        # fraction of the page area covered by images (0..1)
    needs_ocr: bool
    printed_page: str = ""


@dataclass
class BookText:
    blocks: list[Block] = field(default_factory=list)
    pages: list[PageInfo] = field(default_factory=list)
    method: str = ""          # text-layer | ocr | mixed
    errors: list[str] = field(default_factory=list)   # per-page extraction failures
