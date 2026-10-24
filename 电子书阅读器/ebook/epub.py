"""EPUB 打包：章节 XHTML + 目录 + 可选封面。"""

from __future__ import annotations

import html
import re
import uuid
from pathlib import Path

from ebooklib import epub

CSS = """body { font-family: serif; line-height: 1.75; margin: 1.2em; }
h2 { font-size: 1.4em; margin: 1.4em 0 0.8em; text-align: center; }
p { text-indent: 2em; margin: 0.5em 0; }
"""


def render_chapter(chapter: dict) -> str:
    parts = [f"<h2>{html.escape(chapter['title'])}</h2>"]
    for block in re.split(r"\n\s*\n", chapter["body"]):
        lines = [line.strip() for line in block.split("\n") if line.strip()]
        if lines:
            parts.append("".join(f"<p>{html.escape(line)}</p>" for line in lines))
    return "\n".join(parts)


def build_epub(
    path: Path,
    *,
    title: str,
    author: str,
    description: str,
    language: str,
    chapters: list[dict],
    cover_bytes: bytes | None = None,
) -> Path:
    book = epub.EpubBook()
    book.set_identifier(uuid.uuid4().hex)
    book.set_title(title)
    book.set_language(language)
    book.add_author(author or "未知作者")
    if description:
        book.add_metadata("DC", "description", description)
    if cover_bytes:
        book.set_cover("cover.png", cover_bytes)

    style = epub.EpubItem(
        uid="style",
        file_name="style/main.css",
        media_type="text/css",
        content=CSS,
    )
    book.add_item(style)

    items = []
    for index, chapter in enumerate(chapters, start=1):
        item = epub.EpubHtml(
            title=chapter["title"],
            file_name=f"chap_{index:03d}.xhtml",
            lang=language,
        )
        item.content = render_chapter(chapter)
        item.add_item(style)
        book.add_item(item)
        items.append(item)

    book.toc = tuple(items)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ["nav", *items]

    path.parent.mkdir(parents=True, exist_ok=True)
    epub.write_epub(str(path), book)
    return path


def safe_filename(name: str) -> str:
    """去掉路径分隔符等不能做文件名的字符。"""
    return re.sub(r'[\\/:*?"<>|]', "_", name).strip() or "未命名"
