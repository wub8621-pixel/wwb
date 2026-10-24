"""目录扫描与编码探测。"""

import tempfile
import unittest
from pathlib import Path

from ebook.ingest import IngestError, discover_files, garbled_ratio, read_text


class DiscoverTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "a.txt").write_text("一", encoding="utf-8")
        (self.root / "b.TXT").write_text("二", encoding="utf-8")
        (self.root / "c.pdf").write_text("三", encoding="utf-8")
        nested = self.root / "sub"
        nested.mkdir()
        (nested / "d.txt").write_text("四", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_only_allowed_extensions(self):
        names = [p.name for p in discover_files(self.root, [".txt"])]
        self.assertEqual(names, ["a.txt", "b.TXT"])

    def test_recursive_off_by_default(self):
        names = [p.name for p in discover_files(self.root, [".txt"])]
        self.assertNotIn("d.txt", names)

    def test_recursive_picks_nested(self):
        names = [p.name for p in discover_files(self.root, [".txt"], recursive=True)]
        self.assertIn("d.txt", names)

    def test_missing_directory_raises(self):
        with self.assertRaises(IngestError):
            discover_files(self.root / "nope", [".txt"])


class ReadTextTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_utf8(self):
        path = self.dir / "u.txt"
        path.write_text("第一章 开始\n正文", encoding="utf-8")
        text, encoding, garbled = read_text(path, ["utf-8-sig", "utf-8", "gb18030"])
        self.assertIn(encoding, ("utf-8-sig", "utf-8"))
        self.assertIn("第一章", text)
        self.assertEqual(garbled, 0.0)

    def test_gbk_falls_back(self):
        path = self.dir / "g.txt"
        path.write_bytes("中文内容测试".encode("gbk"))
        text, encoding, _ = read_text(path, ["utf-8", "gb18030"])
        self.assertIn(encoding, ("gb18030", "gbk"))
        self.assertIn("中文内容测试", text)

    def test_crlf_normalized(self):
        path = self.dir / "w.txt"
        path.write_bytes("第一行\r\n第二行\r\n".encode("utf-8"))
        text, _, _ = read_text(path, ["utf-8"])
        self.assertNotIn("\r", text)

    def test_bom_removed(self):
        path = self.dir / "bom.txt"
        path.write_bytes("﻿正文".encode("utf-8"))
        text, _, _ = read_text(path, ["utf-8-sig", "utf-8"])
        self.assertTrue(text.startswith("正文"))

    def test_empty_file_raises(self):
        path = self.dir / "empty.txt"
        path.write_bytes(b"   \n")
        with self.assertRaises(IngestError):
            read_text(path, ["utf-8"])

    def test_garbled_ratio_counts_private_use(self):
        self.assertGreater(garbled_ratio("正常"), 0)
        self.assertEqual(garbled_ratio("正常文本"), 0.0)


if __name__ == "__main__":
    unittest.main()
