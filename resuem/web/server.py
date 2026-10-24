"""本地 Web 服务：上传简历 → 走同一条 LangGraph 评估链路 → 返回 JSON。

只用标准库，不额外引入 Web 框架：这个界面是给自己看结果的，
多一个依赖就多一份安装成本。

    python -m web.server            # 监听 0.0.0.0:7880
    python -m web.server --port 8000
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import socket
import sys
import tempfile
import threading
from email.parser import BytesParser
from email.policy import default as default_policy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from resume_agent.assessor import from_env
from resume_agent.graph import build_graph
from resume_agent.main import _as_json
from resume_agent.rubrics import REQUIRED_YEARS

HERE = Path(__file__).resolve().parent
INDEX = HERE / "index.html"
SUPPORTED = {".pdf", ".docx"}
MAX_BYTES = 15 * 1024 * 1024
DEFAULT_PORT = 7880


class UploadError(ValueError):
    """请求本身不合法（格式、大小、字段），返回 400 而不是 500。"""


def basename(filename: str) -> str:
    """只保留文件名部分：浏览器和部分客户端会带上路径，Windows 用反斜杠。"""
    return filename.replace("\\", "/").rsplit("/", 1)[-1].strip()


def parse_upload(content_type: str, body: bytes) -> tuple[str, bytes]:
    """从 multipart/form-data 中取出第一个文件字段。

    Python 的 http.server 不做表单解析，这里借 email 解析器完成——
    比手写 boundary 切分可靠得多（浏览器会在字段里带引号、文件名、UTF-8 编码）。
    """
    if "multipart/form-data" not in (content_type or ""):
        raise UploadError("请用 multipart/form-data 上传文件")

    message = BytesParser(policy=default_policy).parsebytes(
        b"Content-Type: "
        + content_type.encode("utf-8", "replace")
        + b"\r\nMIME-Version: 1.0\r\n\r\n"
        + body
    )
    if not message.is_multipart():
        raise UploadError("上传内容不是合法的表单数据")

    for part in message.iter_parts():
        filename = part.get_filename()
        if filename is None:
            continue
        payload = part.get_payload(decode=True) or b""
        return basename(filename), payload

    raise UploadError("表单里没有找到文件字段")


def assess_upload(app: Any, filename: str, data: bytes) -> dict[str, Any]:
    """把上传的字节落到临时文件，跑图，再按原始文件名返回结果。"""
    safe_name = basename(filename) or "resume"
    suffix = Path(safe_name).suffix.lower()
    if suffix not in SUPPORTED:
        raise UploadError(f"只支持 {', '.join(sorted(SUPPORTED))} 格式")
    if not data:
        raise UploadError("文件是空的")
    if len(data) > MAX_BYTES:
        raise UploadError(f"文件超过 {MAX_BYTES // 1024 // 1024}MB")

    with tempfile.TemporaryDirectory() as tmp:
        # 用固定名，避免上传文件名里的路径字符影响落盘
        path = Path(tmp) / f"upload{suffix}"
        path.write_bytes(data)
        state = app.invoke({"path": str(path), "dims": {}, "errors": []})
        result = _as_json(path, state)

    # 临时路径对用户没有意义，换回上传时的文件名
    result["path"] = safe_name
    result["file"] = safe_name
    return result


class Handler(BaseHTTPRequestHandler):
    server_version = "ResumeAgent/1.0"

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 的约定
        route = urlparse(self.path).path
        if route in ("/", "/index.html"):
            try:
                self._send(200, "text/html; charset=utf-8", INDEX.read_bytes())
            except OSError:
                self._send(500, "text/plain; charset=utf-8", "缺少界面文件".encode())
            return
        if route == "/api/health":
            self._json(200, {"ok": True})
            return
        self._send(404, "text/plain; charset=utf-8", "未找到".encode())

    def do_POST(self) -> None:  # noqa: N802
        if urlparse(self.path).path != "/api/assess":
            self._json(404, {"error": "未找到"})
            return

        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self._json(400, {"error": "Content-Length 不合法"})
            return

        if length <= 0:
            self._json(400, {"error": "请求体为空"})
            return
        if length > MAX_BYTES + 1024 * 1024:
            # 不读超大请求体，直接断开
            self.close_connection = True
            self._json(413, {"error": "文件过大"})
            return

        body = self.rfile.read(length)
        try:
            filename, data = parse_upload(self.headers.get("Content-Type", ""), body)
            # 模型调用串行化：并发请求不会更快，只会撞限流
            with self.server.lock:  # type: ignore[attr-defined]
                result = assess_upload(self.server.app, filename, data)  # type: ignore[attr-defined]
        except UploadError as exc:
            self._json(400, {"error": str(exc)})
            return
        except Exception as exc:  # 兜底：把异常变成可读的错误，而不是断连接
            self._json(500, {"error": f"评估失败：{type(exc).__name__}: {exc}"})
            return

        self._json(200, result)

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        self._send(
            status,
            "application/json; charset=utf-8",
            json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        )

    def _send(self, status: int, content_type: str, payload: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            try:
                self.wfile.write(payload)
            except BrokenPipeError:
                pass

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write(f"[web] {self.address_string()} {fmt % args}\n")


class AssessServer(ThreadingHTTPServer):
    """把评估图和串行锁挂在 server 上，handler 通过 self.server 取用。"""

    daemon_threads = True

    def __init__(self, address: tuple[str, int], app: Any):
        super().__init__(address, Handler)
        self.app = app
        self.lock = threading.Lock()


# 代理软件的 TUN 会把接口地址伪造成 198.18.0.0/15，不能展示给用户
FAKE_NET = ipaddress.ip_network("198.18.0.0/15")


def is_lan_address(address: str) -> bool:
    """是不是一个可以给同网段设备访问的私有地址。"""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    if ip.version != 4 or ip.is_loopback or ip.is_link_local or ip.is_unspecified:
        return False
    return ip.is_private and ip not in FAKE_NET


def local_ip() -> str:
    """取本机在局域网中的地址。

    先看主机名解析出来的地址（这里最可靠），再用 UDP connect 兜底——
    connect 只查路由不发包。代理的 fake-ip 段一律排除。
    """
    try:
        infos = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
        candidates = {info[4][0] for info in infos}
    except OSError:
        candidates = set()
    for address in sorted(candidates):
        if is_lan_address(address):
            return address

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        address = sock.getsockname()[0]
        return address if is_lan_address(address) else "127.0.0.1"
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="简历评估本地界面")
    parser.add_argument("--host", default="0.0.0.0", help="监听地址")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args(argv)

    try:
        assessor = from_env()
    except RuntimeError as exc:
        print(f"模型未配置: {exc}", file=sys.stderr)
        return 2

    app = build_graph(assessor, required_years=REQUIRED_YEARS)
    try:
        server = AssessServer((args.host, args.port), app)
    except OSError as exc:
        print(f"端口 {args.port} 无法监听：{exc}", file=sys.stderr)
        return 1

    host = local_ip() if args.host in ("0.0.0.0", "") else args.host
    print(f"简历评估界面已启动：http://{host}:{args.port}")
    print(f"本机访问：http://127.0.0.1:{args.port}    按 Ctrl+C 停止")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
