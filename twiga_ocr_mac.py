"""twiga_ocr_mac: Mac (Apple Silicon) copy of twiga_ocr for local PaddleOCR-VL-1.6.

This is a separate copy of twiga_ocr.py with Mac-friendly defaults. The Colab
module is untouched. Typical use on macOS with Google Drive for Desktop:

    import twiga_ocr_mac as T
    T.configure(
        books_dir="/Users/YOU/Library/CloudStorage/GoogleDrive-YOU@email/My Drive/"
                  "Twiga Tanzania/Twiga-Primary-School-Books",
        work_dir=str(Path.home() / "twiga_work"),
        PIPELINE_KWARGS={
            "vl_rec_backend": "mlx-vlm-server",
            "vl_rec_server_url": "http://localhost:8111/",
            "vl_rec_api_model_name": "PaddlePaddle/PaddleOCR-VL-1.6",
        },
    )
    T.build_queue()
    T.parse(n=1)

Start the MLX-VLM server in another terminal first (see mac/SETUP.md).
Source PDFs are only ever read; writes go under OUTPUT_DIR or WORK_DIR.
"""
from __future__ import annotations

import datetime as dt
import os
import re
import shutil
import time
import traceback

import pandas as pd

# --------------------------------------------------------------------------------------
# Configuration (set with configure())
# --------------------------------------------------------------------------------------
OUTPUT_DIR_NAME = "parsed-books"
BOOKS_DIR = None            # .../Twiga-Primary-School-Books
OUTPUT_DIR = None           # BOOKS_DIR/parsed-books
PROGRESS_CSV = None         # OUTPUT_DIR/progress.csv
CATALOGUE = None            # BOOKS_DIR/Twiga-Primary-Books-Catalogue.xlsx
WORK_DIR = os.path.expanduser("~/twiga_work")  # local SSD scratch (not Drive)

INCLUDE_NON_CLASS_PDFS = True       # e.g. TIE_Primary_Curriculum_2023_Kiswahili.pdf at top level -> class "general"
UNWARP_QUALITIES = {"scan", ""}     # catalogue "Copy quality" values that get doc unwarping ("" = not catalogued)
UNWARP_MODE = "scans"               # "scans" (use UNWARP_QUALITIES) | "all" | "none"
SAVE_VIS = False                    # also save layout visualisation PNGs per page (large)
STALE_HOURS = 6                     # in_progress older than this (or from an earlier session) is re-picked
PROGRESS_EVERY = 10                 # update pages_parsed of the running row every N pages (for monitoring)
OCR_PLAN = None                     # {drive_path: [1-based pages]} from `python -m ingest` (ocr_plan.csv); None = OCR everything
PIPELINE_KWARGS = {}                # extra PaddleOCRVL(...) kwargs, e.g. {"device": "gpu:0"}

COLUMNS = ["order", "class", "subject", "curriculum", "language", "file_name", "drive_path", "pages",
           "status", "started_at", "finished_at", "pages_parsed", "output_md", "output_json_dir", "error"]
STATUSES = ["done", "in_progress", "pending", "failed", "skipped"]

SESSION_START = dt.datetime.now(dt.timezone.utc)
CATALOGUE_META: dict = {}
_PIPELINE = None


def configure(books_dir, output_dir=None, catalogue=None, work_dir=None, **options):
    """Set paths. output_dir defaults to <books_dir>/parsed-books, catalogue to the xlsx inside books_dir."""
    global BOOKS_DIR, OUTPUT_DIR, PROGRESS_CSV, CATALOGUE, WORK_DIR, CATALOGUE_META
    BOOKS_DIR = os.path.abspath(books_dir)
    if not os.path.isdir(BOOKS_DIR):
        raise FileNotFoundError(f"Books folder not found: {BOOKS_DIR}")
    OUTPUT_DIR = os.path.abspath(output_dir or os.path.join(BOOKS_DIR, OUTPUT_DIR_NAME))
    PROGRESS_CSV = os.path.join(OUTPUT_DIR, "progress.csv")
    CATALOGUE = catalogue or os.path.join(BOOKS_DIR, "Twiga-Primary-Books-Catalogue.xlsx")
    if work_dir:
        WORK_DIR = work_dir
    for k, v in options.items():
        if k not in globals() or k.startswith("_") or not k.isupper():
            raise TypeError(f"unknown option {k}")
        globals()[k] = v
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(WORK_DIR, exist_ok=True)
    CATALOGUE_META = load_catalogue()
    print("BOOKS_DIR   :", BOOKS_DIR)
    print("OUTPUT_DIR  :", OUTPUT_DIR)
    print("PROGRESS_CSV:", PROGRESS_CSV, "(exists)" if os.path.exists(PROGRESS_CSV) else "(will be created)")
    print("Catalogue   :", f"{len(CATALOGUE_META)} rows" if CATALOGUE_META else "not found - metadata from filenames")


def find_books_dir(name="Twiga-Primary-School-Books", roots=None, max_depth=3):
    """Look for the books folder under Colab Drive mounts or macOS Google Drive for Desktop."""
    if roots is None:
        home = os.path.expanduser("~")
        cloud = os.path.join(home, "Library", "CloudStorage")
        mac_roots = []
        if os.path.isdir(cloud):
            for entry in sorted(os.listdir(cloud)):
                if entry.startswith("GoogleDrive-"):
                    base = os.path.join(cloud, entry)
                    for sub in ("My Drive", "MyDrive"):
                        p = os.path.join(base, sub)
                        if os.path.isdir(p):
                            mac_roots.append(p)
        roots = (
            "/content/drive/MyDrive",
            "/content/drive/Shareddrives",
            "/content/drive/.shortcut-targets-by-id",
            *mac_roots,
        )
    hits = []
    for root in roots:
        if not os.path.isdir(root):
            continue
        base_depth = root.rstrip(os.sep).count(os.sep)
        for cur, dirs, _ in os.walk(root):
            if os.path.basename(cur) == name:
                hits.append(cur)
                dirs[:] = []
                continue
            if cur.count(os.sep) - base_depth >= max_depth:
                dirs[:] = []
            else:
                dirs[:] = [d for d in dirs if not d.startswith(".") or root.endswith("shortcut-targets-by-id")]
    return hits


# --------------------------------------------------------------------------------------
# Metadata
# --------------------------------------------------------------------------------------
def now_iso():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def slug(s):
    s = re.sub(r"[^\w\s-]", "", str(s), flags=re.UNICODE).strip()
    return re.sub(r"[\s_]+", "-", s) or "unknown"


def norm_curriculum(v):
    v = str(v or "").strip().lower()
    return "New" if v.startswith("new") else "Old" if v.startswith("old") else ""


def norm_language(v):
    v = str(v or "").strip()
    m = re.fullmatch(r"(?i)(english|kiswahili)[-_ ]?medium", v)
    if m:
        return m.group(1).capitalize() + "-medium"
    return v


def _cell(r, col):
    v = r.get(col)
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    return str(v).strip()


def load_catalogue(path=None):
    """file name -> metadata, from the catalogue's 'Catalogue' sheet (read-only)."""
    path = path or CATALOGUE
    if not path or not os.path.exists(path):
        return {}
    df = pd.read_excel(path, sheet_name="Catalogue")
    out = {}
    for _, r in df.iterrows():
        fn = _cell(r, "Filename")
        if not fn:
            continue
        pages = pd.to_numeric(r.get("Pages"), errors="coerce")
        out[fn] = {
            "class": _cell(r, "Class folder"),
            "subject": _cell(r, "Subject (Kiswahili)") or _cell(r, "Subject (English)"),
            "curriculum": norm_curriculum(_cell(r, "Curriculum")),
            "language": norm_language(_cell(r, "Language/medium")),
            "pages": None if pd.isna(pages) else int(pages),
            "copy_quality": _cell(r, "Copy quality").lower(),
            "book_type": _cell(r, "Book type"),
        }
    return out


_SKIP_TOKENS = re.compile(r"(?i)^(kitabu\d*|b|teachersguide|mwongozomwalimu|guide)$")


def parse_filename(fn, folder_class=""):
    """Std1_Afya-na-Mazingira_Old_Kiswahili.pdf -> ('class-1', 'Afya na Mazingira', 'Old', 'Kiswahili').
    Std3-7_... files take the folder's class; otherwise the first number after 'Std'."""
    stem = os.path.splitext(os.path.basename(fn))[0]
    parts = [p for p in stem.split("_") if p]
    cls = folder_class
    m = re.match(r"(?i)std(\d)", parts[0]) if parts else None
    if not cls and m:
        cls = f"class-{m.group(1)}"
    has_std = bool(m)
    subject = (parts[1] if has_std and len(parts) > 1 else stem).replace("-", " ")
    rest = parts[2:] if has_std else []
    curriculum = next((p.capitalize() for p in rest if p.lower() in ("old", "new")), "")
    lang_tokens = [p for p in rest if p.lower() not in ("old", "new") and not _SKIP_TOKENS.match(p)]
    language = norm_language(lang_tokens[-1]) if lang_tokens else ""
    return cls or "general", subject, curriculum, language


def class_key(c):
    m = re.match(r"class-(\d+)$", str(c))
    return int(m.group(1)) if m else 99


def count_pages(path, fallback=None):
    try:
        from pypdf import PdfReader
        return len(PdfReader(path).pages)
    except Exception as e:  # unreadable/corrupt PDF: keep the catalogue's number
        print(f"  ! pypdf could not read {os.path.basename(path)} ({type(e).__name__}: {e}); pages={fallback}")
        return fallback


def scan_books():
    """All source PDFs under BOOKS_DIR (recursive, includes Teaching Guides), never the parsed-books output."""
    rows = []
    out_real = os.path.realpath(OUTPUT_DIR)
    for root, dirs, files in os.walk(BOOKS_DIR):
        dirs[:] = sorted(d for d in dirs if d != OUTPUT_DIR_NAME and not d.startswith(".")
                         and os.path.realpath(os.path.join(root, d)) != out_real)
        rel_root = os.path.relpath(root, BOOKS_DIR)
        top = rel_root.split(os.sep)[0] if rel_root != "." else ""
        folder_class = top if re.match(r"class-\d+$", top) else ""
        if not folder_class and not INCLUDE_NON_CLASS_PDFS:
            continue
        for fn in sorted(files):
            if not fn.lower().endswith(".pdf"):
                continue
            meta = CATALOGUE_META.get(fn) or {}
            f_cls, f_subj, f_cur, f_lang = parse_filename(fn, folder_class)
            rows.append({
                "class": folder_class or meta.get("class") or f_cls,
                "subject": meta.get("subject") or f_subj,
                "curriculum": meta.get("curriculum") or f_cur,
                "language": meta.get("language") or f_lang,
                "file_name": fn,
                "drive_path": os.path.normpath(os.path.join(rel_root, fn)),
                "_cat_pages": meta.get("pages"),
            })
    rows.sort(key=lambda r: (class_key(r["class"]), r["class"], r["subject"].lower(), r["drive_path"].lower()))
    return rows


# --------------------------------------------------------------------------------------
# progress.csv
# --------------------------------------------------------------------------------------
def save_progress(df):
    """Atomic save: write a temp file next to the CSV, then rename over it."""
    tmp = PROGRESS_CSV + ".tmp"
    df[COLUMNS].to_csv(tmp, index=False)
    with open(tmp, "rb+") as f:
        os.fsync(f.fileno())
    os.replace(tmp, PROGRESS_CSV)


def load_progress():
    df = pd.read_csv(PROGRESS_CSV, dtype=str, keep_default_na=False)
    for c in COLUMNS:
        if c not in df.columns:
            df[c] = ""
    return df[COLUMNS]


def build_queue():
    """Create progress.csv once. If it exists it is never rewritten from scratch: only new PDFs are appended."""
    found = scan_books()
    if os.path.exists(PROGRESS_CSV):
        df = load_progress()
        known = set(df["drive_path"])
        new = [r for r in found if r["drive_path"] not in known]
        start = int(pd.to_numeric(df["order"], errors="coerce").max()) + 1 if len(df) else 1
        print(f"progress.csv exists with {len(df)} rows; {len(new)} new PDF(s) to append.")
    else:
        df = pd.DataFrame(columns=COLUMNS)
        new, start = found, 1
        print(f"Creating progress.csv with {len(new)} PDFs (pypdf reads each file once for its page count)...")
    add = []
    for i, r in enumerate(new):
        pages = count_pages(os.path.join(BOOKS_DIR, r["drive_path"]), r["_cat_pages"])
        add.append({**{k: r[k] for k in ("class", "subject", "curriculum", "language", "file_name", "drive_path")},
                    "order": str(start + i), "pages": "" if pages is None else str(pages), "status": "pending",
                    "started_at": "", "finished_at": "", "pages_parsed": "", "output_md": "",
                    "output_json_dir": "", "error": ""})
        if (i + 1) % 25 == 0:
            print(f"  {i + 1}/{len(new)}")
    if add:
        df = pd.concat([df, pd.DataFrame(add, columns=COLUMNS)], ignore_index=True)
        save_progress(df)
    total = pd.to_numeric(df["pages"], errors="coerce").sum()
    print(f"Queue: {len(df)} books, {int(total)} pages -> {PROGRESS_CSV}")
    return df


# --------------------------------------------------------------------------------------
# OCR
# --------------------------------------------------------------------------------------
def get_pipeline():
    """Create the PaddleOCR-VL-1.6 pipeline once per session and reuse it."""
    global _PIPELINE
    if _PIPELINE is None:
        from paddleocr import PaddleOCRVL
        t = time.time()
        _PIPELINE = PaddleOCRVL(
            pipeline_version="v1.6",
            use_doc_orientation_classify=True,   # load orientation classifier (rotated scans)
            use_doc_unwarping=True,              # load UVDoc unwarping; switched per book in predict
            use_queues=True,
            **PIPELINE_KWARGS,
        )
        print(f"PaddleOCR-VL-1.6 pipeline ready in {time.time() - t:.0f}s")
    return _PIPELINE


def unwarp_for(file_name):
    if UNWARP_MODE == "all":
        return True
    if UNWARP_MODE == "none":
        return False
    return (CATALOGUE_META.get(file_name) or {}).get("copy_quality", "") in UNWARP_QUALITIES


def run_book(pdf_path, out_dir, stem, unwarp=True, on_page=None):
    """OCR one PDF. Each page's JSON, Markdown and cropped images are written to out_dir as soon as the page
    is done; then the pages are merged into out_dir/<stem>.md. Returns (pages, merged_md_path)."""
    pipe = get_pipeline()
    json_dir, pages_dir = os.path.join(out_dir, "json"), os.path.join(out_dir, "pages")
    os.makedirs(json_dir, exist_ok=True)
    os.makedirs(pages_dir, exist_ok=True)
    pages_res = []
    for res in pipe.predict_iter(input=pdf_path, use_queues=True,
                                 use_doc_orientation_classify=True, use_doc_unwarping=unwarp):
        res.save_to_json(save_path=json_dir)        # json/<stem>_<page>_res.json
        # one folder per page: crop names only encode the bbox, so pages sharing a folder could overwrite crops
        page_no = res.get("page_index") if hasattr(res, "get") else None
        page_dir = os.path.join(pages_dir, f"page_{(page_no if page_no is not None else len(pages_res)) + 1:04d}")
        res.save_to_markdown(save_path=page_dir)    # pages/page_0001/<stem>_0.md + imgs/ crops
        if SAVE_VIS:
            res.save_to_img(save_path=os.path.join(out_dir, "vis"))
        pages_res.append(res)
        if on_page:
            on_page(len(pages_res))
    merged = pipe.restructure_pages(pages_res, merge_tables=True, relevel_titles=True, concatenate_pages=True)
    md_path = os.path.join(out_dir, stem + ".md")
    if merged:
        merged[0].save_to_markdown(save_path=md_path)   # explicit .md path; merged crops go to out_dir/imgs/
    return len(pages_res), md_path if os.path.exists(md_path) else ""


def _subset_pdf(path, pages, map_path):
    """Rewrite `path` keeping only `pages` (1-based). Writes {ocr_index: original_page} to map_path so the
    ingest step can put OCR text back on the right page."""
    import json
    from pypdf import PdfReader, PdfWriter
    r, w = PdfReader(path), PdfWriter()
    keep = [p for p in sorted(set(pages)) if 1 <= p <= len(r.pages)]
    for p in keep:
        w.add_page(r.pages[p - 1])
    with open(path, "wb") as f:
        w.write(f)
    with open(map_path, "w") as f:
        json.dump({str(i): p for i, p in enumerate(keep)}, f)


def _parse_time(s):
    try:
        t = dt.datetime.fromisoformat(s)
        return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)
    except Exception:
        return None


def _is_stale(started_at):
    t = _parse_time(started_at)
    return t is None or t < SESSION_START or dt.datetime.now(dt.timezone.utc) - t > dt.timedelta(hours=STALE_HOURS)


def book_rel_dir(row, df=None):
    """<class>/<subject>/<book-name>/parsed-output, relative to OUTPUT_DIR. If two different source files would
    land in the same folder (same name in two subfolders), the later one gets '__<order>' appended."""
    stem = os.path.splitext(row["file_name"])[0]
    cls, subj = row["class"] or "general", slug(row["subject"])
    book = stem
    if df is not None:
        same = df[(df["class"] == row["class"]) & (df["subject"].map(slug) == subj)
                  & (df["file_name"].map(lambda f: os.path.splitext(f)[0].lower()) == stem.lower())]
        if len(same) > 1 and pd.to_numeric(same["order"]).min() != int(row["order"]):
            book = f"{stem}__{row['order']}"
    return os.path.join(cls, subj, book, "parsed-output")


def _match(df, cls=None, subject=None, retry_failed=False):
    ok = (df["status"] == "pending") | ((df["status"] == "in_progress") & df["started_at"].map(_is_stale))
    if retry_failed:
        ok |= df["status"] == "failed"
    if cls:
        c = str(cls).strip().lower()
        m = re.search(r"\d+", c)
        c = f"class-{m.group()}" if m else c          # 3, "Std3", "class-3" -> "class-3"
        ok &= df["class"].str.lower() == c
    if subject:
        s = slug(subject).lower()
        ok &= df["subject"].map(lambda v: slug(v).lower()) == s
    return ok


def load_ocr_plan(path):
    """ocr_plan.csv (written by `python -m ingest`) -> {drive_path: [pages]}."""
    plan = {}
    for _, r in pd.read_csv(path, dtype=str, keep_default_na=False).iterrows():
        plan[r["drive_path"]] = [int(x) for x in r["pages"].split()]
    return plan


def apply_ocr_plan(plan):
    """Activate a plan: pending books with no scanned pages are marked 'skipped' (their text layer is used
    instead), so the queue only OCRs what needs it."""
    global OCR_PLAN
    OCR_PLAN = plan
    df = load_progress()
    skip = (df["status"] == "pending") & ~df["drive_path"].isin(plan.keys())
    df.loc[skip, ["status", "error"]] = ["skipped", "text layer present: no OCR needed"]
    save_progress(df)
    print(f"OCR plan: {len(plan)} books need OCR, {int(skip.sum())} marked skipped")


def _update(idx, **vals):
    df = load_progress()
    for k, v in vals.items():
        df.loc[idx, k] = v
    save_progress(df)
    return df


def parse(n=None, cls=None, subject=None, retry_failed=False):
    """Parse the next n books (all if None). cls='class-3' (or 3 / 'Std3') and subject='Hisabati' restrict the
    queue. Picks pending rows (and stale in_progress rows) in queue order; failed rows only with retry_failed."""
    done = 0
    while n is None or done < n:
        df = load_progress()
        cand = df[_match(df, cls, subject, retry_failed)]
        if cand.empty:
            print("Nothing left to parse for this filter.")
            break
        idx = pd.to_numeric(cand["order"], errors="coerce").idxmin()
        row = df.loc[idx].copy()
        rel_dir = book_rel_dir(row, df)
        out_dir = os.path.join(OUTPUT_DIR, rel_dir)
        stem = os.path.splitext(row["file_name"])[0]
        df.loc[idx, ["status", "started_at", "finished_at", "pages_parsed", "error"]] = \
            ["in_progress", now_iso(), "", "0", ""]
        save_progress(df)
        print(f"> [{row['order']}] {row['drive_path']} ({row['pages']} pages) -> {rel_dir}")
        local_pdf = os.path.join(WORK_DIR, str(row["order"]), row["file_name"])   # keeps the real name in outputs
        t0 = time.time()

        def on_page(k):
            if k % PROGRESS_EVERY == 0:
                _update(idx, pages_parsed=str(k))
                print(f"    ... {k}/{row['pages']} pages, {time.time() - t0:.0f}s")

        try:
            if os.path.isdir(out_dir):           # partial output from an interrupted earlier attempt (ours only)
                shutil.rmtree(out_dir)
            os.makedirs(out_dir, exist_ok=True)
            os.makedirs(os.path.dirname(local_pdf), exist_ok=True)
            shutil.copyfile(os.path.join(BOOKS_DIR, row["drive_path"]), local_pdf)   # read-only copy
            only = (OCR_PLAN or {}).get(row["drive_path"])
            if only and (not row["pages"] or len(only) < int(row["pages"])):
                _subset_pdf(local_pdf, only, os.path.join(out_dir, "page_map.json"))   # OCR just the scanned pages
            pages, md_path = run_book(local_pdf, out_dir, stem, unwarp=unwarp_for(row["file_name"]), on_page=on_page)
            if not md_path:
                raise RuntimeError("merged Markdown was not written")
            _update(idx, status="done", finished_at=now_iso(), pages_parsed=str(pages),
                    output_md=os.path.join(rel_dir, stem + ".md"), output_json_dir=os.path.join(rel_dir, "json"),
                    error="")
            print(f"  done: {pages} pages in {time.time() - t0:.0f}s")
        except KeyboardInterrupt:
            _update(idx, status="pending", started_at="", pages_parsed="")
            print("  interrupted; row set back to pending")
            raise
        except Exception as e:
            traceback.print_exc()
            _update(idx, status="failed", finished_at=now_iso(), error=f"{type(e).__name__}: {e}"[:500])
            print(f"  FAILED after {time.time() - t0:.0f}s: {e}")
            try:
                import paddle
                paddle.device.cuda.empty_cache()
            except Exception:
                pass
        finally:
            shutil.rmtree(os.path.dirname(local_pdf), ignore_errors=True)
        done += 1
    return status()


def status(show=True):
    """done / in_progress / pending / failed per class, plus pages."""
    df = load_progress()
    tab = pd.crosstab(df["class"], df["status"]).reindex(columns=STATUSES, fill_value=0)
    tab = tab.loc[sorted(tab.index, key=lambda c: (class_key(c), c))]
    tab["books"] = tab[STATUSES].sum(axis=1)
    tab["pages_total"] = pd.to_numeric(df["pages"], errors="coerce").groupby(df["class"]).sum().reindex(tab.index).fillna(0).astype(int)
    tab["pages_parsed"] = pd.to_numeric(df["pages_parsed"], errors="coerce").groupby(df["class"]).sum().reindex(tab.index).fillna(0).astype(int)
    tab.loc["TOTAL"] = tab.sum()
    if show:
        print(tab.to_string())
        cur = df[df["status"] == "in_progress"]
        for _, r in cur.iterrows():
            print(f"in progress: [{r['order']}] {r['drive_path']} since {r['started_at']} ({r['pages_parsed'] or 0}/{r['pages']} pages)")
        bad = df[df["status"] == "failed"]
        for _, r in bad.iterrows():
            print(f"failed: [{r['order']}] {r['drive_path']}: {r['error'][:150]}")
    return tab


def test_parse(drive_path=None, first_pages=3, out_root=None):
    """Smoke test: OCR the first pages of one book (default: fewest pages in the queue, else in the catalogue)
    into WORK_DIR/test_out. Does not read or write progress.csv rows."""
    from pypdf import PdfReader, PdfWriter
    if drive_path is None:
        if PROGRESS_CSV and os.path.exists(PROGRESS_CSV):
            df = load_progress()
            p = pd.to_numeric(df["pages"], errors="coerce")
            drive_path = df.loc[p.idxmin(), "drive_path"]
        else:
            fn, meta = min(((f, m) for f, m in CATALOGUE_META.items() if m.get("pages")), key=lambda x: x[1]["pages"])
            drive_path = os.path.join(meta["class"], fn)
    src = os.path.join(BOOKS_DIR, drive_path)
    reader, writer = PdfReader(src), PdfWriter()
    for i in range(min(first_pages, len(reader.pages))):
        writer.add_page(reader.pages[i])
    stem = os.path.splitext(os.path.basename(src))[0]
    out = out_root or os.path.join(WORK_DIR, "test_out")
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(out, exist_ok=True)
    tmp_pdf = os.path.join(out, stem + "_sample.pdf")
    with open(tmp_pdf, "wb") as f:
        writer.write(f)
    get_pipeline()                       # model load not included in the timing
    t = time.time()
    pages, md_path = run_book(tmp_pdf, os.path.join(out, "parsed-output"), stem, unwarp=unwarp_for(os.path.basename(src)))
    secs = time.time() - t
    text = open(md_path, encoding="utf-8").read() if md_path else ""
    print(f"Book: {drive_path} | pages: {pages} | {secs:.1f}s ({secs / max(pages, 1):.1f}s/page)")
    print(f"Markdown: {md_path} ({len(text)} chars)\n" + "-" * 80 + "\n" + text[:800])
    return {"book": drive_path, "pages": pages, "seconds": round(secs, 1), "md": md_path, "excerpt": text[:800]}
