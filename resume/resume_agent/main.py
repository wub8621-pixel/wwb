"""命令行入口：评估一份或多份简历，也可以直接给一个目录。

    python -m resume_agent.main <简历路径或目录> [...] [--json] [--recursive]

最终等级由规则算，模型只负责技术成熟度和项目深度两个维度。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .assessor import from_env
from .graph import build_graph
from .rubrics import REQUIRED_YEARS

DIM_LABELS = {
    "tech": "技术成熟度",
    "project": "项目深度",
    "tenure": "工作年限",
    "education": "教育背景",
}

SUPPORTED = {".pdf", ".docx"}


def collect_files(paths: list[Path], recursive: bool = False) -> list[Path]:
    """文件原样保留；目录展开成其中的 PDF/DOCX。

    跳过隐藏文件和 macOS 的 `~$`/`.~` 锁文件——它们不是简历。
    """
    found: list[Path] = []
    for path in paths:
        if path.is_file():
            found.append(path)
            continue
        if not path.is_dir():
            continue
        pattern = "**/*" if recursive else "*"
        found.extend(
            item
            for item in sorted(path.glob(pattern))
            if item.is_file()
            and item.suffix.lower() in SUPPORTED
            and not item.name.startswith((".", "~"))
        )

    # 去重：目录和文件同时传入时不该跑两遍
    seen: set[Path] = set()
    unique: list[Path] = []
    for item in found:
        key = item.resolve()
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def summary_rows(items: list[tuple[Path, dict[str, Any]]]) -> list[dict[str, Any]]:
    """汇总表的数据：按分数从高到低，失败项排最后。"""
    rows = []
    for path, state in items:
        verdict = state.get("verdict")
        failed = verdict is None or bool(state.get("errors"))
        rows.append({
            "file": path.name,
            "grade": "失败" if failed else verdict.grade,
            "score": None if failed else verdict.score,
            "years": state.get("years"),
            "failed": failed,
        })
    return sorted(rows, key=lambda row: (row["failed"], -(row["score"] or 0.0)))


def _as_json(path: Path, state: dict[str, Any]) -> dict[str, Any]:
    verdict = state.get("verdict")
    doc = state.get("doc")
    return {
        "path": str(path),
        "engine": state.get("engine"),
        "fallbacks": [{"engine": name, "reason": reason} for name, reason in state.get("attempts", [])],
        "name": doc.name if doc else "",
        "target_role": doc.target_role if doc else "",
        "years": state.get("years"),
        "grade": verdict.grade if verdict else None,
        "score": verdict.score if verdict else None,
        "summary": verdict.summary if verdict else "",
        "veto": verdict.veto if verdict else None,
        "low_confidence": verdict.low_confidence if verdict else True,
        "dimensions": {
            key: {
                "score": dim.score,
                "reason": dim.reason,
                "evidence": dim.evidence,
                "confidence": dim.confidence,
            }
            for key, dim in (verdict.dimensions.items() if verdict else [])
        },
        "flags": doc.flags if doc else [],
        "errors": state.get("errors", []),
    }


def _print_report(path: Path, state: dict[str, Any]) -> None:
    data = _as_json(path, state)
    print(f"\n=== {path.name} ===")

    engine = data["engine"] or "未解析"
    print(f"引擎: {engine}")
    for item in data["fallbacks"]:
        print(f"  降级 {item['engine']}: {item['reason']}")

    if data["errors"]:
        for error in data["errors"]:
            print(f"  !! {error}")
        return

    print(f"姓名: {data['name']} | 岗位: {data['target_role']}")
    if data["years"] is not None:
        print(f"工作年限: {data['years']} 年（代码计算）")
    print(f"等级: {data['grade']} | {data['score']} 分")
    print(f"结论: {data['summary']}")

    print("维度:")
    for key in ("tech", "project", "tenure", "education"):
        dim = data["dimensions"].get(key)
        if not dim:
            continue
        print(f"  {DIM_LABELS[key]:6s} {dim['score']:5.1f}  conf={dim['confidence']:.2f}  {dim['reason']}")
        for quote in dim["evidence"]:
            shown = quote if len(quote) <= 120 else quote[:120] + "…"
            print(f"         证据: {shown}")

    if data["low_confidence"]:
        print("  !! 置信度不足，建议人工复核")
    if data["flags"]:
        print(f"标记: {'; '.join(data['flags'])}")


def _print_summary(items: list[tuple[Path, dict[str, Any]]]) -> None:
    print("\n=== 汇总（按分数从高到低） ===")
    print(f"{'等级':<4} {'分数':>6} {'年限':>5}  文件")
    for row in summary_rows(items):
        score = "—" if row["score"] is None else f"{row['score']:.1f}"
        years = "—" if row["years"] is None else f"{row['years']:.1f}"
        print(f"{row['grade']:<4} {score:>6} {years:>5}  {row['file']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="简历评估 Agent")
    parser.add_argument("paths", nargs="+", help="简历文件或目录路径")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--recursive", action="store_true", help="目录是否递归查找")
    parser.add_argument("--required-years", type=float, default=REQUIRED_YEARS)
    args = parser.parse_args(argv)

    missing = [item for item in args.paths if not Path(item).expanduser().exists()]
    if missing:
        parser.error(f"路径不存在: {', '.join(missing)}")

    files = collect_files([Path(item).expanduser() for item in args.paths], args.recursive)
    if not files:
        parser.error("没有找到 PDF/DOCX")

    try:
        assessor = from_env()
    except RuntimeError as exc:
        print(f"模型未配置: {exc}", file=sys.stderr)
        return 2

    app = build_graph(assessor, required_years=args.required_years)
    items: list[tuple[Path, dict[str, Any]]] = []

    for path in files:
        state = app.invoke({"path": str(path), "dims": {}, "errors": []})
        items.append((path, state))
        if not args.json:
            _print_report(path, state)

    # 多份时 JSON 整体输出数组，单份保持对象，便于 jq 和脚本消费
    if args.json:
        payload = [_as_json(path, state) for path, state in items]
        print(json.dumps(payload if len(payload) > 1 else payload[0], ensure_ascii=False, indent=2))
    elif len(items) > 1:
        _print_summary(items)

    failed = any(state.get("errors") or state.get("verdict") is None for _, state in items)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
