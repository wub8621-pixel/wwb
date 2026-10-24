"""解析探针的纯函数测试：不读文件、不联网。"""

import unittest

from probe_parse import match_section, section_map, text_stats


class TextStatsTest(unittest.TestCase):
    def test_counts_chars_and_cjk_ratio(self):
        stats = text_stats("教育背景abc")
        self.assertEqual(stats["chars"], 7)
        self.assertAlmostEqual(stats["cjk_ratio"], 4 / 7, places=3)

    def test_empty_text_is_zeroed(self):
        self.assertEqual(
            text_stats("   "), {"chars": 0, "cjk_ratio": 0.0, "garbled_ratio": 0.0}
        )

    def test_private_use_chars_count_as_garbled(self):
        stats = text_stats("教育\ue000\ue000")
        self.assertAlmostEqual(stats["garbled_ratio"], 2 / 4, places=4)

    def test_replacement_char_counts_as_garbled(self):
        self.assertGreater(text_stats("教育�")["garbled_ratio"], 0)


class MatchSectionTest(unittest.TestCase):
    def test_detects_common_headers(self):
        self.assertEqual(match_section("教育背景"), "教育背景")
        self.assertEqual(match_section("  工作经历  "), "工作经历")

    def test_ignores_long_paragraphs(self):
        # 正文里提到“项目”不算章节标题
        self.assertIsNone(match_section("我负责的项目包括支付系统和订单系统，均为核心链路"))

    def test_ignores_blank_lines(self):
        self.assertIsNone(match_section("   "))

    def test_ignores_sentence_fragments(self):
        # PDF 换行碎片：短、含关键词，但带句读，是正文不是标题
        self.assertIsNone(match_section("体项目进行减负与提效。"))


class SectionMapTest(unittest.TestCase):
    def test_counts_lines_under_each_section(self):
        lines = ["教育背景", "2014-2018 本科", "工作经历", "阿里云", "2020-至今", "项目"]
        self.assertEqual(
            section_map(lines),
            [("教育背景", 1), ("工作经历", 2), ("项目", 0)],
        )

    def test_lines_before_first_section_are_ignored(self):
        self.assertEqual(section_map(["武彪", "北京", "教育背景", "本科"]), [("教育背景", 1)])

    def test_repeated_header_does_not_duplicate(self):
        self.assertEqual(section_map(["技能", "Python", "技能", "Java"]), [("技能", 2)])


if __name__ == "__main__":
    unittest.main()
