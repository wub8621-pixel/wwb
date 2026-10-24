"""LangGraph 编排：读取 → 切章 → 质量判定 →（必要时）LLM 兜底 → 元数据 → 打包。"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from config.loader import project_path

from .chapters import analyze, split_chapters
from .cover import make_cover
from .epub import build_epub, safe_filename
from .ingest import IngestError, load_source
from .llm import LLMError
from .metadata import extract_local, merge_metadata


class BookState(TypedDict, total=False):
    source: str
    output_dir: str
    text: str
    encoding: str
    chapters: list[dict]
    quality: dict
    metadata: dict
    output_path: str
    warnings: list[str]
    repaired: bool


def build_graph(cfg, logger: logging.Logger, llm=None):
    """把配置和依赖闭包进节点，返回可执行的 CompiledGraph。"""

    def ingest_node(state: BookState) -> dict:
        path = Path(state["source"])
        source = load_source(path, cfg.get("ingest.encodings"))
        min_chars = cfg.get("ingest.min_chars")
        if len(source.text) < min_chars:
            raise IngestError(f"正文只有 {len(source.text)} 字，低于下限 {min_chars}")
        warnings = []
        if source.garbled > 0:
            warnings.append(f"按 {source.encoding} 解码后乱码率 {source.garbled:.1%}")
        logger.debug("读取 %s：编码=%s，字符数=%d", path.name, source.encoding, len(source.text))
        return {"text": source.text, "encoding": source.encoding, "warnings": warnings}

    def split_node(state: BookState) -> dict:
        chapters = split_chapters(
            state["text"],
            cfg.get("chapters.patterns"),
            cfg.get("chapters.fallback_title"),
            cfg.get("chapters.max_title_chars"),
        )
        quality = analyze(
            state["text"],
            chapters,
            garbled_threshold=cfg.get("quality.garbled_ratio"),
            blank_threshold=cfg.get("quality.blank_ratio"),
            min_chapters=cfg.get("chapters.min_chapters"),
            long_text_threshold=cfg.get("chapters.long_text_threshold"),
            check_order=cfg.get("quality.check_order"),
        )
        name = Path(state["source"]).name
        if quality["needs_llm"]:
            logger.info("%s：识别 %d 章，需要兜底（%s）", name, len(chapters), "；".join(quality["reasons"]))
        else:
            logger.info("%s：识别 %d 章，未触发兜底", name, len(chapters))
        return {"chapters": [c.as_dict() for c in chapters], "quality": quality}

    def route(state: BookState) -> str:
        if state["quality"]["needs_llm"] and cfg.get("llm_fallback.enabled"):
            return "repair"
        return "metadata"

    def repair_node(state: BookState) -> dict:
        warnings = list(state.get("warnings", []))
        if llm is None:
            warnings.append("需要 LLM 兜底，但模型不可用，保留正则切分结果")
            return {"warnings": warnings}
        try:
            chapters = llm.repair_chapters(
                state["text"],
                state["quality"]["reasons"],
                max_input_chars=cfg.get("llm_fallback.max_input_chars"),
                max_chapters=cfg.get("llm_fallback.max_chapters"),
                fallback_title=cfg.get("chapters.fallback_title"),
            )
        except LLMError as exc:
            logger.warning("%s：LLM 兜底失败，保留正则结果：%s", Path(state["source"]).name, exc)
            warnings.append(f"LLM 兜底失败：{exc}")
            return {"warnings": warnings}

        if not chapters:
            warnings.append("LLM 未返回可用章节，保留正则切分结果")
            return {"warnings": warnings}

        logger.info("%s：LLM 兜底后得到 %d 章", Path(state["source"]).name, len(chapters))
        return {
            "chapters": [c.as_dict() for c in chapters],
            "repaired": True,
            "warnings": warnings,
        }

    def metadata_node(state: BookState) -> dict:
        path = Path(state["source"])
        local = extract_local(
            state["text"],
            path,
            cfg.get("metadata.author_patterns"),
            cfg.get("metadata.description_max_chars"),
        )
        warnings = list(state.get("warnings", []))
        metadata = local
        if llm is not None and not (local["author"] and local["description"]):
            try:
                extra = llm.extract_metadata(state["text"][:3000], path.stem)
                metadata = merge_metadata(local, extra)
            except LLMError as exc:
                logger.warning("%s：元数据兜底失败：%s", path.name, exc)
                warnings.append(f"元数据兜底失败：{exc}")
        return {"metadata": metadata, "warnings": warnings}

    def build_node(state: BookState) -> dict:
        path = Path(state["source"])
        metadata = state["metadata"]
        output_dir = Path(state["output_dir"]) if state.get("output_dir") else project_path(
            cfg.get("runtime.output_dir")
        )
        target = output_dir / f"{safe_filename(path.stem)}.epub"

        cover_bytes = None
        if cfg.get("epub.cover.enabled"):
            cover_bytes = make_cover(
                metadata["title"],
                metadata["author"],
                width=cfg.get("epub.cover.width"),
                height=cfg.get("epub.cover.height"),
                background=cfg.get("epub.cover.background"),
                text_color=cfg.get("epub.cover.text_color"),
                font_path=cfg.get("epub.cover.font_path"),
            )

        build_epub(
            target,
            title=metadata["title"],
            author=metadata["author"],
            description=metadata["description"],
            language=cfg.get("epub.language"),
            chapters=state["chapters"],
            cover_bytes=cover_bytes,
        )
        logger.info("%s：已生成 %s", path.name, target)
        return {"output_path": str(target)}

    graph = StateGraph(BookState)
    graph.add_node("ingest", ingest_node)
    graph.add_node("split", split_node)
    graph.add_node("repair", repair_node)
    graph.add_node("metadata", metadata_node)
    graph.add_node("build", build_node)

    graph.add_edge(START, "ingest")
    graph.add_edge("ingest", "split")
    graph.add_conditional_edges("split", route, {"repair": "repair", "metadata": "metadata"})
    graph.add_edge("repair", "metadata")
    graph.add_edge("metadata", "build")
    graph.add_edge("build", END)
    return graph.compile()
