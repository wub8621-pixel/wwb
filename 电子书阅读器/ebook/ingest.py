"""目录扫描与文本读取：负责编码探测和换行规范化。"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from pathlib import Path


class IngestError(RuntimeError):
    """文件读不出来或内容不可用。"""


@dataclass(frozen=True)
class SourceFile:
    path: Path
    text: str
    encoding: str
    garbled: float


def is_garbled_char(char: str) -> bool:
    """私用区、替换符和控制字符都算乱码；换行与制表符不算。"""
    if char in "\n\r\t":
        return False
    code = ord(char)
    if 0xE000 <= code <= 0xF8FF:
        return True
    if code == 0xFFFD:
        return True
    return unicodedata.category(char) == "Cc"


def garbled_ratio(text: str) -> float:
    if not text:
        return 0.0
    bad = sum(1 for char in text if is_garbled_char(char))
    return bad / len(text)


def normalize(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text.lstrip("﻿")


def discover_files(
    directory: Path,
    extensions: list[str] | tuple[str, ...],
    recursive: bool = False,
) -> list[Path]:
    """按扩展名找文件；默认只扫一层，避免把参考目录也收进来。"""
    if not directory.is_dir():
        raise IngestError(f"目录不存在：{directory}")
    allowed = {ext.lower() for ext in extensions}
    pattern = "**/*" if recursive else "*"
    files = [p for p in directory.glob(pattern) if p.is_file() and p.suffix.lower() in allowed]
    return sorted(files)


def read_text(path: Path, encodings: list[str] | tuple[str, ...]) -> tuple[str, str, float]:
    """按给定顺序严格解码，第一个成功的即采用；全失败则用替换符解码。"""
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise IngestError(f"读不了文件 {path.name}：{exc}") from exc

    if not data.strip():
        raise IngestError(f"{path.name} 是空文件")

    for encoding in encodings:
        try:
            text = normalize(data.decode(encoding))
        except UnicodeDecodeError:
            continue
        return text, encoding, garbled_ratio(text)

    text = normalize(data.decode(encodings[-1], errors="replace"))
    return text, f"{encodings[-1]}(replace)", garbled_ratio(text)


def load_source(path: Path, encodings: list[str] | tuple[str, ...]) -> SourceFile:
    text, encoding, garbled = read_text(path, encodings)
    return SourceFile(path=path, text=text, encoding=encoding, garbled=garbled)
