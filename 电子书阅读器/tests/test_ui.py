"""Gradio 页面逻辑：走真实 FastAPI（ASGI 内存传输），校验产物与错误分支。"""

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
from ebook.ui import convert_files

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
                "api_base": "http://testserver",
                "output_dir": str(root / "ui"),
                "max_files": 2,
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

    def _convert(self, paths, use_llm=True, client=None):
        return convert_files(
            paths,
            use_llm,
            self.cfg,
            self.logger,
            client=client or self.client,
            output_dir=self.output_dir,
        )

    def test_converts_and_saves_epub(self):
        status, files = self._convert([str(self.source)])
        self.assertIn("完成 1/1", status)
        self.assertEqual(len(files), 1)
        saved = Path(files[0])
        self.assertTrue(saved.is_file())
        self.assertEqual(saved.suffix, ".epub")
        self.assertTrue(saved.read_bytes().startswith(b"PK"))

    def test_batch_two_files(self):
        other = self.source.with_name("第二本.txt")
        other.write_text(SAMPLE, encoding="utf-8")
        status, files = self._convert([str(self.source), str(other)])
        self.assertIn("完成 2/2", status)
        self.assertEqual(len(files), 2)

    def test_empty_selection(self):
        status, files = self._convert([])
        self.assertIn("请先选择", status)
        self.assertEqual(files, [])

    def test_rejects_non_txt(self):
        pdf = self.source.with_suffix(".pdf")
        pdf.write_bytes(b"pdf")
        status, files = self._convert([str(pdf)])
        self.assertIn("只支持 .txt", status)
        self.assertEqual(files, [])

    def test_rejects_too_many_files(self):
        paths = []
        for index in range(3):
            path = self.source.with_name(f"第{index}本.txt")
            path.write_text(SAMPLE, encoding="utf-8")
            paths.append(str(path))
        status, files = self._convert(paths)
        self.assertIn("一次最多 2 个", status)
        self.assertEqual(files, [])

    def test_backend_unreachable(self):
        def boom(request):
            raise httpx.ConnectError("后端没开", request=request)

        client = httpx.Client(transport=httpx.MockTransport(boom), base_url="http://testserver")
        try:
            status, files = self._convert([str(self.source)], client=client)
        finally:
            client.close()
        self.assertIn("调用后端失败", status)
        self.assertEqual(files, [])

    def test_backend_rejection_is_reported(self):
        short = self.source.with_name("太短.txt")
        short.write_text("太短", encoding="utf-8")
        status, files = self._convert([str(short)])
        self.assertIn("转换失败", status)
        self.assertEqual(files, [])


if __name__ == "__main__":
    unittest.main()
