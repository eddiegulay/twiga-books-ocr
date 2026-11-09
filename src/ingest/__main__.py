import argparse
import json
import os

from . import pipeline

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    ap = argparse.ArgumentParser(prog="python -m ingest", description="Build embedding-ready chunks from the Twiga books")
    ap.add_argument("--books-dir", default=os.path.join(HERE, "books"))
    ap.add_argument("--out", default=os.path.join(HERE, "ingest-output"))
    ap.add_argument("--ocr-root", action="append", help="folder(s) holding parsed-output OCR results (default: parsed-books)")
    ap.add_argument("--only", help="only books whose path contains this text (e.g. class-3)")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--include-duplicates", action="store_true")
    a = ap.parse_args()
    roots = a.ocr_root or [os.path.join(HERE, "parsed-books"), os.path.join(a.books_dir, "parsed-books")]
    s = pipeline.build(a.books_dir, a.out, roots, a.only, a.workers, a.include_duplicates)
    print(json.dumps(s, indent=2))


main()
