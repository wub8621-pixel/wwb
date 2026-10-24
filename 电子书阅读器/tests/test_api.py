"""FastAPI 接口：健康检查、单本/批量转换、下载、错误分支。"""

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from config.loader import load_config

from ebook.api import create_app

SAMPLE = (
    "第一章 起点\n"
    + "他推开门，风从走廊尽头灌进来。\n" * 8
    + "第二章 转折\n"
    + "那封信在桌上放了三天，没有人拆。\n" * 8
    + "第三章 归途\n"
    + "火车驶过隧道，灯光一盏盏亮起。\n" * 8
)


class ApiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.cfg = load_config().with_overrides({
            "runtime": {"log_dir": str(root / "logs")},
            "server": {"runs_dir": str(root / "runs"), "max_upload_mb": 1},
        })
        self.app = create_app(self.cfg, llm=None)
        self.client = TestClient(self.app)

    def tearDown(self):
        self.tmp.cleanup()

    def _upload(self, name: str = "测试小说.txt", text: str = SAMPLE):
        return self.client.post(
            "/api/convert",
            files={"file": (name, text.encode("utf-8"), "text/plain")},
        )

    def test_root_redirects_to_docs(self):
        response = self.client.get("/", follow_redirects=False)
        self.assertEqual(response.status_code, 307)
        self.assertEqual(response.headers["location"], "/docs")
        self.assertEqual(self.client.get("/docs").status_code, 200)

    def test_health(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "ok")
        self.assertFalse(body["llm_enabled"])
        self.assertEqual(body["routes"]["convert"], "/api/convert")

    def test_convert_returns_epub(self):
        response = self._upload()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["content-type"], "application/epub+zip")
        self.assertTrue(response.content.startswith(b"PK"))
        self.assertEqual(response.headers["x-chapters"], "3")
        self.assertTrue(response.headers["x-run-id"])

    def test_convert_rejects_bad_extension(self):
        response = self.client.post(
            "/api/convert", files={"file": ("a.pdf", b"pdf", "application/pdf")}
        )
        self.assertEqual(response.status_code, 400)

    def test_convert_rejects_oversize(self):
        response = self._upload(text="字" * (1024 * 1024 + 10))
        self.assertEqual(response.status_code, 413)

    def test_convert_rejects_short_text(self):
        response = self._upload(text="太短")
        self.assertEqual(response.status_code, 422)

    def test_run_metadata_and_download(self):
        run_id = self._upload().headers["x-run-id"]

        meta = self.client.get(f"/api/runs/{run_id}")
        self.assertEqual(meta.status_code, 200)
        item = meta.json()["results"][0]
        self.assertTrue(item["ok"])

        download = self.client.get(f"/api/download/{run_id}/{item['output']}")
        self.assertEqual(download.status_code, 200)
        self.assertTrue(download.content.startswith(b"PK"))

    def test_download_rejects_traversal(self):
        run_id = self._upload().headers["x-run-id"]
        response = self.client.get(f"/api/download/{run_id}/..%2F..%2Fetc%2Fpasswd")
        self.assertIn(response.status_code, (400, 404))

        bad_run = self.client.get("/api/runs/..%2F..%2Fetc")
        self.assertIn(bad_run.status_code, (400, 404))

    def test_unknown_run_is_404(self):
        response = self.client.get("/api/runs/20260101-000000-deadbeef")
        self.assertEqual(response.status_code, 404)

    def test_batch(self):
        response = self.client.post(
            "/api/convert/batch",
            files=[
                ("files", ("第一本.txt", SAMPLE.encode("utf-8"), "text/plain")),
                ("files", ("第二本.txt", SAMPLE.encode("utf-8"), "text/plain")),
            ],
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(len(body["results"]), 2)
        self.assertTrue(all(item["ok"] for item in body["results"]))
        self.assertTrue(all(item["download_url"] for item in body["results"]))

    def test_batch_keeps_good_files_when_one_is_bad(self):
        response = self.client.post(
            "/api/convert/batch",
            files=[
                ("files", ("好的.txt", SAMPLE.encode("utf-8"), "text/plain")),
                ("files", ("坏的.pdf", b"pdf", "application/pdf")),
            ],
        )
        self.assertEqual(response.status_code, 200, response.text)
        results = response.json()["results"]
        self.assertTrue(results[0]["ok"])
        self.assertFalse(results[1]["ok"])


if __name__ == "__main__":
    unittest.main()
