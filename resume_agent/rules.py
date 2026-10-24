"""确定性规则：工作年限、学历评分、最终聚合。

模型只给“技术成熟度”和“项目深度”的分数；凡是能算的都在这里算，
保证同一份简历每次得到同样的结果。
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date

from .dates import DateRange, compute_tenure
from .normalize import normalize_degree
from .rubrics import (
    DEGREE_SCORES,
    FAIL_GRADE,
    GRADE_THRESHOLDS,
    REQUIRED_YEARS,
    TENURE_BANDS,
    TIER_BONUS,
    UNKNOWN_DEGREE_SCORE,
    VETO_RATIO,
    WEIGHTS,
)
from .schema import DimResult, ResumeDoc, Verdict


def work_ranges(doc: ResumeDoc) -> list[DateRange]:
    """工作经历区间；实习按月计 0.5。"""
    ranges: list[DateRange] = []
    for entry in doc.work:
        if entry.period:
            ranges.append(replace(entry.period, weight=0.5 if entry.is_internship else 1.0))
    return ranges


def work_years(doc: ResumeDoc, as_of: date | None = None) -> float | None:
    """解析不到任何工作起止时间时返回 None——未知不等于零年。"""
    ranges = work_ranges(doc)
    return compute_tenure(ranges, as_of) if ranges else None


def tenure_dim(years: float | None, required: float = REQUIRED_YEARS) -> DimResult:
    if years is None:
        return DimResult(
            "tenure", 60.0, "未解析到工作起止时间，按中性分处理", [], confidence=0.2
        )

    score = 0.0
    ratio = years / required if required > 0 else 1.0
    for floor, band_score in TENURE_BANDS:
        if ratio >= floor:
            score = band_score
            break

    reason = f"{years} 年 / 要求 {required} 年"
    if ratio >= 2.5:
        reason += "；经验显著超出岗位要求，注意 overqualified"
    return DimResult("tenure", score, reason, [f"{years} 年"], confidence=1.0)


def school_tier(school: str, tiers: dict[str, str] | None) -> str | None:
    """按配置的院校名单匹配层次；未配置或未命中返回 None。"""
    for name, tier in (tiers or {}).items():
        if name and name in school:
            return tier
    return None


def education_dim(
    doc: ResumeDoc, tiers: dict[str, str] | None = None
) -> DimResult:
    if not doc.education:
        return DimResult("education", 0.0, "未识别到教育经历", [], confidence=0.3)

    best_score = -1.0
    best_reason = ""
    best_evidence: list[str] = []
    matched_tier = False

    for entry in doc.education:
        degree = normalize_degree(entry.degree) or normalize_degree(entry.raw)
        base = DEGREE_SCORES.get(degree, UNKNOWN_DEGREE_SCORE)
        tier = school_tier(entry.school, tiers)
        score = min(100.0, base + TIER_BONUS.get(tier or "", 0.0))

        if score > best_score:
            best_score = score
            matched_tier = tier is not None
            level = f"{degree or '学历未知'}+{tier}" if tier else (degree or "学历未知")
            best_reason = f"{level}（{entry.school or '院校未知'}）"
            best_evidence = [text for text in (entry.school, entry.degree) if text]

    # 院校层次未配置时不硬猜，降低置信度但不影响可用性
    confidence = 1.0 if matched_tier else 0.6
    if not matched_tier:
        best_reason += "；院校层次未匹配，仅按学历计分"
    return DimResult("education", best_score, best_reason, best_evidence, confidence)


def aggregate(
    dims: dict[str, DimResult],
    years: float | None,
    required: float = REQUIRED_YEARS,
) -> Verdict:
    """规则聚合：先看硬门槛，再按加权分定等级。"""
    missing = set(WEIGHTS) - set(dims)
    if missing:
        raise ValueError(f"缺少维度: {sorted(missing)}")

    score = round(sum(WEIGHTS[key] * dim.score for key, dim in dims.items()), 2)
    veto: str | None = None
    grade: str

    min_years = required * VETO_RATIO
    if years is not None and years < min_years:
        grade = FAIL_GRADE
        veto = f"工作年限 {years} 年，低于门槛 {min_years} 年"
    else:
        grade = next(
            (name for name, floor in GRADE_THRESHOLDS if score >= floor), FAIL_GRADE
        )

    low_confidence = any(dim.confidence < 0.6 for dim in dims.values())
    if veto:
        summary = f"{grade}：{veto}"
    else:
        summary = (
            f"{grade}：综合 {score} 分"
            f"（技术 {dims['tech'].score:.0f} / 项目 {dims['project'].score:.0f}"
            f" / 年限 {dims['tenure'].score:.0f} / 教育 {dims['education'].score:.0f}）"
        )

    return Verdict(
        grade=grade,
        score=score,
        dimensions=dict(dims),
        veto=veto,
        low_confidence=low_confidence,
        summary=summary,
    )
