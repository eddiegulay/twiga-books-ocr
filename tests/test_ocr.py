"""Unit tests for twiga_ocr with dummy PDFs and a mocked PaddleOCR-VL pipeline (no Paddle needed)."""
import datetime as dt
import os
import shutil
import sys
import tempfile
import unittest

import pandas as pd
from pypdf import PdfReader, PdfWriter

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from src.ocr import runner as T  # noqa: E402

CATALOGUE = os.path.join(ROOT, "catalogue.xlsx")


def mkpdf(path, n):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    w = PdfWriter()
    for _ in range(n):
        w.add_blank_page(100, 100)
    with open(path, "wb") as f:
        w.write(f)


class FakeRes(dict):
    def __init__(self, stem, page):
        super().__init__(page_index=page)
        self.stem, self.page = stem, page

    def save_to_json(self, save_path):
        open(os.path.join(save_path, f"{self.stem}_{self.page}_res.json"), "w").write('{"page": %d}' % self.page)

    def save_to_markdown(self, save_path):
        os.makedirs(os.path.join(save_path, "imgs"), exist_ok=True)
        open(os.path.join(save_path, "imgs", f"crop_{self.page}.jpg"), "wb").write(b"x")
        open(os.path.join(save_path, f"{self.stem}_{self.page}.md"), "w").write(f"page {self.page}")


class FakeMerged:
    def __init__(self, pages):
        self.pages = pages

    def save_to_markdown(self, save_path):
        assert save_path.endswith(".md")
        open(save_path, "w").write("# merged\n" + "\n".join(f"page {p.page}" for p in self.pages))


class FakePipeline:
    def __init__(self):
        self.calls, self.restructure_kwargs, self.fail_on = [], [], set()
        self.seen_json_during_run = []

    def predict_iter(self, input, **kw):
        self.calls.append((os.path.basename(input), kw))
        n = len(PdfReader(input).pages)
        stem = os.path.splitext(os.path.basename(input))[0]
        for i in range(n):
            if self.fail_on and any(f in self.current for f in self.fail_on):
                raise RuntimeError("boom")
            yield FakeRes(stem, i)

    def restructure_pages(self, res_list, **kw):
        self.restructure_kwargs.append(kw)
        return [FakeMerged(res_list)]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.books = os.path.join(self.tmp, "MyDrive", "Twiga Tanzania", "Twiga-Primary-School-Books")
        cat = pd.read_excel(CATALOGUE, sheet_name="Catalogue")
        sample = cat.groupby("Class folder").head(3)            # 3 books per class keeps tests fast
        for _, r in sample.iterrows():
            sub = "Teaching Guides" if r["Book type"] == "Teacher's guide" else ""
            mkpdf(os.path.join(self.books, r["Class folder"], sub, r["Filename"]), 2)
        mkpdf(os.path.join(self.books, "TIE_Primary_Curriculum_2023_Kiswahili.pdf"), 1)
        mkpdf(os.path.join(self.books, "class-2", "Std2_Unknown-Subject_New_Kiswahili.pdf"), 3)   # not catalogued
        shutil.copy(CATALOGUE, os.path.join(self.books, "Twiga-Primary-Books-Catalogue.xlsx"))
        # outputs from an earlier run must never be queued
        mkpdf(os.path.join(self.books, "parsed-books", "class-1", "x", "y", "parsed-output", "stray.pdf"), 1)
        self.n_sources = len(sample) + 2
        T.SESSION_START = dt.datetime.now(dt.timezone.utc)
        T.configure(self.books, work_dir=os.path.join(self.tmp, "work"))
        self.fake = FakePipeline()
        self.fake.current = ""
        T._PIPELINE = self.fake
        orig = T.run_book

        def run_book(pdf, out, stem, **kw):
            self.fake.current = stem
            return orig(pdf, out, stem, **kw)
        T.run_book = run_book
        self.addCleanup(setattr, T, "run_book", orig)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        T._PIPELINE = None


class TestFilename(unittest.TestCase):
    def test_examples(self):
        self.assertEqual(T.parse_filename("Std1_Afya-na-Mazingira_Old_Kiswahili.pdf"),
                         ("class-1", "Afya na Mazingira", "Old", "Kiswahili"))
        self.assertEqual(T.parse_filename("Std1_Kusoma_Kitabu2_Old_Kiswahili.pdf"), ("class-1", "Kusoma", "Old", "Kiswahili"))
        self.assertEqual(T.parse_filename("Std3-7_English_TeachersGuide_Old_English.pdf", "class-3"),
                         ("class-3", "English", "Old", "English"))
        self.assertEqual(T.parse_filename("Std1_Kusoma_New_EnglishMedium_b.pdf"), ("class-1", "Kusoma", "New", "English-medium"))
        self.assertEqual(T.parse_filename("Std1_LearnEnglish_EnglishMedium_New.pdf"),
                         ("class-1", "LearnEnglish", "New", "English-medium"))
        self.assertEqual(T.parse_filename("Std1_AfyaMazingira_New.pdf"), ("class-1", "AfyaMazingira", "New", ""))
        self.assertEqual(T.parse_filename("Std3-7_Kiswahili_MwongozoMwalimu_Old_Kiswahili.pdf"),
                         ("class-3", "Kiswahili", "Old", "Kiswahili"))
        self.assertEqual(T.parse_filename("TIE_Primary_Curriculum_2023_Kiswahili.pdf")[0], "general")

    def test_norm(self):
        self.assertEqual(T.norm_curriculum("Old 2015-16"), "Old")
        self.assertEqual(T.norm_curriculum("New 2023"), "New")
        self.assertEqual(T.norm_language("English-medium"), "English-medium")
        self.assertEqual(T.norm_language("KiswahiliMedium"), "Kiswahili-medium")


class TestQueue(Base):
    def test_build_order_and_metadata(self):
        q = T.build_queue()
        self.assertEqual(len(q), self.n_sources)
        self.assertFalse(q["drive_path"].str.contains("parsed-books").any())
        self.assertEqual(list(q.columns), T.COLUMNS)
        keys = [(T.class_key(c), s.lower()) for c, s in zip(q["class"], q["subject"])]
        self.assertEqual(keys, sorted(keys), "queue must be class by class, then subject by subject")
        self.assertEqual(list(q["order"]), [str(i) for i in range(1, len(q) + 1)])
        self.assertEqual(q.iloc[-1]["class"], "general")
        self.assertTrue((q["status"] == "pending").all())
        self.assertTrue((q["pages"] != "").all())
        unk = q[q.file_name == "Std2_Unknown-Subject_New_Kiswahili.pdf"].iloc[0]
        self.assertEqual((unk["class"], unk["subject"], unk["curriculum"], unk["language"], unk["pages"]),
                         ("class-2", "Unknown Subject", "New", "Kiswahili", "3"))
        tg = q[q.drive_path.str.contains("Teaching Guides")]
        self.assertGreater(len(tg), 0)
        r = q[q.file_name == "Std1-2_English_TeachersGuide_Old_English.pdf"].iloc[0]
        self.assertEqual((r["class"], r["curriculum"], r["language"]), ("class-1", "Old", "English"))
        r = q[q.file_name == "Std1_Afya-na-Mazingira_New_Englishmedium.pdf"]
        if len(r):
            self.assertEqual(r.iloc[0]["language"], "English-medium")

    def test_no_overwrite_append_only(self):
        T.build_queue()
        df = T.load_progress()
        df.loc[0, ["status", "pages_parsed", "error"]] = ["done", "2", "user note kept"]
        T.save_progress(df)
        before = T.load_progress()
        mtime = os.path.getmtime(T.PROGRESS_CSV)
        q = T.build_queue()                                    # nothing new: file untouched
        self.assertEqual(os.path.getmtime(T.PROGRESS_CSV), mtime)
        pd.testing.assert_frame_equal(q, before)
        mkpdf(os.path.join(self.books, "class-1", "Std1_Zzz-New-Book_New_Kiswahili.pdf"), 2)
        q = T.build_queue()
        self.assertEqual(len(q), len(before) + 1)
        pd.testing.assert_frame_equal(q.iloc[:len(before)].reset_index(drop=True), before)
        last = q.iloc[-1]
        self.assertEqual((last["order"], last["status"], last["class"]), (str(len(before) + 1), "pending", "class-1"))
        self.assertFalse(os.path.exists(T.PROGRESS_CSV + ".tmp"))


class TestParse(Base):
    def test_parse_writes_outputs_and_marks_done(self):
        T.build_queue()
        T.parse(n=2)
        df = T.load_progress()
        done = df[df.status == "done"]
        self.assertEqual(list(done["order"]), ["1", "2"])
        for _, r in done.iterrows():
            md = os.path.join(T.OUTPUT_DIR, r["output_md"])
            self.assertTrue(os.path.exists(md), md)
            self.assertTrue(r["output_md"].endswith("/parsed-output/" + r["file_name"][:-4] + ".md"))
            parts = r["output_md"].split("/")
            self.assertEqual(parts[0], r["class"])
            self.assertEqual(parts[1], T.slug(r["subject"]))
            jd = os.path.join(T.OUTPUT_DIR, r["output_json_dir"])
            self.assertEqual(len(os.listdir(jd)), int(r["pages"]))
            self.assertEqual(r["pages_parsed"], r["pages"])
            pdir = os.path.join(os.path.dirname(md), "pages")
            self.assertEqual(sorted(os.listdir(pdir)), [f"page_{i:04d}" for i in range(1, int(r["pages"]) + 1)])
            self.assertTrue(os.path.isdir(os.path.join(pdir, "page_0001", "imgs")))
            self.assertTrue(sorted(os.listdir(jd))[0].startswith(r["file_name"][:-4] + "_0"))
            self.assertTrue(r["started_at"] and r["finished_at"] and not r["error"])
        self.assertEqual(self.fake.restructure_kwargs[0],
                         {"merge_tables": True, "relevel_titles": True, "concatenate_pages": True})
        kw = self.fake.calls[0][1]
        self.assertTrue(kw["use_queues"] and kw["use_doc_orientation_classify"])
        self.assertFalse(os.listdir(T.WORK_DIR), "local PDF copies are cleaned up")

    def test_filters_failure_and_retry(self):
        q = T.build_queue()
        target = q[q["class"] == "class-3"].iloc[0]
        self.fake.fail_on = {target["file_name"][:-4]}
        T.parse(n=1, cls="class-3")
        df = T.load_progress()
        row = df[df.order == target["order"]].iloc[0]
        self.assertEqual(row["status"], "failed")
        self.assertIn("boom", row["error"])
        T.parse(n=1, cls=3)                                    # skips the failed one
        df = T.load_progress()
        self.assertEqual((df[df["class"] == "class-3"]["status"] == "done").sum(), 1)
        self.fake.fail_on = set()
        T.parse(n=1, cls="Std3", retry_failed=True)
        df = T.load_progress()
        self.assertEqual(df[df.order == target["order"]].iloc[0]["status"], "done")
        subj = q.iloc[0]["subject"]
        T.parse(subject=subj)
        df = T.load_progress()
        self.assertTrue((df[df.subject == subj]["status"] == "done").all())

    def test_resume_stale_in_progress(self):
        T.build_queue()
        df = T.load_progress()
        df.loc[4, ["status", "started_at"]] = ["in_progress", "2026-01-01T00:00:00+00:00"]   # from a dead session
        df.loc[5, ["status", "started_at"]] = ["in_progress", T.now_iso()]                 # live in this session
        T.save_progress(df)
        stale_dir = os.path.join(T.OUTPUT_DIR, T.book_rel_dir(df.loc[4], df))
        os.makedirs(stale_dir)
        open(os.path.join(stale_dir, "partial.json"), "w").write("{}")
        T.parse(n=5)
        df = T.load_progress()
        self.assertEqual(df.loc[4, "status"], "done")
        self.assertFalse(os.path.exists(os.path.join(stale_dir, "partial.json")), "partial output replaced")
        self.assertEqual(df.loc[5, "status"], "in_progress", "a fresh in_progress row is left alone")

    def test_status_counts(self):
        T.build_queue()
        T.parse(n=1)
        tab = T.status(show=False)
        self.assertEqual(int(tab.loc["TOTAL", "done"]), 1)
        self.assertEqual(int(tab.loc["TOTAL", "books"]), self.n_sources)
        self.assertEqual(list(tab.index[:-1]), sorted(tab.index[:-1], key=lambda c: (T.class_key(c), c)))

    def test_collision_suffix(self):
        mkpdf(os.path.join(self.books, "class-1", "Teaching Guides", "Std1_Kuandika_New_Kiswahili.pdf"), 1)
        mkpdf(os.path.join(self.books, "class-1", "Std1_Kuandika_New_Kiswahili.pdf"), 1)
        q = T.build_queue()
        rows = q[q.file_name == "Std1_Kuandika_New_Kiswahili.pdf"]
        dirs = {T.book_rel_dir(r, q) for _, r in rows.iterrows()}
        self.assertEqual(len(dirs), len(rows))

    def test_unwarp_choice(self):
        T.UNWARP_MODE = "scans"
        scan = next(f for f, m in T.CATALOGUE_META.items() if m["copy_quality"] == "scan")
        text = next(f for f, m in T.CATALOGUE_META.items() if m["copy_quality"] == "text pdf")
        self.assertTrue(T.unwarp_for(scan))
        self.assertFalse(T.unwarp_for(text))
        self.assertTrue(T.unwarp_for("not-in-catalogue.pdf"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
