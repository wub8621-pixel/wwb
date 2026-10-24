"""LangGraph 编排：正常路径、LLM 兜底路径、兜底失败路径。"""

import logging
import tempfile
import unittest
from pathlib import Path

from config.loader import load_config

from ebook.chapters import Chapter
from ebook.graph import build_graph
from ebook.llm import LLMError

logger = logging.getLogger("ebook.test")
logger.addHandler(logging.NullHandler())

HEALTHY = (
    "第一章 开始\n" + "正文内容" * 30 + "\n"
    "第二章 发展\n" + "正文内容" * 30 + "\n"
    "第三章 结束\n" + "正文内容" * 30
)
MESSY = "没有任何章节标题的正文段落。\n" * 500


class FakeLLM:
    def __init__(self, chapters=None, metadata=None, error=None):
        self.chapters = chapters or []
        self.metadata = metadata or {}
        self.error = error
        self.repair_calls = 0

    def repair_chapters(self, text, reasons, **kwargs):
        self.repair_calls += 1
        if self.error:
            raise self.error
        return self.chapters

    def extract_metadata(self, head, filename):
        if self.error:
            raise self.error
        return self.metadata


class GraphTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.out = self.root / "out"
        self.cfg = load_config().with_overrides({
            "runtime": {"output_dir": str(self.out)},
        })

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, text, name="book.txt"):
        path = self.root / name
        path.write_text(text, encoding="utf-8")
        return path

    def test_healthy_book_skips_llm_and_builds_epub(self):
        state = self.run_graph(HEALTHY)
        self.assertFalse(state["quality"]["needs_llm"])
        self.assertEqual(len(state["chapters"]), 3)
        self.assertTrue(Path(state["output_path"]).exists())

    def test_metadata_uses_filename(self):
        state = self.run_graph(HEALTHY, name_source="三体.txt")
        self.assertEqual(state["metadata"]["title"], "三体")

    def test_messy_book_triggers_llm_repair(self):
        fake = FakeLLM(
            chapters=[Chapter("第一章 起点", "正文一"), Chapter("第二章 终点", "正文二")],
            metadata={"title": "", "author": "模型作者", "description": "模型简介"},
        )
        state = self.run_graph(MESSY, llm=fake)
        self.assertTrue(state["quality"]["needs_llm"])
        self.assertTrue(state["repaired"])
        self.assertEqual(len(state["chapters"]), 2)
        self.assertEqual(state["metadata"]["author"], "模型作者")
        self.assertEqual(fake.repair_calls, 1)

    def test_repair_failure_keeps_regex_result(self):
        fake = FakeLLM(error=LLMError("网络不通"))
        state = self.run_graph(MESSY, llm=fake)
        self.assertFalse(state.get("repaired", False))
        self.assertEqual(len(state["chapters"]), 1)
        self.assertTrue(any("兜底失败" in warning for warning in state["warnings"]))

    def test_missing_llm_is_reported_not_fatal(self):
        state = self.run_graph(MESSY, llm=None)
        self.assertEqual(len(state["chapters"]), 1)
        self.assertTrue(any("模型不可用" in warning for warning in state["warnings"]))

    def run_graph(self, text, llm=None, name_source="book.txt"):
        path = self.write(text, name_source)
        graph = build_graph(self.cfg, logger, llm)
        return graph.invoke(
            {"source": str(path), "output_dir": str(self.out)},
            config={"recursion_limit": self.cfg.get("graph.recursion_limit")},
        )


if __name__ == "__main__":
    unittest.main()
