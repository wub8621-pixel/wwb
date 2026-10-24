"""Web 界面的测试：表单解析、大小/格式校验、HTTP 往返。不调用模型。"""

import json
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from resume_agent.schema import DimResult, ResumeDoc, Verdict
from web.server import (
    AssessServer,
    UploadError,
    assess_upload,
    basename,
    is_lan_address,
    parse_upload,
)

# 本机环境装了 HTTP 代理，访问 127.0.0.1 也会被拦成 502，测试里显式绕过
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def multipart(filename: str, data: bytes, field: str = "file") -> tuple[bytes, str]:
    boundary = "----resumeagenttest"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'
        "Content-Type: application/octet-stream\r\n\r\n"
    ).encode() + data + f"\r\n--{boundary}--\r\n".encode()
    return body, f"multipart/form-data; boundary={boundary}"


class FakeApp:
    """替身图：记录收到的文件内容，返回固定的评估结果。"""

    def __init__(self):
        self.content = b""
        self.calls = 0

    def invoke(self, state):
        self.calls += 1
        self.content = Path(state["path"]).read_bytes()
        dims = {
            "tech": DimResult("tech", 70, "有落地经验", ["主导系统研发"], 0.9),
            "project": DimResult("project", 72, "有量化结果", ["响应时间降低32%"], 0.9),
            "tenure": DimResult("tenure", 100, "3 年 / 要求 1 年", ["3 年"], 1.0),
            "education": DimResult("education", 75, "本科", ["某某大学"], 0.6),
        }
        verdict = Verdict("一般", 72.0, dims, None, False, "一般：综合 72.0 分")
        return {
            "engine": "native",
            "attempts": [],
            "doc": ResumeDoc(name="张三", target_role="后端工程师"),
            "years": 3.0,
            "verdict": verdict,
            "errors": [],
        }


class LanAddressTest(unittest.TestCase):
    def test_private_lan_address_is_usable(self):
        self.assertTrue(is_lan_address("192.168.1.2"))
        self.assertTrue(is_lan_address("10.0.0.8"))

    def test_loopback_and_proxy_fake_ip_are_rejected(self):
        self.assertFalse(is_lan_address("127.0.0.1"))
        # 代理软件 TUN 的 fake-ip 段，写在界面上会打不开
        self.assertFalse(is_lan_address("198.18.0.0"))
        self.assertFalse(is_lan_address("198.18.1.14"))

    def test_public_and_invalid_addresses_are_rejected(self):
        self.assertFalse(is_lan_address("8.8.8.8"))
        self.assertFalse(is_lan_address("not-an-ip"))


class ParseUploadTest(unittest.TestCase):
    def test_extracts_filename_and_bytes(self):
        body, content_type = multipart("简历.pdf", b"%PDF-1.4 data")
        filename, data = parse_upload(content_type, body)
        self.assertEqual(filename, "简历.pdf")
        self.assertEqual(data, b"%PDF-1.4 data")

    def test_strips_path_from_filename(self):
        body, content_type = multipart("文件夹/简历.docx", b"docx")
        filename, _ = parse_upload(content_type, body)
        self.assertEqual(filename, "简历.docx")

    def test_rejects_non_multipart(self):
        with self.assertRaises(UploadError):
            parse_upload("application/json", b"{}")

    def test_rejects_body_without_file(self):
        boundary = "----resumeagenttest"
        body = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="note"\r\n\r\n'
            f"hello\r\n--{boundary}--\r\n"
        ).encode()
        with self.assertRaises(UploadError):
            parse_upload(f"multipart/form-data; boundary={boundary}", body)


class BasenameTest(unittest.TestCase):
    def test_windows_separator_is_stripped(self):
        # email 解析会把反斜杠当转义吃掉，所以单独测这一层
        self.assertEqual(basename("C:\\Users\\x\\简历.docx"), "简历.docx")

    def test_plain_name_is_kept(self):
        self.assertEqual(basename("简历.pdf"), "简历.pdf")


class AssessUploadTest(unittest.TestCase):
    def test_runs_graph_and_keeps_original_name(self):
        app = FakeApp()
        result = assess_upload(app, "武彪的简历.pdf", b"%PDF-fake")
        self.assertEqual(result["file"], "武彪的简历.pdf")
        self.assertEqual(result["path"], "武彪的简历.pdf")
        self.assertEqual(app.content, b"%PDF-fake")
        self.assertEqual(result["grade"], "一般")
        self.assertEqual(app.calls, 1)

    def test_rejects_unsupported_suffix(self):
        with self.assertRaises(UploadError):
            assess_upload(FakeApp(), "简历.txt", b"x")

    def test_rejects_empty_file(self):
        with self.assertRaises(UploadError):
            assess_upload(FakeApp(), "简历.pdf", b"")

    def test_rejects_oversized_file(self):
        with self.assertRaises(UploadError):
            assess_upload(FakeApp(), "简历.pdf", b"x" * (16 * 1024 * 1024))


class HttpTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = FakeApp()
        cls.server = AssessServer(("127.0.0.1", 0), cls.app)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def test_index_is_served(self):
        with OPENER.open(self.url("/"), timeout=5) as response:
            html = response.read().decode("utf-8")
        self.assertEqual(response.status, 200)
        self.assertIn("简历评估", html)

    def test_health(self):
        with OPENER.open(self.url("/api/health"), timeout=5) as response:
            self.assertEqual(json.load(response), {"ok": True})

    def test_upload_returns_assessment(self):
        body, content_type = multipart("简历.pdf", b"%PDF-fake")
        request = urllib.request.Request(
            self.url("/api/assess"), data=body, headers={"Content-Type": content_type}
        )
        with OPENER.open(request, timeout=5) as response:
            data = json.load(response)
        self.assertEqual(response.status, 200)
        self.assertEqual(data["file"], "简历.pdf")
        self.assertEqual(data["grade"], "一般")
        self.assertIn("tech", data["dimensions"])

    def test_unsupported_file_returns_400(self):
        body, content_type = multipart("简历.txt", b"x")
        request = urllib.request.Request(
            self.url("/api/assess"), data=body, headers={"Content-Type": content_type}
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            OPENER.open(request, timeout=5)
        self.assertEqual(ctx.exception.code, 400)
        payload = json.loads(ctx.exception.read())
        self.assertIn("只支持", payload["error"])

    def test_unknown_route_returns_404(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            OPENER.open(self.url("/nope"), timeout=5)
        self.assertEqual(ctx.exception.code, 404)


if __name__ == "__main__":
    unittest.main()
