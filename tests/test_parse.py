"""主备解析链路的测试：用注入的假引擎，不读真实文件。"""

import unittest
from pathlib import Path

from resume_agent.parse import ParseError, parse_resume

GOOD = ["教育背景", "燕京理工学院 本科"] + ["具备扎实的后端工程化能力"] * 20
PATH = Path("fake.pdf")


def ok_engine(path):
    return GOOD, [GOOD]


def empty_engine(path):
    return [], []


def garbled_engine(path):
    return ["\ue000" * 300], [["\ue000" * 300]]


def broken_engine(path):
    raise RuntimeError("未安装 unstructured")


class ParseResumeTest(unittest.TestCase):
    def test_primary_engine_wins_when_healthy(self):
        result = parse_resume(
            PATH,
            ("unstructured", "native"),
            {"unstructured": ok_engine, "native": broken_engine},
        )
        self.assertEqual(result.engine, "unstructured")
        self.assertEqual(result.attempts, ())

    def test_falls_back_when_primary_returns_empty(self):
        result = parse_resume(
            PATH,
            ("unstructured", "native"),
            {"unstructured": empty_engine, "native": ok_engine},
        )
        self.assertEqual(result.engine, "native")
        self.assertIn("文本过短", result.attempts[0][1])

    def test_falls_back_when_primary_returns_garbled(self):
        result = parse_resume(
            PATH,
            ("unstructured", "native"),
            {"unstructured": garbled_engine, "native": ok_engine},
        )
        self.assertEqual(result.engine, "native")
        self.assertIn("乱码", result.attempts[0][1])

    def test_falls_back_when_primary_raises(self):
        result = parse_resume(
            PATH,
            ("unstructured", "native"),
            {"unstructured": broken_engine, "native": ok_engine},
        )
        self.assertEqual(result.engine, "native")
        self.assertIn("RuntimeError", result.attempts[0][1])

    def test_records_every_failed_attempt(self):
        result = parse_resume(
            PATH,
            ("unstructured", "native"),
            {"unstructured": empty_engine, "native": ok_engine},
        )
        self.assertEqual([name for name, _ in result.attempts], ["unstructured"])

    def test_raises_when_all_engines_fail(self):
        with self.assertRaises(ParseError) as ctx:
            parse_resume(
                PATH,
                ("unstructured", "native"),
                {"unstructured": empty_engine, "native": garbled_engine},
            )
        self.assertIn("unstructured", str(ctx.exception))
        self.assertIn("native", str(ctx.exception))

    def test_text_property_drops_blank_lines(self):
        lines = ["教育背景", "", "  ", "本科"] + ["具备扎实的后端工程化能力"] * 20
        result = parse_resume(PATH, ("native",), {"native": lambda p: (lines, [lines])})
        self.assertNotIn("", result.text.splitlines())
        self.assertTrue(result.text.startswith("教育背景\n本科"))


if __name__ == "__main__":
    unittest.main()
