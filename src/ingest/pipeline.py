"""Book -> chunks pipeline.

Per page, the cheapest reliable source wins: embedded text layer (watermark removed) when present, PaddleOCR-VL
only for pages that are scans. Identical files are collapsed, catalogue language labels are cross-checked, and
everything is written as JSONL plus a manifest that records what happened to every book."""
from __future__ import annotations

import csv
import glob
import json
import os
import re
from concurrent.futures import ProcessPoolExecutor

from . import SCHEMA_VERSION, chunk as C, lang, ocrjson, textlayer
from .model import BookText

OCR_COVERAGE_MIN = 0.5      # share of pages with text below which the book is routed to OCR as a whole


# ---------------------------------------------------------------------------------------------- discovery
def discover(books_dir: str, catalogue: str | None = None) -> list[dict]:
    """One dict per source PDF with catalogue/filename metadata (reuses the OCR queue's logic)."""
    import twiga_ocr_mac as T
    T.configure(books_dir=books_dir, output_dir=os.path.join(books_dir, "parsed-books"), catalogue=catalogue)
    rows = T.scan_books()
    for r in rows:
        meta = T.CATALOGUE_META.get(r["file_name"]) or {}
        r["book_type"] = meta.get("book_type", "")
        r["copy_quality"] = meta.get("copy_quality", "")
        low = r["drive_path"].lower()
        r["audience"] = "teacher" if ("teaching guide" in low or "teachersguide" in low or "mwongozo" in low
                                      or "kiongozi" in low or "guide" in (r["book_type"] or "").lower()) else "student"
        r["book_id"] = re.sub(r"[^\w.-]+", "-", os.path.splitext(r["drive_path"])[0]).strip("-")
    return rows


def find_ocr_dir(row: dict, roots: list[str]) -> str:
    stem = os.path.splitext(row["file_name"])[0]
    for root in roots:
        pat = os.path.join(root, "**", stem, "parsed-output", "json")
        for hit in sorted(glob.glob(pat, recursive=True)):
            if row["class"] in hit.split(os.sep) and glob.glob(os.path.join(hit, "*_res.json")):
                return hit
    return ""


# ---------------------------------------------------------------------------------------------- extraction
def _extract(args) -> tuple[str, BookText | None, str]:
    book_id, path = args
    try:
        return book_id, textlayer.extract_book(path), ""
    except Exception as e:      # unreadable PDF: reported in the manifest, never fatal
        return book_id, None, f"{type(e).__name__}: {e}"[:300]


def route(book: BookText, n_pages: int) -> tuple[str, list[int]]:
    """('text'|'mixed'|'ocr', pages that need OCR). A page needs OCR if it has no text layer but is mostly image."""
    with_text = sum(p.text_chars >= textlayer.MIN_TEXT_CHARS for p in book.pages)
    # covers (first/last page) are title art; the title comes from the catalogue, so they never justify OCR
    need = [p.page for p in book.pages if p.needs_ocr and p.page not in (1, n_pages)]
    if n_pages and with_text / n_pages < OCR_COVERAGE_MIN:
        return "ocr", [p.page for p in book.pages if p.text_chars < textlayer.MIN_TEXT_CHARS and p.image_cover >= 0.2] or need
    return ("mixed" if need else "text"), need


def merge_ocr(book: BookText, ocr: BookText, pages: set[int] | None) -> BookText:
    """Combine text-layer and OCR content. pages=None: the whole book comes from OCR (scans). Otherwise OCR
    replaces only the listed pages and every other page keeps its text-layer blocks."""
    if pages is None:
        return BookText(blocks=sorted(ocr.blocks, key=lambda b: b.page), pages=ocr.pages or book.pages, method="ocr",
                        errors=book.errors)
    keep = [b for b in book.blocks if b.page not in pages]
    new = [b for b in ocr.blocks if b.page in pages]
    done = {p.page: p for p in ocr.pages}
    infos = [done[p.page] if p.page in pages and p.page in done else p for p in book.pages]
    return BookText(blocks=sorted(keep + new, key=lambda b: b.page), pages=infos, method="mixed", errors=book.errors)


# ---------------------------------------------------------------------------------------------- build
def embed_text(meta: dict, path: list[str], text: str) -> str:
    head = f"{meta['subject']} - Class {meta['class_no']}" if meta.get("class_no") else meta["subject"]
    ctx = " > ".join([head] + path)
    return f"{ctx}\n\n{text}"


def build(books_dir: str, out_dir: str, ocr_roots: list[str] | None = None, only: str | None = None,
          workers: int = 6, include_duplicates: bool = False, log=print) -> dict:
    rows = discover(books_dir)
    if only:
        rows = [r for r in rows if only.lower() in r["drive_path"].lower()]
    ocr_roots = ocr_roots or [os.path.join(books_dir, "parsed-books"), os.path.join(os.path.dirname(books_dir), "parsed-books")]
    os.makedirs(os.path.join(out_dir, "chunks"), exist_ok=True)

    log(f"Extracting text layers from {len(rows)} PDFs with {workers} workers...")
    jobs = [(r["book_id"], os.path.join(books_dir, r["drive_path"])) for r in rows]
    results = {}
    with ProcessPoolExecutor(max_workers=workers) as ex:
        for i, (bid, book, err) in enumerate(ex.map(_extract, jobs, chunksize=1), 1):
            results[bid] = (book, err)
            if i % 20 == 0:
                log(f"  {i}/{len(jobs)}")

    manifest, ocr_plan, built = [], [], {}
    for r in rows:
        book, err = results[r["book_id"]]
        m = {"book_id": r["book_id"], "file": r["drive_path"], "class": r["class"], "subject": r["subject"],
             "curriculum": r["curriculum"], "language_label": r["language"], "audience": r["audience"],
             "pages": 0, "route": "", "method": "", "ocr_pages_needed": 0, "ocr_pages_missing": 0, "status": "",
             "chunks": 0, "excluded": "", "language_detected": "", "duplicate_of": "", "warnings": ""}
        warn = []
        if book is not None and book.errors:
            warn.append(f"{len(book.errors)} page extraction errors (first: {book.errors[0]})")
        if book is None:
            m.update(status="failed", warnings=err)
            manifest.append(m)
            continue
        n_pages = len(book.pages)
        m["pages"] = n_pages
        kind, need = route(book, n_pages)
        m["route"], m["ocr_pages_needed"] = kind, len(need)
        ocr_dir = find_ocr_dir(r, ocr_roots) if need else ""
        if need:
            pages = None if kind == "ocr" else set(need)
            if ocr_dir:
                pmap_file = os.path.join(os.path.dirname(ocr_dir), "page_map.json")
                pmap = json.load(open(pmap_file)) if os.path.exists(pmap_file) else None
                ocr = ocrjson.read_book(ocr_dir, pmap)
                have = {p.page for p in ocr.pages}
                missing = [p for p in need if p not in have]
                m["ocr_pages_missing"] = len(missing)
                if missing:
                    warn.append(f"{len(missing)} pages not OCRed yet")
                book = merge_ocr(book, ocr, pages)
                m["method"] = book.method
            else:
                ocr_plan.append({"drive_path": r["drive_path"], "total_pages": n_pages,
                                 "ocr_pages": len(need), "pages": " ".join(map(str, need))})
                m["ocr_pages_missing"] = len(need)
                if kind == "ocr":
                    m.update(status="awaiting_ocr", method="none")
                    manifest.append(m)
                    continue
                warn.append(f"{len(need)} image-only pages awaiting OCR")
                m["method"] = "text-layer"
        else:
            m["method"] = "text-layer"

        text_all = "\n".join(b.text for b in book.blocks)
        m["language_detected"] = lang.detect_language(text_all)
        expect = lang.label_to_code(r["language"], r["subject"], r["file_name"])
        if expect and m["language_detected"] not in ("unknown", expect):
            warn.append(f"language label says {expect} but text looks {m['language_detected']}")
        chunks = C.chunk_blocks(book.blocks)
        m["status"] = "ok" if chunks else "empty"
        m["warnings"] = "; ".join(warn)
        built[r["book_id"]] = (r, m, book, chunks, text_all)
        manifest.append(m)

    # ---- duplicates: identical normalised text -> keep one canonical file
    groups: dict[str, list[str]] = {}
    for bid, (r, m, book, chunks, text_all) in built.items():
        groups.setdefault(C.content_hash(text_all), []).append(bid)

    def canon_key(bid):
        r, m = built[bid][0], built[bid][1]
        ugly = bool(re.search(r"\(\d+\)|_\d\.pdf|_b\.pdf|Kitabu\d", r["file_name"]))
        consistent = not m["warnings"].startswith("language label")
        return (ugly, not consistent, r["file_name"].lower())

    for h, ids in groups.items():
        if len(ids) > 1:
            ids.sort(key=canon_key)
            for d in ids[1:]:
                built[d][1]["duplicate_of"] = ids[0]
                built[d][1]["status"] = "duplicate"

    # ---- export
    all_path = os.path.join(out_dir, "chunks.jsonl")
    n_total = 0
    with open(all_path, "w", encoding="utf-8") as allf:
        for bid, (r, m, book, chunks, _) in built.items():
            if m["status"] == "duplicate" and not include_duplicates:
                continue
            cmeta = {"subject": r["subject"], "class_no": (re.search(r"\d+", r["class"]) or [""])[0] if r["class"].startswith("class-") else ""}
            excluded, kept = {}, []
            for c in chunks:
                why = C.classify_chunk(c)
                if why:
                    excluded[why] = excluded.get(why, 0) + 1
                else:
                    kept.append(c)
            m["excluded"] = ", ".join(f"{k}:{v}" for k, v in sorted(excluded.items()))
            m["chunks"] = len(kept)
            code = m["language_detected"] if m["language_detected"] != "unknown" else lang.label_to_code(r["language"], r["subject"], r["file_name"])
            odir = os.path.join(out_dir, "chunks", r["class"])
            os.makedirs(odir, exist_ok=True)
            with open(os.path.join(odir, bid.split("-", 2)[-1][:150] + ".jsonl"), "w", encoding="utf-8") as bf:
                for i, c in enumerate(kept):
                    rec = {
                        "schema_version": SCHEMA_VERSION,
                        "id": f"{bid}:{i:05d}",
                        "book_id": bid,
                        "chunk_index": i,
                        "text": c.text,
                        "embed_text": embed_text(cmeta, c.section_path, c.text),
                        "class": r["class"], "subject": r["subject"], "curriculum": r["curriculum"],
                        "audience": r["audience"], "language": code, "language_label": r["language"],
                        "source_file": r["drive_path"],
                        "page_start": c.page_start, "page_end": c.page_end,
                        "printed_page_start": c.printed_page_start, "printed_page_end": c.printed_page_end,
                        "section_path": c.section_path,
                        "has_table": "table" in c.kinds, "has_figure_caption": C.has_figure(c.text, c.kinds),
                        "extraction": book.method, "char_count": len(c.text),
                        "content_hash": C.content_hash(c.text),
                    }
                    line = json.dumps(rec, ensure_ascii=False)
                    bf.write(line + "\n")
                    allf.write(line + "\n")
                    n_total += 1

    cols = list(manifest[0].keys()) if manifest else []
    with open(os.path.join(out_dir, "manifest.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(manifest)
    with open(os.path.join(out_dir, "ocr_plan.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["drive_path", "total_pages", "ocr_pages", "pages"])
        w.writeheader()
        w.writerows(ocr_plan)
    summary = {"schema_version": SCHEMA_VERSION, "books": len(manifest), "chunks": n_total,
               "by_status": _count(m["status"] for m in manifest), "by_route": _count(m["route"] for m in manifest),
               "ocr_books_pending": len(ocr_plan), "ocr_pages_pending": sum(int(p["ocr_pages"]) for p in ocr_plan)}
    json.dump(summary, open(os.path.join(out_dir, "summary.json"), "w"), indent=2)
    return summary


def _count(it):
    d: dict = {}
    for x in it:
        d[x] = d.get(x, 0) + 1
    return d
