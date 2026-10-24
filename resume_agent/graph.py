"""LangGraph 主图：解析 → 归一化 → 三维并行评估 → 规则聚合。

图里只做编排；所有判断都委托给 rules（确定性）或 assessor（模型）。
并行分支写同一个 `dims` 键，因此必须带 reducer，否则 LangGraph 会报错。
"""

from __future__ import annotations

import operator
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Callable, Protocol, TypedDict

from langgraph.graph import END, START, StateGraph

from .normalize import build_doc
from .parse import ParseError, ParseResult, parse_resume
from .quality import QualityReport
from .rubrics import REQUIRED_YEARS, RUBRICS, WEIGHTS
from .rules import aggregate, education_dim, tenure_dim, work_years
from .schema import DimResult, ResumeDoc, Verdict


@dataclass
class Assessment:
    """模型对单个维度的判断。分数由模型给，等级仍由规则算。"""

    score: float
    reason: str = ""
    evidence: list[str] = field(default_factory=list)
    confidence: float = 1.0


class Assessor(Protocol):
    def assess(self, dimension: str, resume_text: str, rubric: str) -> Assessment:
        """dimension 为 tech 或 project；必须引用原文作为 evidence。"""


def _merge_dims(left: dict[str, DimResult], right: dict[str, DimResult]) -> dict[str, DimResult]:
    return {**left, **right}


class ResumeState(TypedDict, total=False):
    path: str
    lines: list[str]
    text: str
    engine: str
    attempts: list[tuple[str, str]]
    quality: QualityReport | None
    doc: ResumeDoc | None
    years: float | None
    dims: Annotated[dict[str, DimResult], _merge_dims]
    verdict: Verdict | None
    errors: Annotated[list[str], operator.add]


def _squash(text: str) -> str:
    return re.sub(r"\s+", "", text)


def verify_evidence(evidence: list[str], resume_text: str) -> list[str]:
    """只保留能在简历原文里逐字找到的引用，防止模型编造证据。"""
    haystack = _squash(resume_text)
    return [quote for quote in evidence if quote.strip() and _squash(quote) in haystack]


def build_graph(
    assessor: Assessor,
    parser: Callable[[Path], ParseResult] = parse_resume,
    required_years: float = REQUIRED_YEARS,
    tiers: dict[str, str] | None = None,
):
    """编译后的图。parser / assessor 可注入，测试时无需真实文件与模型。"""

    def parse_node(state: ResumeState) -> ResumeState:
        try:
            result = parser(Path(state["path"]))
        except ParseError as exc:
            return {"errors": [str(exc)]}
        return {
            "lines": result.lines,
            "text": result.text,
            "engine": result.engine,
            "attempts": list(result.attempts),
            "quality": result.quality,
            "dims": {},
            "errors": [],
        }

    def normalize_node(state: ResumeState) -> ResumeState:
        lines = state.get("lines") or []
        if not lines:
            return {"errors": ["归一化失败：没有可用的解析结果"]}
        return {"doc": build_doc(lines, state.get("text", ""))}

    def facts_node(state: ResumeState) -> ResumeState:
        doc = state["doc"]
        assert doc is not None
        years = work_years(doc)
        return {
            "years": years,
            "dims": {
                "tenure": tenure_dim(years, required_years),
                "education": education_dim(doc, tiers),
            },
        }

    def make_assess_node(dimension: str):
        def assess_node(state: ResumeState) -> ResumeState:
            doc = state["doc"]
            assert doc is not None
            result = assessor.assess(dimension, doc.raw_text, RUBRICS[dimension])

            evidence = verify_evidence(result.evidence, doc.raw_text)
            confidence = result.confidence
            if result.evidence and not evidence:
                # 模型给了引用却一条都对不上原文，按低置信处理
                confidence = min(confidence, 0.3)

            return {
                "dims": {
                    dimension: DimResult(
                        key=dimension,
                        score=max(0.0, min(100.0, result.score)),
                        reason=result.reason,
                        evidence=evidence,
                        confidence=confidence,
                    )
                }
            }

        return assess_node

    def aggregate_node(state: ResumeState) -> ResumeState:
        dims = state.get("dims", {})
        missing = set(WEIGHTS) - set(dims)
        if missing:
            return {"errors": [f"缺少维度结果: {sorted(missing)}"]}
        return {"verdict": aggregate(dims, state.get("years"), required_years)}

    def route_after_parse(state: ResumeState) -> str:
        return "fail" if state.get("errors") else "ok"

    graph = StateGraph(ResumeState)
    graph.add_node("parse", parse_node)
    graph.add_node("normalize", normalize_node)
    graph.add_node("facts", facts_node)
    graph.add_node("tech", make_assess_node("tech"))
    graph.add_node("project", make_assess_node("project"))
    graph.add_node("aggregate", aggregate_node)

    graph.add_edge(START, "parse")
    graph.add_conditional_edges("parse", route_after_parse, {"ok": "normalize", "fail": END})

    # 三个分支并行执行，最后在 aggregate 汇合
    for node in ("facts", "tech", "project"):
        graph.add_edge("normalize", node)
        graph.add_edge(node, "aggregate")
    graph.add_edge("aggregate", END)

    return graph.compile()
