"""评测脚本的测试：模板生成、标注读取、报告计算。不调用模型。"""

import csv
import tempfile
import unittest
from pathlib import Path

from eval.run_eval import (
    LabelRow,
    build_report,
    init_template,
    labeled_files,
    load_labels,
)


def write_csv(path: Path, rows: list[dict]) -> None:
    fieldnames = ["file", "annotator", "tech", "project", "education", "tenure", "grade", "notes"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def fake_result(path: Path, grade: str, tech: float = 80) -> dict:
    return {
        "file": str(path),
        "grade": grade,
        "score": 80.0,
        "low_confidence": False,
        "dims": {"tech": tech, "project": 80, "education": 75, "tenure": 100},
    }


class InitTemplateTest(unittest.TestCase):
    def test_writes_one_row_per_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.pdf").write_bytes(b"x")
            (root / "b.docx").write_bytes(b"x")
            (root / "ignore.txt").write_text("x")
            (root / ".~lock.docx").write_bytes(b"x")

            out = root / "labels.csv"
            self.assertEqual(init_template(root, out), 0)

            with out.open(encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            names = [Path(row["file"]).name for row in rows]
            self.assertEqual(names, ["a.pdf", "b.docx"])
            self.assertTrue(all(Path(row["file"]).is_absolute() for row in rows))
            self.assertEqual(rows[0]["grade"], "")

    def test_empty_directory_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(init_template(Path(tmp), Path(tmp) / "labels.csv"), 1)


class LoadLabelsTest(unittest.TestCase):
    def test_parses_scores_and_flags(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "labels.csv"
            write_csv(path, [{
                "file": "a.pdf", "annotator": "me", "tech": "85", "project": "88",
                "education": "", "tenure": "100", "grade": "优秀", "notes": "ok",
            }])
            (row,) = load_labels(path)
            self.assertEqual(row.tech, 85.0)
            self.assertIsNone(row.education)
            self.assertEqual(row.grade, "优秀")

    def test_rejects_unknown_grade(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "labels.csv"
            write_csv(path, [{"file": "a.pdf", "grade": "很好"}])
            with self.assertRaises(ValueError):
                load_labels(path)


class LabeledFilesTest(unittest.TestCase):
    def test_only_labeled_rows_are_run(self):
        base = Path("/tmp/eval_case")
        rows = [
            LabelRow("a.pdf", grade="优秀"),
            LabelRow("b.pdf", grade=""),
            LabelRow("c.pdf", grade="一般"),
        ]
        self.assertEqual(
            labeled_files(rows, base), [base / "a.pdf", base / "c.pdf"]
        )

    def test_duplicate_rows_run_once(self):
        base = Path("/tmp/eval_case")
        rows = [LabelRow("a.pdf", "me", grade="优秀"), LabelRow("a.pdf", "peer", grade="一般")]
        self.assertEqual(labeled_files(rows, base), [base / "a.pdf"])


class BuildReportTest(unittest.TestCase):
    def setUp(self):
        self.base = Path("/tmp/eval_case")
        self.rows = [
            LabelRow("a.pdf", "me", tech=85, grade="优秀"),
            LabelRow("b.pdf", "me", tech=70, grade="一般"),
            LabelRow("c.pdf", "me", tech=60, grade="pass"),
        ]
        self.results = {
            str(self.base / "a.pdf"): fake_result(self.base / "a.pdf", "优秀", 86),
            str(self.base / "b.pdf"): fake_result(self.base / "b.pdf", "及格", 72),
            str(self.base / "c.pdf"): fake_result(self.base / "c.pdf", "pass", 58),
        }

    def test_report_contains_core_metrics(self):
        report = build_report(self.rows, self.results, self.base, repeat=1)
        self.assertIn("精确一致率", report)
        self.assertIn("二次加权 kappa", report)
        self.assertIn("技术成熟度", report)
        self.assertIn("相邻一致率", report)

    def test_missing_result_is_reported_as_skipped(self):
        results = dict(self.results)
        results.pop(str(self.base / "b.pdf"))
        report = build_report(self.rows, results, self.base, repeat=1)
        self.assertIn("跳过：1", report)
        self.assertIn("b.pdf", report)

    def test_inter_rater_is_reported_when_two_annotators(self):
        rows = self.rows + [LabelRow("a.pdf", "peer", grade="一般")]
        report = build_report(rows, self.results, self.base, repeat=1)
        self.assertIn("1 对", report)

    def test_empty_labels(self):
        report = build_report([], {}, self.base, repeat=1)
        self.assertIn("没有可评测的数据", report)


if __name__ == "__main__":
    unittest.main()
