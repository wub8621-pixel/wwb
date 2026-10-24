"""EPUB 质检：结构可读性、原文字符覆盖率、乱码、元数据，可选 EPUBCheck。

覆盖率是字符级召回：原文本里每个字符在产物中还剩多少，用来发现丢字。
它不检查顺序和段落结构，版式问题交给 EPUBCheck。
"""

from __future__ import annotations

import argparse
import html
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

from ebooklib import ITEM_DOCUMENT, epub

from config.loader import ConfigError, load_config, project_path
from ebook.ingest import garbled_ratio, is_garbled_char, read_text
from ebook.logging_setup import setup_logging, write_report

TAG_RE = re.compile(r"<[^>]+>")
MESSAGES_RE = re.compile(r"Messages:\s*(\d+) fatals / (\d+) errors / (\d+) warnings")


def strip_html(raw: str) -> str:
    return html.unescape(TAG_RE.sub(" ", raw))


def character_coverage(source: str, output: str) -> tuple[float, int]:
    """返回（覆盖率, 缺失字符数）；只统计非空白字符。"""
    src = Counter(char for char in source if not char.isspace())
    out = Counter(char for char in output if not char.isspace())
    total = sum(src.values())
    if total == 0:
        return 1.0, 0
    missing = sum(max(0, count - out[char]) for char, count in src.items())
    return 1 - missing / total, missing


def inspect(epub_path: Path, source_path: Path | None, encodings) -> dict:
    """打开一本产物，返回结构、内容与元数据指标。"""
    try:
        book = epub.read_epub(str(epub_path))
    except Exception as exc:
        return {"file": epub_path.name, "ok": False, "error": f"无法解析：{exc}"}

    documents = [
        item for item in book.get_items_of_type(ITEM_DOCUMENT)
        if item.get_name() not in ("nav.xhtml", "cover.xhtml")
    ]
    text = "".join(
        strip_html(item.get_content().decode("utf-8", "ignore")) for item in documents
    )
    titles = [value for value, _ in book.get_metadata("DC", "title")]
    authors = [value for value, _ in book.get_metadata("DC", "creator")]

    result = {
        "file": epub_path.name,
        "ok": True,
        "chapters": len(documents),
        "title": titles[0] if titles else "",
        "author": authors[0] if authors else "",
        "garbled": sum(1 for char in text if is_garbled_char(char)),
        "size_kb": round(epub_path.stat().st_size / 1024, 1),
    }

    if source_path and source_path.exists():
        source_text, _, _ = read_text(source_path, encodings)
        coverage, missing = character_coverage(source_text, text)
        result.update({
            "source": source_path.name,
            "coverage": round(coverage, 4),
            "missing_chars": missing,
            "source_garbled_ratio": round(garbled_ratio(source_text), 4),
        })
    return result


def find_source(epub_path: Path, source_dirs: list[Path]) -> Path | None:
    for directory in source_dirs:
        candidate = directory / f"{epub_path.stem}.txt"
        if candidate.exists():
            return candidate
    return None


def run_epubcheck(epub_path: Path, java_bin: str, jar: str, timeout_s: int = 120) -> dict:
    """返回 EPUBCheck 的退出码与错误数；工具本身不可用时返回 ok=False。"""
    try:
        proc = subprocess.run(
            [java_bin, "-jar", jar, str(epub_path)],
            capture_output=True, text=True, timeout=timeout_s,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"epubcheck_ok": False, "epubcheck_error": str(exc)}

    output = f"{proc.stdout}\n{proc.stderr}"
    match = MESSAGES_RE.search(output)
    if match:
        fatals, errors, warnings = (int(value) for value in match.groups())
    else:
        fatals = errors = warnings = None
    return {
        "epubcheck_ok": proc.returncode == 0,
        "epubcheck_fatals": fatals,
        "epubcheck_errors": errors,
        "epubcheck_warnings": warnings,
    }


def main(argv: list[str] | None = None, cfg=None) -> int:
    parser = argparse.ArgumentParser(description="质检生成的 EPUB")
    parser.add_argument("--epub-dir", default="output", help="EPUB 所在目录")
    parser.add_argument("--source-dir", nargs="*", default=[], help="原始 txt 目录（可多个）")
    parser.add_argument("--java", help="java 可执行文件路径，提供后启用 EPUBCheck")
    parser.add_argument("--epubcheck-jar", help="epubcheck.jar 路径")
    args = parser.parse_args(argv)

    try:
        cfg = cfg or load_config()
    except ConfigError as exc:
        print(f"配置有误：{exc}", file=sys.stderr)
        return 2

    logger, log_dir = setup_logging(cfg)
    epub_dir = Path(args.epub_dir).expanduser()
    if not epub_dir.is_absolute():
        epub_dir = project_path(epub_dir)
    if not epub_dir.is_dir():
        print(f"目录不存在：{epub_dir}", file=sys.stderr)
        return 2

    epubs = sorted(epub_dir.glob("*.epub"))
    if not epubs:
        print(f"{epub_dir} 下没有 EPUB", file=sys.stderr)
        return 2

    source_dirs = [Path(item).expanduser() for item in args.source_dir]
    use_epubcheck = bool(args.java and args.epubcheck_jar)
    encodings = cfg.get("ingest.encodings")

    results = []
    for path in epubs:
        result = inspect(path, find_source(path, source_dirs), encodings)
        if use_epubcheck and result["ok"]:
            result.update(run_epubcheck(path, args.java, args.epubcheck_jar))
        results.append(result)
        logger.info("质检 %s：%s", path.name, result)

    payload = {
        "epub_dir": str(epub_dir),
        "sources": [str(item) for item in source_dirs],
        "epubcheck": use_epubcheck,
        "results": results,
    }
    report = write_report(log_dir, payload, prefix="eval")
    _print_table(results, payload)
    logger.info("评测报告：%s", report)

    failed = [item for item in results if not item["ok"] or item.get("epubcheck_ok") is False]
    return 1 if failed else 0


def _print_table(results: list[dict], payload: dict) -> None:
    print(f"{'文件':<24}{'章节':>4}{'覆盖率':>8}{'缺字':>6}{'乱码':>6}{'EPUBCheck':>10}")
    for item in results:
        if not item["ok"]:
            print(f"{item['file']:<24}  解析失败：{item['error']}")
            continue
        coverage = f"{item['coverage']:.1%}" if "coverage" in item else "—"
        missing = item.get("missing_chars", "—")
        if payload["epubcheck"]:
            check = "通过" if item.get("epubcheck_ok") else "有问题"
        else:
            check = "未启用"
        print(f"{item['file']:<24}{item['chapters']:>4}{coverage:>8}{missing:>6}{item['garbled']:>6}{check:>10}")


if __name__ == "__main__":
    raise SystemExit(main())
