"""日期区间解析与工作年限计算。

工作年限必须由代码算，不能交给模型：日期抽取可以让模型帮忙，
但“几段经历重叠了多少个月”这种算术必须可复现。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

# 全角数字/符号统一成半角，简历里混用很常见
_NORMALIZE = str.maketrans("０１２３４５６７８９．／－—～　", "0123456789./-—~ ")

_YEAR = r"(?:19|20)\d{2}"
# 1[0-2] 必须排在前面，否则 "2022.12" 会被截成月份 1
_MONTH_SUFFIX = r"(?:[./年-]\s*(?P<{0}>1[0-2]|0?[1-9])\s*月?)?"
_END = r"(?:至今|现在|目前|present|now|today)"

RANGE_RE = re.compile(
    rf"(?P<sy>{_YEAR})" + _MONTH_SUFFIX.format("sm")
    + r"\s*(?:-|—|–|~|至|到)\s*"
    + rf"(?:(?P<ey>{_YEAR})" + _MONTH_SUFFIX.format("em") + rf"|{_END})",
    re.IGNORECASE,
)


@dataclass(frozen=True, order=True)
class YearMonth:
    year: int
    month: int

    def __post_init__(self) -> None:
        if not 1 <= self.month <= 12:
            raise ValueError(f"月份越界: {self.month}")


@dataclass(frozen=True)
class DateRange:
    start: YearMonth
    end: YearMonth | None  # None 表示“至今”
    raw: str
    weight: float = 1.0   # 实习按 0.5 计


def parse_ranges(text: str) -> list[DateRange]:
    """从一段文本里抽出所有日期区间；识别不了的直接跳过。"""
    normalized = text.translate(_NORMALIZE)
    ranges: list[DateRange] = []

    for match in RANGE_RE.finditer(normalized):
        start = YearMonth(int(match["sy"]), int(match["sm"] or 1))
        end = (
            YearMonth(int(match["ey"]), int(match["em"] or 12))
            if match["ey"]
            else None
        )
        if end and end < start:
            start, end = end, start
        ranges.append(DateRange(start, end, match.group(0).strip()))
    return ranges


def iter_months(start: YearMonth, end: YearMonth):
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        yield (year, month)
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)


def compute_tenure(ranges: list[DateRange], as_of: date | None = None) -> float:
    """把多段经历求并集后换算成年数；重叠月份只算一次，实习月按 0.5 计。"""
    today = as_of or date.today()
    ceiling = YearMonth(today.year, today.month)

    covered: dict[tuple[int, int], float] = {}
    for item in ranges:
        end = min(item.end or ceiling, ceiling)
        if end < item.start:
            continue
        for year, month in iter_months(item.start, end):
            # 同一月份若既有全职又有实习，按全职计
            covered[(year, month)] = max(covered.get((year, month), 0.0), item.weight)
    return round(sum(covered.values()) / 12, 2)
