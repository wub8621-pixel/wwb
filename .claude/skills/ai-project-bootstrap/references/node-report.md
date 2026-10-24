# 交付报告模板

项目完成后必须输出以下两部分。**没有这两部分，就等于没交付。**

---

## 一、框架节点表

每个节点一行。`代码位置` 必须精确到 `文件:行号`，方便直接跳转修改。

| 节点 | 职责 | 输入 | 输出 | 关键配置 | 代码位置 |
|---|---|---|---|---|---|
| parse | 解析文件为纯文本 | 文件路径 | `lines`、`pages` | `parsing.*` | `xxx.py:42` |
| retrieve | 按问题召回文档片段 | `query` | `docs` | `rag.top_k`、`rag.fusion.*` | `xxx.py:88` |
| generate | 基于上下文生成回答 | `query`、`docs` | `answer` | `model.*` | `xxx.py:120` |

**同时说明图的形状**（用户要靠它理解调用顺序）：

```
START → parse → retrieve → (有文档?) → generate → END
                       └──(无文档)→ fallback → END
```

若存在条件边，写清条件判断依据（规则还是模型）和判断阈值所在的配置键。

## 二、可调参数表

按用户最可能想调的顺序排列，**每项都要写「调大 / 调小会发生什么」**。

| 配置键 | 当前值 | 调大 | 调小 | 代码位置 | 验证方式 |
|---|---|---|---|---|---|
| `rag.top_k` | 8 | 召回更全，但更慢、噪声更多 | 更精准，但可能漏召回 | `retrieve.py:31` | `python -m eval.run_eval` |
| `rag.fusion.strategy` | rrf | — | — | `retrieve.py:24` | 同上，看命中率 |
| `tools.max_rounds` | 6 | 允许更长的工具链，可能变慢 | 更快，但复杂任务可能中断 | `agent.py:77` | 跑 3 个复杂用例 |
| `graph.recursion_limit` | 25 | 容纳更多循环，死循环风险上升 | 更早中止 | `graph.py:18` | 观察是否抛 GraphRecursionError |

---

## 三、调参速查（按「我想解决什么」索引）

| 你的感受 | 先动哪个 | 再动哪个 |
|---|---|---|
| 回答太笼统、没细节 | `rag.top_k` ↑ | `rag.chunk_size` ↓ |
| 检索不到该有的内容 | `rag.score_threshold` ↓ | `rag.hybrid: true`、`top_k` ↑ |
| 召回一堆不相关内容 | `rag.rerank.enabled: true`、`rerank.keep` ↓ | `top_k` ↓ |
| 回答太慢 | `top_k` ↓、`rerank.keep` ↓ | `max_tokens` ↓、`retries` ↓ |
| 模型该调工具时不调 | `tools.enabled` 检查、`allowed` 白名单 | 提示词里补充工具使用规则 |
| 模型反复调工具停不下来 | `tools.max_rounds` ↓ | `tools.max_calls_per_turn` ↓ |
| 结果不稳定、每次不一样 | 降低随机性（若模型支持）、`retries` ↑ | 固定评测集对比 |
| 图跑到一半报 recursion 错误 | `graph.recursion_limit` ↑ | 检查条件边是否形成死循环 |

---

## 四、填写示例（LangGraph RAG 问答）

### 节点表

| 节点 | 职责 | 输入 | 输出 | 关键配置 | 代码位置 |
|---|---|---|---|---|---|
| load | 读入并清洗文档 | 文件路径 | `chunks` | `rag.chunk_size`、`rag.chunk_overlap` | `graph.py:35` |
| embed | 向量化并写入索引 | `chunks` | `index` | `model.embedding` | `graph.py:58` |
| retrieve | 混合检索 + 融合 + 重排 | `query` | `docs` | `rag.top_k`、`rag.fusion.*`、`rag.rerank.*` | `graph.py:72` |
| tools | 模型决定是否调用工具 | `query`、`docs` | `tool_results` | `tools.enabled`、`tools.max_rounds` | `graph.py:96` |
| answer | 生成最终回答 | `docs`、`tool_results` | `answer` | `model.name`、`model.max_tokens` | `graph.py:120` |

图：

```
START → load → embed → retrieve → tools ─┬─(不需要工具)→ answer → END
                                          └─(需要工具)──→ tools（循环，最多 max_rounds）
```

### 可调参数（节选）

| 配置键 | 当前值 | 调大 | 调小 | 代码位置 |
|---|---|---|---|---|
| `rag.fusion.strategy` | `rrf` | — | — | `retrieve.py:24`：改为 `weighted` 后按 `weight_bm25` / `weight_vector` 加权 |
| `rag.top_k` | 8 | 召回更全、更慢 | 更准、可能漏 | `retrieve.py:31` |
| `tools.max_rounds` | 6 | 支持更复杂任务 | 更快收敛 | `agent.py:77` |
| `tools.max_calls_per_turn` | 3 | 一轮可并行更多工具 | 单轮更保守 | `agent.py:81` |

---

## 五、报告结束后必须补一句

> 以上参数都可以直接改 `config/settings.yaml`，不用动代码。
> 改完告诉我改了什么，我会重跑测试/评测并给你前后对比。
