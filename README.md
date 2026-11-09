# PrimarySchoolBookParse

Local PaddleOCR-VL-1.6 parsing of the Twiga primary books (Std 1–7) on Apple Silicon.
Layout detection runs on PaddlePaddle CPU; the VL recognition model runs on Metal via `mlx-vlm`.

## 1. Setup (once)
```bash
./setup.sh
```
Creates `.venv` (Python 3.12) and installs PaddlePaddle 3.2.1 CPU, `paddleocr[doc-parser]>=3.6`, `mlx-vlm>=0.3.11`, pandas, pypdf, openpyxl.
The first run downloads the models (~2 GB VLM + layout models into `~/.cache/huggingface` and `~/.paddlex`).

## 2. Books
Unzip/move the downloaded Drive folder so the class folders and `Twiga-Primary-Books-Catalogue.xlsx` sit directly in `./books`
(or pass `--books-dir /path/to/Twiga-Primary-School-Books`).

## 3. Run
```bash
./parse.sh --test            # 2-page smoke test, doesn't touch progress.csv -> note seconds/page
./parse.sh --n 1             # parse the next book in the queue
./parse.sh --cls class-7     # only one class
./parse.sh                   # everything remaining
./parse.sh --status          # progress summary
./parse.sh --retry-failed    # re-run failed books
```
`parse.sh` starts the MLX server on port 8111 if needed and keeps the Mac awake (`caffeinate`). Keep it plugged in.

Output: `parsed-books/<class>/<subject>/<book>/parsed-output/` (merged Markdown + per-page JSON), queue in `parsed-books/progress.csv`.
Progress is saved per page and per book, so you can stop (Ctrl-C) and rerun; it picks up the next pending book.

## Notes
- This queue is separate from the Colab `progress.csv` on Drive. Colab is currently on class-1, so use `--cls` for other classes to avoid duplicate work.
- Faster alternative backend: llama.cpp (`brew install llama.cpp`, see `scripts/start_llama_server.sh`, then `python run_parse.py --backend llama`).
- Disk: models + venv need ~10–15 GB; outputs grow with pages.
- Tests: `source .venv/bin/activate && pytest tests -q`
