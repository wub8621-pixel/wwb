"""CLI 输出结构的测试：不调用模型。"""

import unittest
from pathlib import Path

from resume_agent.main import _as_json
from resume_agent.schema import DimResult, ResumeDoc, Verdict


def make_state() -> dict:
    dims = {
        "tech": DimResult("tech", 82, "有落地经验", ["主导系统研发"], 0.9),
        "project": DimResult("project", 85, "有量化结果", ["响应时间降低32%"], 0.9),
        "tenure": DimResult("tenure", 100, "6 年 / 要求 1 年", ["6 年"], 1.0),
        "education": DimResult("education", 75, "本科", ["某某大学"], 0.6),
    }
    verdict = Verdict("优秀", 85.55, dims, None, False, "优秀：综合 85.55 分")
    return {
        "engine": "native",
        "attempts": [("unstructured", "文本过短(0字符)")],
        "doc": ResumeDoc(name="张三", target_role="后端工程师", flags=["院校层次未匹配"]),
        "years": 6.0,
        "verdict": verdict,
        "errors": [],
    }


class AsJsonTest(unittest.TestCase):
    def test_top_level_fields(self):
        data = _as_json(Path("a.pdf"), make_state())
        self.assertEqual(data["grade"], "优秀")
        self.assertEqual(data["engine"], "native")
        self.assertEqual(data["name"], "张三")
        self.assertEqual(data["fallbacks"][0]["engine"], "unstructured")
        self.assertFalse(data["low_confidence"])

    def test_dimensions_are_serialized(self):
        data = _as_json(Path("a.pdf"), make_state())
        self.assertEqual(set(data["dimensions"]), {"tech", "project", "tenure", "education"})
        self.assertEqual(data["dimensions"]["tech"]["evidence"], ["主导系统研发"])

    def test_missing_verdict_is_handled(self):
        data = _as_json(Path("a.pdf"), {"errors": ["解析失败"]})
        self.assertIsNone(data["grade"])
        self.assertTrue(data["low_confidence"])
        self.assertEqual(data["errors"], ["解析失败"])


if __name__ == "__main__":
    unittest.main()
