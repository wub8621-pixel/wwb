"""元数据提取：先本地规则，缺字段再交给 LLM 兜底。"""

from __future__ import annotations

import re
from pathlib import Path

JUNK_SUFFIXES = ("_最终版", "-最终版", "（完）", "(完)", "_精校版")


def title_from_filename(path: Path) -> str:
    stem = path.stem
    for junk in JUNK_SUFFIXES:
        stem = stem.replace(junk, "")
    return re.sub(r"[_]+", " ", stem).strip() or "未命名"


def extract_local(
    text: str,
    path: Path,
    author_patterns: list[str],
    description_max_chars: int,
) -> dict[str, str]:
    """书名取文件名，作者看正文开头的「作者：xxx」，简介取第一段正文。"""
    lines = text.split("\n")
    title = title_from_filename(path)

    author = ""
    for line in lines[:40]:
        for pattern in author_patterns:
            match = re.search(pattern, line)
            if match:
                author = match.group(1).strip()
                break
        if author:
            break

    description = ""
    for line in lines[:200]:
        candidate = line.strip()
        if not candidate or candidate == title:
            continue
        if author and author in candidate and len(candidate) <= 40:
            continue
        description = candidate
        break

    return {
        "title": title,
        "author": author,
        "description": description[:description_max_chars],
    }


def merge_metadata(local: dict[str, str], extra: dict[str, str] | None) -> dict[str, str]:
    """本地有值的一律保留，LLM 只补空缺。"""
    merged = dict(local)
    for key in ("title", "author", "description"):
        value = (extra or {}).get(key, "").strip()
        if value and not merged.get(key):
            merged[key] = value
    return merged
