#!/usr/bin/env python3
"""harness_dm01.py — 一个最小可用的 Harness Agent（MVP）。

    Agent = Model + Harness

这份实现刻意保持小而完整，只做四件事：

    1. ToolRegistry —— 工具注册表：模型只能看到 JSON Schema，看不到实现
    2. Workspace    —— 文件沙箱：所有路径限制在工作区根目录内
    3. Model        —— 模型适配器：官方 anthropic SDK，自动读取环境变量
    4. AgentLoop    —— 主循环：调模型 → 执行工具 → 回填结果 → 再调模型

依赖：
    pip install anthropic

环境变量（SDK 自动读取）：
    ANTHROPIC_BASE_URL / ANTHROPIC_AUTH_TOKEN / ANTHROPIC_API_KEY
    ANTHROPIC_MODEL   模型名，默认 claude-opus-5-5

用法：
    python harness_dm01.py "统计当前目录下有多少个 .py 文件"
    python harness_dm01.py --allow-shell "运行 ls 看看目录结构"
    python harness_dm01.py --self-test          # 不联网的自检
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Protocol

# 单次工具输出回填给模型的最大字符数，避免上下文被撑爆
MAX_TOOL_OUTPUT = 20_000


def log(message: str) -> None:
    """进度信息走 stderr，stdout 只留给最终答案。"""
    print(f"[harness] {message}", file=sys.stderr, flush=True)


# ============================================================
# 1. 工具注册表：Harness 的“行动空间”
# ============================================================


@dataclass
class ToolSpec:
    """一个工具的完整描述。

    name / description / input_schema 是给模型看的；fn 只在本地执行。
    """

    name: str
    description: str
    input_schema: dict
    fn: Callable[..., str]

    def to_api(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


class ToolRegistry:
    """管理工具并负责执行。

    工具执行失败不会让 Agent 崩溃，而是转成一条 is_error 的 tool_result，
    让模型看到错误后自行调整——这是 Harness 工程化的关键点之一。
    """

    def __init__(self, root: Path, allow_shell: bool = False):
        self.root = root.resolve()
        self.allow_shell = allow_shell
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        self._tools[spec.name] = spec

    def specs(self) -> list[dict]:
        return [tool.to_api() for tool in self._tools.values()]

    def execute(self, name: str, arguments: dict) -> tuple[str, bool]:
        """返回 (输出文本, 是否出错)。"""
        spec = self._tools.get(name)
        if spec is None:
            return f"[工具错误] 未知工具：{name}", True
        try:
            return str(spec.fn(**arguments)), False
        except Exception as exc:  # noqa: BLE001 - 工具错误要回填给模型
            return f"[工具错误] {name} 执行失败：{type(exc).__name__}: {exc}", True

    # ---- 路径沙箱 ----

    def resolve_path(self, path: str) -> Path:
        """把相对路径解析到工作区内；越界直接报错。"""
        candidate = (self.root / path).resolve()
        if candidate != self.root and not candidate.is_relative_to(self.root):
            raise ValueError(f"路径越界，只能访问工作区内的文件：{path}")
        return candidate

    # ---- 内置工具实现 ----

    def list_dir(self, path: str = ".") -> str:
        target = self.resolve_path(path)
        if not target.is_dir():
            raise NotADirectoryError(f"不是目录：{path}")
        entries = [
            child.name + ("/" if child.is_dir() else "")
            for child in sorted(target.iterdir())
            if not child.name.startswith(".git")
        ]
        return "\n".join(entries[:200]) or "(空目录)"

    def read_file(self, path: str, start_line: int = 1, max_lines: int = 400) -> str:
        target = self.resolve_path(path)
        lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
        start = max(start_line, 1) - 1
        chunk = lines[start : start + max_lines]
        numbered = "\n".join(
            f"{start + offset + 1:>5}\t{line}" for offset, line in enumerate(chunk)
        )
        if start + max_lines < len(lines):
            numbered += f"\n...（共 {len(lines)} 行，已截断）"
        return numbered or "(空文件)"

    def write_file(self, path: str, content: str) -> str:
        target = self.resolve_path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return f"已写入 {target.relative_to(self.root)}（{len(content)} 字符）"

    def run_shell(self, command: str, timeout: int = 30) -> str:
        if not self.allow_shell:
            raise PermissionError("shell 工具未启用，请启动时加 --allow-shell")
        completed = subprocess.run(
            command,
            shell=True,
            cwd=self.root,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        output = (completed.stdout or "") + (completed.stderr or "")
        return f"exit={completed.returncode}\n{output[-MAX_TOOL_OUTPUT:]}"


def register_builtin_tools(registry: ToolRegistry) -> None:
    registry.register(
        ToolSpec(
            name="list_dir",
            description="列出工作区内某个目录下的文件和子目录。",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "相对路径，默认当前目录"},
                },
                "required": [],
            },
            fn=registry.list_dir,
        )
    )
    registry.register(
        ToolSpec(
            name="read_file",
            description="读取工作区内文本文件的指定行范围，返回带行号的内容。",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "相对路径"},
                    "start_line": {"type": "integer", "description": "起始行，默认 1"},
                    "max_lines": {"type": "integer", "description": "最多读取行数，默认 400"},
                },
                "required": ["path"],
            },
            fn=registry.read_file,
        )
    )
    registry.register(
        ToolSpec(
            name="write_file",
            description="在工作区内创建或覆盖一个文本文件。",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "相对路径"},
                    "content": {"type": "string", "description": "完整文件内容"},
                },
                "required": ["path", "content"],
            },
            fn=registry.write_file,
        )
    )
    registry.register(
        ToolSpec(
            name="run_shell",
            description="在工作区内执行一条 shell 命令，返回退出码和输出。",
            input_schema={
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "要执行的命令"},
                    "timeout": {"type": "integer", "description": "超时秒数，默认 30"},
                },
                "required": ["command"],
            },
            fn=registry.run_shell,
        )
    )


# ============================================================
# 2. 模型适配器：Harness 与“外部大脑”的边界
# ============================================================


class Model(Protocol):
    def complete(self, *, system: str, messages: list[dict], tools: list[dict]) -> Any:
        """返回一个带 .content 和 .stop_reason 的响应对象。"""


class AnthropicModel:
    """官方 anthropic SDK 适配器。

    不硬编码地址和密钥——SDK 会读取 ANTHROPIC_BASE_URL / ANTHROPIC_AUTH_TOKEN
    等环境变量，因此同一份代码既能连 Anthropic，也能连兼容网关。
    """

    def __init__(self, model: str | None = None, max_tokens: int = 4096):
        try:
            import anthropic  # 延迟导入，保证 --self-test 无需安装依赖
        except ImportError as exc:  # pragma: no cover - 依赖缺失
            raise SystemExit("缺少依赖，请先执行：pip install anthropic") from exc

        self._client = anthropic.Anthropic()
        self.model = model or os.environ.get("ANTHROPIC_MODEL") or "claude-opus-5-5"
        self.max_tokens = max_tokens

    def complete(self, *, system: str, messages: list[dict], tools: list[dict]) -> Any:
        return self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=messages,
            tools=tools,
        )


# ============================================================
# 3. Agent Loop：Harness 的心脏
# ============================================================

SYSTEM_PROMPT = """你是一个在本地工作区内执行任务的 Agent。

规则：
1. 需要了解文件内容时，先用工具查看，不要凭空猜测。
2. 一次可以请求多个工具；拿到结果后再决定下一步。
3. 任务完成后，用简洁的中文给出最终结果，不要再请求工具。
4. 路径一律使用相对路径，不要尝试访问工作区之外的位置。"""


class AgentLoop:
    """只负责编排，不关心模型厂商，也不关心工具具体做什么。"""

    def __init__(
        self,
        model: Model,
        tools: ToolRegistry,
        system: str = SYSTEM_PROMPT,
        max_turns: int = 8,
    ):
        self.model = model
        self.tools = tools
        self.system = system
        self.max_turns = max_turns
        self.messages: list[dict] = []

    def run(self, task: str) -> str:
        self.messages.append({"role": "user", "content": task})

        for turn in range(1, self.max_turns + 1):
            log(f"turn {turn}/{self.max_turns}：调用模型")

            try:
                response = self.model.complete(
                    system=self.system,
                    messages=self.messages,
                    tools=self.tools.specs(),
                )
            except Exception as exc:  # noqa: BLE001 - 顶层兜底，避免栈溢出式报错
                return f"[harness 错误] 模型调用失败：{type(exc).__name__}: {exc}"

            # 必须把完整的 content 块回填，tool_use 块也要保留
            self.messages.append({"role": "assistant", "content": response.content})

            tool_uses = [
                block
                for block in response.content
                if getattr(block, "type", None) == "tool_use"
            ]

            if not tool_uses:
                answer = "".join(
                    getattr(block, "text", "")
                    for block in response.content
                    if getattr(block, "type", None) == "text"
                )
                log("模型未请求工具，任务结束")
                return answer.strip()

            # 所有工具结果放在同一条 user 消息里回填
            results: list[dict] = []
            for block in tool_uses:
                arguments = block.input or {}
                log(f"工具调用 {block.name}({json.dumps(arguments, ensure_ascii=False)})")
                output, is_error = self.tools.execute(block.name, arguments)
                log(f"工具结果 {'错误' if is_error else '成功'}：{output[:200]}")
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": output[:MAX_TOOL_OUTPUT],
                        "is_error": is_error,
                    }
                )

            self.messages.append({"role": "user", "content": results})

        return "[harness] 达到最大轮次上限，任务未完成。"


# ============================================================
# 4. 自检：不联网、不花钱地验证主循环
# ============================================================


class ScriptedModel:
    """按脚本依次返回预设响应的假模型，用于自检。"""

    def __init__(self, responses: list[Any]):
        self.responses = responses
        self.turns = 0
        self.received: list[list[dict]] = []

    def complete(self, *, system: str, messages: list[dict], tools: list[dict]) -> Any:
        self.received.append(messages)
        response = self.responses[min(self.turns, len(self.responses) - 1)]
        self.turns += 1
        return response


def _tool_use(call_id: str, name: str, arguments: dict) -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", id=call_id, name=name, input=arguments)


def _text(text: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=text)


def _response(blocks: list[Any], stop_reason: str) -> SimpleNamespace:
    return SimpleNamespace(content=blocks, stop_reason=stop_reason)


def self_test() -> int:
    """验证：工具执行、结果回填、最终答案、路径沙箱。"""
    failures: list[str] = []

    def check(name: str, condition: bool) -> None:
        print(f"{'PASS' if condition else 'FAIL'}  {name}")
        if not condition:
            failures.append(name)

    with tempfile.TemporaryDirectory(prefix="harness_dm01_") as temp_dir:
        root = Path(temp_dir)
        (root / "hello.txt").write_text("hello\nworld\n", encoding="utf-8")

        registry = ToolRegistry(root)
        register_builtin_tools(registry)

        # 1) 工具本身
        content = registry.read_file("hello.txt")
        check("read_file 返回带行号内容", "1\thello" in content and "2\tworld" in content)

        output, is_error = registry.execute("read_file", {"path": "hello.txt"})
        check("execute 成功路径", not is_error and "world" in output)

        output, is_error = registry.execute("read_file", {"path": "../outside.txt"})
        check("路径越界被拦截", is_error and "越界" in output)

        output, is_error = registry.execute("run_shell", {"command": "echo hi"})
        check("shell 默认禁用", is_error and "未启用" in output)

        # 2) 主循环：先调工具，再给最终答案
        scripted = ScriptedModel(
            [
                _response([_tool_use("call_1", "read_file", {"path": "hello.txt"})], "tool_use"),
                _response([_text("文件内容是 hello world。")], "end_turn"),
            ]
        )
        loop = AgentLoop(scripted, registry, system="自检", max_turns=4)
        answer = loop.run("读取 hello.txt 的内容")

        check("返回最终答案", answer == "文件内容是 hello world。")
        check("模型被调用两次", scripted.turns == 2)

        tool_results = [
            block
            for message in scripted.received[-1]
            if isinstance(message.get("content"), list)
            for block in message["content"]
            if isinstance(block, dict) and block.get("type") == "tool_result"
        ]
        check(
            "tool_result 正确回填",
            len(tool_results) == 1
            and tool_results[0]["tool_use_id"] == "call_1"
            and "world" in tool_results[0]["content"],
        )

        # 3) 达到轮次上限时优雅终止
        looping = ScriptedModel(
            [_response([_tool_use("call_x", "list_dir", {})], "tool_use")]
        )
        limited = AgentLoop(looping, registry, system="自检", max_turns=2)
        result = limited.run("无限循环测试")
        check("达到轮次上限优雅终止", result.startswith("[harness] 达到最大轮次"))

    if failures:
        print(f"\n自检失败：{len(failures)} 项")
        return 1
    print("\n自检全部通过")
    return 0


# ============================================================
# 5. 命令行入口
# ============================================================


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="最小可用 Harness Agent（MVP）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("task", nargs="?", help="交给 Agent 的任务描述")
    parser.add_argument(
        "-C", "--workspace", default=".", help="工作区根目录，默认当前目录"
    )
    parser.add_argument("--model", default=None, help="模型名，默认读取 ANTHROPIC_MODEL")
    parser.add_argument("--max-turns", type=int, default=8, help="最大轮次，默认 8")
    parser.add_argument(
        "--allow-shell", action="store_true", help="启用 run_shell 工具（默认关闭）"
    )
    parser.add_argument("--self-test", action="store_true", help="运行本地自检后退出")
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()

    if not args.task:
        parser.error("请提供任务描述，或使用 --self-test")

    root = Path(args.workspace).expanduser().resolve()
    if not root.is_dir():
        parser.error(f"工作区不存在：{root}")

    registry = ToolRegistry(root, allow_shell=args.allow_shell)
    register_builtin_tools(registry)

    model = AnthropicModel(model=args.model)
    log(f"模型：{model.model}｜工作区：{root}")
    log(f"工具：{', '.join(tool['name'] for tool in registry.specs())}")

    answer = AgentLoop(model, registry, max_turns=args.max_turns).run(args.task)
    print(answer)
    return 0


if __name__ == "__main__":
    sys.exit(main())
