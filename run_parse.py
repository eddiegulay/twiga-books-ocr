#!/usr/bin/env python3
"""Configure paths and run one or more Twiga books on Apple Silicon.

Examples:
  python run_parse.py --books-dir "/Users/YOU/Library/CloudStorage/GoogleDrive-YOU/My Drive/Twiga Tanzania/Twiga-Primary-School-Books" --n 1
  python run_parse.py --find --test
  python run_parse.py --find --backend llama --n 1
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import twiga_ocr_mac as T  # noqa: E402


def backends(name: str) -> dict:
    if name == "mlx":
        return {
            "vl_rec_backend": "mlx-vlm-server",
            "vl_rec_server_url": "http://127.0.0.1:8111/",
            "vl_rec_api_model_name": "PaddlePaddle/PaddleOCR-VL-1.6",
        }
    if name == "llama":
        return {
            "vl_rec_backend": "llama-cpp-server",
            "vl_rec_server_url": "http://127.0.0.1:8080/v1",
        }
    if name == "cpu":
        return {}  # native PaddlePaddle CPU for the VLM too (slow)
    raise SystemExit(f"unknown backend {name!r}; use mlx|llama|cpu")


def main() -> None:
    ap = argparse.ArgumentParser(description="Run Twiga OCR locally on Apple Silicon")
    ap.add_argument("--books-dir", default=str(HERE / "books"), help="Path to the downloaded books folder (default: ./books)")
    ap.add_argument("--find", action="store_true", help="Search Google Drive for Desktop for BOOKS_DIR")
    ap.add_argument("--output-dir", default=str(HERE / "parsed-books"), help="Default: ./parsed-books (local, separate from the Colab progress.csv)")
    ap.add_argument("--work-dir", default=str(HERE / ".work"))
    ap.add_argument("--backend", choices=("mlx", "llama", "cpu"), default="mlx")
    ap.add_argument("--n", type=int, default=None, help="Number of books (default: all remaining)")
    ap.add_argument("--cls", help="Restrict to class-N")
    ap.add_argument("--subject", help="Restrict to subject")
    ap.add_argument("--retry-failed", action="store_true")
    ap.add_argument("--test", action="store_true", help="Smoke-test first pages; does not touch progress.csv")
    ap.add_argument("--test-pages", type=int, default=2)
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args()

    books = args.books_dir
    if args.find or not books:
        hits = T.find_books_dir()
        if not hits:
            raise SystemExit("No Twiga-Primary-School-Books folder found. Pass --books-dir.")
        print("Found:")
        for h in hits:
            print(" ", h)
        books = hits[0]
        print("Using:", books)

    T.configure(
        books_dir=books,
        output_dir=args.output_dir,
        work_dir=args.work_dir,
        PIPELINE_KWARGS=backends(args.backend),
    )
    if args.status:
        if not os.path.exists(T.PROGRESS_CSV):
            raise SystemExit("progress.csv not found; run without --status first")
        T.status()
        return
    if not os.path.exists(T.PROGRESS_CSV):
        T.build_queue()
    if args.test:
        T.test_parse(first_pages=args.test_pages)
        return
    T.parse(n=args.n, cls=args.cls, subject=args.subject, retry_failed=args.retry_failed)


if __name__ == "__main__":
    main()
