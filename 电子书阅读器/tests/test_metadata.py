"""元数据提取与合并。"""

import unittest
from pathlib import Path

from config.loader import load_config

from ebook.metadata import extract_local, merge_metadata, title_from_filename


class TitleTest(unittest.TestCase):
    def test_uses_filename(self):
        self.assertEqual(title_from_filename(Path("三体.txt")), "三体")

    def test_strips_junk_suffix(self):
        self.assertEqual(title_from_filename(Path("三体_最终版.txt")), "三体")

    def test_replaces_underscores(self):
        self.assertEqual(title_from_filename(Path("the_lord_of_the_rings.txt")), "the lord of the rings")

    def test_only_junk_stem_falls_back(self):
        self.assertEqual(title_from_filename(Path("_最终版.txt")), "未命名")


class ExtractTest(unittest.TestCase):
    def setUp(self):
        self.cfg = load_config()
        self.patterns = self.cfg.get("metadata.author_patterns")
        self.max_chars = self.cfg.get("metadata.description_max_chars")

    def extract(self, text, name="书名.txt"):
        return extract_local(text, Path(name), self.patterns, self.max_chars)

    def test_author_from_head(self):
        meta = self.extract("书名\n作者：张三\n这是第一段简介。")
        self.assertEqual(meta["author"], "张三")

    def test_author_with_space_and_colon(self):
        meta = self.extract("著 者： 李四\n简介")
        self.assertEqual(meta["author"], "李四")

    def test_description_is_first_paragraph(self):
        meta = self.extract("书名\n\n这是一段简介。\n第二段")
        self.assertEqual(meta["description"], "这是一段简介。")

    def test_description_truncated(self):
        meta = self.extract("书名\n" + "长" * 500)
        self.assertEqual(len(meta["description"]), self.max_chars)

    def test_missing_author_is_empty(self):
        self.assertEqual(self.extract("只有正文")["author"], "")


class MergeTest(unittest.TestCase):
    def test_llm_fills_only_missing_fields(self):
        local = {"title": "本地书名", "author": "", "description": "本地简介"}
        extra = {"title": "模型书名", "author": "模型作者", "description": "模型简介"}
        merged = merge_metadata(local, extra)
        self.assertEqual(merged["title"], "本地书名")
        self.assertEqual(merged["author"], "模型作者")
        self.assertEqual(merged["description"], "本地简介")

    def test_none_extra_is_safe(self):
        local = {"title": "书名", "author": "", "description": ""}
        self.assertEqual(merge_metadata(local, None), local)


if __name__ == "__main__":
    unittest.main()
