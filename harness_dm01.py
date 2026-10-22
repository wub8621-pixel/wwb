#!/usr/bin/env python3
"""harness_dm01.py — 一个最小可用的 Harness Agent（MVP）。

    Agent = Model + Harness
    Harness = 工具系统 + 上下文管理 + 主循环 + 安全边界

这份实现刻意保持小而完整，只做四件事：

    1. ToolRegistry —— 工具注册表：模型只能看到 JSON Schema，看不到实现
    2. Workspace    —— 文件沙箱：所有路径限制在工作区根目录内
    3. Model        —— 模型适配器：官方 anthropic SDK，自动读取环境变量
    4. AgentLoop    —— 主循环：调模型 → 执行工具 → 回填结果 → 再调模型

与 `harness agent_demo.py` 的区别：
    - demo 用 OpenAI/DeepSeek 风格的 `tool_calls` + `role="tool"` 消息；
    - 本文件用 Anthropic Messages API 的 `tool_use` / `tool_result` content block；
    - demo 是纯教学 Mock，本文件可以直接接真实模型执行真实任务。

Anthropic 工具调用的三个易错点（代码里都有对应位置）：

    1. assistant 的整条 `response.content` 必须原样回填，不能只保留文本；
       否则下一轮模型找不到自己刚才发出的 `tool_use` 块。
    2. 工具结果必须通过 `tool_use_id` 与请求一一对应，且全部放在**同一条**
       user 消息里返回；拆成多条 user 消息会被 API 拒绝。
    3. 工具执行失败不要抛异常中断循环，而要作为 `is_error=True` 的
       tool_result 回填，让模型看到错误并自行修正。

依赖：
    pip install anthropic

环境变量（SDK 自动读取）：
    ANTHROPIC_BASE_URL / ANTHROPIC_AUTH_TOKEN / ANTHROPIC_API_KEY
    ANTHROPIC_MODEL   模型名，默认 claude-opus-5-5

    注意：SDK 的凭据优先级是 ANTHROPIC_API_KEY > ANTHROPIC_AUTH_TOKEN。
    如果同时设置了两个、且只有后者是有效网关令牌，可能会收到 401，
    此时先 `unset ANTHROPIC_API_KEY` 再运行。

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

# 单次工具输出回填给模型的最大字符数。
# 文件或命令输出可能非常大，直接塞进上下文会迅速耗尽 token；
# 这里是 MVP 的简单截断，生产级 Harness 应改为分页读取 / 摘要压缩。
MAX_TOOL_OUTPUT = 20_000


def log(message: str) -> None:
    """打印进度信息。

    约定：日志走 stderr，stdout 只输出最终答案。
    这样调用方可以 `python harness_dm01.py "..." > answer.txt`，
    而不必从一堆进度日志里再解析结果。
    """
    print(f"[harness] {message}", file=sys.stderr, flush=True)


# ============================================================
# 1. 工具注册表：Harness 的“行动空间”
#
# 模型能做什么，完全由这里注册的工具决定。
# 工具是 Harness 与外部世界之间唯一的受控通道。
# ============================================================


@dataclass
class ToolSpec:
    """一个工具的完整描述。

    字段说明：
    - name:         工具名，模型通过它请求调用。
    - description:  给模型看的自然语言说明，直接影响模型是否/何时选它，
                    所以要写清“做什么、什么时候用”，而不是只写函数作用。
    - input_schema: JSON Schema，告诉模型参数结构、类型和必填项。
    - fn:           真正执行的 Python 函数。

    关键点：fn 只存在于本地进程，永远不要序列化给模型。
    模型看到的只有前三项（见 to_api()）。
    """

    name: str
    description: str
    input_schema: dict
    fn: Callable[..., str]

    def to_api(self) -> dict:
        """转换成 Anthropic Messages API 需要的工具描述格式。

        注意字段名是 `input_schema`（Anthropic），
        而不是 OpenAI 风格的 `parameters`。
        """
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


class ToolRegistry:
    """管理工具并负责执行。

    设计要点：
    - 模型只能拿到 specs() 返回的 Schema，拿不到 fn；
    - 真正的执行发生在 execute()，Harness 在这里统一做参数校验和异常兜底；
    - 工具执行失败不会让 Agent 崩溃，而是转成一条 is_error 的 tool_result，
      让模型看到错误后自行调整——这是 Harness 工程化的关键点之一。
    """

    def __init__(self, root: Path, allow_shell: bool = False):
        # 工作区根目录：所有文件类工具都被限制在这个目录内
        self.root = root.resolve()
        # shell 是高危能力，默认关闭，必须由使用者显式开启
        self.allow_shell = allow_shell
        # name -> ToolSpec。同名工具会被覆盖（MVP 不做冲突检查）
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        """注册一个工具。"""
        self._tools[spec.name] = spec

    def specs(self) -> list[dict]:
        """返回“给模型看的工具清单”（只有 Schema，没有实现）。"""
        return [tool.to_api() for tool in self._tools.values()]

    def execute(self, name: str, arguments: dict) -> tuple[str, bool]:
        """执行工具，返回 (输出文本, 是否出错)。

        异常在这里被捕获并转换成普通的工具结果，而不是向上抛出。
        原因是：对 Agent 来说，“工具报错”本身也是一种观察结果，
        模型可以据此重试、换工具或向用户解释。
        """
        spec = self._tools.get(name)
        if spec is None:
            # 模型可能幻觉出不存在的工具名，这里也要温和地告诉它
            return f"[工具错误] 未知工具：{name}", True

        try:
            # arguments 已经是解析好的 dict（由模型返回的 JSON 反序列化而来）
            return str(spec.fn(**arguments)), False
        except Exception as exc:  # noqa: BLE001 - 工具错误要回填给模型
            # 保留异常类型，方便模型判断是参数问题还是环境问题
            return f"[工具错误] {name} 执行失败：{type(exc).__name__}: {exc}", True

    # ---- 路径沙箱 ----

    def resolve_path(self, path: str) -> Path:
        """把相对路径解析到工作区内；越界直接报错。

        安全边界：`../`、绝对路径、软链接都要经过 resolve() 后再判断，
        否则模型可以用 `../../etc/passwd` 读到工作区之外的文件。
        """
        candidate = (self.root / path).resolve()
        if candidate != self.root and not candidate.is_relative_to(self.root):
            raise ValueError(f"路径越界，只能访问工作区内的文件：{path}")
        return candidate

    # ---- 内置工具实现 ----
    #
    # 这些方法与下面的 register_builtin_tools() 一一对应：
    # 方法是“怎么执行”，Schema 是“模型怎么调用”。

    def list_dir(self, path: str = ".") -> str:
        """列出目录内容。目录名后加 `/`，方便模型区分文件和目录。"""
        target = self.resolve_path(path)
        if not target.is_dir():
            raise NotADirectoryError(f"不是目录：{path}")

        entries = [
            child.name + ("/" if child.is_dir() else "")
            for child in sorted(target.iterdir())
            # 隐藏 .git 等内部目录，避免把无关噪音塞进上下文
            if not child.name.startswith(".git")
        ]
        # 最多返回 200 项，防止超大目录撑爆上下文
        return "\n".join(entries[:200]) or "(空目录)"

    def read_file(self, path: str, start_line: int = 1, max_lines: int = 400) -> str:
        """读取文本文件的指定行范围，返回带行号的内容。

        - 用 errors="replace" 兜底，避免二进制文件让工具直接失败；
        - 返回行号，模型才能准确引用和后续做精确修改；
        - 默认限制 400 行，超出部分提示总行数，引导模型分段读取。
        """
        target = self.resolve_path(path)
        lines = target.read_text(encoding="utf-8", errors="replace").splitlines()

        # start_line 是 1-based，列表下标是 0-based
        start = max(start_line, 1) - 1
        chunk = lines[start : start + max_lines]

        numbered = "\n".join(
            f"{start + offset + 1:>5}\t{line}" for offset, line in enumerate(chunk)
        )
        if start + max_lines < len(lines):
            numbered += f"\n...（共 {len(lines)} 行，已截断）"
        return numbered or "(空文件)"

    def write_file(self, path: str, content: str) -> str:
        """创建或覆盖文件。

        父目录不存在时自动创建，减少模型为“建目录”多绕一轮。
        注意这里是整文件覆盖，MVP 不做 diff / 追加 / 原子写。
        """
        target = self.resolve_path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return f"已写入 {target.relative_to(self.root)}（{len(content)} 字符）"

    def run_shell(self, command: str, timeout: int = 30) -> str:
        """在工作区目录下执行一条 shell 命令。

        安全说明：`shell=True` 意味着命令字符串会被 shell 解释，
        具备管道、重定向、命令替换等全部能力。因此：
        - 默认关闭，必须 --allow-shell 显式开启；
        - 设置超时，避免模型跑一个永不结束的命令；
        - 捕获 stdout/stderr 并合并返回，让模型看到完整反馈。
        生产环境应进一步做命令白名单或容器隔离。
        """
        if not self.allow_shell:
            raise PermissionError("shell 工具未启用，请启动时加 --allow-shell")

        completed = subprocess.run(
            command,
            shell=True,
            cwd=self.root,          # 限制命令的默认工作目录
            capture_output=True,    # 不让子进程输出直接打到终端
            text=True,
            timeout=timeout,        # 超时会抛 TimeoutExpired，由 execute() 兜底
        )
        output = (completed.stdout or "") + (completed.stderr or "")
        # 只保留尾部：命令输出通常“最新、最相关”的内容在最后
        return f"exit={completed.returncode}\n{output[-MAX_TOOL_OUTPUT:]}"


def register_builtin_tools(registry: ToolRegistry) -> None:
    """注册内置的四个工具。

    每个 ToolSpec 的三段式：
        name + description（给模型判断用）+ input_schema（给模型填参用）
    """

    # --- 查看目录 ---
    registry.register(
        ToolSpec(
            name="list_dir",
            description="列出工作区内某个目录下的文件和子目录。",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "相对路径，默认当前目录"},
                },
                # 没有必填参数，模型可以不传 path
                "required": [],
            },
            fn=registry.list_dir,
        )
    )

    # --- 读文件 ---
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

    # --- 写文件 ---
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

    # --- 执行命令（默认禁用） ---
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
#
# AgentLoop 只依赖下面的 Model 协议，不依赖任何厂商 SDK。
# 想换模型（Claude / DeepSeek / 本地模型）时，只需要写一个新的适配器。
# ============================================================


class Model(Protocol):
    """统一的模型接口。

    返回值约定：一个对象，至少带 `.content`（内容块列表）。
    Anthropic SDK 返回的 Message 对象天然满足这个结构，
    自检用的 ScriptedModel 也用 SimpleNamespace 模拟同样的结构。
    """

    def complete(self, *, system: str, messages: list[dict], tools: list[dict]) -> Any:
        """传入系统提示、完整消息历史和工具清单，返回模型响应。"""


class AnthropicModel:
    """官方 anthropic SDK 适配器。

    两个刻意的设计：

    1. 延迟导入 anthropic。
       这样 `--self-test` 不需要安装任何第三方依赖，
       在 CI 或没网的环境里也能验证核心逻辑。

    2. 不硬编码地址和密钥。
       SDK 会读取 ANTHROPIC_BASE_URL / ANTHROPIC_AUTH_TOKEN 等环境变量，
       所以同一份代码既能直连 Anthropic，也能走兼容网关（如 DeepSeek）。
    """

    def __init__(self, model: str | None = None, max_tokens: int = 4096):
        try:
            import anthropic  # 延迟导入，保证 --self-test 无需安装依赖
        except ImportError as exc:  # pragma: no cover - 依赖缺失
            raise SystemExit("缺少依赖，请先执行：pip install anthropic") from exc

        # 无参构造：凭据、base_url、超时全部交给 SDK 从环境变量解析
        self._client = anthropic.Anthropic()

        # 优先级：命令行 --model > 环境变量 > 默认值
        self.model = model or os.environ.get("ANTHROPIC_MODEL") or "claude-opus-5-5"
        # 单次响应的最大输出 token。工具调用通常很短，4096 足够；
        # 若让模型生成长文本，可在构造时调大。
        self.max_tokens = max_tokens

    def complete(self, *, system: str, messages: list[dict], tools: list[dict]) -> Any:
        """调用 Messages API。

        - system 单独传，不放进 messages；
        - messages 是本轮完整历史（每次都要全量传，API 无状态）；
        - tools 接收 register_builtin_tools() 生成的 Schema 列表。

        返回的 Message.content 是一个“内容块列表”，可能同时包含
        text / tool_use 块；AgentLoop 会原样把它回填进历史。
        """
        return self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=messages,
            tools=tools,
        )


# ============================================================
# 3. Agent Loop：Harness 的心脏
#
#   调用模型 → 解析工具请求 → 执行工具 → 回填结果 → 重复
# 直到模型不再请求工具，或达到 max_turns 上限。
# ============================================================

# 系统提示词是 Harness 的一部分：它约束模型的行为方式。
# 这里重点强调“先看再答”“用相对路径”，减少幻觉和越界尝试。
SYSTEM_PROMPT = """你是一个在本地工作区内执行任务的 Agent。

规则：
1. 需要了解文件内容时，先用工具查看，不要凭空猜测。
2. 一次可以请求多个工具；拿到结果后再决定下一步。
3. 任务完成后，用简洁的中文给出最终结果，不要再请求工具。
4. 路径一律使用相对路径，不要尝试访问工作区之外的位置。"""


class AgentLoop:
    """Harness 的核心执行循环。

    它不关心模型是真实 API 还是脚本 Mock，也不关心工具具体做什么，
    只负责编排：
        模型 → 工具 → 观察结果 → 模型 → ……
    这种“编排与实现分离”是 Harness 可测试、可替换的基础。
    """

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
        # 硬性轮次上限：防止模型反复调用工具导致死循环和费用失控
        self.max_turns = max_turns
        # 完整对话历史。每轮都全量传给模型（Messages API 无状态）
        self.messages: list[dict] = []

    def run(self, task: str) -> str:
        """执行一个任务，返回最终答案文本。"""
        # 1) 用户任务进入上下文，作为本轮循环的起点
        self.messages.append({"role": "user", "content": task})

        for turn in range(1, self.max_turns + 1):
            log(f"turn {turn}/{self.max_turns}：调用模型")

            # 2) 调用模型：传入系统提示、完整历史、当前工具 Schema。
            #    工具清单每轮都传，模型才知道自己有哪些能力。
            try:
                response = self.model.complete(
                    system=self.system,
                    messages=self.messages,
                    tools=self.tools.specs(),
                )
            except Exception as exc:  # noqa: BLE001 - 顶层兜底，避免栈溢出式报错
                # 网络、鉴权、限流等问题在这里统一转成可读信息
                return f"[harness 错误] 模型调用失败：{type(exc).__name__}: {exc}"

            # 3) 回填 assistant 的完整内容块。
            #    必须包含 tool_use 块：下一轮模型要靠它把 tool_result
            #    和刚才的调用请求对应起来。只回填文本是常见错误。
            self.messages.append({"role": "assistant", "content": response.content})

            # 4) 找出本轮所有工具调用请求
            tool_uses = [
                block
                for block in response.content
                if getattr(block, "type", None) == "tool_use"
            ]

            # 5) 没有工具调用 = 模型给出了最终答案 → 自然终止
            if not tool_uses:
                answer = "".join(
                    getattr(block, "text", "")
                    for block in response.content
                    if getattr(block, "type", None) == "text"
                )
                log("模型未请求工具，任务结束")
                return answer.strip()

            # 6) 依次执行工具。模型可能一次请求多个工具（并行调用），
            #    所以用循环逐个执行，并收集所有结果。
            results: list[dict] = []
            for block in tool_uses:
                arguments = block.input or {}
                log(f"工具调用 {block.name}({json.dumps(arguments, ensure_ascii=False)})")

                output, is_error = self.tools.execute(block.name, arguments)
                log(f"工具结果 {'错误' if is_error else '成功'}：{output[:200]}")

                results.append(
                    {
                        "type": "tool_result",
                        # tool_use_id 必须精确对应请求，否则 API 会报错
                        "tool_use_id": block.id,
                        # 截断后再回填，保护上下文长度
                        "content": output[:MAX_TOOL_OUTPUT],
                        # 让模型明确知道这是失败结果，而不是正常输出
                        "is_error": is_error,
                    }
                )

            # 7) 所有工具结果必须放在同一条 user 消息里返回。
            #    如果一个 tool_use 一个 user 消息，API 会拒绝请求。
            self.messages.append({"role": "user", "content": results})

            # 8) 继续下一轮：模型会看到工具结果并决定下一步

        # 达到轮次上限：优雅终止，而不是抛异常或静默失败。
        # 生产环境可以在这里追加一次“请总结当前进展”的收尾调用。
        return "[harness] 达到最大轮次上限，任务未完成。"


# ============================================================
# 4. 自检：不联网、不花钱地验证主循环
#
# 做法是用 ScriptedModel 按脚本返回预设响应，
# 从而精确控制“模型第几轮请求什么工具、第几轮给最终答案”。
# ============================================================


class ScriptedModel:
    """按脚本依次返回预设响应的假模型，用于自检。

    除了返回响应，还记录每次收到的 messages，
    这样自检可以断言“工具结果有没有被正确回填”。
    """

    def __init__(self, responses: list[Any]):
        self.responses = responses
        self.turns = 0
        # received[i] = 第 i 次调用时看到的完整消息历史
        self.received: list[list[dict]] = []

    def complete(self, *, system: str, messages: list[dict], tools: list[dict]) -> Any:
        self.received.append(messages)
        # 脚本用完后一直返回最后一条，便于测试“无限循环”场景
        response = self.responses[min(self.turns, len(self.responses) - 1)]
        self.turns += 1
        return response


# --- 构造与真实 SDK 响应同构的测试替身 ---
# 真实 SDK 返回的是 pydantic 对象；这里用 SimpleNamespace 模拟同样的属性访问方式。


def _tool_use(call_id: str, name: str, arguments: dict) -> SimpleNamespace:
    """模拟一个 tool_use 内容块。"""
    return SimpleNamespace(type="tool_use", id=call_id, name=name, input=arguments)


def _text(text: str) -> SimpleNamespace:
    """模拟一个 text 内容块。"""
    return SimpleNamespace(type="text", text=text)


def _response(blocks: list[Any], stop_reason: str) -> SimpleNamespace:
    """模拟一次完整的模型响应。"""
    return SimpleNamespace(content=blocks, stop_reason=stop_reason)


def self_test() -> int:
    """验证：工具执行、结果回填、最终答案、路径沙箱、轮次上限。

    返回 0 表示全部通过，1 表示有失败项（可直接用于 CI）。
    """
    failures: list[str] = []

    def check(name: str, condition: bool) -> None:
        print(f"{'PASS' if condition else 'FAIL'}  {name}")
        if not condition:
            failures.append(name)

    # 用临时目录当工作区，测试结束自动清理，不污染项目目录
    with tempfile.TemporaryDirectory(prefix="harness_dm01_") as temp_dir:
        root = Path(temp_dir)
        (root / "hello.txt").write_text("hello\nworld\n", encoding="utf-8")

        registry = ToolRegistry(root)
        register_builtin_tools(registry)

        # ---- 1) 工具本身 ----
        content = registry.read_file("hello.txt")
        check("read_file 返回带行号内容", "1\thello" in content and "2\tworld" in content)

        output, is_error = registry.execute("read_file", {"path": "hello.txt"})
        check("execute 成功路径", not is_error and "world" in output)

        # 安全边界：尝试跳出工作区
        output, is_error = registry.execute("read_file", {"path": "../outside.txt"})
        check("路径越界被拦截", is_error and "越界" in output)

        # 默认配置下 shell 必须不可用
        output, is_error = registry.execute("run_shell", {"command": "echo hi"})
        check("shell 默认禁用", is_error and "未启用" in output)

        # ---- 2) 主循环：先调工具，再给最终答案 ----
        scripted = ScriptedModel(
            [
                # 第 1 轮：模型请求读取 hello.txt
                _response([_tool_use("call_1", "read_file", {"path": "hello.txt"})], "tool_use"),
                # 第 2 轮：模型看到结果后给最终答案
                _response([_text("文件内容是 hello world。")], "end_turn"),
            ]
        )
        loop = AgentLoop(scripted, registry, system="自检", max_turns=4)
        answer = loop.run("读取 hello.txt 的内容")

        check("返回最终答案", answer == "文件内容是 hello world。")
        check("模型被调用两次", scripted.turns == 2)

        # 检查第 2 次调用时，历史里是否包含正确的 tool_result。
        # 这是整个 Harness 最关键的数据流：模型请求 → 本地执行 → 结果回填。
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

        # ---- 3) 达到轮次上限时优雅终止 ----
        # 脚本只有一个 tool_use 响应，模型会“永远”请求工具；
        # max_turns=2 时 Harness 必须主动停下，而不是无限循环。
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
#
#   把配置参数集中在这里，核心类保持可复用、可被其他程序 import。
# ============================================================


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="最小可用 Harness Agent（MVP）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # 任务描述是位置参数：python harness_dm01.py "你的任务"
    parser.add_argument("task", nargs="?", help="交给 Agent 的任务描述")
    # 工作区根目录，也是所有文件操作的安全边界
    parser.add_argument(
        "-C", "--workspace", default=".", help="工作区根目录，默认当前目录"
    )
    parser.add_argument("--model", default=None, help="模型名，默认读取 ANTHROPIC_MODEL")
    parser.add_argument("--max-turns", type=int, default=8, help="最大轮次，默认 8")
    # 高危能力必须显式开启
    parser.add_argument(
        "--allow-shell", action="store_true", help="启用 run_shell 工具（默认关闭）"
    )
    parser.add_argument("--self-test", action="store_true", help="运行本地自检后退出")
    args = parser.parse_args(argv)

    # 自检模式：不构造真实模型、不读取凭据、不发起网络请求
    if args.self_test:
        return self_test()

    if not args.task:
        parser.error("请提供任务描述，或使用 --self-test")

    root = Path(args.workspace).expanduser().resolve()
    if not root.is_dir():
        parser.error(f"工作区不存在：{root}")

    # 装配 Harness：工具 → 模型 → 循环
    registry = ToolRegistry(root, allow_shell=args.allow_shell)
    register_builtin_tools(registry)

    model = AnthropicModel(model=args.model)
    log(f"模型：{model.model}｜工作区：{root}")
    log(f"工具：{', '.join(tool['name'] for tool in registry.specs())}")

    answer = AgentLoop(model, registry, max_turns=args.max_turns).run(args.task)

    # 最终答案走 stdout，便于脚本消费
    print(answer)
    return 0


if __name__ == "__main__":
    sys.exit(main())
