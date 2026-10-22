---
name: coding
description: Git 提交流程规范。每次创建 commit 后记录该提交的版本号（hash），并在执行 git push 前向用户完整展示本次将要推送的提交清单，等待确认。在提交代码、推送到远程仓库时使用。
---

# Coding 规范

## 1. 记录每次提交的版本

每次创建 Git commit 后，必须记录该提交的版本号：

- 同时给出**短 hash**（7 位，如 `9af5140`）和**完整 hash**
- 一并记录提交信息
- 在回复中明确写出，例如：

  ```
  提交：9af5140 (9af5140a1b2c3d4e5f6789012345678901234567) Add AGENTS.md with working guidelines
  ```

获取方式：

```bash
git show -s --format='%h (%H) %s' HEAD
```

## 2. 推送前必须展示

在执行任何 `git push` 之前，必须先向用户展示本次将要推送的提交清单，**不得直接推送**。

展示内容：

```bash
# 将要推送的提交（按远端分支为准）
git log origin/<分支名>..HEAD --oneline
```

- 列出每个提交的短 hash 和提交信息
- 标明目标远端和分支，例如：`即将推送到 origin/main：`
- 同时说明当前分支与远端的领先/落后情况（`git status -sb`）

示例输出：

```
即将推送到 origin/main：
  9af5140 Add AGENTS.md with working guidelines
  432e33f Add b.txt

当前状态：## main...origin/main [ahead 2]
```

用户确认后再执行推送；推送完成后回报推送结果（`<旧hash>..<新hash>  分支 -> 分支`）。

## 3. 例外

- 用户明确要求「直接推送 / 不要确认」时，可以跳过等待确认，但仍需在推送前展示清单并在推送后回报版本号。
