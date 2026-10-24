"""命令行入口：评估一份或多份简历。

    python -m resume_agent.main <简历路径> [...] [--json]

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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="简历评估 Agent")
    parser.add_argument("paths", nargs="+", help="简历文件路径")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--required-years", type=float, default=REQUIRED_YEARS)
    args = parser.parse_args(argv)

    files = [Path(item).expanduser() for item in args.paths]
    missing = [str(path) for path in files if not path.is_file()]
    if missing:
        parser.error(f"文件不存在: {', '.join(missing)}")

    try:
        assessor = from_env()
    except RuntimeError as exc:
        print(f"模型未配置: {exc}", file=sys.stderr)
        return 2

    app = build_graph(assessor, required_years=args.required_years)
    exit_code = 0

    for path in files:
        state = app.invoke({"path": str(path), "dims": {}, "errors": []})
        if args.json:
            print(json.dumps(_as_json(path, state), ensure_ascii=False, indent=2))
        else:
            _print_report(path, state)
        if state.get("errors") or state.get("verdict") is None:
            exit_code = 1

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
