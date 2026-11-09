# PrimarySchoolBookParse

This project reads the Twiga primary school books (Std 1 to 7) and turns them into small, clean pieces of text, called **chunks**.
Each chunk comes with details about where it came from (class, subject, page and so on). The chunks are ready to be turned into
**text embeddings** for search.

- **Input:** 189 PDF books, about 25,000 pages, in Kiswahili, English, French, Chinese and Arabic.
- **Output:** one file, `final-chunks/chunks.jsonl`. Each line is one chunk.
- Everything runs on your own Mac. No cloud services are needed.

## Contents
1. [How it works](#1-how-it-works)
2. [Setup](#2-setup)
3. [Run it](#3-run-it)
4. [Folders](#4-folders)
5. [What the output looks like](#5-what-the-output-looks-like)
6. [What we found in the books, and what we do about it](#6-what-we-found-in-the-books-and-what-we-do-about-it)
7. [How chunks are made](#7-how-chunks-are-made)
8. [Details about the OCR step](#8-details-about-the-ocr-step)
9. [Known limits](#9-known-limits)
10. [Code layout and tests](#10-code-layout-and-tests)

---

## 1. How it works

There are two kinds of pages in the books, and each one is read in a different way:

| Kind of page | How much | How we read it | Speed |
|---|---|---|---|
| **Text pages.** The PDF already contains the words as real text. This is most of the `Old` books and all of classes 6 and 7. | about 68% | We copy the text straight out of the PDF. | Very fast: minutes for everything. No AI. |
| **Scanned pages.** The PDF page is only a picture of a page. This is most of the `New` books in classes 1 to 5. | about 32% | An OCR model "looks" at the picture and reads it. | Slow: about 14 seconds per page, so around 30 hours in total. |

Because scanned pages are slow, we only OCR the pages that really need it. The first step finds them for us.

```
  books (PDFs)
      |
      v
  1. python main.py ingest      reads the text pages, cleans them, and makes a list
      |                         of the scanned pages (final-chunks/ocr_plan.csv)
      v
  2. python main.py ocr         OCR on the scanned pages only; saves the raw result in ocr-raw/
      |
      v
  3. python main.py ingest      runs again and joins the text pages and the OCR pages
      |
      v
  final-chunks/chunks.jsonl     <-- this is what you give to your embedding model
```

You can run the three steps one by one, or run `python main.py all` to do all of them.

## 2. Setup

Do this once.

```bash
./scripts/setup.sh
```

This creates a `.venv` folder (Python 3.12) and installs everything. The first OCR run also downloads the OCR models (about 2 GB).
Keep about 10 to 15 GB of disk free for the models and the environment.

Then put the books in the `books/` folder. Unzip the downloaded archives so that the folders `class-1` to `class-7` and the file
`Twiga-Primary-Books-Catalogue.xlsx` sit directly inside `books/`. (Or use `--books-dir /path/to/folder` on any command.)

> **Disk space:** the zip files are about 4.2 GB, and the unzipped books are about 4.6 GB. Delete the zips once you have unzipped them.

The catalogue tells us each book's subject, curriculum (Old or New), language and type. If a book is not in the catalogue, we use its file name instead.

## 3. Run it

`main.py` is the only command you need. It uses the project's `.venv` by itself, so you do not have to activate it.

```bash
python main.py all        # the whole job in one go: ingest, then ocr, then ingest again
```

Or step by step:

```bash
python main.py ingest     # Step 1. Text pages are done. Also lists the scanned pages to OCR.
python main.py ocr        # Step 2. OCR those pages. This takes hours; keep the Mac plugged in.
python main.py ingest     # Step 3. Join everything into final-chunks/
```

**Before you embed anything,** open `final-chunks/manifest.csv`. It has one row per book. Look for books marked `awaiting_ocr` or `failed`,
and for any text in the `warnings` column.

Useful options:

| Command | Option | What it does |
|---|---|---|
| `ocr` | `--status` | Show OCR progress. |
| `ocr` | `--test` | OCR 2 pages only, and show how many seconds each page takes. |
| `ocr` | `--n 1` | Do only the next book. |
| `ocr` | `--cls class-7` or `--subject Hisabati` | Do only one class or subject. |
| `ocr` | `--retry-failed` | Try the failed books again. |
| `ocr` | `--all-pages` | Ignore the list and OCR whole books. Rarely needed. |
| `ingest` | `--only class-3` | Handle only the books whose path contains this text. |
| `ingest` | `--workers 8` | Use more CPU cores. |
| both | `--books-dir DIR` | Use a different books folder. |

`python main.py <command> --help` shows everything.

**Stopping and restarting:** you can press Ctrl-C during OCR at any time. Progress is saved after every page and every book. Run the command
again and it carries on with the next book.

## 4. Folders

```
books/           INPUT          the source PDFs and the catalogue. We never change these.
ocr-raw/         INTERMEDIATE   the raw OCR results and the OCR to-do list (progress.csv)
final-chunks/    OUTPUT         the clean chunks, ready to embed
```

**Why are there two output folders?** They are two stages of the work, not copies of each other:

| | `ocr-raw/` | `final-chunks/` |
|---|---|---|
| What it is | The raw result of the OCR step | The final result |
| Made by | `python main.py ocr` | `python main.py ingest` |
| Which books | Scanned books only | All books |
| How clean | Not clean: it still has page headers, footers and image tags | Clean, split into chunks, with details added |
| Size | Large (it includes cropped images) | Small (about 65 MB of text) |
| Who reads it | `ingest` uses it as input | Your embedding step |

Text-page books never appear in `ocr-raw/`, because we read them directly from the PDF.

**Keep `ocr-raw/`.** OCR takes many hours, and `ocr-raw/` lets you resume and re-clean without doing the OCR again.
`final-chunks/` can be deleted and rebuilt at any time, because everything in it is made from the other folders.

What is inside `final-chunks/`:

| File | What it is |
|---|---|
| `chunks.jsonl` | All the chunks, one per line. **This is the main output.** |
| `chunks/<class>/<book>.jsonl` | The same chunks, split by book. |
| `manifest.csv` | One row per book: what happened to it, how many chunks, any warnings. |
| `ocr_plan.csv` | The scanned pages that still need OCR. |
| `summary.json` | Totals. |

Old OCR results from earlier runs may be in a folder called `parsed-books/`. That old name is still read automatically, so those results are not lost.
You can also point to other folders with `ingest --ocr-root DIR`.

## 5. What the output looks like

### The fields in each chunk

| Field | Meaning |
|---|---|
| `embed_text` | **The text to embed.** It is the chunk text with a short label in front, such as `Hisabati - Class 4 > Sura ya Nne`. The label helps search find the right chunk. |
| `text` | The chunk exactly as read from the book (without the label). |
| `id`, `book_id`, `chunk_index` | A stable name for the chunk, like `class-4-Std4_Hisabati_Old_Kiswahili:00022`. |
| `class`, `subject`, `curriculum`, `audience` | For filtering. `curriculum` is Old or New. `audience` is student or teacher. |
| `language` | The language found in the text itself: `sw`, `en`, `fr`, `zh` or `ar`. |
| `language_label` | The language the catalogue says. We keep both because some catalogue labels are wrong. |
| `source_file`, `page_start`, `page_end` | Where the chunk comes from, for showing sources. |
| `printed_page_start`, `printed_page_end` | The page number printed on the page, which can differ from the PDF page number. |
| `section_path` | The chapter and section the chunk belongs to. |
| `has_table`, `has_figure_caption` | Whether the chunk has a table, or mentions a figure or picture. |
| `extraction` | How it was read: `text-layer`, `ocr` or `mixed`. |
| `char_count`, `content_hash` | The size of the chunk, and a fingerprint for spotting repeats. |

A good choice is to embed `embed_text` and store all the other fields next to it as metadata. The books mix several languages, so use a
**multilingual** embedding model. Use the `language` field to filter, and `curriculum` to tell the Old and New editions apart, because they cover
similar content.

### Example 1: a book with pictures

This is `Std3_Sayansi-na-Teknolojia_Old_Englishmedium.pdf` (a text-page book). The picture itself is not in the output, but its caption and the
text around it stay together in the chunk. The watermark, the page footer and the page number have been removed.

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

For a **scanned** picture book (read by OCR), images are also dropped, and tables become simple rows such as `S/N | Item | Name`.
An example is `Std1_Afya-na-Mazingira_New_Englishmedium`, which has `extraction: "ocr"` and `has_table: true`.

### Example 2: a mathematics book

This is `Std4_Hisabati_Old_Kiswahili.pdf`. Numbers, signs like `+` and `=`, and exercise numbers are all kept, because in a maths book they are content.

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

**A problem with maths pages.** The PDF stores a column sum such as

```
  31072
+ 29853
 ------
  60925
```

in the order it was drawn, so it comes out as `31072 ... + 29853 5`. Exercises that sit side by side on one row also get mixed together
(`1. 2636 2. 47345 3. 36328 + 8345 ...`). All the words and numbers are still there, so searching by topic (for example "kujumlisha" or "carrying")
works well. But do not trust a model to work out the exact sums from these chunks. A fix would be to send the maths pages through OCR, which understands page layout.
The books use plain digits and symbols, not typeset formulas, so no LaTeX is produced.

## 6. What we found in the books, and what we do about it

Before building this, we checked all the PDFs. Here is what we found and what the project does.

| What we found | What we do |
|---|---|
| About 68% of pages already contain real text. Only about 32% are pictures of pages. | We read text pages directly and only OCR the scanned ones. This cuts OCR from about 25,000 pages to about 8,000. |
| Every text page has a "FOR ONLINE USE ONLY / DO NOT DUPLICATE" watermark. Its letters get mixed into the sentences when the text is copied out. | We remove the watermark letters before the text is assembled. This is more reliable than trying to delete the words afterwards. |
| Footers are messy: doubled letters (`SSCCIIEENNCCEE`), software labels (`.indd 23 30/07/2021`) and repeated titles (`TAASISI YA ELIMU TANZANIA 2021`). | We remove doubled letters, these labels, and any line that repeats at the page edge on many pages. |
| 16 files are the same book under different names (for example `Std5_StadiZaKazi...` and `Std5_Stadi-za-Kazi...`, or files ending in `_2` or `(1)`). | We keep one copy. The others are marked `duplicate` in the manifest, so the same text is not embedded twice. |
| Some catalogue languages are wrong. For example `Std1_Kusoma_New_EnglishMedium_b` is really in Kiswahili. | We detect the language from the text. The catalogue label is kept as `language_label`, and a mismatch is shown as a warning. |
| Copyright pages, thank-you pages, tables of contents and reference lists would add noise to search. | We find them and leave them out. The manifest counts what was left out in the `excluded` column. |
| Some text books still have a few picture-only pages (activity pages, 1 to 12 per book). | Those pages are listed by page number in the OCR list. Only they are OCRed, and they are put back into the right place. |
| The first version of the project ran OCR on every book, even the ones with real text, and stopped at raw Markdown. | OCR now only runs on what needs it, and the project goes all the way to clean chunks. |

## 7. How chunks are made

- A chunk is about **1,400 characters** (roughly 350 to 500 tokens). The maximum is about 2,000.
- A chunk **never crosses a chapter**. Chunks follow the book's headings.
- Small neighbouring sections in the same chapter are put together, and their sub-heading is kept inside the text so it is not lost.
- A very long paragraph is split at the end of a sentence.
- The last short paragraph of a chunk is repeated at the start of the next one, so context is not lost at the border.
- Chunks with nothing useful (page-number lines, table-of-contents dots, scanning noise) are left out. Maths is not treated as noise.

## 8. Details about the OCR step

The OCR step uses a model called **PaddleOCR-VL**. It works in two parts: first it finds the paragraphs, titles, tables and pictures on the page
(this always runs on the CPU), then a language model reads each part. The `--backend` option on `ocr` chooses how that second part runs:

| `--backend` | What it is | When to use it |
|---|---|---|
| `mlx` (default) | Runs on the Mac's GPU. `main.py` starts the model server for you (port 8111). | Normal use. |
| `llama` | The same model run by llama.cpp (`brew install llama.cpp`, then `scripts/start_llama_server.sh`). | Only if `mlx` is too slow. |
| `cpu` | Runs on the CPU. | Very slow. Only for testing. |

If you are not sure, use the default and ignore the other two.

`ocr` also keeps the Mac awake while it works, and it skips books that already have real text (they are marked `skipped` in `ocr-raw/progress.csv`).

## 9. Known limits

- **Maths layout:** column sums and side-by-side exercises are mixed up in text pages (see [Example 2](#example-2-a-mathematics-book)).
- **Multi-column pages:** exercises like "match group A with group B" can mix the two columns together.
- **Pictures:** the pictures themselves are not embedded. Only their captions and nearby text are.
- **OCR quality:** we have only checked the OCR result on one scanned book so far. Look at some chunks from the scanned books before trusting them fully.
- **Wrong labels:** a few catalogue labels are wrong. The detected `language` is more trustworthy than `language_label`.

## 10. Code layout and tests

```
main.py                   the one command: ingest | ocr | all
src/
  ingest/                 everything that makes final-chunks/
    textlayer.py            reads text pages and removes the watermark
    ocrjson.py              reads the OCR results from ocr-raw/
    clean.py                text cleaning rules
    chunk.py                splits text into chunks
    lang.py                 detects the language
    pipeline.py             connects all the steps, removes duplicates, writes the files
    model.py                the small data types shared by the other files
  ocr/runner.py           the OCR step: to-do list, resume, page-by-page output
scripts/                  setup.sh, and the model servers for the OCR step
tests/                    automatic tests
```

Run the tests with:

```bash
.venv/bin/python -m pytest tests -q
```
