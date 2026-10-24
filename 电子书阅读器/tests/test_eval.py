"""评测脚本：覆盖率、HTML 清洗、产物检查。"""

import json
import tempfile
import unittest
from pathlib import Path

from config.loader import load_config

from ebook.epub import build_epub
from eval.check_epub import character_coverage, find_source, inspect, strip_html

CHAPTERS = [
    {"title": "第一章", "body": "第一段内容。"},
    {"title": "第二章", "body": "第二段内容。"},
]


class CoverageTest(unittest.TestCase):
    def test_identical_text_is_full_coverage(self):
        coverage, missing = character_coverage("第一章正文", "第一章正文")
        self.assertEqual(coverage, 1.0)
        self.assertEqual(missing, 0)

    def test_missing_chars_detected(self):
        coverage, missing = character_coverage("甲乙丙丁", "甲乙")
        self.assertAlmostEqual(coverage, 0.5)
        self.assertEqual(missing, 2)

    def test_whitespace_ignored(self):
        coverage, _ = character_coverage("甲 乙\n丙", "甲乙丙")
        self.assertEqual(coverage, 1.0)

    def test_empty_source(self):
        self.assertEqual(character_coverage("", "任意"), (1.0, 0))


class HtmlTest(unittest.TestCase):
    def test_strip_tags_and_unescape(self):
        self.assertEqual(strip_html("<p>a &lt; b</p>").strip(), "a < b")


class InspectTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source = self.root / "书名.txt"
        self.source.write_text(
            "第一章\n第一段内容。\n第二章\n第二段内容。", encoding="utf-8"
        )
        self.epub = self.root / "书名.epub"
        build_epub(
            self.epub,
            title="书名",
            author="作者",
            description="",
            language="zh-CN",
            chapters=CHAPTERS,
        )
        self.encodings = load_config().get("ingest.encodings")

    def tearDown(self):
        self.tmp.cleanup()

    def test_chapters_and_metadata(self):
        result = inspect(self.epub, self.source, self.encodings)
        self.assertTrue(result["ok"])
        self.assertEqual(result["chapters"], 2)
        self.assertEqual(result["title"], "书名")
        self.assertEqual(result["author"], "作者")

    def test_coverage_is_full_for_built_epub(self):
        result = inspect(self.epub, self.source, self.encodings)
        self.assertEqual(result["coverage"], 1.0)
        self.assertEqual(result["missing_chars"], 0)

    def test_no_source_skips_coverage(self):
        result = inspect(self.epub, None, self.encodings)
        self.assertNotIn("coverage", result)

    def test_broken_file_reports_error(self):
        broken = self.root / "broken.epub"
        broken.write_bytes(b"not an epub")
        result = inspect(broken, None, self.encodings)
        self.assertFalse(result["ok"])

    def test_find_source(self):
        found = find_source(self.epub, [self.root])
        self.assertEqual(found, self.source)
        self.assertIsNone(find_source(self.epub, [self.root / "nope"]))


if __name__ == "__main__":
    unittest.main()
