"""日期解析与工作年限计算的测试。"""

import unittest
from datetime import date

from resume_agent.dates import DateRange, YearMonth, compute_tenure, parse_ranges


def ym(year: int, month: int) -> YearMonth:
    return YearMonth(year, month)


class ParseRangesTest(unittest.TestCase):
    def test_dotted_format(self):
        (item,) = parse_ranges("2014.09-2018.07")
        self.assertEqual((item.start, item.end), (ym(2014, 9), ym(2018, 7)))

    def test_open_ended(self):
        (item,) = parse_ranges("2019.11—至今")
        self.assertEqual(item.start, ym(2019, 11))
        self.assertIsNone(item.end)

    def test_year_only_uses_full_year(self):
        (item,) = parse_ranges("2014-2018")
        self.assertEqual((item.start, item.end), (ym(2014, 1), ym(2018, 12)))

    def test_fullwidth_characters(self):
        (item,) = parse_ranges("２０１４．０９－２０１８．０７")
        self.assertEqual((item.start, item.end), (ym(2014, 9), ym(2018, 7)))

    def test_chinese_date_format(self):
        (item,) = parse_ranges("2014年9月 - 2018年7月")
        self.assertEqual((item.start, item.end), (ym(2014, 9), ym(2018, 7)))

    def test_two_digit_month_10_to_12(self):
        (item,) = parse_ranges("2019.11—2022.12")
        self.assertEqual((item.start, item.end), (ym(2019, 11), ym(2022, 12)))

    def test_slash_format(self):
        (item,) = parse_ranges("2014/09-2018/07")
        self.assertEqual((item.start, item.end), (ym(2014, 9), ym(2018, 7)))

    def test_reversed_range_is_normalized(self):
        (item,) = parse_ranges("2018.07-2014.09")
        self.assertEqual((item.start, item.end), (ym(2014, 9), ym(2018, 7)))

    def test_multiple_ranges_in_one_line(self):
        ranges = parse_ranges("2014.09-2018.07 本科；2019.11-至今 工作")
        self.assertEqual(len(ranges), 2)

    def test_text_without_range(self):
        self.assertEqual(parse_ranges("负责后端系统开发"), [])

    def test_invalid_month_is_not_parsed_as_range(self):
        # 13 不是合法月份，不能把 2014-13 当成日期
        self.assertEqual(parse_ranges("2014-13"), [])


class ComputeTenureTest(unittest.TestCase):
    def test_single_range_counts_inclusive_months(self):
        ranges = parse_ranges("2019.01-2020.01")
        self.assertAlmostEqual(compute_tenure(ranges, date(2024, 1, 1)), 13 / 12, places=2)

    def test_overlap_is_counted_once(self):
        ranges = parse_ranges("2019.01-2020.01") + parse_ranges("2019.06-2020.06")
        # 并集 2019-01 ~ 2020-06 共 18 个月
        self.assertAlmostEqual(compute_tenure(ranges, date(2024, 1, 1)), 1.5, places=2)

    def test_internship_months_are_halved(self):
        start, end = ym(2019, 1), ym(2020, 6)
        ranges = [DateRange(start, end, "实习", weight=0.5)]
        self.assertAlmostEqual(compute_tenure(ranges, date(2024, 1, 1)), 0.75, places=2)

    def test_fulltime_wins_over_internship_in_same_month(self):
        start, end = ym(2019, 1), ym(2019, 12)
        ranges = [
            DateRange(start, end, "实习", weight=0.5),
            DateRange(start, end, "全职", weight=1.0),
        ]
        self.assertAlmostEqual(compute_tenure(ranges, date(2024, 1, 1)), 1.0, places=2)

    def test_open_ended_is_clamped_to_as_of(self):
        ranges = parse_ranges("2020.01-至今")
        self.assertAlmostEqual(compute_tenure(ranges, date(2021, 1, 15)), 13 / 12, places=2)

    def test_future_range_contributes_nothing(self):
        ranges = parse_ranges("2030.01-2031.01")
        self.assertEqual(compute_tenure(ranges, date(2024, 1, 1)), 0.0)

    def test_empty_ranges(self):
        self.assertEqual(compute_tenure([], date(2024, 1, 1)), 0.0)


if __name__ == "__main__":
    unittest.main()
