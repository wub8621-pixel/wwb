"""Gradio 页面：上传 txt 自动转换，展示封面/目录/正文预览并下载 EPUB。

页面不直接跑转换逻辑，全部走 HTTP API，保证命令行、接口、页面三条路径行为一致。
"""

from __future__ import annotations

import html
import logging
import re
import time
from pathlib import Path

import gradio as gr
import httpx
from ebooklib import ITEM_COVER, ITEM_DOCUMENT, ITEM_IMAGE, epub

from config.loader import ConfigError, load_config, project_path

from .logging_setup import setup_logging

REQUIRED_SUFFIX = ".txt"
TAG_RE = re.compile(r"<[^>]+>")
H2_RE = re.compile(r"<h2[^>]*>(.*?)</h2>", re.S | re.I)

PLACEHOLDER = (
    '<div class="book-card placeholder">'
    "<div class=\"placeholder-icon\">📖</div>"
    "<div>上传 txt 后自动排版</div>"
    '<div class="placeholder-hint">封面、目录和正文预览会显示在这里</div>'
    "</div>"
)

CSS = """
:root { --ink: #1f2937; --muted: #6b7280; --line: #e5e7eb; --accent: #2f6f5e; }
.gradio-container { max-width: 1180px !important; margin: 0 auto !important; }
footer { display: none !important; }

#hero {
  background: linear-gradient(135deg, #143f34 0%, #2f6f5e 60%, #4c8f79 100%);
  border-radius: 20px; padding: 30px 34px; margin-bottom: 18px;
  box-shadow: 0 14px 40px rgba(20, 63, 52, .22);
}
#hero h1 { color: #fff; font-size: 30px; margin: 0 0 8px; letter-spacing: .02em; }
#hero p { color: rgba(255,255,255,.82); margin: 0; font-size: 15px; }
#backend-note { margin: 10px 2px 18px; color: var(--muted); font-size: 13px; }

.panel {
  background: #fff; border: 1px solid var(--line); border-radius: 18px;
  padding: 18px 20px !important; box-shadow: 0 8px 26px rgba(15, 23, 42, .05);
}

#status-box { min-height: 42px; font-size: 14px; }

.book-card {
  background: #fff; border: 1px solid var(--line); border-radius: 18px;
  padding: 24px 26px; min-height: 260px; line-height: 1.7;
}
.book-card.placeholder {
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  gap: 8px; color: var(--muted); border-style: dashed; background: #fbfbfa;
}
.placeholder-icon { font-size: 40px; }
.placeholder-hint { font-size: 13px; color: #9aa2ad; }

.book-head { border-bottom: 1px solid var(--line); padding-bottom: 14px; margin-bottom: 16px; }
.book-title { font-size: 24px; font-weight: 700; color: var(--ink); }
.book-author { color: var(--muted); font-size: 14px; margin-top: 4px; }
.book-desc { color: #4b5563; font-size: 14px; margin-top: 10px; }

.chips { margin: 4px 0 18px; }
.chips span {
  display: inline-block; margin: 0 8px 8px 0; padding: 4px 12px;
  background: #eef4f1; color: #2f6f5e; border: 1px solid #d7e6df;
  border-radius: 999px; font-size: 12.5px;
}
.chips .more { background: #f3f4f6; color: #6b7280; border-color: #e5e7eb; }

.reading {
  font-family: "Songti SC", "Noto Serif SC", "STSong", serif;
  font-size: 16.5px; line-height: 2.0; color: #2b2b2b;
  white-space: pre-wrap; letter-spacing: .015em;
}
.reading-label { font-size: 12px; color: #9aa2ad; letter-spacing: .12em; margin-bottom: 8px; }

/* 护眼模式：暖纸色 + 低对比（卡片本身也带 eye 类，JS 失效时预览仍可读） */
.book-card.eye { background: #f6eed9 !important; border-color: #dfd3b2 !important; }
.book-card.eye .book-title { color: #3b3324; }
.book-card.eye .book-desc,
.book-card.eye .reading { color: #42392a; }
.book-card.eye .chips span { background: #ece1c4; border-color: #ddcfab; color: #5a4b2f; }

body.eye-care { background: #e9e0c8 !important; }
body.eye-care .gradio-container { background: transparent !important; }
body.eye-care .panel,
body.eye-care .block { background: #f6eed9 !important; border-color: #dfd3b2 !important; }
body.eye-care .book-card { background: #f6eed9 !important; border-color: #dfd3b2 !important; }
body.eye-care .book-title { color: #3b3324; }
body.eye-care .book-desc,
body.eye-care .reading { color: #42392a; }
body.eye-care .chips span { background: #ece1c4; border-color: #ddcfab; color: #5a4b2f; }
body.eye-care #hero { background: linear-gradient(135deg, #4a3f28 0%, #6b5a3a 100%); }
"""


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
        return f"⚠️ 后端不可用：{exc}。请先运行 `python -m ebook.api`。"
    return f"后端已连接 · 模型 `{body.get('model', '')}` · LLM 兜底：{llm}"


def _strip_xhtml(raw: str) -> str:
    """把章节 XHTML 转成可读纯文本，保留段落换行。"""
    raw = re.sub(r"</p\s*>", "\n\n", raw, flags=re.I)
    raw = re.sub(r"<br\s*/?>", "\n", raw, flags=re.I)
    raw = H2_RE.sub("", raw)
    text = html.unescape(TAG_RE.sub("", raw))
    lines = [re.sub(r"[ \t　]+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def read_book(epub_path: Path, preview_chars: int) -> dict:
    """从生成的 EPUB 里取展示所需信息；封面写到同目录，供 gr.Image 使用。"""
    book = epub.read_epub(str(epub_path))
    documents = [
        item for item in book.get_items_of_type(ITEM_DOCUMENT)
        if item.get_name() not in ("nav.xhtml", "cover.xhtml")
    ]
    titles = [value for value, _ in book.get_metadata("DC", "title")]
    authors = [value for value, _ in book.get_metadata("DC", "creator")]
    descriptions = [value for value, _ in book.get_metadata("DC", "description")]

    chapters = []
    for item in documents:
        title = (getattr(item, "title", "") or "").strip()
        if not title:
            match = H2_RE.search(item.get_content().decode("utf-8", "ignore"))
            title = html.unescape(match.group(1)).strip() if match else ""
        chapters.append(title or "未命名章节")

    first_text = _strip_xhtml(documents[0].get_content().decode("utf-8", "ignore")) if documents else ""
    first_text = re.sub(r"\n{3,}", "\n\n", first_text).strip()
    preview = first_text[:preview_chars]
    if len(first_text) > preview_chars:
        preview += "……"

    # set_cover 生成的条目类型是 ITEM_COVER；兼容按文件名找图的旧产物
    cover_path = None
    candidates = list(book.get_items_of_type(ITEM_COVER)) or [
        item for item in book.get_items_of_type(ITEM_IMAGE)
        if "cover" in item.get_name().lower()
    ]
    for item in candidates:
        cover_path = epub_path.with_name(f"{epub_path.stem}-cover.png")
        cover_path.write_bytes(item.get_content())
        break

    return {
        "title": titles[0] if titles else epub_path.stem,
        "author": authors[0] if authors else "未知作者",
        "description": descriptions[0] if descriptions else "",
        "chapters": chapters,
        "preview": preview,
        "cover": str(cover_path) if cover_path else None,
    }


def _card_html(book: dict | None, eye: bool, extra: str = "") -> str:
    if not book:
        return PLACEHOLDER
    titles = book["chapters"]
    shown = titles[:12]
    chips = "".join(f"<span>{html.escape(title)}</span>" for title in shown)
    if len(titles) > len(shown):
        chips += f'<span class="more">… 共 {len(titles)} 章</span>'
    description = (
        f'<div class="book-desc">{html.escape(book["description"])}</div>'
        if book.get("description") else ""
    )
    reading = html.escape(book.get("preview", ""))
    return f"""
<div class="book-card{' eye' if eye else ''}">
  <div class="book-head">
    <div class="book-title">{html.escape(book['title'])}</div>
    <div class="book-author">{html.escape(book['author'])} · 共 {len(titles)} 章</div>
    {description}
  </div>
  <div class="chips">{chips}</div>
  <div class="reading-label">正文预览</div>
  <div class="reading">{reading}</div>
  {extra}
</div>"""


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


def convert_and_preview(
    paths: list[str] | None,
    use_llm: bool,
    eye: bool,
    cfg,
    logger: logging.Logger,
    client: httpx.Client | None = None,
    output_dir: Path | None = None,
) -> tuple[str, dict | None, str, str | None, list[str]]:
    """上传即转换：返回（状态 Markdown, 预览状态, 卡片 HTML, 封面路径, 下载路径）。"""
    selected = [Path(item) for item in (paths or [])]
    if not selected:
        return "上传 `.txt` 后会自动开始转换。", None, _card_html(None, eye), None, []
    if len(selected) > cfg.get("frontend.max_files"):
        message = f"一次最多 {cfg.get('frontend.max_files')} 个文件，当前选了 {len(selected)} 个。"
        return message, None, _card_html(None, eye), None, []
    wrong = [item.name for item in selected if item.suffix.lower() != REQUIRED_SUFFIX]
    if wrong:
        return f"只支持 .txt：{'、'.join(wrong)}", None, _card_html(None, eye), None, []

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
            return (
                f"❌ 转换失败（HTTP {response.status_code}）：{detail}",
                None, _card_html(None, eye), None, [],
            )

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
        return f"❌ 调用后端失败：{exc}", None, _card_html(None, eye), None, []
    finally:
        if owns_client:
            client.close()

    ok = sum(1 for item in results if item.get("ok"))
    elapsed = time.monotonic() - started
    logger.info("页面转换 %d/%d 本，用时 %.1fs，run_id=%s", ok, len(results), elapsed, run_id)

    book = read_book(Path(saved[0]), cfg.get("frontend.preview_chars")) if saved else None
    header = f"✅ 已生成 {ok}/{len(results)} 本 · {elapsed:.1f}s · run_id `{run_id}`"
    status = f"{header}\n\n{_table(results)}"
    extra = (
        f'<div class="book-desc">本次共生成 {ok} 本，右下角可下载全部。</div>'
        if ok > 1 else ""
    )
    state = {"book": book, "extra": extra}
    return status, state, _card_html(book, eye, extra), (book or {}).get("cover"), saved


def rerender(state: dict | None, eye: bool) -> str:
    """切换护眼模式时只重绘卡片，不重新转换。"""
    if not state:
        return _card_html(None, eye)
    return _card_html(state.get("book"), eye, state.get("extra", ""))


def build_ui(cfg=None) -> gr.Blocks:
    cfg = cfg or load_config()
    logger, _ = setup_logging(cfg)
    output_dir = project_path(cfg.get("frontend.output_dir"))

    with gr.Blocks(title="电子书阅读器", fill_width=False) as demo:
        gr.HTML(
            '<div id="hero"><h1>📖 电子书阅读器</h1>'
            "<p>上传 txt，自动切章、补元数据、配封面，生成可阅读的 EPUB。</p></div>"
        )
        gr.Markdown(_backend_summary(cfg), elem_id="backend-note")

        with gr.Row(equal_height=False):
            with gr.Column(scale=4, min_width=320, elem_classes=["panel"]):
                files = gr.File(
                    label=f"选择 txt（可多选，最多 {cfg.get('frontend.max_files')} 本）",
                    file_count="multiple",
                    file_types=[REQUIRED_SUFFIX],
                    type="filepath",
                )
                use_llm = gr.Checkbox(
                    value=True,
                    label="允许 LLM 兜底",
                    info="正则切分异常时（乱序、乱码、章节过少）再调模型。",
                )
                eye = gr.Checkbox(value=False, label="护眼模式", info="暖纸色底、低对比，适合长时间阅读。")
                status = gr.Markdown("上传 `.txt` 后会自动开始转换。", elem_id="status-box")
                downloads = gr.File(
                    label="下载 EPUB", file_count="multiple", type="filepath"
                )

            with gr.Column(scale=7, min_width=420):
                cover = gr.Image(
                    label=None,
                    show_label=False,
                    type="filepath",
                    interactive=False,
                    height=300,
                    elem_classes=["panel"],
                )
                card = gr.HTML(PLACEHOLDER)
                state = gr.State(None)

        outputs = [status, state, card, cover, downloads]
        convert = lambda paths, allow, eye_on: convert_and_preview(  # noqa: E731
            paths, allow, eye_on, cfg, logger, output_dir=output_dir
        )
        files.change(
            convert,
            inputs=[files, use_llm, eye],
            outputs=outputs,
            show_progress="full",
        )
        use_llm.change(
            convert,
            inputs=[files, use_llm, eye],
            outputs=outputs,
            show_progress="full",
        )
        eye.change(
            rerender,
            inputs=[state, eye],
            outputs=[card],
            js="(st, on) => { document.body.classList.toggle('eye-care', !!on); return [st, on]; }",
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
        css=CSS,
        theme=gr.themes.Soft(primary_hue="emerald", neutral_hue="slate"),
        footer_links=[],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
