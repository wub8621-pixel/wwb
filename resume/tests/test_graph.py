"""LangGraph 主图的测试：注入假解析器和假模型，不联网、不读真实文件。"""

import unittest
from pathlib import Path

from resume_agent.graph import Assessment, build_graph, verify_evidence
from resume_agent.parse import ParseError, ParseResult
from resume_agent.quality import assess_quality

LINES = [
    "张三", "后端工程师",
    "教育信息", "某某大学", "2014.09-2018.07", "本科",
    "工作经历", "某公司｜后端开发｜2019.01-2023.01",
    "主导支付系统重构，QPS 提升 3 倍，支撑日均百万订单",
    "项目经历", "负责订单中心拆分，接口耗时下降 40%",
    "技能", "Python", "FastAPI", "Redis",
] + ["具备扎实的后端工程化能力，能独立负责核心模块"] * 8
TEXT = "\n".join(LINES)


def parser_from(lines: list[str], engine: str = "native"):
    def parser(path: Path) -> ParseResult:
        quality = assess_quality(lines, [lines])
        return ParseResult(
            Path(path), engine, lines, [lines], quality,
            (("unstructured", "文本过短(0字符)"),) if engine == "native" else (),
        )

    return parser


def failing_parser(path: Path) -> ParseResult:
    raise ParseError("所有解析引擎均不可用：unstructured: 空结果 | native: 乱码")


class StubAssessor:
    def __init__(self, tech: Assessment | None = None, project: Assessment | None = None):
        self.tech = tech or Assessment(90, "架构能力清晰", ["主导支付系统重构"], 1.0)
        self.project = project or Assessment(90, "有量化结果", ["接口耗时下降 40%"], 1.0)
        self.calls: list[str] = []

    def assess(self, dimension: str, resume_text: str, rubric: str) -> Assessment:
        self.calls.append(dimension)
        return self.tech if dimension == "tech" else self.project


def invoke(parser, assessor, path: str = "resume.pdf") -> dict:
    app = build_graph(assessor, parser=parser)
    return app.invoke({"path": path, "dims": {}, "errors": []})


class HappyPathTest(unittest.TestCase):
    def setUp(self):
        self.assessor = StubAssessor()
        self.state = invoke(parser_from(LINES), self.assessor)

    def test_runs_all_four_dimensions(self):
        self.assertEqual(set(self.state["dims"]), {"tech", "project", "tenure", "education"})

    def test_produces_verdict(self):
        verdict = self.state["verdict"]
        self.assertIsNotNone(verdict)
        self.assertEqual(verdict.grade, "优秀")
        self.assertAlmostEqual(verdict.score, 90.75, places=2)

    def test_records_engine_and_fallback(self):
        self.assertEqual(self.state["engine"], "native")
        self.assertEqual(self.state["attempts"][0][0], "unstructured")

    def test_model_is_called_once_per_dimension(self):
        self.assertCountEqual(self.assessor.calls, ["tech", "project"])

    def test_tenure_comes_from_code_not_model(self):
        self.assertAlmostEqual(self.state["years"], 49 / 12, places=2)


class ParseFailureTest(unittest.TestCase):
    def test_stops_before_assessment(self):
        assessor = StubAssessor()
        state = invoke(failing_parser, assessor)
        self.assertTrue(state["errors"])
        self.assertIsNone(state.get("verdict"))
        self.assertEqual(assessor.calls, [])


class EvidenceTest(unittest.TestCase):
    def test_unverified_evidence_is_dropped_and_lowers_confidence(self):
        assessor = StubAssessor(
            tech=Assessment(90, "很好", ["这句话简历里根本没有"], 1.0),
            project=Assessment(90, "很好", ["接口耗时下降 40%"], 1.0),
        )
        state = invoke(parser_from(LINES), assessor)
        tech = state["dims"]["tech"]
        self.assertEqual(tech.evidence, [])
        self.assertLessEqual(tech.confidence, 0.3)
        self.assertTrue(state["verdict"].low_confidence)

    def test_whitespace_is_ignored_when_matching(self):
        self.assertEqual(
            verify_evidence(["主导支付系统 重构"], TEXT), ["主导支付系统 重构"]
        )

    def test_paraphrased_quote_is_rejected(self):
        self.assertEqual(verify_evidence(["主导了支付系统的重构"], TEXT), [])


class VetoTest(unittest.TestCase):
    def test_short_experience_forces_pass(self):
        lines = [
            "李四", "后端工程师",
            "教育信息", "某某大学", "2018.09-2022.07", "本科",
            "工作经历", "某公司｜后端开发｜2023.01-2023.06",
        ] + ["具备扎实的后端工程化能力，能独立负责核心模块"] * 8
        state = invoke(parser_from(lines), StubAssessor())
        self.assertEqual(state["verdict"].grade, "pass")
        self.assertIsNotNone(state["verdict"].veto)


class ScoreClampTest(unittest.TestCase):
    def test_model_score_is_clamped_to_range(self):
        assessor = StubAssessor(tech=Assessment(150, "离谱", ["主导支付系统重构"]))
        state = invoke(parser_from(LINES), assessor)
        self.assertEqual(state["dims"]["tech"].score, 100.0)


if __name__ == "__main__":
    unittest.main()
