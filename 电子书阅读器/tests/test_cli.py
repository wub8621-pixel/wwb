"""命令行端到端：目录 → EPUB + 日志 + 运行报告。"""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from config.loader import load_config

from ebook.cli import main

BOOK = (
    "第一章 开始\n" + "正文内容" * 30 + "\n"
    "第二章 发展\n" + "正文内容" * 30 + "\n"
    "第三章 结束\n" + "正文内容" * 30
)


class CliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.input = self.root / "books"
        self.input.mkdir()
        self.output = self.root / "out"
        self.logs = self.root / "logs"
        self.cfg = load_config().with_overrides({
            "runtime": {"output_dir": str(self.output), "log_dir": str(self.logs)},
        })

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, args):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(args, cfg=self.cfg)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_generates_epub_log_and_report(self):
        (self.input / "三体.txt").write_text(BOOK, encoding="utf-8")
        code, out, _ = self.run_cli([str(self.input), "--no-llm"])

        self.assertEqual(code, 0)
        self.assertIn("三体.txt", out)
        self.assertTrue((self.output / "三体.epub").exists())
        self.assertTrue((self.logs / "ebook.log").exists())
        reports = list((self.logs / "reports").glob("run-*.json"))
        self.assertEqual(len(reports), 1)
        payload = json.loads(reports[0].read_text(encoding="utf-8"))
        self.assertTrue(payload["results"][0]["ok"])

    def test_empty_directory_returns_code_2(self):
        code, _, err = self.run_cli([str(self.input), "--no-llm"])
        self.assertEqual(code, 2)
        self.assertIn("没有找到 txt", err)

    def test_missing_directory_returns_code_2(self):
        code, _, err = self.run_cli([str(self.root / "nope"), "--no-llm"])
        self.assertEqual(code, 2)
        self.assertIn("目录不存在", err)

    def test_json_output(self):
        (self.input / "a.txt").write_text(BOOK, encoding="utf-8")
        code, out, _ = self.run_cli([str(self.input), "--no-llm", "--json"])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload[0]["chapters"], 3)

    def test_one_book_per_txt(self):
        (self.input / "a.txt").write_text(BOOK, encoding="utf-8")
        (self.input / "b.txt").write_text(BOOK, encoding="utf-8")
        code, _, _ = self.run_cli([str(self.input), "--no-llm"])
        self.assertEqual(code, 0)
        self.assertTrue((self.output / "a.epub").exists())
        self.assertTrue((self.output / "b.epub").exists())


if __name__ == "__main__":
    unittest.main()
