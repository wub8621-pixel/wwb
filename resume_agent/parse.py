"""解析层：unstructured 主解析，结果异常时降级到 native。

主备不是“再解析一遍”这么简单，关键是**每次降级都要有可读的理由**。
调用方拿到的 ParseResult 里保留 attempts，能回答“为什么没用主解析”。

native = pymupdf 读 PDF、python-docx 读 DOCX，不依赖版面模型。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .quality import QualityReport, assess_quality

# 引擎顺序即优先级：主解析在前，备用在后
ENGINE_ORDER = ("unstructured", "native")

Extractor = Callable[[Path], "tuple[list[str], list[list[str]]]"]


class ParseError(RuntimeError):
    """所有解析引擎都不可用，必须显式失败，不能返回空文本来蒙混。"""


@dataclass(frozen=True)
class ParseResult:
    path: Path
    engine: str
    lines: list[str]
    pages: list[list[str]]
    quality: QualityReport
    attempts: tuple[tuple[str, str], ...]

    @property
    def text(self) -> str:
        return "\n".join(line for line in self.lines if line.strip())


def extract_native(path: Path) -> tuple[list[str], list[list[str]]]:
    """pymupdf / python-docx 抽取，保留每页的原始行（含空行）。"""
    if path.suffix.lower() == ".pdf":
        import pymupdf

        pages: list[list[str]] = []
        with pymupdf.open(path) as doc:
            for page in doc:
                pages.append(page.get_text("text").splitlines())
        return [line for page in pages for line in page], pages

    from docx import Document

    document = Document(str(path))
    lines = [p.text for p in document.paragraphs]
    # 简历常用表格排版，单元格内容同样要收进来
    for table in document.tables:
        for row in table.rows:
            for cell in row.cells:
                lines.extend(cell.text.splitlines())
    return lines, [lines]


def extract_unstructured(path: Path) -> tuple[list[str], list[list[str]]]:
    """官方解析器。中文必须显式指定 language，否则会按英文处理并返回空结果。"""
    try:
        from unstructured.partition.auto import partition
    except ImportError as exc:
        raise RuntimeError("未安装 unstructured") from exc

    kwargs: dict[str, object] = {"filename": str(path), "languages": ["chi_sim"]}
    if path.suffix.lower() == ".pdf":
        kwargs["strategy"] = "fast"

    elements = partition(**kwargs)
    by_page: dict[int, list[str]] = {}
    for element in elements:
        page = getattr(element.metadata, "page_number", None) or 1
        by_page.setdefault(int(page), []).append(element.text)
    pages = [by_page[number] for number in sorted(by_page)]
    return [line for page in pages for line in page], pages


EXTRACTORS: dict[str, Extractor] = {
    "unstructured": extract_unstructured,
    "native": extract_native,
}


def parse_resume(
    path: Path,
    engines: tuple[str, ...] = ENGINE_ORDER,
    extractors: dict[str, Extractor] | None = None,
) -> ParseResult:
    """按优先级尝试各引擎，第一个通过质量闸门的胜出。"""
    available = extractors or EXTRACTORS
    attempts: list[tuple[str, str]] = []

    for engine in engines:
        try:
            lines, pages = available[engine](path)
        except Exception as exc:  # 兜底：任何解析器异常都算该引擎失败
            message = str(exc).splitlines()[0][:120] if str(exc).strip() else ""
            attempts.append((engine, f"{type(exc).__name__}: {message}".rstrip(": ")))
            continue

        report = assess_quality(lines, pages)
        if report.ok:
            return ParseResult(path, engine, lines, pages, report, tuple(attempts))
        attempts.append((engine, "; ".join(report.problems)))

    detail = " | ".join(f"{engine}: {reason}" for engine, reason in attempts)
    raise ParseError(f"所有解析引擎均不可用：{detail}")
