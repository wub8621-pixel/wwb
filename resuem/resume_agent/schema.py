"""领域数据结构：结构化简历、维度结果、最终结论。

这些类型是 LangGraph State 的骨架，必须保持可序列化、无业务逻辑。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .dates import DateRange

# 章节规范名 → 简历里可能出现的写法
SECTION_ALIASES: dict[str, tuple[str, ...]] = {
    "education": ("教育", "教育信息", "教育背景"),
    "work": ("工作经历", "工作经验"),
    "project": ("项目", "项目经历"),
    "skills": ("技能", "掌握技能", "专业技能"),
    "summary": ("个人优势", "个人总结", "自我评价"),
    "certs": ("证书", "荣誉"),
    "intent": ("求职意向",),
    "basic": ("个人信息",),
}


@dataclass
class WorkEntry:
    company: str = ""
    title: str = ""
    period: DateRange | None = None
    is_internship: bool = False
    lines: list[str] = field(default_factory=list)


@dataclass
class EducationEntry:
    school: str = ""
    degree: str = ""
    major: str = ""
    period: DateRange | None = None
    raw: str = ""


@dataclass
class ResumeDoc:
    name: str = ""
    target_role: str = ""
    raw_text: str = ""
    sections: dict[str, list[str]] = field(default_factory=dict)
    work: list[WorkEntry] = field(default_factory=list)
    education: list[EducationEntry] = field(default_factory=list)
    dates: list[DateRange] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)

    def section(self, key: str) -> list[str]:
        return self.sections.get(key, [])


@dataclass
class DimResult:
    """单个维度的评估结果。score 用 0-100，evidence 必须是简历原文。"""

    key: str
    score: float
    reason: str = ""
    evidence: list[str] = field(default_factory=list)
    confidence: float = 1.0

    @property
    def has_evidence(self) -> bool:
        return bool(self.evidence)


@dataclass
class Verdict:
    grade: str
    score: float
    dimensions: dict[str, DimResult] = field(default_factory=dict)
    veto: str | None = None
    low_confidence: bool = False
    summary: str = ""
