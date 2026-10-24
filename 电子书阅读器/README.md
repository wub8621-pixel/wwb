# 电子书阅读器

把指定目录里的 txt 转成 EPUB：读取 → 切章 → 质量判定 →（必要时）LLM 兜底 → 生成封面与目录 → 打包。

**一个 txt 出一本书，产物落在 `output/`，日志落在 `logs/`。**

## 快速开始

```bash
cd 电子书阅读器
.venv/bin/pip install -r requirements.txt          # 首次

.venv/bin/python -m ebook.cli /path/to/txt目录
.venv/bin/python -m ebook.cli /path/to/目录 --recursive   # 含子目录
.venv/bin/python -m ebook.cli /path/to/目录 --no-llm      # 只用正则，不调模型
.venv/bin/python -m ebook.cli /path/to/目录 --json        # 结果以 JSON 输出
```

不传目录时读取 `config/settings.yaml` 的 `runtime.default_input_dir`；两者都空则报错退出（返回码 2）。

## HTTP 服务

```bash
.venv/bin/python -m ebook.api          # 监听配置里的 server.host:server.port（默认 127.0.0.1:7881）
```

| 方法 | 路由 | 说明 |
|---|---|---|
| GET | `/` | 浏览器打开时跳转到 `/docs` 接口文档 |
| GET | `/docs` | FastAPI 自带的交互式接口文档，可直接试调 |
| GET | `/health` | 存活检查；返回模型名、LLM 是否可用、各路由 |
| POST | `/api/convert` | 上传一个 txt，同步返回 EPUB 文件流 |
| POST | `/api/convert/batch` | 上传多个 txt，返回逐本结果与下载链接 |
| GET | `/api/runs/{run_id}` | 查询某次运行的结果（`output/runs/<run_id>/run.json`） |
| GET | `/api/download/{run_id}/{filename}` | 下载已生成的书 |

`/api/convert` 的响应头带 `X-Run-Id`、`X-Chapters`、`X-Repaired`。表单字段 `use_llm=false`
可对单次请求禁用模型兜底。上传上限、批量上限、路由前缀都在 `server.*` 配置里。

```bash
curl http://127.0.0.1:7881/health

curl -o 书名.epub -F "file=@/path/to/书名.txt" http://127.0.0.1:7881/api/convert

curl -F "files=@一本.txt" -F "files=@另一本.txt" \
  http://127.0.0.1:7881/api/convert/batch
```

每次请求的上传与产物落在 `output/runs/<run_id>/`，结果同时写入 `logs/reports/api-*.json`。

## 流程图

```
START → ingest → split → (needs_llm?) ─┬─ 是 → repair（LLM 兜底）→ metadata → build → END
                                        └─ 否 ─────────────────────→ metadata → build → END
```

## 节点表

| 节点 | 职责 | 输入 | 输出 | 关键配置 | 代码位置 |
|---|---|---|---|---|---|
| ingest | 读文件、探测编码、规范化换行 | 文件路径 | `text`、`encoding` | `ingest.encodings`、`ingest.min_chars` | `ebook/graph.py:37` |
| split | 正则切章并计算质量指标 | `text` | `chapters`、`quality` | `chapters.*`、`quality.*` | `ebook/graph.py:49` |
| route | 条件边：是否需要 LLM 兜底 | `quality` | 下一节点名 | `llm_fallback.enabled` | `ebook/graph.py:72` |
| repair | 让模型重新识别章节标题行 | `text`、失败原因 | `chapters` | `llm_fallback.max_input_chars`、`max_chapters` | `ebook/graph.py:77` |
| metadata | 书名/作者/简介，缺字段时问模型 | `text`、文件名 | `metadata` | `metadata.*` | `ebook/graph.py:106` |
| build | 生成封面、写 EPUB | `chapters`、`metadata` | `output_path` | `epub.*` | `ebook/graph.py:125` |

配套模块：切章规则 `ebook/chapters.py:57`、质量判定 `ebook/chapters.py:107`、
模型调用 `ebook/llm.py:97`、封面 `ebook/cover.py:40`、EPUB 打包 `ebook/epub.py:27`、
日志与报告 `ebook/logging_setup.py`。

## 可调参数

| 配置键 | 当前值 | 调大 | 调小 | 代码位置 |
|---|---|---|---|---|
| `chapters.patterns` | 第X章 / 序章 / Chapter N | 支持更多排版 | 误判减少 | `ebook/chapters.py:57` |
| `chapters.max_title_chars` | 40 | 长标题也能识别 | 正文误判为标题 | `ebook/chapters.py:47` |
| `chapters.min_chapters` | 3 | 更容易触发兜底 | 更难触发 | `ebook/chapters.py:107` |
| `chapters.long_text_threshold` | 5000 | 短文本不要求章节 | 短文本也要求 | `ebook/chapters.py:107` |
| `quality.garbled_ratio` | 0.05 | 更宽容 | 更容易调模型 | `ebook/chapters.py:107` |
| `quality.blank_ratio` | 0.40 | 更宽容 | 更容易调模型 | `ebook/chapters.py:107` |
| `llm_fallback.max_input_chars` | 30000 | 一次修更多正文，更慢更贵 | 超出部分不修 | `ebook/llm.py:97` |
| `epub.cover.width/height` | 800×1200 | 封面更清晰、体积更大 | 体积更小 | `ebook/cover.py:40` |
| `runtime.log_level` | DEBUG | 记录正文片段，便于排错 | 更安静 | `ebook/logging_setup.py:16` |
| `runtime.log_retention_days` | 7 | 日志留更久 | 更早清理 | `ebook/logging_setup.py:30` |

**调参速查**

| 你的感受 | 先动哪个 |
|---|---|
| 章节切得太碎 | `chapters.max_title_chars` ↓ |
| 该有的章节没切出来 | `chapters.patterns` 补规则；仍不行看质量报告是否触发兜底 |
| 模型调用太频繁 | `chapters.min_chapters` ↑、`quality.*` 放宽 |
| 书名/作者不对 | `metadata.author_patterns`；书名默认取文件名 |
| 封面中文变方块 | `epub.cover.font_path` 指定系统里的中文字体 |

## 日志与报告

- `logs/ebook.log`：按天切分，保留 `runtime.log_retention_days` 天；记录编码、章节数、兜底原因、产物路径
- `logs/reports/run-YYYYmmdd-HHMMSS.json`：命令行每次运行的逐本结果（供后续评测使用）
- `logs/reports/api-YYYYmmdd-HHMMSS.json`：HTTP 每次请求的逐本结果

## 评测

```bash
# 结构与内容质检（无需额外依赖）
.venv/bin/python -m eval.check_epub --epub-dir output --source-dir /path/to/txt目录

# 叠加 W3C EPUBCheck（需要 java 与 epubcheck.jar）
.venv/bin/python -m eval.check_epub --epub-dir output --source-dir /path/to/目录 \
  --java /path/to/java --epubcheck-jar /path/to/epubcheck.jar
```

| 指标 | 含义 | 期望 |
|---|---|---|
| 章节数 | 产物中的正文文档数 | 与预期一致 |
| 覆盖率 | 原文字符在产物中的保留比例（忽略空白，不校验顺序） | 100% |
| 缺字 | 原文有、产物没有的字符数 | 0 |
| 乱码 | 产物中的私用区/替换符字符数 | 0 |
| EPUBCheck | W3C 结构合规 | 0 errors / 0 warnings |

结果写入 `logs/reports/eval-YYYYmmdd-HHMMSS.json`，同时记入 `logs/ebook.log`。

> macOS 没装 Java 时，可以直接借用 IDE 自带的 JRE：
> `/Applications/PyCharm.app/Contents/jbr/Contents/Home/bin/java`。

## 测试

```bash
PYTHONPATH=. .venv/bin/python -m unittest discover -s tests -t .
```

## 已知限制

- 只处理纯文本；扫描版 PDF/图片不做 OCR
- 正文超过 `llm_fallback.max_input_chars` 时只修前一段，剩余内容并入最后一章
- 书前的目录页会被当成正文，暂不剔除
- 中文数字只解析常见写法（一～万），生僻写法不识别
- 封面依赖系统中文字体，找不到时会退回默认字体（中文可能显示为方块）
- 评测里的覆盖率是字符级召回，不校验顺序与段落结构；版式合规交给 EPUBCheck
- HTTP 服务默认只监听 `127.0.0.1` 且没有鉴权，不要直接暴露到公网；`output/runs/` 会随请求累积，需定期清理
