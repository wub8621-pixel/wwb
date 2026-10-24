"""解析探针：验证主备解析链路，并把每份简历抽出的纯文本落盘供人工核对。

用法：
    .venv/bin/python probe_parse.py <文件或目录> [...] [--engine auto|unstructured|native]

auto = 走主备链（unstructured 优先，不达标降级 native）
"""

from __future__ import annotations

import sys
from pathlib import Path

from resume_agent.parse import ENGINE_ORDER, ParseError, parse_resume
from resume_agent.quality import section_map

OUT_DIR = Path("_probe_out")
SUPPORTED = {".pdf", ".docx"}


def probe(path: Path, engine: str) -> bool:
    print(f"\n=== {path.name} ===")
    engines = ENGINE_ORDER if engine == "auto" else (engine,)

    try:
        result = parse_resume(path, engines=engines)
    except ParseError as exc:
        print(f"  !! {exc}")
        return False

    for failed_engine, reason in result.attempts:
        print(f"  降级 {failed_engine}: {reason}")
    print(f"  引擎: {result.engine} | 页数: {len(result.pages)}")

    quality = result.quality
    print(
        f"  质量: {quality.chars} 字符 | 中文 {quality.cjk_ratio:.1%} "
        f"| 乱码 {quality.garbled_ratio:.2%} | 空元素 {quality.blank_ratio:.0%}"
        f" | 页眉页脚 {quality.header_footer or '无'}"
    )
    print(f"  章节: {section_map(result.lines) or '未识别到'}")

    OUT_DIR.mkdir(exist_ok=True)
    out = OUT_DIR / f"{path.stem}.{result.engine}.txt"
    out.write_text(result.text, encoding="utf-8")
    print(f"  纯文本 -> {out}")
    return True


def collect(targets: list[str]) -> list[Path]:
    files: list[Path] = []
    for raw in targets:
        path = Path(raw).expanduser()
        if path.is_dir():
            files.extend(sorted(p for p in path.iterdir() if p.suffix.lower() in SUPPORTED))
        elif path.is_file():
            files.append(path)
        else:
            print(f"跳过不存在的路径: {path}")
    return files


def main(argv: list[str]) -> int:
    engine = "auto"
    if "--engine" in argv:
        index = argv.index("--engine")
        if index + 1 >= len(argv):
            print("--engine 需要一个值")
            return 2
        engine = argv[index + 1]
        del argv[index:index + 2]
    if engine not in {"auto", *ENGINE_ORDER}:
        print(f"未知 engine: {engine}")
        return 2

    files = collect(argv)
    if not files:
        print("没有可解析的文件")
        return 1
    results = [probe(path, engine) for path in files]
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
