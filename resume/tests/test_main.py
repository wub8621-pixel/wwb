"""CLI 输出结构的测试：不调用模型。"""

import tempfile
import unittest
from pathlib import Path

from resume_agent.main import _as_json, collect_files, summary_rows
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


class CollectFilesTest(unittest.TestCase):
    def test_directory_expands_only_supported_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.pdf").write_bytes(b"x")
            (root / "b.docx").write_bytes(b"x")
            (root / "c.txt").write_text("x")
            (root / ".DS_Store").write_bytes(b"x")
            (root / "~$a.docx").write_bytes(b"x")

            self.assertEqual(
                [path.name for path in collect_files([root])], ["a.pdf", "b.docx"]
            )

    def test_subdirectory_is_skipped_unless_recursive(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.pdf").write_bytes(b"x")
            sub = root / "sub"
            sub.mkdir()
            (sub / "b.pdf").write_bytes(b"x")

            self.assertEqual([p.name for p in collect_files([root])], ["a.pdf"])
            self.assertEqual(
                [p.name for p in collect_files([root], recursive=True)],
                ["a.pdf", "b.pdf"],
            )

    def test_file_and_its_directory_are_deduped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "a.pdf"
            target.write_bytes(b"x")

            self.assertEqual(collect_files([target, root]), [target])

    def test_explicit_files_keep_their_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first, second = root / "z.pdf", root / "a.pdf"
            first.write_bytes(b"x")
            second.write_bytes(b"x")

            self.assertEqual(collect_files([first, second]), [first, second])


def state_with(grade: str | None, score: float = 80.0) -> dict:
    if grade is None:
        return {"verdict": None, "errors": ["解析失败"]}
    verdict = Verdict(grade, score, {}, None, False, "")
    return {"verdict": verdict, "errors": [], "years": 3.0}


class SummaryRowsTest(unittest.TestCase):
    def test_sorted_by_score_descending_with_failures_last(self):
        items = [
            (Path("低.pdf"), state_with("及格", 62.0)),
            (Path("坏.pdf"), state_with(None)),
            (Path("高.pdf"), state_with("优秀", 91.0)),
        ]
        rows = summary_rows(items)
        self.assertEqual([row["file"] for row in rows], ["高.pdf", "低.pdf", "坏.pdf"])
        self.assertTrue(rows[-1]["failed"])
        self.assertEqual(rows[-1]["grade"], "失败")
        self.assertIsNone(rows[-1]["score"])


if __name__ == "__main__":
    unittest.main()
