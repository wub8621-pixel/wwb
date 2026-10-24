"""LLM 兜底：切章纠错与元数据补全。只在正则结果不达标时调用。"""

from __future__ import annotations

import json
import logging
import re

from config.loader import ConfigError

from .chapters import Chapter


class LLMError(RuntimeError):
    """模型调用失败或返回不可用内容。"""


CHAPTER_SYSTEM = (
    "你是电子书排版助手。用户会给你按行编号的正文，你要找出所有章节标题所在的行。"
    "只输出 JSON 数组，不要解释，不要 Markdown 代码块。"
)
CHAPTER_PROMPT = """下面是正文，每行前面是行号，用制表符分隔。
请找出所有章节标题所在的行，输出 JSON 数组：
[{{"title": "第一章 标题", "line": 12}}, ...]

规则：
1. line 必须是给定行号，不能自己编造；
2. 行号必须严格递增；
3. title 用原文标题，不要改写；
4. 最多返回 {max_chapters} 个章节；
5. 没有标题就返回空数组 []。

正文：
{numbered}"""

METADATA_SYSTEM = (
    "你是图书元数据助手。根据文件名和正文开头推断书名、作者和一句话简介。"
    "只输出 JSON 对象，不要解释，不要 Markdown 代码块。"
)
METADATA_PROMPT = """文件名：{filename}

正文开头：
{head}

输出 JSON：{{"title": "书名", "author": "作者", "description": "不超过 120 字的简介"}}
推断不出来的字段填空字符串，不要编造。"""


def load_json(raw: str):
    """模型常带 ```json 围栏或前后解释，这里做一次宽容解析。"""
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", cleaned)
        cleaned = re.sub(r"```\s*$", "", cleaned).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"[\[{].*[\]}]", cleaned, re.S)
        if not match:
            raise LLMError("模型返回里找不到 JSON")
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise LLMError(f"模型返回的 JSON 无法解析：{exc}") from exc


def make_llm(cfg, logger: logging.Logger):
    """按配置创建模型客户端；关闭、缺密钥或 SDK 不可用时返回 None。"""
    if not cfg.get("llm_fallback.enabled"):
        return None
    try:
        return LLMClient(cfg, logger)
    except ConfigError as exc:
        logger.warning("模型未配置，跳过 LLM 兜底：%s", exc)
        return None
    except ImportError as exc:
        logger.warning("anthropic SDK 不可用，跳过 LLM 兜底：%s", exc)
        return None


class LLMClient:
    """对 Anthropic 兼容端点的薄封装；测试里可替换成假实现。"""

    def __init__(self, cfg, logger: logging.Logger | None = None):
        from anthropic import Anthropic

        self._client = Anthropic(
            base_url=cfg.get("model.base_url"),
            api_key=cfg.env("model.api_key_env"),
            timeout=cfg.get("model.timeout_s"),
            max_retries=cfg.get("model.retries"),
        )
        self._model = cfg.get("model.name")
        self._max_tokens = cfg.get("model.max_tokens")
        self._logger = logger or logging.getLogger("ebook")

    def _ask(self, system: str, prompt: str) -> str:
        try:
            message = self._client.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                system=system,
                messages=[{"role": "user", "content": prompt}],
            )
        except Exception as exc:  # SDK 的异常类型较多，统一转成 LLMError
            raise LLMError(f"模型调用失败：{exc}") from exc

        text = "".join(getattr(block, "text", "") for block in message.content)
        if not text.strip():
            raise LLMError("模型返回空内容")
        return text

    def repair_chapters(
        self,
        text: str,
        reasons: list[str],
        *,
        max_input_chars: int,
        max_chapters: int,
        fallback_title: str = "正文",
    ) -> list[Chapter]:
        clipped = len(text) > max_input_chars
        source = text[:max_input_chars]
        if clipped:
            self._logger.warning(
                "正文 %d 字超过送模型上限 %d，只修前 %d 字，剩余部分并入最后一章",
                len(text), max_input_chars, max_input_chars,
            )

        lines = source.split("\n")
        numbered = "\n".join(f"{index}\t{line}" for index, line in enumerate(lines))
        raw = self._ask(
            CHAPTER_SYSTEM,
            CHAPTER_PROMPT.format(max_chapters=max_chapters, numbered=numbered),
        )
        chapters = self._to_chapters(load_json(raw), lines, max_chapters, fallback_title)

        if clipped and chapters:
            remainder = text[len(source):].strip()
            if remainder:
                chapters[-1] = Chapter(chapters[-1].title, f"{chapters[-1].body}\n{remainder}".strip())
        self._logger.debug("LLM 兜底完成，原因：%s", "；".join(reasons))
        return chapters

    @staticmethod
    def _to_chapters(payload, lines: list[str], max_chapters: int, fallback_title: str) -> list[Chapter]:
        if not isinstance(payload, list):
            raise LLMError("模型返回的不是章节数组")
        entries: list[tuple[int, str]] = []
        for item in payload:
            if not isinstance(item, dict):
                raise LLMError("章节条目不是对象")
            title = str(item.get("title", "")).strip()
            line = item.get("line")
            if not title:
                raise LLMError("章节标题为空")
            if not isinstance(line, int) or isinstance(line, bool) or not 0 <= line < len(lines):
                raise LLMError(f"章节行号非法：{line!r}")
            entries.append((line, title))
        if len(entries) > max_chapters:
            raise LLMError(f"章节数 {len(entries)} 超过上限 {max_chapters}")
        if len({line for line, _ in entries}) != len(entries):
            raise LLMError("章节行号有重复")

        entries.sort()
        chapters: list[Chapter] = []
        if entries and entries[0][0] > 0:
            preface = "\n".join(lines[: entries[0][0]]).strip()
            if preface:
                chapters.append(Chapter(fallback_title, preface))
        for index, (start, title) in enumerate(entries):
            end = entries[index + 1][0] if index + 1 < len(entries) else len(lines)
            body = "\n".join(lines[start + 1:end]).strip()
            chapters.append(Chapter(title, body))
        return chapters

    def extract_metadata(self, head: str, filename: str) -> dict[str, str]:
        raw = self._ask(METADATA_SYSTEM, METADATA_PROMPT.format(filename=filename, head=head))
        payload = load_json(raw)
        if not isinstance(payload, dict):
            raise LLMError("模型返回的不是 JSON 对象")
        return {
            key: str(payload.get(key) or "").strip()
            for key in ("title", "author", "description")
        }
