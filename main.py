#!/usr/bin/env python3
"""Single entry point for the Twiga book pipeline.

  python main.py ingest      # text-layer books -> final-chunks/ (+ writes the list of pages that need OCR)
  python main.py ocr         # OCR only those scanned pages -> ocr-raw/
  python main.py all         # ingest -> ocr -> ingest  (the whole job)
  python main.py ocr --status

See README.md. Code lives in src/ (src/ingest = text extraction/cleaning/chunking, src/ocr = OCR runner).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV_PY = ROOT / ".venv" / "bin" / "python"
if sys.prefix == sys.base_prefix and VENV_PY.exists():          # not in a venv: use the project's one
    os.execv(str(VENV_PY), [str(VENV_PY), *sys.argv])
sys.path.insert(0, str(ROOT))

BOOKS = ROOT / "books"
OCR_RAW = ROOT / "ocr-raw"
FINAL = ROOT / "final-chunks"
MLX_URL = "http://127.0.0.1:8111/"


# ------------------------------------------------------------------------------------------ ingest
def cmd_ingest(a) -> dict:
    from src.ingest import pipeline
    roots = a.ocr_root or [os.path.join(b, n) for n in pipeline.OCR_DIR_NAMES for b in (str(ROOT), a.books_dir)]
    summary = pipeline.build(a.books_dir, a.out, roots, a.only, a.workers, a.include_duplicates)
    print(json.dumps(summary, indent=2))
    return summary


# ------------------------------------------------------------------------------------------ ocr
def backend_kwargs(name: str) -> dict:
    if name == "mlx":
        return {"vl_rec_backend": "mlx-vlm-server", "vl_rec_server_url": MLX_URL,
                "vl_rec_api_model_name": "PaddlePaddle/PaddleOCR-VL-1.6"}
    if name == "llama":
        return {"vl_rec_backend": "llama-cpp-server", "vl_rec_server_url": "http://127.0.0.1:8080/v1"}
    return {}                                                    # cpu: native Paddle for the VLM too (slow)


def _server_up(url: str) -> bool:
    try:
        urllib.request.urlopen(url, timeout=2)
        return True
    except urllib.error.HTTPError:
        return True                                              # it answered, so it is running
    except Exception:
        return False


def ensure_mlx_server() -> None:
    if _server_up(MLX_URL):
        return
    (ROOT / "logs").mkdir(exist_ok=True)
    print("Starting MLX server on :8111 (log: logs/mlx_server.log)")
    env = {**os.environ, "PATH": f"{VENV_PY.parent}{os.pathsep}{os.environ.get('PATH', '')}"}
    subprocess.Popen(["bash", str(ROOT / "scripts" / "start_mlx_server.sh")], env=env, start_new_session=True,
                     stdout=open(ROOT / "logs" / "mlx_server.log", "ab"), stderr=subprocess.STDOUT)
    for _ in range(60):
        if _server_up(MLX_URL):
            return
        time.sleep(2)
    raise SystemExit("MLX server did not start; see logs/mlx_server.log")


def cmd_ocr(a) -> None:
    from src.ocr import runner as T
    if a.backend == "mlx" and not a.status:
        ensure_mlx_server()
    if not a.status:                                             # keep the Mac awake while this process runs
        subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())])
    books = a.books_dir
    if a.find:
        hits = T.find_books_dir()
        if not hits:
            raise SystemExit("No Twiga-Primary-School-Books folder found. Pass --books-dir.")
        books = hits[0]
        print("Using:", books)
    T.configure(books_dir=books, output_dir=a.output_dir, work_dir=a.work_dir,
                PIPELINE_KWARGS=backend_kwargs(a.backend))
    if a.status:
        if not os.path.exists(T.PROGRESS_CSV):
            raise SystemExit("No OCR queue yet; run `python main.py ocr` first")
        T.status()
        return
    if not os.path.exists(T.PROGRESS_CSV):
        T.build_queue()
    plan = Path(a.ocr_plan) if a.ocr_plan else FINAL / "ocr_plan.csv"
    if not a.all_pages:
        if not plan.exists():
            raise SystemExit(f"{plan} not found. Run `python main.py ingest` first (it lists the pages that need OCR), "
                             "or pass --all-pages to OCR everything.")
        T.apply_ocr_plan(T.load_ocr_plan(str(plan)))
    if a.test:
        T.test_parse(first_pages=a.test_pages)
        return
    T.parse(n=a.n, cls=a.cls, subject=a.subject, retry_failed=a.retry_failed)


# ------------------------------------------------------------------------------------------ cli
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="main.py", description="Twiga primary books -> embedding-ready chunks")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--books-dir", default=str(BOOKS), help="folder with the class-N folders and the catalogue (default: ./books)")

    i = sub.add_parser("ingest", help="build final-chunks/ from text layers + any OCR output")
    common(i)
    i.add_argument("--out", default=str(FINAL))
    i.add_argument("--ocr-root", action="append", help="extra folder holding OCR results (default: ocr-raw, and the older name parsed-books)")
    i.add_argument("--only", help="only books whose path contains this text, e.g. class-3")
    i.add_argument("--workers", type=int, default=6)
    i.add_argument("--include-duplicates", action="store_true")

    o = sub.add_parser("ocr", help="OCR the scanned pages (writes ocr-raw/)")
    common(o)
    o.add_argument("--find", action="store_true", help="search Google Drive for Desktop for the books folder")
    o.add_argument("--output-dir", default=str(OCR_RAW))
    o.add_argument("--work-dir", default=str(ROOT / ".work"))
    o.add_argument("--backend", choices=("mlx", "llama", "cpu"), default="mlx")
    o.add_argument("--ocr-plan", help="default: final-chunks/ocr_plan.csv (written by `ingest`)")
    o.add_argument("--all-pages", action="store_true", help="ignore the plan and OCR every book completely")
    o.add_argument("--n", type=int, help="number of books (default: all remaining)")
    o.add_argument("--cls", help="only class-N")
    o.add_argument("--subject")
    o.add_argument("--retry-failed", action="store_true")
    o.add_argument("--test", action="store_true", help="2-page smoke test; shows seconds/page")
    o.add_argument("--test-pages", type=int, default=2)
    o.add_argument("--status", action="store_true", help="show OCR progress")

    al = sub.add_parser("all", help="ingest -> ocr -> ingest (the whole job)")
    common(al)
    al.add_argument("--workers", type=int, default=6)
    return ap


def main(argv=None) -> None:
    a = build_parser().parse_args(argv)
    if a.cmd == "ingest":
        cmd_ingest(a)
    elif a.cmd == "ocr":
        cmd_ocr(a)
    else:
        base = dict(books_dir=a.books_dir, out=str(FINAL), ocr_root=None, only=None, workers=a.workers, include_duplicates=False)
        print("== 1/3 ingest (text-layer books) ==")
        s = cmd_ingest(argparse.Namespace(**base))
        if s.get("ocr_pages_pending"):
            print(f"== 2/3 OCR ({s['ocr_pages_pending']} scanned pages) ==")
            cmd_ocr(build_parser().parse_args(["ocr", "--books-dir", a.books_dir]))
            print("== 3/3 ingest again (merge OCR) ==")
            cmd_ingest(argparse.Namespace(**base))
        else:
            print("No scanned pages left: nothing to OCR.")


if __name__ == "__main__":
    main()
