"""命令行入口：扫描目录里的 txt，逐本生成 EPUB。"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from config.loader import ConfigError, load_config, project_path

from .graph import build_graph
from .ingest import IngestError, discover_files
from .llm import LLMClient
from .logging_setup import setup_logging, write_report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="把目录中的 txt 转成 EPUB 电子书")
    parser.add_argument("input", nargs="?", help="txt 所在目录；不给则用配置里的 default_input_dir")
    parser.add_argument("--output", help="EPUB 输出目录；默认取配置 runtime.output_dir")
    parser.add_argument("--recursive", action="store_true", help="递归子目录")
    parser.add_argument("--no-llm", action="store_true", help="禁用 LLM 兜底，只跑正则")
    parser.add_argument("--json", action="store_true", help="把逐本结果以 JSON 打到标准输出")
    return parser


def _resolve_input(raw: str) -> Path:
    path = Path(raw).expanduser()
    return path if path.is_absolute() else Path.cwd() / path


def _make_llm(cfg, logger):
    if not cfg.get("llm_fallback.enabled"):
        return None
    try:
        return LLMClient(cfg, logger)
    except ConfigError as exc:
        logger.warning("模型未配置，跳过 LLM 兜底：%s", exc)
        return None
    except ImportError as exc:
        logger.warning("anthropic SDK 不可用，跳过 LLM 兜底：%s", exc)
        return None


def main(argv: list[str] | None = None, cfg=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        cfg = cfg or load_config()
    except ConfigError as exc:
        print(f"配置有误：{exc}", file=sys.stderr)
        return 2

    logger, log_dir = setup_logging(cfg)

    raw_input = args.input or cfg.get("runtime.default_input_dir")
    if not raw_input:
        message = "请提供 txt 目录，或在 config/settings.yaml 设置 runtime.default_input_dir"
        logger.error(message)
        print(message, file=sys.stderr)
        return 2

    input_dir = _resolve_input(raw_input)
    recursive = args.recursive or cfg.get("ingest.recursive")
    try:
        files = discover_files(input_dir, cfg.get("ingest.extensions"), recursive)
    except IngestError as exc:
        logger.error(str(exc))
        print(str(exc), file=sys.stderr)
        return 2

    if not files:
        message = f"{input_dir} 下没有找到 txt 文件"
        logger.warning(message)
        print(message, file=sys.stderr)
        return 2

    output_dir = Path(args.output).expanduser() if args.output else project_path(
        cfg.get("runtime.output_dir")
    )
    llm = None if args.no_llm else _make_llm(cfg, logger)
    graph = build_graph(cfg, logger, llm)

    results = []
    for path in files:
        try:
            state = graph.invoke(
                {"source": str(path), "output_dir": str(output_dir)},
                config={"recursion_limit": cfg.get("graph.recursion_limit")},
            )
        except Exception as exc:  # 单本失败不拖垮整批
            logger.exception("处理 %s 失败", path.name)
            results.append({"file": path.name, "ok": False, "error": str(exc)})
            continue
        results.append({
            "file": path.name,
            "ok": True,
            "chapters": len(state["chapters"]),
            "repaired": bool(state.get("repaired")),
            "output": state["output_path"],
            "warnings": state.get("warnings", []),
        })

    if args.json:
        import json

        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        _print_table(results)

    report = write_report(log_dir, {"input": str(input_dir), "output": str(output_dir), "results": results})
    logger.info("本次运行报告：%s", report)
    return 0 if all(item["ok"] for item in results) else 1


def _print_table(results: list[dict]) -> None:
    for item in results:
        if not item["ok"]:
            print(f"✗ {item['file']}：{item['error']}")
            continue
        flag = "（LLM 兜底）" if item["repaired"] else ""
        print(f"✓ {item['file']}：{item['chapters']} 章{flag} → {item['output']}")
        for warning in item["warnings"]:
            print(f"  ! {warning}")


if __name__ == "__main__":
    raise SystemExit(main())
