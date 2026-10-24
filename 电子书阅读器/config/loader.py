"""配置的唯一入口：加载即校验，业务代码不直接碰 YAML 和环境变量。"""

from __future__ import annotations

import codecs
import os
import re
from pathlib import Path
from typing import Any

import yaml

CONFIG_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CONFIG_DIR.parent
DEFAULT_FILE = CONFIG_DIR / "settings.yaml"
LOCAL_FILE = CONFIG_DIR / "settings.local.yaml"

LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")


class ConfigError(RuntimeError):
    """配置缺失或非法——启动阶段就失败，不允许带着坏配置运行。"""


# 必填项 -> (类型, 最小值, 最大值)；范围用于挡住 0、负数这类明显错误
CHECKS: dict[str, tuple[type, float | None, float | None]] = {
    "model.base_url": (str, None, None),
    "model.name": (str, None, None),
    "model.api_key_env": (str, None, None),
    "model.max_tokens": (int, 1, 1_000_000),
    "model.timeout_s": (int, 1, 600),
    "model.retries": (int, 0, 10),
    "runtime.log_level": (str, None, None),
    "runtime.log_retention_days": (int, 1, 3650),
    "server.host": (str, None, None),
    "server.port": (int, 1, 65535),
    "server.max_upload_mb": (int, 1, 1024),
    "server.max_batch_files": (int, 1, 100),
    "frontend.host": (str, None, None),
    "frontend.port": (int, 1, 65535),
    "frontend.request_timeout_s": (int, 1, 3600),
    "frontend.max_files": (int, 1, 100),
    "frontend.preview_chars": (int, 100, 20_000),
    "graph.recursion_limit": (int, 1, 200),
    "ingest.min_chars": (int, 0, 10_000_000),
    "chapters.min_chapters": (int, 0, 10_000),
    "chapters.long_text_threshold": (int, 0, 10_000_000),
    "chapters.max_title_chars": (int, 5, 200),
    "quality.garbled_ratio": (float, 0.0, 1.0),
    "quality.blank_ratio": (float, 0.0, 1.0),
    "llm_fallback.max_input_chars": (int, 100, 10_000_000),
    "llm_fallback.max_chapters": (int, 1, 10_000),
    "metadata.description_max_chars": (int, 10, 10_000),
    "epub.cover.width": (int, 100, 5000),
    "epub.cover.height": (int, 100, 5000),
}


def _deep_merge(base: dict, override: dict) -> dict:
    """本地覆盖：只覆盖写了的键，嵌套字典递归合并。"""
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _read(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ConfigError(f"{path.name} 顶层必须是映射")
    return data


def project_path(value: str | Path) -> Path:
    """把配置里的相对路径解析到项目根目录，避免受运行时 cwd 影响。"""
    path = Path(value).expanduser()
    return path if path.is_absolute() else PROJECT_ROOT / path


class Config:
    def __init__(self, data: dict[str, Any]):
        self._data = data
        self._validate()

    def get(self, dotted: str) -> Any:
        """按 a.b.c 取值；缺失直接报错，不给隐式默认值。"""
        node: Any = self._data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                raise ConfigError(f"缺少配置项 {dotted}")
            node = node[part]
        return node

    def env(self, dotted: str) -> str:
        """密钥类配置：配置里存变量名，运行时从环境变量取。"""
        name = self.get(dotted)
        value = os.environ.get(name)
        if not value:
            raise ConfigError(f"环境变量 {name} 未设置（由配置项 {dotted} 指定）")
        return value

    def with_overrides(self, overrides: dict) -> "Config":
        """深合并出一份新配置，测试与 CLI 参数覆盖用。"""
        return Config(_deep_merge(self._data, overrides))

    def _validate(self) -> None:
        for key, (kind, low, high) in CHECKS.items():
            value = self.get(key)
            if not isinstance(value, kind) or isinstance(value, bool) and kind is int:
                raise ConfigError(f"{key} 必须是 {kind.__name__}，实际是 {type(value).__name__}")
            if low is not None and value < low:
                raise ConfigError(f"{key} 不能小于 {low}，当前 {value}")
            if high is not None and value > high:
                raise ConfigError(f"{key} 不能大于 {high}，当前 {value}")

        if self.get("runtime.log_level") not in LOG_LEVELS:
            raise ConfigError(f"runtime.log_level 只能是 {' / '.join(LOG_LEVELS)}")

        patterns = self.get("chapters.patterns")
        if not isinstance(patterns, list) or not patterns:
            raise ConfigError("chapters.patterns 必须是非空列表")
        for pattern in patterns:
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ConfigError(f"chapters.patterns 里的正则非法：{pattern!r}（{exc}）") from exc

        encodings = self.get("ingest.encodings")
        if not isinstance(encodings, list) or not encodings:
            raise ConfigError("ingest.encodings 必须是非空列表")
        for encoding in encodings:
            try:
                codecs.lookup(encoding)
            except LookupError as exc:
                raise ConfigError(f"不认识的编码：{encoding!r}") from exc


def load_config(path: Path | None = None) -> Config:
    """读默认配置，若存在本地覆盖则深合并。进程启动时调用一次。"""
    target = path or DEFAULT_FILE
    data = _read(target)
    if path is None and LOCAL_FILE.is_file():
        data = _deep_merge(data, _read(LOCAL_FILE))
    return Config(data)
