# 配置方案参考（YAML + 类型化 loader）

目标：**所有可调参数只有一处真相**，代码里读不到魔法数字，密钥永远不落盘。

## 1. 目录结构

```
config/
├── settings.yaml          # 提交到仓库，唯一事实来源
├── settings.local.yaml    # 不提交，本机覆盖（可选）
└── loader.py              # 加载 + 校验 + 取值
tests/
└── test_config.py         # 配置加载与非法值测试
```

`.gitignore` 追加：

```
config/settings.local.yaml
```

## 2. settings.yaml 完整示例

```yaml
# 唯一事实来源。每个键都写清「改大 / 改小会发生什么」。
# 修改后跑：pytest tests/test_config.py -q

model:
  provider: deepseek
  name: deepseek-chat
  base_url: https://api.deepseek.com/anthropic
  api_key_env: ANTHROPIC_AUTH_TOKEN   # 只存环境变量名，绝不写密钥
  max_tokens: 2048                    # 单次回复上限；调大更完整，但更慢更贵
  timeout_s: 60                       # 单次调用超时
  retries: 2                          # 失败重试；调大会拉长最坏耗时
  structured_output: true             # 要求模型返回 JSON；关闭则需自己解析文本

runtime:
  log_level: INFO                     # DEBUG 会打印完整提示词，注意隐私
  concurrency: 1                      # 并发调用数；>1 需自行限流

graph:
  recursion_limit: 25                 # 图的最大步数；超出抛 GraphRecursionError
  checkpoint: false                   # 断点续跑；开启后需要持久化目录
  checkpoint_path: .state/

rag:
  chunk_size: 800                     # 调大 → 上下文更完整；调小 → 召回更精准
  chunk_overlap: 120                  # 通常取 chunk_size 的 10%~20%
  top_k: 8                            # 召回条数；调大覆盖更全，但噪声和延迟上升
  score_threshold: 0.2                # 低于此分直接丢弃
  hybrid: true                        # 关键词 + 向量混合检索
  fusion:
    strategy: rrf                     # rrf（倒数排名融合）| weighted（分数加权）
    rrf_k: 60                         # RRF 平滑常数，常用 60
    weight_bm25: 0.4                  # 仅 weighted 策略生效
    weight_vector: 0.6
  rerank:
    enabled: true
    model: bge-reranker-v2-m3
    keep: 5                           # 重排后保留条数，需 <= top_k

tools:
  enabled: true                       # 关闭后图里不再进入工具节点
  allowed: [search_docs, calculator]  # 白名单；为空 = 全部拒绝
  unknown_tool_policy: reject         # reject（默认）| warn（放行但记录）| ignore
  log_denials: true                   # 记录被拒绝的调用，便于排查模型乱调工具
  max_calls_per_turn: 3               # 单轮最多并行调用几次工具
  max_rounds: 6                       # 工具循环总上限，防止模型来回打转

memory:
  short_term:
    max_turns: 10                     # 上下文保留最近多少轮；调大更连贯，但更贵
    overflow: summarize               # summarize（压缩成摘要）| drop（直接丢弃）
  long_term:
    enabled: true
    backend: postgresql               # postgresql | none
    dsn_env: MEMORY_PG_DSN            # 连接串只存环境变量名
    table: agent_memory
    store: [summary, facts]           # 存什么：messages / summary / facts（可多选）
    retention_days: 90                # 0 = 永久保留
    per_user: true                    # 是否按用户隔离

vector_store:
  type: milvus                        # milvus | pgvector | ...
  uri_env: MILVUS_URI
  collection: agent_docs
  metric: COSINE                      # COSINE | IP | L2；必须与索引一致
  index:
    type: HNSW                        # HNSW | IVF_FLAT | IVF_SQ8 | FLAT
    M: 16                             # HNSW：每个节点的邻接数，调大召回更好、内存更高
    ef_construction: 200              # HNSW：建索引时的候选宽度，越大越准、建得越慢
    nlist: 1024                       # IVF：聚类簇数，通常取数据量的平方根量级
  search:
    top_k: 8                          # 最终返回条数
    ef: 64                            # HNSW：查询候选宽度，需 >= top_k，调大更准更慢
    nprobe: 16                        # IVF：查询时探测的簇数，调大更准更慢

web:
  host: 0.0.0.0
  port: 7880
  max_upload_mb: 15

scoring:                              # 按业务改；没有该功能就整段删掉
  weights:
    tech: 0.40
    project: 0.40
    tenure: 0.15
    education: 0.05
  thresholds:
    优秀: 85
    一般: 70
    及格: 60
  hard_gate:
    min_years: 1.0                    # 岗位最低年限
    ratio: 0.6                        # 低于 min_years × ratio 直接判不通过
```

**注意**：不要留没有代码读取的键。配置项要么被用到，要么删掉。

## 3. loader.py 参考实现

```python
"""配置的唯一入口：加载即校验，业务代码不直接碰 YAML 和环境变量。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

CONFIG_DIR = Path(__file__).resolve().parent
DEFAULT_FILE = CONFIG_DIR / "settings.yaml"
LOCAL_FILE = CONFIG_DIR / "settings.local.yaml"


class ConfigError(RuntimeError):
    """配置缺失或非法——启动阶段就失败，不允许带着坏配置运行。"""


# 必填项 -> (类型, 最小值, 最大值)；范围项用于挡住 0、负数这类明显错误
CHECKS: dict[str, tuple[type, float | None, float | None]] = {
    "model.name": (str, None, None),
    "model.api_key_env": (str, None, None),
    "model.max_tokens": (int, 1, 1_000_000),
    "model.timeout_s": (int, 1, 600),
    "model.retries": (int, 0, 10),
    "graph.recursion_limit": (int, 1, 200),
    "rag.chunk_size": (int, 50, 8000),
    "rag.top_k": (int, 1, 100),
    "tools.max_rounds": (int, 0, 50),
    "memory.short_term.max_turns": (int, 1, 500),
    "memory.long_term.retention_days": (int, 0, 3650),
    "vector_store.search.top_k": (int, 1, 200),
    "vector_store.search.ef": (int, 1, 4096),
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

    def _validate(self) -> None:
        for key, (kind, low, high) in CHECKS.items():
            value = self.get(key)
            if not isinstance(value, kind) or isinstance(value, bool) and kind is int:
                raise ConfigError(f"{key} 必须是 {kind.__name__}，实际是 {type(value).__name__}")
            if low is not None and value < low:
                raise ConfigError(f"{key} 不能小于 {low}，当前 {value}")
            if high is not None and value > high:
                raise ConfigError(f"{key} 不能大于 {high}，当前 {value}")

        top_k = self.get("rag.top_k")
        keep = self.get("rag.rerank.keep")
        if keep > top_k:
            raise ConfigError(f"rag.rerank.keep({keep}) 不能大于 rag.top_k({top_k})")

        strategy = self.get("rag.fusion.strategy")
        if strategy not in ("rrf", "weighted"):
            raise ConfigError(f"rag.fusion.strategy 只能是 rrf 或 weighted，当前 {strategy!r}")

        index_type = self.get("vector_store.index.type")
        if index_type not in ("HNSW", "IVF_FLAT", "IVF_SQ8", "FLAT"):
            raise ConfigError(f"vector_store.index.type 不支持 {index_type!r}")
        # HNSW 的查询宽度必须覆盖返回条数，否则 ef 会悄悄限制住召回
        if index_type == "HNSW" and self.get("vector_store.search.ef") < self.get("vector_store.search.top_k"):
            raise ConfigError("vector_store.search.ef 必须 >= vector_store.search.top_k")

        policy = self.get("tools.unknown_tool_policy")
        if policy not in ("reject", "warn", "ignore"):
            raise ConfigError(f"tools.unknown_tool_policy 只能是 reject / warn / ignore，当前 {policy!r}")


def load_config(path: Path | None = None) -> Config:
    """读默认配置，若存在本地覆盖则深合并。进程启动时调用一次。"""
    target = path or DEFAULT_FILE
    data = _read(target)
    if path is None and LOCAL_FILE.is_file():
        data = _deep_merge(data, _read(LOCAL_FILE))
    return Config(data)
```

## 4. 在代码里使用

```python
# main.py —— 入口处加载一次，往下传
from config.loader import load_config

def main() -> None:
    cfg = load_config()
    app = build_graph(cfg)
    ...

# graph.py —— 只接收配置，不自己读文件
def build_graph(cfg) -> CompiledGraph:
    limit = cfg.get("graph.recursion_limit")
    ...
```

```python
# 节点里读参数
def retrieve(state, cfg):
    top_k = cfg.get("rag.top_k")
    ...
```

**不要**写成这样：

```python
top_k = 8                     # ✗ 魔法数字
top_k = os.getenv("TOP_K")    # ✗ 环境变量散落，配置失去唯一性
client = Anthropic(api_key="sk-...")  # ✗ 密钥硬编码
```

## 5. 工具白名单：必须在代码里校验

提示词里写「只能使用 search_docs / calculator」不构成约束，模型可以无视。
**执行前必须查白名单，默认拒绝**：

```python
import logging

logger = logging.getLogger(__name__)


class ToolDenied(RuntimeError):
    """工具不在白名单内，拒绝执行。"""


def dispatch_tool(name: str, args: dict, cfg) -> str:
    """所有工具调用的唯一入口：先校验，再执行。"""
    allowed = set(cfg.get("tools.allowed"))
    enabled = cfg.get("tools.enabled")
    if not enabled or name not in allowed:
        if cfg.get("tools.log_denials"):
            logger.warning("工具被拒绝：%s args=%s", name, args)
        policy = cfg.get("tools.unknown_tool_policy")
        if policy == "ignore":
            return ""
        if policy == "warn":          # 放行但留痕，仅用于本地调试
            logger.warning("白名单外放行：%s", name)
        else:
            raise ToolDenied(f"工具 {name} 不在白名单内")
    return REGISTRY[name](**args)
```

白名单的测试必须覆盖两条路径：

```python
def test_allowed_tool_runs():
    assert dispatch_tool("calculator", {"expr": "1+1"}, cfg) == "2"


def test_unknown_tool_is_denied():
    with pytest.raises(ToolDenied):
        dispatch_tool("shell_exec", {"cmd": "rm -rf /"}, cfg)
```

要点：白名单为空 = 全部拒绝；拒绝要留日志；别把校验散落在每个工具内部，
否则新增一个工具就可能漏一层。

## 6. 记忆与向量库：三个容易踩的坑

1. **短期轮数是轮数，不是 token**。`memory.short_term.max_turns: 10` 在长消息下仍可能超上下文，
   需要再按 token 预算截断一次。
2. **长期记忆的隐私与可追溯**。只存摘要/事实时保留原始消息 ID；`retention_days` 要真的执行
   （定时清理任务），写在配置里不等于会删除。
3. **索引参数要跟数据规模匹配**。HNSW 的 `M` / `ef` 适合中小规模；到百万级再对比 IVF 的
   `nlist` / `nprobe`。`metric` 建库后不能改，必须和 embedding 的训练方式一致。

## 7. 测试模板

```python
import os
import tempfile
import unittest
from pathlib import Path

import yaml

from config.loader import CHECKS, Config, ConfigError, load_config


class ConfigTest(unittest.TestCase):
    def test_default_file_loads(self):
        cfg = load_config()
        self.assertIsInstance(cfg.get("rag.top_k"), int)

    def test_missing_key_fails_fast(self):
        with self.assertRaises(ConfigError):
            Config({"model": {}}).get("model.name")

    def test_invalid_range_fails(self):
        data = yaml.safe_load(Path("config/settings.yaml").read_text(encoding="utf-8"))
        data["rag"]["top_k"] = 0
        with self.assertRaises(ConfigError):
            Config(data)

    def test_secret_comes_from_env_only(self):
        cfg = load_config()
        name = cfg.get("model.api_key_env")
        with tempfile.TemporaryDirectory() as tmp:   # 不依赖真实密钥
            os.environ.pop(name, None)
            with self.assertRaises(ConfigError):
                cfg.env("model.api_key_env")
```

## 8. 什么时候该加配置项

| 该进配置 | 不该进配置 |
|---|---|
| 用户可能想调的数字 | 语言关键字、常量 `0` / `1` 的含义 |
| 随环境变化的路径、端口 | 一次性计算的中间变量 |
| 模型名、阈值、权重 | 业务规则里的固定公式结构 |
| 开关（是否重排、是否调工具） | 测试里的期望值 |

判断标准：**如果用户问「这个能不能调」，答案是「能」，它就该在配置里。**
