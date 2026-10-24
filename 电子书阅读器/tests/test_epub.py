"""EPUB 打包与封面生成。"""

import tempfile
import unittest
import zipfile
from pathlib import Path

from config.loader import load_config

from ebook.cover import make_cover, wrap_text
from ebook.epub import build_epub, render_chapter, safe_filename

CHAPTERS = [
    {"title": "第一章 开始", "body": "第一段。\n\n第二段。"},
    {"title": "第二章 结束", "body": "结尾。"},
]


class RenderTest(unittest.TestCase):
    def test_escapes_html(self):
        html = render_chapter({"title": "<标题>", "body": "a < b"})
        self.assertIn("&lt;标题&gt;", html)
        self.assertIn("&lt;", html)
        self.assertNotIn("<标题>", html)

    def test_paragraph_split(self):
        html = render_chapter(CHAPTERS[0])
        self.assertEqual(html.count("<p>"), 2)

    def test_safe_filename(self):
        self.assertEqual(safe_filename("a/b:c"), "a_b_c")


class BuildTest(unittest.TestCase):
    def test_builds_epub_with_nav_and_chapters(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "book.epub"
            build_epub(
                target,
                title="测试书",
                author="张三",
                description="简介",
                language="zh-CN",
                chapters=CHAPTERS,
            )
            self.assertTrue(target.exists())
            with zipfile.ZipFile(target) as archive:
                names = archive.namelist()
            self.assertTrue(any(name.endswith("nav.xhtml") for name in names))
            self.assertTrue(any(name.endswith("chap_001.xhtml") for name in names))
            self.assertTrue(any(name.endswith("chap_002.xhtml") for name in names))

    def test_builds_epub_with_cover(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "book.epub"
            cover = make_cover(
                "测试书", "张三",
                width=300, height=450,
                background="#1f3a5f", text_color="#ffffff",
            )
            build_epub(
                target,
                title="测试书",
                author="张三",
                description="",
                language="zh-CN",
                chapters=CHAPTERS,
                cover_bytes=cover,
            )
            with zipfile.ZipFile(target) as archive:
                names = archive.namelist()
            self.assertTrue(any("cover" in name for name in names))


class CoverTest(unittest.TestCase):
    def test_wrap_respects_width(self):
        lines = wrap_text("一二三四五六七八九十", 4)
        self.assertEqual(lines, ["一二三四", "五六七八", "九十"])

    def test_cover_is_png(self):
        data = make_cover(
            "书名", "作者",
            width=300, height=450,
            background="#1f3a5f", text_color="#ffffff",
        )
        self.assertTrue(data.startswith(b"\x89PNG"))

    def test_cover_config_dimensions(self):
        cfg = load_config()
        data = make_cover(
            "书名", "",
            width=cfg.get("epub.cover.width"),
            height=cfg.get("epub.cover.height"),
            background=cfg.get("epub.cover.background"),
            text_color=cfg.get("epub.cover.text_color"),
        )
        self.assertTrue(data.startswith(b"\x89PNG"))


if __name__ == "__main__":
    unittest.main()
