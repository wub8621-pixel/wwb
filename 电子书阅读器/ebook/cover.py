"""封面生成：纯色底 + 书名 + 作者，不需要额外素材。"""

from __future__ import annotations

import io

from PIL import Image, ImageDraw, ImageFont

# 常见系统中文字体，按顺序尝试；都没有就退回 Pillow 默认字体（中文可能显示为方块）
FONT_CANDIDATES = (
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "C:/Windows/Fonts/msyh.ttc",
)


def _load_font(size: int, configured: str = "") -> ImageFont.FreeTypeFont:
    candidates = ([configured] if configured else []) + list(FONT_CANDIDATES)
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def wrap_text(text: str, per_line: int) -> list[str]:
    lines: list[str] = []
    for paragraph in text.split("\n"):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        lines.extend(paragraph[i:i + per_line] for i in range(0, len(paragraph), per_line))
    return lines or ["未命名"]


def make_cover(
    title: str,
    author: str,
    *,
    width: int,
    height: int,
    background: str,
    text_color: str,
    font_path: str = "",
) -> bytes:
    """返回 PNG 字节流，直接塞进 EPUB。"""
    image = Image.new("RGB", (width, height), background)
    draw = ImageDraw.Draw(image)

    title_size = max(28, width // 9)
    title_font = _load_font(title_size, font_path)
    author_font = _load_font(max(16, width // 28), font_path)

    lines = wrap_text(title, max(4, int(width // (title_size * 1.05))))
    line_height = int(title_size * 1.4)
    block_height = line_height * len(lines)
    start_y = max(int(height * 0.28), (height - block_height) // 2)

    for index, line in enumerate(lines):
        x = (width - draw.textlength(line, font=title_font)) / 2
        draw.text((x, start_y + index * line_height), line, font=title_font, fill=text_color)

    if author.strip():
        author_y = start_y + block_height + int(height * 0.08)
        x = (width - draw.textlength(author, font=author_font)) / 2
        draw.text((x, author_y), author.strip(), font=author_font, fill=text_color)

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()
