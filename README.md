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

## 4. Ingestion (embedding-ready output)
See [INGESTION.md](INGESTION.md): `./ingest.sh` builds `ingest-output/chunks.jsonl` (clean chunks + metadata) from the text layers and the OCR output; `./parse.sh --ocr-plan ingest-output/ocr_plan.csv` OCRs only the pages that need it.

### What the output looks like

Pictures are never embedded (the model sees text only), but their captions and the sentences around them stay in the chunk, and `has_figure_caption` is set so you can filter on it. Real records from `ingest-output/chunks.jsonl` (long fields trimmed):

**Book with pictures** - `Std3_Sayansi-na-Teknolojia_Old_Englishmedium.pdf`, a text-layer book. The diagram itself is dropped, its caption and description are kept together with the activity, and the watermark, page footer and page number are gone:
```json
{
  "id": "class-3-Std3_Sayansi-na-Teknolojia_Old_Englishmedium:00038",
  "text": "Look at the following picture and then read the description that follows.\n\nFigure 6 Using a weighing scales\n\nThe picture in Figure 6 shows a boy and a girl measuring the mass of sugar using weighing scales.\n\nActivity 6 Measuring mass Materials a tin, a bag, sugar, and two weighing scales\n\nProcedure\n(i) Weigh one kilogram of sugar ...",
  "embed_text": "Sayansi na Teknolojia - Class 3 > Chapter Six Measurements\n\nLook at the following picture ...",
  "class": "class-3", "subject": "Sayansi na Teknolojia", "curriculum": "Old", "audience": "student",
  "language": "en", "language_label": "English-medium",
  "source_file": "class-3/Std3_Sayansi-na-Teknolojia_Old_Englishmedium.pdf",
  "page_start": 70, "page_end": 71, "printed_page_start": "63", "printed_page_end": "64",
  "section_path": ["Chapter Six Measurements"],
  "has_table": false, "has_figure_caption": true, "extraction": "text-layer", "char_count": 838
}
```
For a scanned picture book (OCR path) image blocks are dropped and tables are flattened to `col | col` rows, e.g. `S/N | Item | Name` / `1. |  | ` from `Std1_Afya-na-Mazingira_New_Englishmedium` (`extraction: "ocr"`, `has_table: true`).

**Book with mathematics** - `Std4_Hisabati_Old_Kiswahili.pdf`. Digits, operators and exercise numbering are kept (they count as content, not noise); the section path is the chapter:
```json
{
  "id": "class-4-Std4_Hisabati_Old_Kiswahili:00022",
  "text": "1. Jumlisha mamoja; 2 + 3 = 5. 31072 Andika 5 katika nafasi ya mamoja; + 29853 5\n\n2. Jumlisha makumi; 7 + 5 = 12. ...\n\nZoezi la 3\nJibu maswali yafuatayo: 1. 2636 2. 47345 3. 36328 + 8345 + 17092 + 60606\n\n4. 64646 5. 36473 ...",
  "embed_text": "Hisabati - Class 4 > Sura ya Nne\n\n1. Jumlisha mamoja; ...",
  "class": "class-4", "subject": "Hisabati", "curriculum": "Old", "audience": "student",
  "language": "sw", "language_label": "Kiswahili",
  "source_file": "class-4/Std4_Hisabati_Old_Kiswahili.pdf",
  "page_start": 51, "page_end": 52, "printed_page_start": "44", "printed_page_end": "45",
  "section_path": ["Sura ya Nne"],
  "has_table": false, "has_figure_caption": false, "extraction": "text-layer", "char_count": 974
}
```
**Known limitation for maths:** the text layer stores vertical column sums in drawing order, so `31072 / + 29853 / 60925` comes out as `31072 ... + 29853 5`, and neighbouring exercises on one row interleave (`1. 2636 2. 47345 3. 36328 + 8345 ...`). The explanatory words and the numbers are all present, so retrieval by topic ("kujumlisha", "carrying") works, but a model should not be trusted to re-derive the exact sums from these chunks. Fixing it needs layout-aware extraction (run those pages through the OCR model, which reads layout) - the `needs_ocr` page routing is where to add a "maths-layout" rule. No LaTeX is produced: the books use plain digits and symbols, not typeset formulas (the OCR path would emit formula blocks if present).

## Notes
- This queue is separate from the Colab `progress.csv` on Drive. Colab is currently on class-1, so use `--cls` for other classes to avoid duplicate work.
- Faster alternative backend: llama.cpp (`brew install llama.cpp`, see `scripts/start_llama_server.sh`, then `python run_parse.py --backend llama`).
- Disk: models + venv need ~10–15 GB; outputs grow with pages.
- Tests: `source .venv/bin/activate && pytest tests -q`
