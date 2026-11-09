"""Tests for the ingestion layer (no OCR/Paddle needed)."""
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from ingest import chunk as C, clean, lang, ocrjson, pipeline  # noqa: E402
from ingest.model import Block, BookText, PageInfo  # noqa: E402


class CleanTests(unittest.TestCase):
    def test_watermark_and_slugs(self):
        self.assertEqual(clean.strip_watermark("FOR ONLINE USE ONLY text").strip(), "text")
        self.assertTrue(clean.is_junk_line("Science and Technology 3.indd 23   30/07/2021 15:27"))
        self.assertTrue(clean.is_junk_line("09/12/2023 15:40"))
        self.assertFalse(clean.is_junk_line("Heat energy"))

    def test_hyphen_join_and_normalize(self):
        self.assertEqual(clean.join_lines(["The environ-", "ment is", "clean"]), "The environment is clean")
        self.assertEqual(clean.normalize("a­ b​  c"), "a b c")
        self.assertEqual(clean.normalize("Chapt er One"), "Chapter One")

    def test_table_html(self):
        t = clean.html_table_to_text('<table><tr><td>1 <img src="x.jpg"/></td><td>kiti</td></tr></table>')
        self.assertEqual(t, "1 | kiti")

    def test_math_is_not_garbage_but_noise_is(self):
        self.assertFalse(clean.is_garbage("1. Jumlisha: 31072 + 29853 = 60925, 25300 + 60200 = 85500"))
        self.assertTrue(clean.is_garbage("~~~ ... --- 12"))


class LangTests(unittest.TestCase):
    def test_detect(self):
        sw = "Mwalimu alisema kwamba watoto wote wa darasa la kwanza wanatakiwa kuwa na usafi na afya katika shule yao. " * 4
        en = "The teacher said that all the pupils in the class should keep the school clean and use the books for their work. " * 4
        self.assertEqual(lang.detect_language(sw), "sw")
        self.assertEqual(lang.detect_language(en), "en")
        self.assertEqual(lang.detect_language("这是一个测试" * 30), "zh")
        self.assertEqual(lang.detect_language("short"), "unknown")

    def test_labels(self):
        self.assertEqual(lang.label_to_code("English-medium"), "en")
        self.assertEqual(lang.label_to_code("Kiswahili (1)"), "sw")
        self.assertEqual(lang.label_to_code("", "Kichina"), "zh")


def para(page, text, kind="para", level=0):
    return Block(page=page, kind=kind, text=text, level=level)


class ChunkTests(unittest.TestCase):
    def test_sections_do_not_cross_chapters_and_carry_path(self):
        a = "Maji ni muhimu kwa maisha ya binadamu na wanyama wote duniani. " * 12
        blocks = [para(1, "Chapter One", "heading", 1), para(1, "Water", "heading", 2), para(1, a),
                  para(2, "Chapter Two", "heading", 1), para(2, a)]
        cs = C.chunk_blocks(blocks)
        self.assertEqual(cs[0].section_path, ["Chapter One", "Water"])
        self.assertEqual(cs[-1].section_path, ["Chapter Two"])
        self.assertTrue(all(len(c.text) <= C.MAX_CHARS + C.OVERLAP_CHARS for c in cs))

    def test_front_matter_until_first_chapter(self):
        body = "Hii ni sentensi ya majaribio yenye maneno ya kutosha kabisa. " * 8
        blocks = [para(2, "Shukurani", "heading", 1), para(2, body), para(5, "Sura ya Kwanza Usafi", "heading", 1), para(5, body)]
        cs = C.chunk_blocks(blocks)
        self.assertEqual([C.classify_chunk(c) for c in cs], ["front_matter", ""])

    def test_long_paragraph_is_split(self):
        blob = " ".join(f"Sentence number {i} says something useful about science." for i in range(200))
        cs = C.chunk_blocks([para(1, "Chapter One", "heading", 1), para(1, blob)])
        self.assertGreater(len(cs), 3)
        self.assertTrue(all(len(c.text) <= C.MAX_CHARS + C.OVERLAP_CHARS for c in cs))

    def test_small_sections_pack_with_subheading_kept(self):
        t = "Short text about the tongue and how it tastes food well enough. "
        cs = C.chunk_blocks([para(1, "Chapter Two", "heading", 1), para(1, "The nose", "heading", 2), para(1, t),
                             para(1, "The skin", "heading", 2), para(1, t)])
        self.assertEqual(len(cs), 1)
        self.assertIn("The skin", cs[0].text)


class OcrJsonTests(unittest.TestCase):
    def test_read_book_with_page_map(self):
        d = tempfile.mkdtemp()
        page = {"page_index": 0, "parsing_res_list": [
            {"block_label": "header", "block_content": "FOR ONLINE USE ONLY", "block_order": None},
            {"block_label": "paragraph_title", "block_content": "Usafi", "block_order": 1},
            {"block_label": "text", "block_content": "Osha mikono yako kila siku.", "block_order": 2},
            {"block_label": "image", "block_content": "", "block_order": 3},
            {"block_label": "number", "block_content": "7", "block_order": 4}]}
        json.dump(page, open(os.path.join(d, "Book_0_res.json"), "w"))
        b = ocrjson.read_book(d, {"0": 42})
        self.assertEqual([(x.page, x.kind) for x in b.blocks], [(42, "heading"), (42, "para")])
        self.assertEqual(b.blocks[0].printed_page, "7")


class RoutingTests(unittest.TestCase):
    def _book(self, infos):
        return BookText(pages=[PageInfo(i + 1, c, cov, c < 50 and cov >= 0.5) for i, (c, cov) in enumerate(infos)])

    def test_routes(self):
        text = self._book([(0, 1.0)] + [(500, 0.1)] * 18 + [(0, 1.0)])          # only covers lack text
        self.assertEqual(pipeline.route(text, 20), ("text", []))
        mixed = self._book([(500, 0.1)] * 5 + [(0, 0.9)] + [(500, 0.1)] * 4)
        self.assertEqual(pipeline.route(mixed, 10), ("mixed", [6]))
        scan = self._book([(0, 0.95)] * 10)
        kind, pages = pipeline.route(scan, 10)
        self.assertEqual(kind, "ocr")

    def test_merge_ocr_keeps_text_pages(self):
        txt = BookText(blocks=[para(1, "one"), para(2, "two")], pages=[PageInfo(1, 100, 0, False), PageInfo(2, 0, 1, True)])
        ocr = BookText(blocks=[para(2, "ocr two")], pages=[PageInfo(2, 10, 0, False)])
        m = pipeline.merge_ocr(txt, ocr, {2})
        self.assertEqual([b.text for b in m.blocks], ["one", "ocr two"])
        m = pipeline.merge_ocr(txt, ocr, None)
        self.assertEqual([b.text for b in m.blocks], ["ocr two"])


class TextLayerTests(unittest.TestCase):
    def test_rotated_watermark_and_stream_watermark_removed(self):
        try:
            from reportlab.pdfgen import canvas  # optional dev dependency
        except ImportError:
            self.skipTest("reportlab not installed")
        import pdfplumber
        from ingest import textlayer
        path = os.path.join(tempfile.mkdtemp(), "t.pdf")
        c = canvas.Canvas(path)
        c.setFont("Helvetica", 12)
        c.drawString(72, 700, "FOR ONLINE USE ONLY DO NOT DUPLICATE")
        c.drawString(72, 650, "Real body text about water and health.")
        c.saveState(); c.translate(200, 300); c.rotate(45); c.setFont("Helvetica", 60); c.drawString(0, 0, "FOR ONLINE USE ONLY"); c.restoreState()
        c.save()
        with pdfplumber.open(path) as pdf:
            text = textlayer.clean_page_chars(pdf.pages[0]).extract_text()
        self.assertIn("Real body text", text)
        self.assertNotRegex(text.upper(), "ONLINE|DUPLICATE")


if __name__ == "__main__":
    unittest.main()


class FigureTests(unittest.TestCase):
    def test_has_figure(self):
        self.assertTrue(C.has_figure("See Figure 6 Using a weighing scales"))
        self.assertTrue(C.has_figure("Tazama Kielelezo 3"))
        self.assertFalse(C.has_figure("Plain text about numbers 12 + 3"))
