"""章节切分与质量判定：正则优先，异常时交由 LLM 兜底。"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .ingest import garbled_ratio

NUMBER_RE = re.compile(r"第\s*([0-9零〇一二三四五六七八九十百千万两]+)\s*[章回节卷篇]")
CN_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
             "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


@dataclass(frozen=True)
class Chapter:
    title: str
    body: str

    def as_dict(self) -> dict[str, str]:
        return {"title": self.title, "body": self.body}


def parse_number(text: str) -> int | None:
    """解析阿拉伯数字或常见中文数字，解析不了返回 None。"""
    if text.isdigit():
        return int(text)
    total, current = 0, 0
    for char in text:
        if char in CN_DIGITS:
            current = CN_DIGITS[char]
        elif char == "十":
            total += (current or 1) * 10
            current = 0
        elif char == "百":
            total += (current or 1) * 100
            current = 0
        elif char == "千":
            total += (current or 1) * 1000
            current = 0
        elif char == "万":
            total = (total + current) * 10_000
            current = 0
        else:
            return None
    return total + current


def is_heading(line: str, compiled: list[re.Pattern], max_title_chars: int) -> bool:
    """标题行既要有章节特征，也不能长到像正文段落。"""
    stripped = line.strip()
    if not stripped or len(stripped) > max_title_chars:
        return False
    return any(pattern.match(stripped) for pattern in compiled)


def split_chapters(
    text: str,
    patterns: list[str],
    fallback_title: str = "正文",
    max_title_chars: int = 40,
) -> list[Chapter]:
    """按标题行切章；第一个标题之前的内容保留为前置章节。"""
    compiled = [re.compile(pattern) for pattern in patterns]
    chapters: list[Chapter] = []
    preface = ""
    title: str | None = None
    body: list[str] = []

    for line in text.split("\n"):
        if is_heading(line, compiled, max_title_chars):
            if title is None:
                preface = "\n".join(body).strip()
            else:
                chapters.append(Chapter(title=title, body="\n".join(body).strip()))
            title = line.strip()
            body = []
        else:
            body.append(line)

    if title is None:
        return [Chapter(title=fallback_title, body=text.strip())]

    chapters.append(Chapter(title=title, body="\n".join(body).strip()))
    if preface:
        chapters.insert(0, Chapter(title=fallback_title, body=preface))
    return _drop_empty(chapters)


def _drop_empty(chapters: list[Chapter]) -> list[Chapter]:
    """只有标题没有正文的，多半是目录残片，直接丢掉。"""
    kept = [chapter for chapter in chapters if chapter.body.strip()]
    return kept or chapters[:1]


def out_of_order(chapters: list[Chapter]) -> bool:
    numbers = []
    for chapter in chapters:
        match = NUMBER_RE.search(chapter.title)
        if match:
            number = parse_number(match.group(1))
            if number is not None:
                numbers.append(number)
    return any(later < earlier for earlier, later in zip(numbers, numbers[1:]))


def analyze(
    text: str,
    chapters: list[Chapter],
    *,
    garbled_threshold: float,
    blank_threshold: float,
    min_chapters: int,
    long_text_threshold: int,
    check_order: bool = True,
) -> dict:
    """返回质量报告；needs_llm 为真表示应该走 LLM 兜底。"""
    lines = text.split("\n")
    blank_ratio = sum(1 for line in lines if not line.strip()) / max(len(lines), 1)
    garbled = garbled_ratio(text)
    reversed_order = check_order and out_of_order(chapters)
    too_few = len(chapters) < min_chapters and len(text) >= long_text_threshold

    reasons = []
    if garbled > garbled_threshold:
        reasons.append(f"乱码率 {garbled:.1%} 超过阈值 {garbled_threshold:.0%}")
    if blank_ratio > blank_threshold:
        reasons.append(f"空白行占比 {blank_ratio:.0%} 超过阈值 {blank_threshold:.0%}")
    if reversed_order:
        reasons.append("章节序号出现倒序")
    if too_few:
        reasons.append(f"正文 {len(text)} 字但只识别到 {len(chapters)} 章，低于 {min_chapters}")

    return {
        "chapter_count": len(chapters),
        "garbled_ratio": round(garbled, 4),
        "blank_ratio": round(blank_ratio, 4),
        "out_of_order": reversed_order,
        "needs_llm": bool(reasons),
        "reasons": reasons,
    }
