"""解析质量闸门：判断一份解析结果能不能用于评估。

主备解析的核心不是“再解析一遍”，而是**明确判断主解析的结果是否可用**。
这里的每条规则都必须可解释、可测试，阈值集中放在模块顶部便于调参。
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

CJK_RE = re.compile(r"[\u4e00-\u9fff]")
# 私用区字符：中文字体缺少 ToUnicode 映射时最常见的乱码形态
PUA_RE = re.compile(r"[\ue000-\uf8ff]")
CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")

# 简历常见章节标题，用来判断结构有没有被保住
SECTIONS = (
    "个人信息", "求职意向", "教育", "工作经历", "工作经验", "项目",
    "技能", "专业技能", "实习", "证书", "荣誉", "自我评价", "个人优势",
    "个人总结", "工作描述",
)

MIN_CHARS = 200          # 低于这个长度基本不可能是有效简历
MAX_GARBLED_RATIO = 0.005  # 乱码超过 0.5% 直接判定失败
MIN_CJK_RATIO = 0.15     # 中文简历的中文字符占比下限（英文简历需调低）
MAX_BLANK_RATIO = 0.35   # 空元素占比过高说明解析把版面切碎了


@dataclass(frozen=True)
class QualityReport:
    chars: int
    cjk_ratio: float
    garbled_ratio: float
    blank_ratio: float
    header_footer: tuple[str, ...]
    problems: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.problems


def text_stats(text: str) -> dict[str, float | int]:
    """统计一段文本的可用性：字符数、中文占比、乱码率。"""
    total = len(text.strip())
    if total == 0:
        return {"chars": 0, "cjk_ratio": 0.0, "garbled_ratio": 0.0}
    cjk = len(CJK_RE.findall(text))
    garbled = len(PUA_RE.findall(text)) + len(CTRL_RE.findall(text)) + text.count("�")
    return {
        "chars": total,
        "cjk_ratio": round(cjk / total, 3),
        "garbled_ratio": round(garbled / total, 4),
    }


def match_section(line: str) -> str | None:
    """把一行判为章节标题，要求短且命中关键词。"""
    line = line.strip()
    if not line or len(line) > 12:
        return None
    # 带句读的短句是正文换行碎片，不是标题
    if any(mark in line for mark in "。！？，；"):
        return None
    return line if any(key in line for key in SECTIONS) else None


def section_map(lines: list[str]) -> list[tuple[str, int]]:
    """返回 [(章节标题, 该章节下的行数)]，用于判断结构是否被保住。"""
    order: list[str] = []
    counts: dict[str, int] = {}
    current: str | None = None
    for line in lines:
        section = match_section(line)
        if section:
            current = section
            if section not in counts:
                order.append(section)
                counts[section] = 0
        elif current and line.strip():
            counts[current] += 1
    return [(section, counts[section]) for section in order]


def header_footer_lines(pages: list[list[str]]) -> tuple[str, ...]:
    """找出在**每一页**都出现过的短行——页眉、页脚、水印。

    按“出现在多少页”计数，而不是按总出现次数：同一页里重复多次的行
    不该被当成页眉。只出现一次的章节标题也不会被误判。
    """
    if len(pages) < 2:
        return ()
    page_sets = [{line.strip() for line in page if line.strip()} for page in pages]
    page_sets = [lines for lines in page_sets if lines]
    if len(page_sets) < 2:
        return ()

    counts: Counter[str] = Counter()
    for lines in page_sets:
        counts.update(lines)

    repeated = [line for line, count in counts.items() if count == len(page_sets)]
    # 过长的重复行更可能是正文，不做判定
    return tuple(sorted(line for line in repeated if len(line) <= 40))


def strip_repeated_edges(pages: list[list[str]]) -> list[list[str]]:
    """去掉每页首尾重复出现的行（页眉、页脚、水印）。

    重复页眉是正常现象，不该因此判定整份解析失败；正确做法是剥掉它，
    再判断剩下的内容够不够用。
    """
    repeated = set(header_footer_lines(pages))
    if not repeated:
        return pages
    return [[line for line in page if line.strip() not in repeated] for page in pages]


def assess_quality(lines: list[str], pages: list[list[str]] | None = None) -> QualityReport:
    """对解析结果做一次可用性判定；problems 非空即应降级到备用引擎。"""
    non_empty = [line.strip() for line in lines if line.strip()]
    text = "\n".join(non_empty)
    stats = text_stats(text)
    blank_ratio = round(1 - len(non_empty) / len(lines), 3) if lines else 1.0
    edge_lines = header_footer_lines(pages or [])

    problems: list[str] = []
    if stats["chars"] < MIN_CHARS:
        problems.append(f"文本过短({stats['chars']}字符)")
    if stats["garbled_ratio"] > MAX_GARBLED_RATIO:
        problems.append(f"乱码率过高({stats['garbled_ratio']:.2%})")
    if stats["chars"] >= MIN_CHARS and stats["cjk_ratio"] < MIN_CJK_RATIO:
        problems.append(f"中文占比过低({stats['cjk_ratio']:.1%})")
    if blank_ratio > MAX_BLANK_RATIO:
        problems.append(f"空元素过多({blank_ratio:.0%})")
    # 页眉页脚只做记录，不判失败：调用方会先把重复行剥掉再评估

    return QualityReport(
        chars=int(stats["chars"]),
        cjk_ratio=float(stats["cjk_ratio"]),
        garbled_ratio=float(stats["garbled_ratio"]),
        blank_ratio=blank_ratio,
        header_footer=edge_lines,
        problems=tuple(problems),
    )
