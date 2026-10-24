"""评测脚本：生成标注模板、跑模型、算一致性。

用法：
    python -m eval.run_eval --init <简历目录>        # 生成 eval/labels.csv 模板
    python -m eval.run_eval                          # 跑分（默认 eval/labels.csv）
    python -m eval.run_eval --repeat 3               # 每份跑 3 次取中位数
    python -m eval.run_eval --no-run                 # 复用 eval/results.json

标注文件相对路径按 labels.csv 所在目录解析。
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from statistics import median

from resume_agent.assessor import from_env
from resume_agent.graph import build_graph
from resume_agent.rules import aggregate
from resume_agent.schema import DimResult

from .metrics import (
    GRADE_ORDER,
    adjacent_accuracy,
    confusion_matrix,
    exact_accuracy,
    format_confusion,
    mean_absolute_error,
    quadratic_weighted_kappa,
)

LABEL_COLUMNS = ["file", "annotator", "tech", "project", "education", "tenure", "grade", "notes"]
DIMENSIONS = ("tech", "project", "education", "tenure")
DIM_LABELS = {"tech": "技术成熟度", "project": "项目深度", "education": "教育背景", "tenure": "工作年限"}
SUPPORTED = {".pdf", ".docx"}


@dataclass
class LabelRow:
    file: str
    annotator: str = ""
    tech: float | None = None
    project: float | None = None
    education: float | None = None
    tenure: float | None = None
    grade: str = ""
    notes: str = ""


def _to_float(text: str) -> float | None:
    text = (text or "").strip()
    return float(text) if text else None


def load_labels(path: Path) -> list[LabelRow]:
    rows: list[LabelRow] = []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for raw in csv.DictReader(handle):
            grade = (raw.get("grade") or "").strip()
            if grade and grade not in GRADE_ORDER:
                raise ValueError(f"未知等级 {grade!r}，必须是 {GRADE_ORDER}")
            rows.append(
                LabelRow(
                    file=(raw.get("file") or "").strip(),
                    annotator=(raw.get("annotator") or "").strip(),
                    tech=_to_float(raw.get("tech", "")),
                    project=_to_float(raw.get("project", "")),
                    education=_to_float(raw.get("education", "")),
                    tenure=_to_float(raw.get("tenure", "")),
                    grade=grade,
                    notes=(raw.get("notes") or "").strip(),
                )
            )
    return rows


def init_template(resume_dir: Path, out: Path) -> int:
    files = sorted(
        p for p in resume_dir.iterdir()
        # 跳过 .DS_Store 和 macOS 的 .~xxx.docx 锁文件
        if p.suffix.lower() in SUPPORTED and not p.name.startswith((".", "~"))
    )
    if not files:
        print(f"{resume_dir} 下没有 PDF/DOCX")
        return 1
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=LABEL_COLUMNS)
        writer.writeheader()
        for path in files:
            # 写绝对路径：简历通常在别的目录，相对路径会解析错
            writer.writerow({"file": str(path.resolve()), "annotator": "", "grade": "", "notes": ""})
    print(f"已生成 {out}（{len(files)} 份简历）")
    print("请填写 tech/project/education/tenure（0-100）与 grade（优秀/一般/及格/pass）。")
    return 0


def _resolve(raw: str, base: Path) -> Path:
    path = Path(raw).expanduser()
    return path if path.is_absolute() else (base / path)


def labeled_files(rows: list[LabelRow], base: Path) -> list[Path]:
    """只跑有标注的简历——没标 grade 的跑了也算不出分数，纯浪费调用。"""
    return sorted({_resolve(row.file, base) for row in rows if row.file and row.grade})


def _median_dims(runs: list[dict]) -> dict[str, DimResult]:
    dims: dict[str, DimResult] = {}
    for key in DIMENSIONS:
        present = [run["dims"][key] for run in runs if key in run.get("dims", {})]
        if not present:
            continue
        dims[key] = DimResult(
            key=key,
            score=median(dim.score for dim in present),
            reason="多次运行取中位数",
            evidence=present[0].evidence,
            confidence=median(dim.confidence for dim in present),
        )
    return dims


def _extract(path: Path, state: dict, repeat: int) -> dict:
    verdict = state["verdict"]
    return {
        "file": str(path),
        "grade": verdict.grade,
        "score": verdict.score,
        "low_confidence": verdict.low_confidence,
        "years": state.get("years"),
        "dims": {key: dim.score for key, dim in verdict.dimensions.items()},
        "repeat": repeat,
    }


def run_agent(files: list[Path], repeat: int) -> dict[str, dict]:
    app = build_graph(from_env())
    results: dict[str, dict] = {}
    for path in files:
        runs = [
            app.invoke({"path": str(path), "dims": {}, "errors": []}) for _ in range(repeat)
        ]
        valid = [state for state in runs if state.get("verdict")]
        if not valid:
            errors = runs[-1].get("errors") or ["未知错误"]
            results[str(path)] = {"file": str(path), "error": "; ".join(errors)}
            continue
        if repeat == 1:
            results[str(path)] = _extract(path, valid[0], repeat)
            continue

        dims = _median_dims(valid)
        verdict = aggregate(dims, valid[0].get("years"))
        merged = {
            "verdict": verdict,
            "dims": dims,
            "years": valid[0].get("years"),
        }
        results[str(path)] = _extract(path, merged, repeat)
    return results


def _inter_rater_kappa(rows: list[LabelRow]) -> tuple[float, int]:
    """同一份简历被两位标注者标过时，计算人与人之间的一致性上限。"""
    grouped: dict[str, list[LabelRow]] = {}
    for row in rows:
        if row.grade:
            grouped.setdefault(row.file, []).append(row)

    pairs = [items[:2] for items in grouped.values() if len(items) >= 2]
    if not pairs:
        return float("nan"), 0
    first = [items[0].grade for items in pairs]
    second = [items[1].grade for items in pairs]
    return quadratic_weighted_kappa(first, second), len(pairs)


def build_report(rows: list[LabelRow], results: dict[str, dict], base: Path, repeat: int) -> str:
    usable = []
    skipped = []
    for row in rows:
        if not row.grade:
            continue
        result = results.get(str(_resolve(row.file, base)))
        if not result or result.get("error"):
            skipped.append(row.file)
            continue
        usable.append((row, result))

    lines = [
        "# 简历评估报告",
        "",
        f"- 标注条数：{len(rows)}，可用：{len(usable)}，跳过：{len(skipped)}",
        f"- 每份运行次数：{repeat}",
    ]
    if skipped:
        lines.append(f"- 跳过的文件：{', '.join(sorted(set(skipped)))}")
    lines.append("")

    if not usable:
        lines.append("没有可评测的数据。")
        return "\n".join(lines)

    human = [row.grade for row, _ in usable]
    model = [result["grade"] for _, result in usable]

    lines += [
        "## 模型 vs 人工",
        "",
        f"- 精确一致率：{exact_accuracy(human, model):.1%}",
        f"- 相邻一致率（差一档以内）：{adjacent_accuracy(human, model):.1%}",
        f"- 二次加权 kappa：{quadratic_weighted_kappa(human, model):.3f}",
        "",
        "```",
        format_confusion(confusion_matrix(human, model)),
        "```",
        "",
        "## 各维度平均绝对误差",
        "",
        "| 维度 | MAE | 样本数 |",
        "|---|---|---|",
    ]
    for key in DIMENSIONS:
        pairs = [
            (getattr(row, key), result["dims"][key])
            for row, result in usable
            if getattr(row, key) is not None and key in result.get("dims", {})
        ]
        mae = f"{mean_absolute_error(pairs):.1f}" if pairs else "—"
        lines.append(f"| {DIM_LABELS[key]} | {mae} | {len(pairs)} |")

    kappa, count = _inter_rater_kappa(rows)
    lines += ["", "## 标注者之间（人的一致性上限）", ""]
    if count:
        lines.append(f"- 二次加权 kappa：{kappa:.3f}（{count} 对）")
        lines.append("- 模型 kappa 接近或超过这个数字，就已经达到人类水平。")
    else:
        lines.append("- 暂无两位标注者标过同一份简历，无法计算。")

    low_conf = [row.file for row, result in usable if result.get("low_confidence")]
    if low_conf:
        lines += ["", "## 低置信度（建议人工复核）", ""]
        lines += [f"- {name}" for name in low_conf]

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="简历评估跑分")
    parser.add_argument("--labels", default="eval/labels.csv")
    parser.add_argument("--results", default="eval/results.json")
    parser.add_argument("--init", metavar="简历目录", help="生成标注模板后退出")
    parser.add_argument("--out", help="把报告写到指定 Markdown 文件")
    parser.add_argument("--repeat", type=int, default=1, help="每份简历运行次数，取中位数")
    parser.add_argument("--no-run", action="store_true", help="复用缓存结果，不调用模型")
    args = parser.parse_args(argv)

    if args.init:
        return init_template(Path(args.init), Path(args.labels))

    labels_path = Path(args.labels)
    if not labels_path.is_file():
        print(f"找不到标注文件 {labels_path}，先用 --init 生成", file=sys.stderr)
        return 2

    rows = load_labels(labels_path)
    base = labels_path.parent
    files = labeled_files(rows, base)

    results_path = Path(args.results)
    if not files:
        print("标注文件里还没有任何 grade，先填写再跑。", file=sys.stderr)
        return 2

    if args.no_run:
        if not results_path.is_file():
            print(f"找不到缓存结果 {results_path}", file=sys.stderr)
            return 2
        results = json.loads(results_path.read_text(encoding="utf-8"))
    else:
        results = run_agent(files, max(1, args.repeat))
        results_path.parent.mkdir(parents=True, exist_ok=True)
        results_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    report = build_report(rows, results, base, max(1, args.repeat))
    print(report)
    if args.out:
        Path(args.out).write_text(report, encoding="utf-8")
        print(f"\n报告已写入 {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
