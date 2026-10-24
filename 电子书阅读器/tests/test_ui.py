"""Gradio 页面逻辑：走真实 FastAPI（本地 uvicorn），校验自动转换、预览与护眼模式。"""

import logging
import tempfile
import threading
import time
import unittest
from pathlib import Path

import httpx
import uvicorn

from config.loader import load_config

from ebook.api import create_app
from ebook.ui import _card_html, convert_and_preview, read_book, rerender

SAMPLE = (
    "第一章 起点\n"
    + "他推开门，风从走廊尽头灌进来。\n" * 8
    + "第二章 转折\n"
    + "那封信在桌上放了三天，没有人拆。\n" * 8
    + "第三章 归途\n"
    + "火车驶过隧道，灯光一盏盏亮起。\n" * 8
)


class UiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.cfg = load_config().with_overrides({
            "runtime": {"log_dir": str(root / "logs")},
            "server": {"runs_dir": str(root / "runs")},
            "frontend": {
                "api_base": "http://127.0.0.1:0",
                "output_dir": str(root / "ui"),
                "max_files": 2,
                "preview_chars": 200,
            },
        })
        self.output_dir = root / "ui"

        # 起一个真实 uvicorn（随机端口），页面逻辑走完整 HTTP 链路
        app = create_app(self.cfg, llm=None)
        self.server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning", lifespan="off")
        )
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        deadline = time.time() + 10
        while not self.server.started and time.time() < deadline:
            time.sleep(0.02)
        self.assertTrue(self.server.started, "测试服务器未能启动")
        port = self.server.servers[0].sockets[0].getsockname()[1]
        self.cfg = self.cfg.with_overrides({
            "frontend": {"api_base": f"http://127.0.0.1:{port}"}
        })
        self.client = httpx.Client(timeout=30)

        self.logger = logging.getLogger("test-ui")
        self.logger.addHandler(logging.NullHandler())

        self.source = root / "测试小说.txt"
        self.source.write_text(SAMPLE, encoding="utf-8")

    def tearDown(self):
        self.client.close()
        self.server.should_exit = True
        self.thread.join(timeout=5)
        self.tmp.cleanup()

    def _convert(self, paths, use_llm=True, eye=False, client=None):
        return convert_and_preview(
            paths,
            use_llm,
            eye,
            self.cfg,
            self.logger,
            client=client or self.client,
            output_dir=self.output_dir,
        )

    def test_auto_convert_returns_book_preview(self):
        status, state, card, cover, files = self._convert([str(self.source)])
        self.assertIn("已生成 1/1", status)
        self.assertIn("正文预览", card)
        self.assertIn("第一章", card)
        self.assertIn("测试小说", card)
        self.assertTrue(Path(cover).is_file())
        self.assertEqual(len(files), 1)
        self.assertTrue(Path(files[0]).read_bytes().startswith(b"PK"))
        self.assertFalse(card.startswith('<div class="book-card eye"'))

    def test_preview_metadata_and_chapters(self):
        _, state, _, _, files = self._convert([str(self.source)])
        book = state["book"]
        self.assertEqual(book["title"], "测试小说")
        self.assertEqual(len(book["chapters"]), 3)
        self.assertIn("他推开门", book["preview"])
        self.assertLessEqual(len(book["preview"]), 202)  # 200 字 + 省略号
        again = read_book(Path(files[0]), 50)
        self.assertEqual(again["title"], "测试小说")

    def test_eye_mode_rerenders_without_converting(self):
        _, state, card, _, _ = self._convert([str(self.source)])
        eye_card = rerender(state, True)
        self.assertIn("book-card eye", eye_card)
        self.assertIn("第一章", eye_card)
        self.assertNotIn("book-card eye", card)

    def test_eye_mode_placeholder(self):
        self.assertIn("护眼", _card_html(None, True) + "护眼")
        self.assertEqual(rerender(None, True), _card_html(None, True))

    def test_batch_two_files(self):
        other = self.source.with_name("第二本.txt")
        other.write_text(SAMPLE, encoding="utf-8")
        status, _, card, _, files = self._convert([str(self.source), str(other)])
        self.assertIn("已生成 2/2", status)
        self.assertIn("本次共生成 2 本", card)
        self.assertEqual(len(files), 2)

    def test_empty_selection(self):
        status, state, card, cover, files = self._convert([])
        self.assertIn("上传", status)
        self.assertIsNone(state)
        self.assertIn("上传 txt 后自动排版", card)
        self.assertIsNone(cover)
        self.assertEqual(files, [])

    def test_rejects_non_txt(self):
        pdf = self.source.with_suffix(".pdf")
        pdf.write_bytes(b"pdf")
        status, _, _, _, files = self._convert([str(pdf)])
        self.assertIn("只支持 .txt", status)
        self.assertEqual(files, [])

    def test_rejects_too_many_files(self):
        paths = []
        for index in range(3):
            path = self.source.with_name(f"第{index}本.txt")
            path.write_text(SAMPLE, encoding="utf-8")
            paths.append(str(path))
        status, _, _, _, files = self._convert(paths)
        self.assertIn("一次最多 2 个", status)
        self.assertEqual(files, [])

    def test_backend_unreachable(self):
        def boom(request):
            raise httpx.ConnectError("后端没开", request=request)

        client = httpx.Client(transport=httpx.MockTransport(boom), base_url="http://testserver")
        try:
            status, state, _, _, files = self._convert([str(self.source)], client=client)
        finally:
            client.close()
        self.assertIn("调用后端失败", status)
        self.assertIsNone(state)
        self.assertEqual(files, [])

    def test_backend_rejection_is_reported(self):
        short = self.source.with_name("太短.txt")
        short.write_text("太短", encoding="utf-8")
        status, state, _, _, files = self._convert([str(short)])
        self.assertIn("转换失败", status)
        self.assertIsNone(state)
        self.assertEqual(files, [])

    def test_card_escapes_html(self):
        state = {
            "book": {
                "title": "<script>alert(1)</script>",
                "author": "作者",
                "description": "",
                "chapters": ["第一章"],
                "preview": "<b>正文</b>",
                "cover": None,
            },
            "extra": "",
        }
        card = rerender(state, False)
        self.assertNotIn("<script>", card)
        self.assertIn("&lt;script&gt;", card)


if __name__ == "__main__":
    unittest.main()
