"""解析质量闸门的纯函数测试：不读文件、不联网。"""

import unittest

from resume_agent.quality import (
    assess_quality,
    header_footer_lines,
    match_section,
    section_map,
    strip_repeated_edges,
    text_stats,
)

GOOD_LINES = ["教育背景", "燕京理工学院 本科"] + ["具备扎实的后端工程化能力"] * 20


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
        self.assertIsNone(match_section("我负责的项目包括支付系统和订单系统，均为核心链路"))

    def test_ignores_blank_lines(self):
        self.assertIsNone(match_section("   "))

    def test_ignores_sentence_fragments(self):
        self.assertIsNone(match_section("体项目进行减负与提效。"))


class SectionMapTest(unittest.TestCase):
    def test_counts_lines_under_each_section(self):
        lines = ["教育背景", "2014-2018 本科", "工作经历", "阿里云", "2020-至今", "项目"]
        self.assertEqual(
            section_map(lines), [("教育背景", 1), ("工作经历", 2), ("项目", 0)]
        )

    def test_lines_before_first_section_are_ignored(self):
        self.assertEqual(section_map(["武彪", "北京", "教育背景", "本科"]), [("教育背景", 1)])

    def test_repeated_header_does_not_duplicate(self):
        self.assertEqual(section_map(["技能", "Python", "技能", "Java"]), [("技能", 2)])


class HeaderFooterTest(unittest.TestCase):
    def test_detects_lines_repeated_on_every_page(self):
        pages = [["武彪 简历", "第一页内容", "页码 1/3"],
                 ["武彪 简历", "第二页内容", "页码 1/3"]]
        self.assertEqual(header_footer_lines(pages), ("武彪 简历", "页码 1/3"))

    def test_single_page_has_no_header_footer(self):
        self.assertEqual(header_footer_lines([["武彪 简历", "内容"]]), ())

    def test_unique_edge_lines_are_not_flagged(self):
        pages = [["第一页标题", "第一页正文"], ["第二页标题", "第二页正文"]]
        self.assertEqual(header_footer_lines(pages), ())


class AssessQualityTest(unittest.TestCase):
    def test_normal_resume_passes(self):
        report = assess_quality(GOOD_LINES, [GOOD_LINES])
        self.assertTrue(report.ok, report.problems)

    def test_empty_result_fails(self):
        self.assertIn("文本过短", " ".join(assess_quality([]).problems))

    def test_garbled_result_fails(self):
        lines = ["\ue000" * 300]
        self.assertIn("乱码", " ".join(assess_quality(lines).problems))

    def test_non_chinese_result_fails(self):
        lines = ["Backend engineer with six years of experience."] * 10
        self.assertIn("中文占比过低", " ".join(assess_quality(lines).problems))

    def test_excessive_blank_elements_fail(self):
        lines = GOOD_LINES + [""] * 200
        self.assertIn("空元素过多", " ".join(assess_quality(lines).problems))

    def test_repeated_header_is_recorded_but_not_fatal(self):
        # 重复页眉是正常现象，只记录不判失败，由 parse 层先剥掉
        pages = [
            ["武彪 简历", *GOOD_LINES],
            ["武彪 简历", "第二页独有的内容" * 10],
        ]
        report = assess_quality(GOOD_LINES + ["第二页独有的内容" * 10], pages)
        self.assertTrue(report.ok, report.problems)
        self.assertEqual(report.header_footer, ("武彪 简历",))

    def test_section_header_on_one_page_is_not_a_header(self):
        pages = [["教育背景", "本科"], ["工作经历", "某公司"]]
        self.assertEqual(header_footer_lines(pages), ())


class StripRepeatedEdgesTest(unittest.TestCase):
    def test_removes_repeated_edge_lines(self):
        pages = [["博学谷", "第一页正文"], ["博学谷", "第二页正文"]]
        self.assertEqual(strip_repeated_edges(pages), [["第一页正文"], ["第二页正文"]])

    def test_keeps_pages_without_repetition(self):
        pages = [["第一页"], ["第二页"]]
        self.assertEqual(strip_repeated_edges(pages), pages)

    def test_single_page_is_untouched(self):
        pages = [["博学谷", "正文"]]
        self.assertEqual(strip_repeated_edges(pages), pages)


if __name__ == "__main__":
    unittest.main()
