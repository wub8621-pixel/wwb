# cloud

工作区根目录，按子项目分目录存放。

| 路径 | 内容 |
|---|---|
| `resuem/` | 简历评估 Agent（LangGraph）：解析 → 四维评估 → 规则出等级，含 CLI、本地 Web 界面与评测脚本 |
| `.claude/skills/sopskill/` | 项目启动 SOP skill；根目录的 `sopskill` 是指向它的软链接 |

## 简历评估 Agent

见 [`resuem/README.md`](resuem/README.md)。命令在 `resuem/` 下执行，依赖装在根目录 `.venv/`。

```bash
cd resuem
../.venv/bin/python -m resume_agent.main "/path/to/简历目录"
../.venv/bin/python -m web.server          # http://<本机局域网IP>:7880
```

## sopskill

问项目名称 → 判断新建还是更新 → 选框架 → 分批收集配置并落到 `config/settings.yaml`
（代码只引用配置、密钥只存环境变量名）→ 交付「节点—职责—可调参数」对照表。

说一句「用 sopskill 新建一个 XX 项目」即可触发。
