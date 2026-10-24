"""Gradio 页面：选 txt → 调本机 FastAPI → 下载 EPUB。

页面不直接跑转换逻辑，全部走 HTTP API，保证命令行、接口、页面三条路径行为一致。
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

import gradio as gr
import httpx

from config.loader import ConfigError, load_config, project_path

from .logging_setup import setup_logging

REQUIRED_SUFFIX = ".txt"


def _api_url(cfg, path: str) -> str:
    return f"{cfg.get('frontend.api_base').rstrip('/')}{path}"


def _backend_summary(cfg) -> str:
    """启动时探一次后端；挂了页面里也要说清楚，而不是等用户上传后才报错。"""
    try:
        response = httpx.get(_api_url(cfg, "/health"), timeout=3)
        response.raise_for_status()
        body = response.json()
        llm = "可用" if body.get("llm_enabled") else "不可用（仅正则切分）"
    except httpx.HTTPError as exc:
        return f"> ⚠️ 后端不可用：{exc}。请先运行 `python -m ebook.api`。"
    return f"> 后端已连接 · 模型 `{body.get('model', '')}` · LLM 兜底：{llm}"


def _table(results: list[dict]) -> str:
    rows = ["| 文件 | 章节 | LLM 兜底 | 大小 | 备注 |", "|---|---|---|---|---|"]
    for item in results:
        if not item.get("ok"):
            rows.append(f"| {item.get('file', '')} | — | — | — | ❌ {item.get('error', '失败')} |")
            continue
        note = "；".join(item.get("warnings", [])) or "—"
        rows.append(
            f"| {item['file']} | {item['chapters']} | "
            f"{'是' if item.get('repaired') else '否'} | {item.get('size_kb', '—')} KB | {note} |"
        )
    return "\n".join(rows)


def convert_files(
    paths: list[str] | None,
    use_llm: bool,
    cfg,
    logger: logging.Logger,
    client: httpx.Client | None = None,
    output_dir: Path | None = None,
) -> tuple[str, list[str]]:
    """把选中的文件发给批量接口，下载产物，返回（状态 Markdown, 文件路径列表）。"""
    selected = [Path(item) for item in (paths or [])]
    if not selected:
        return "请先选择至少一个 `.txt` 文件。", []
    if len(selected) > cfg.get("frontend.max_files"):
        return f"一次最多 {cfg.get('frontend.max_files')} 个文件，当前选了 {len(selected)} 个。", []
    wrong = [item.name for item in selected if item.suffix.lower() != REQUIRED_SUFFIX]
    if wrong:
        return f"只支持 .txt：{'、'.join(wrong)}", []

    owns_client = client is None
    if owns_client:
        client = httpx.Client(timeout=cfg.get("frontend.request_timeout_s"))
    prefix = cfg.get("server.route_prefix").rstrip("/")
    started = time.monotonic()

    try:
        files = [
            ("files", (item.name, item.read_bytes(), "text/plain")) for item in selected
        ]
        response = client.post(
            _api_url(cfg, f"{prefix}/convert/batch"),
            files=files,
            data={"use_llm": "true" if use_llm else "false"},
        )
        if response.status_code != 200:
            detail = response.json().get("detail", response.text)
            logger.warning("接口返回 %s：%s", response.status_code, detail)
            return f"❌ 转换失败（HTTP {response.status_code}）：{detail}", []

        payload = response.json()
        run_id = payload["run_id"]
        results = payload["results"]
        target_dir = output_dir or project_path(cfg.get("frontend.output_dir"))
        target_dir = target_dir / run_id
        target_dir.mkdir(parents=True, exist_ok=True)

        saved = []
        for item in results:
            if not item.get("ok"):
                continue
            download = client.get(_api_url(cfg, item["download_url"]))
            download.raise_for_status()
            name = Path(item["output"]).name
            path = target_dir / name
            path.write_bytes(download.content)
            saved.append(str(path))
    except httpx.HTTPError as exc:
        logger.exception("调用后端失败")
        return f"❌ 调用后端失败：{exc}", []
    finally:
        if owns_client:
            client.close()

    ok = sum(1 for item in results if item.get("ok"))
    elapsed = time.monotonic() - started
    logger.info("页面转换 %d/%d 本，用时 %.1fs，run_id=%s", ok, len(results), elapsed, run_id)
    header = f"✅ 完成 {ok}/{len(results)} 本，用时 {elapsed:.1f}s · run_id `{run_id}`"
    return f"{header}\n\n{_table(results)}", saved


def build_ui(cfg=None) -> gr.Blocks:
    cfg = cfg or load_config()
    logger, _ = setup_logging(cfg)
    output_dir = project_path(cfg.get("frontend.output_dir"))

    with gr.Blocks(title="电子书阅读器") as demo:
        gr.Markdown("# 电子书阅读器\n把 txt 转成带封面和目录的 EPUB。")
        gr.Markdown(_backend_summary(cfg))
        files = gr.File(
            label=f"选择 txt（可多选，最多 {cfg.get('frontend.max_files')} 本）",
            file_count="multiple",
            file_types=[REQUIRED_SUFFIX],
            type="filepath",
        )
        use_llm = gr.Checkbox(
            value=True,
            label="允许 LLM 兜底",
            info="正则切分异常时（乱序、乱码、章节过少）再调模型；关闭则只跑正则。",
        )
        button = gr.Button("开始转换", variant="primary")
        status = gr.Markdown()
        downloads = gr.File(label="生成的 EPUB（点击下载）", file_count="multiple", type="filepath")

        button.click(
            lambda paths, allow: convert_files(paths, allow, cfg, logger, output_dir=output_dir),
            inputs=[files, use_llm],
            outputs=[status, downloads],
        )
    return demo


def main() -> int:
    try:
        cfg = load_config()
    except ConfigError as exc:
        print(f"配置有误：{exc}")
        return 2
    demo = build_ui(cfg)
    demo.launch(
        server_name=cfg.get("frontend.host"),
        server_port=cfg.get("frontend.port"),
        show_error=True,
        allowed_paths=[str(project_path(cfg.get("frontend.output_dir")))],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
