# Ingestion: from PDFs to embedding-ready chunks

`python -m ingest` (or `./ingest.sh`) turns every book into clean, chunked, metadata-rich JSONL.
Embed the `embed_text` field of each record in `ingest-output/chunks.jsonl`; store the other fields as filterable metadata.

## Project assessment (what we found in the 189 PDFs, 25,246 pages)

| Finding | Evidence | What we do about it |
|---|---|---|
| ~68% of pages already have a text layer; ~32% (75 books, almost all `New`-curriculum books in classes 1-5) are pure scans | `pdftotext` per page | Route per page: text layer when present, OCR only for scans. Cuts OCR from ~25k to ~8k pages (~2/3 less GPU time) |
| Every text-layer page carries a "FOR ONLINE USE ONLY / DO NOT DUPLICATE" watermark whose letters scatter through the extracted text | rotated 45 deg glyphs + a horizontal copy in a non-embedded font | Removed at glyph level (rotation, and content-stream-order match), not by regex on the text |
| Faux-bold overprint doubles letters in footers (`SSCCIIEENNCCEE`), InDesign slugs (`.indd 23 30/07/2021`), running footers (`TAASISI YA ELIMU TANZANIA 2021`) | page samples | `dedupe_chars`, slug patterns, and removal of page-edge lines repeated on many pages |
| 16 files are byte-for-byte the same content under different names (`Std5_StadiZaKazi...` vs `Std5_Stadi-za-Kazi...`, `..._2.pdf`, `(1).pdf`) | text hash | Collapsed to one canonical book; the rest are `status=duplicate` in the manifest |
| Some catalogue language labels are wrong (e.g. `Std1_Kusoma_New_EnglishMedium_b` is the Kiswahili text) | language detector vs label | Chunk `language` comes from the detected text; the label is kept as `language_label`; mismatches are in the manifest `warnings` |
| Front matter (copyright, ISBN, acknowledgements, TOC) and reference lists would pollute retrieval | | Detected and excluded (counted in the manifest `excluded` column) |
| The original pipeline stopped at Markdown + page JSON and OCR'd every book, including ones with a text layer | `twiga_ocr_mac.py` | Added `--ocr-plan` so the OCR queue only processes what needs it; books with a text layer are marked `skipped` |
| A few "text" books have image-only pages (activity pages, 1-12 per book) | image coverage vs text | Listed in the OCR plan by page; only those pages are OCRed and merged back |

Known limits: vertical column sums and multi-column maths exercises are scrambled in the text layer (see the README example); two-column exercise layouts (e.g. "match group A with B") can interleave columns in the text layer; figures are not embedded (their captions are); OCR quality of the scanned books has been checked on one book only.

## Output contract (`ingest-output/`, schema_version 1)

- `chunks.jsonl` - all chunks, one JSON object per line. Also split per book under `chunks/<class>/`.
- `manifest.csv` - one row per book: route (`text|mixed|ocr`), `status` (`ok|awaiting_ocr|duplicate|failed|empty`), pages, chunks, excluded counts, detected language, duplicate_of, warnings.
- `ocr_plan.csv` - books/pages that still need OCR. `summary.json` - totals.

Chunk record:

| field | meaning |
|---|---|
| `id`, `book_id`, `chunk_index` | stable id `<book_id>:<00000>` |
| `text` | the chunk as read from the book |
| `embed_text` | `Subject - Class N > Chapter > Section` + text: **embed this** |
| `class`, `subject`, `curriculum` (New/Old), `audience` (student/teacher) | filter metadata |
| `language` (sw/en/fr/zh/ar, detected), `language_label` | |
| `source_file`, `page_start`, `page_end`, `printed_page_start/end` | citations (PDF page and the number printed on the page) |
| `section_path` | heading path, chapter first |
| `has_table`, `has_figure_caption`, `extraction` (`text-layer`/`ocr`/`mixed`), `char_count`, `content_hash` | |

Chunking: ~1,400 chars target (max ~2,000, about 350-500 tokens), never across chapters, sentence-split when a paragraph is too long, small sections packed together with sub-headings kept inline, one paragraph of overlap. Use a multilingual embedding model (the corpus is Kiswahili + English + some French/Chinese/Arabic); use the `language` field to filter, and `curriculum` to separate Old from New editions that overlap in content.

## Workflow

```bash
# 0. unzip the Drive archives into ./books (the zips alone are 4.2 GB; delete them after - disk is tight)
./ingest.sh --books-dir books                        # text-layer books are ready immediately; writes ocr_plan.csv
./parse.sh --ocr-plan ingest-output/ocr_plan.csv     # OCR only scanned pages (~7,950 pages)
./ingest.sh --books-dir books                        # rerun: scanned books now join chunks.jsonl
```
Colab OCR output (`parsed-books/` inside the Drive export) is picked up automatically via `--ocr-root`.
Check `manifest.csv` for `awaiting_ocr`, `failed` or non-empty `warnings` before embedding.
