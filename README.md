# 简历评估 Agent

用 LangGraph 编排的简历筛选工作流：解析 → 结构化 → 四维评估 → 规则聚合 → 等级。

**设计原则：能算的不问模型。** 工作年限由代码从日期区间计算，学历由规则查表；
模型只负责「技术成熟度」和「项目深度」两个需要语义理解的维度，最终等级仍由规则算出。

## 评分

| 维度 | 权重 | 来源 |
|---|---|---|
| 技术成熟度 | 40% | 模型判定 + 原文证据 |
| 项目深度 | 40% | 模型判定 + 原文证据 |
| 工作年限 | 15% | 代码计算（重叠月份只计一次，实习按 0.5） |
| 教育背景 | 5% | 规则查表（院校层次未配置时只按学历计分） |

硬门槛：工作年限 < 0.6 × 岗位要求时直接 `pass`，不看加权分。
等级：`≥85 优秀`、`≥70 一般`、`≥60 及格`、其余 `pass`。

## 快速开始

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

.venv/bin/python -m resume_agent.main "简历.pdf"
.venv/bin/python -m resume_agent.main "简历.pdf" --json

# 直接给目录：自动找 PDF/DOCX，多份时末尾输出按分数排序的汇总表
.venv/bin/python -m resume_agent.main "/path/to/简历目录"
.venv/bin/python -m resume_agent.main "/path/to/简历目录" --recursive   # 含子目录
```

目录默认只扫一层（`AI面试资料/` 这类参考目录不会被误收），加 `--recursive` 才递归。
多份时 `--json` 输出数组，单份仍输出对象。

模型复用环境变量 `ANTHROPIC_BASE_URL` / `ANTHROPIC_MODEL` / `ANTHROPIC_AUTH_TOKEN`
（当前指向 DeepSeek 的 Anthropic 兼容端点）。

## 本地界面

```bash
.venv/bin/python -m web.server            # 默认 http://<本机局域网IP>:7880
.venv/bin/python -m web.server --port 8000
```

浏览器打开后选择或拖入 PDF/DOCX，页面直接展示等级、总分、四维分数条、判定理由与原文证据。
只依赖标准库，不需要额外安装 Web 框架。

- 上传文件落临时目录，请求结束即删；**不落库、不保留简历**
- 模型调用串行执行，避免并发撞限流
- 只监听本机/局域网，不做公网暴露

## 评测

正确率必须用标注集衡量，否则无法判断任何改动是进步还是退步。

```bash
# 1. 生成标注模板（每份简历一行）
.venv/bin/python -m eval.run_eval --init "/path/to/简历目录"

# 2. 填写 eval/labels.csv
#    tech/project/education/tenure 填 0-100；grade 只能是 优秀/一般/及格/pass
#    同一份简历可由两位标注者各填一行，用于计算人的一致性上限

# 3. 跑分
.venv/bin/python -m eval.run_eval                  # 单次
.venv/bin/python -m eval.run_eval --repeat 3       # 每份跑 3 次取中位数
.venv/bin/python -m eval.run_eval --no-run         # 复用缓存结果
```

报告包含：精确一致率、相邻一致率、**二次加权 kappa**、混淆矩阵、各维度 MAE、
标注者之间的一致性，以及低置信度清单。

等级是有序的，所以主指标用二次加权 kappa：差一档的惩罚远小于差三档。

> `eval/labels.csv` 和 `eval/results.json` 含个人信息，已在 `.gitignore` 中排除。

## 解析层

主备双引擎：`unstructured` 优先，结果不达标（空结果、乱码、中文占比过低、
空元素过多）时自动降级到 `pymupdf` + `python-docx`，每次降级的原因都记录在结果里。

每页都出现的短行（页眉、页脚、培训机构水印）会被**剥掉而不是判失败**——
重复页眉是正常排版，不该让整份简历降级。剥离后的文本才是评估输入。

## 已知限制

- **主解析引擎当前空转**：中文 PDF 下 `unstructured` 返回 0 元素，DOCX 缺 NLTK 数据，
  实际总是走 native 分支。
- **分数有 3～4 分波动**：所用 SDK 已不支持 `temperature`，同一份简历重复跑，
  总分实测在 70.8～74.8、88.0～89.2 之间浮动（等级未变）。用 `--repeat 3` 取中位数缓解。
- **院校层次表未配置**：教育背景目前只按学历计分，配置后才会加分。
- **标注样本太少**：目前只有 2 份标注（1 优秀、1 一般），准确率数字只能证明链路通，
  说明不了泛化能力；且只标了等级、没标维度分，MAE 暂时算不出来。

## 项目启动 skill

`.claude/skills/sopskill/` 把这套流程固化成了一个 skill：

问项目名称 → 判断新建还是更新 → 选框架 → 分批收集配置并落到 `config/settings.yaml`
（代码只引用配置、密钥只存环境变量名）→ 交付「节点—职责—可调参数」对照表。

说一句「用 sopskill 新建一个 XX 项目」即可触发。

## 测试

```bash
PYTHONPATH=. .venv/bin/python -m unittest discover -s tests
```
