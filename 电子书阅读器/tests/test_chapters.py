"""章节切分与质量判定。"""

import unittest

from config.loader import load_config

from ebook.chapters import analyze, out_of_order, parse_number, split_chapters


class ParseNumberTest(unittest.TestCase):
    def test_arabic(self):
        self.assertEqual(parse_number("12"), 12)

    def test_chinese(self):
        self.assertEqual(parse_number("一"), 1)
        self.assertEqual(parse_number("十"), 10)
        self.assertEqual(parse_number("十一"), 11)
        self.assertEqual(parse_number("二十"), 20)
        self.assertEqual(parse_number("一百零五"), 105)

    def test_unparseable(self):
        self.assertIsNone(parse_number("上"))


class SplitTest(unittest.TestCase):
    def setUp(self):
        self.cfg = load_config()
        self.patterns = self.cfg.get("chapters.patterns")

    def split(self, text, max_chars=40):
        return split_chapters(text, self.patterns, "正文", max_chars)

    def test_splits_on_headings(self):
        text = "第一章 起点\n内容一\n第二章 转折\n内容二\n第三章 结尾\n内容三"
        chapters = self.split(text)
        self.assertEqual([c.title for c in chapters], ["第一章 起点", "第二章 转折", "第三章 结尾"])
        self.assertEqual(chapters[0].body, "内容一")

    def test_preface_kept_as_first_chapter(self):
        text = "这是前言。\n第一章 起点\n内容一"
        chapters = self.split(text)
        self.assertEqual(chapters[0].title, "正文")
        self.assertEqual(chapters[0].body, "这是前言。")
        self.assertEqual(chapters[1].title, "第一章 起点")

    def test_without_headings_returns_single_chapter(self):
        chapters = self.split("没有任何章节标记的散文。")
        self.assertEqual(len(chapters), 1)
        self.assertEqual(chapters[0].title, "正文")

    def test_heading_without_body_is_dropped(self):
        text = "第一章 空\n第二章 有内容\n内容"
        chapters = self.split(text)
        self.assertEqual([c.title for c in chapters], ["第二章 有内容"])

    def test_long_line_starting_with_heading_is_not_heading(self):
        long_line = "第一章 " + "很长的正文" * 20
        chapters = self.split(long_line, max_chars=40)
        self.assertEqual(len(chapters), 1)
        self.assertEqual(chapters[0].title, "正文")

    def test_chinese_number_out_of_order(self):
        text = "第三章 三\n正文\n第一章 一\n正文"
        chapters = self.split(text)
        self.assertTrue(out_of_order(chapters))

    def test_english_chapter_pattern(self):
        chapters = self.split("Chapter 1\n内容\nChapter 2\n内容")
        self.assertEqual(len(chapters), 2)


class AnalyzeTest(unittest.TestCase):
    def setUp(self):
        self.cfg = load_config()

    def analyze(self, text, chapters, **overrides):
        options = {
            "garbled_threshold": self.cfg.get("quality.garbled_ratio"),
            "blank_threshold": self.cfg.get("quality.blank_ratio"),
            "min_chapters": self.cfg.get("chapters.min_chapters"),
            "long_text_threshold": self.cfg.get("chapters.long_text_threshold"),
            "check_order": self.cfg.get("quality.check_order"),
        }
        options.update(overrides)
        return analyze(text, chapters, **options)

    def test_healthy_text_needs_no_llm(self):
        text = "第一章 开始\n" + "正文" * 100
        chapters = split_chapters(text, self.cfg.get("chapters.patterns"), "正文")
        report = self.analyze(text, chapters)
        self.assertFalse(report["needs_llm"])

    def test_few_chapters_in_long_text_triggers(self):
        text = "没有章节标题的正文" * 1000
        chapters = split_chapters(text, self.cfg.get("chapters.patterns"), "正文")
        report = self.analyze(text, chapters)
        self.assertTrue(report["needs_llm"])
        self.assertTrue(any("低于" in reason for reason in report["reasons"]))

    def test_blank_lines_trigger(self):
        text = "第一章\n正文\n" + "\n" * 80
        chapters = split_chapters(text, self.cfg.get("chapters.patterns"), "正文")
        report = self.analyze(text, chapters)
        self.assertTrue(report["needs_llm"])

    def test_garbled_triggers(self):
        text = "第一章\n" + "" * 200
        chapters = split_chapters(text, self.cfg.get("chapters.patterns"), "正文")
        report = self.analyze(text, chapters)
        self.assertTrue(any("乱码" in reason for reason in report["reasons"]))

    def test_reordered_chapters_trigger(self):
        text = "第三章\n正文\n第一章\n正文"
        chapters = split_chapters(text, self.cfg.get("chapters.patterns"), "正文")
        report = self.analyze(text, chapters)
        self.assertTrue(report["out_of_order"])

    def test_order_check_can_be_disabled(self):
        text = "第三章\n正文\n第一章\n正文"
        chapters = split_chapters(text, self.cfg.get("chapters.patterns"), "正文")
        report = self.analyze(text, chapters, check_order=False)
        self.assertFalse(report["out_of_order"])


if __name__ == "__main__":
    unittest.main()
