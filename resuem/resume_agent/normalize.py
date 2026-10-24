"""把解析出的行序列归一成 ResumeDoc。

这里只做**结构**，不做判断：识别章节、切出教育/工作条目、抽出日期。
任何需要“理解”的评价都留给后面的评估节点。
"""

from __future__ import annotations

import re

from .dates import DateRange, parse_ranges
from .schema import SECTION_ALIASES, EducationEntry, ResumeDoc, WorkEntry

DEGREES = ("博士", "硕士", "学士", "本科", "大专", "专科")
SCHOOL_HINTS = ("大学", "学院", "学校", "研究院")
COMPANY_HINTS = ("公司", "集团", "科技", "有限", "银行", "研究院", "事业部")
TITLE_HINTS = ("工程师", "经理", "开发", "架构", "总监", "主管", "分析师", "专家", "负责人")

# 简历里出现这些措辞，说明内容可能在试图操纵评估
INJECTION_PATTERNS = (
    "忽略之前", "忽略上述", "忽略以上", "ignore previous", "ignore all previous",
    "system prompt", "系统提示词", "直接通过", "评价为优秀", "打满分",
)
INJECTION_RE = re.compile(r"(请.{0,6}(打分|评分|评价)|give.{0,10}(score|rating))", re.IGNORECASE)
PIPE_RE = re.compile(r"\s*[|｜]\s*")


def canonical_section(line: str) -> str | None:
    """把一行识别成规范章节名；`工作描述` 这类子标题不算新章节。"""
    text = line.strip()
    if not text or len(text) > 12 or any(mark in text for mark in "。！？，；"):
        return None
    for key, aliases in SECTION_ALIASES.items():
        if any(alias in text for alias in aliases):
            return key
    return None


def split_sections(lines: list[str]) -> tuple[list[str], dict[str, list[str]]]:
    """返回 (首个章节之前的抬头行, {规范章节名: 内容行})。"""
    header: list[str] = []
    sections: dict[str, list[str]] = {}
    current: str | None = None

    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        key = canonical_section(line)
        if key:
            current = key
            sections.setdefault(key, [])
        elif current:
            sections[current].append(line)
        else:
            header.append(line)
    return header, sections


def _windows(lines: list[str], span: int = 4) -> list[tuple[DateRange, list[str]]]:
    """以每个日期区间为锚点，取前后若干行作为一个条目窗口。"""
    windows: list[tuple[DateRange, list[str]]] = []
    for index, line in enumerate(lines):
        for period in parse_ranges(line):
            start = max(0, index - span)
            windows.append((period, lines[start:index + span]))
    return windows


def _first(lines: list[str], hints: tuple[str, ...]) -> str:
    return next((line for line in lines if any(hint in line for hint in hints)), "")


def _guess_company(window: list[str], title: str) -> str:
    """先按公司类关键词找，找不到就取窗口里第一个短行。

    “阿里云”这类名字不含“公司/集团”，只靠关键词会漏。
    """
    hinted = _first(window, COMPANY_HINTS)
    if hinted:
        return hinted
    for line in window:
        if line == title or parse_ranges(line):
            continue
        if 0 < len(line) <= 20 and not any(mark in line for mark in "。：，；"):
            return line
    return ""


def normalize_degree(text: str) -> str:
    if "博士" in text:
        return "博士"
    if "硕士" in text:
        return "硕士"
    if "本科" in text or "学士" in text:
        return "本科"
    if "大专" in text or "专科" in text:
        return "专科"
    return ""


def parse_education(lines: list[str]) -> list[EducationEntry]:
    entries: list[EducationEntry] = []
    for period, window in _windows(lines):
        entry = EducationEntry(
            school=_first(window, SCHOOL_HINTS),
            degree=normalize_degree(" ".join(window)),
            period=period,
            raw=" | ".join(window),
        )
        if entry.school or entry.degree:
            entries.append(entry)

    # 没有任何日期时，仍尝试从整段里抽出唯一一条教育经历
    if not entries and lines:
        school = _first(lines, SCHOOL_HINTS)
        degree = normalize_degree(" ".join(lines))
        if school or degree:
            entries.append(EducationEntry(school=school, degree=degree, raw=" | ".join(lines)))

    return _dedupe(entries, key=lambda e: (e.school, e.degree, e.period.raw if e.period else ""))


def parse_work(lines: list[str]) -> list[WorkEntry]:
    """工作条目：优先利用 `公司｜职位｜时间` 这种单行格式，否则回退到窗口推断。"""
    entries: list[WorkEntry] = []
    for index, line in enumerate(lines):
        fields = [part.strip() for part in PIPE_RE.split(line) if part.strip()]
        for period in parse_ranges(line):
            if len(fields) >= 2:
                company, title = fields[0], fields[1]
                window = [line]
            else:
                window = lines[max(0, index - 3):index + 4]
                title = _first(window, TITLE_HINTS)
                company = _guess_company(window, title)
            joined = " | ".join(window)
            entries.append(
                WorkEntry(
                    company=company,
                    title=title,
                    period=period,
                    is_internship="实习" in joined,
                    lines=window,
                )
            )
    return _dedupe(entries, key=lambda w: (w.company, w.title, w.period.raw if w.period else ""))


def detect_injection(lines: list[str]) -> list[str]:
    hits: list[str] = []
    for line in lines:
        lowered = line.lower()
        if any(pattern in lowered for pattern in INJECTION_PATTERNS) or INJECTION_RE.search(line):
            hits.append(line.strip()[:80])
    return hits


def _dedupe(items: list, key) -> list:
    seen: set = set()
    result = []
    for item in items:
        marker = key(item)
        if marker not in seen:
            seen.add(marker)
            result.append(item)
    return result


def build_doc(lines: list[str], raw_text: str = "") -> ResumeDoc:
    header, sections = split_sections(lines)
    doc = ResumeDoc(
        name=header[0] if header else "",
        target_role=header[1] if len(header) > 1 else "",
        raw_text=raw_text,
        sections=sections,
        dates=[period for line in lines for period in parse_ranges(line)],
    )
    doc.education = parse_education(sections.get("education", []))
    doc.work = parse_work(sections.get("work", []))

    injections = detect_injection(lines)
    if injections:
        doc.flags.append(f"可疑指令注入: {injections[0]}")
    if not doc.education:
        doc.flags.append("未识别到教育经历")
    if not doc.work:
        doc.flags.append("未识别到工作经历")
    return doc
