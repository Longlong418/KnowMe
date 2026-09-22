# Coding Workspace MVP

这次新增的是 Coding Workspace 的第一条可用闭环：选择项目根目录 → 浏览文件树 → 打开文本文件 → 自动把文件内容放进当前 Agent 的 Context Bridge。

## 怎么用

启动 Web 前，可以用 `KNOWME_PROJECT_ROOT` 指定项目目录；不设置时使用启动命令所在的目录：

```powershell
$env:KNOWME_PROJECT_ROOT = "D:\LLM\Agent\knowme-agent"
.\.venv\Scripts\python.exe -m knowme.ops.web
```

左侧打开 **Coding Workspace**，点击文件即可预览。打开的文件会以 `application: coding` 发布到当前 Agent，随后在右侧聊天框提问时，Agent 会收到文件上下文和对应的 trace/context 统计。

## 安全边界

- 只展示常见文本文件，不展示 `.env`、`.git`、`.knowme`、虚拟环境、缓存和二进制文件。
- 文件路径必须位于项目根目录内，`..` 路径穿越会被拒绝。
- 单个文件最多读取 240 KB，超出部分不会送入浏览器上下文。
- 这个 MVP 不执行 Terminal 命令，也不允许浏览器直接写任意项目文件。
- Coding Agent 现有的 `delegate_task(cwd=...)` 仍然是明确的编码执行入口；后续会再补审批、命令回显和变更审阅。

## 主要代码

- `knowme/applications/coding_workspace.py`：路径边界、文件树和只读读取逻辑。
- `knowme/ops/web/`：`/api/workspace` 和 `/api/data.workspace`。
- `knowme/ops/static/js/views.js`：文件树与预览界面。
- `knowme/ops/static/js/main.js`：读取文件并发布 Context Bridge。
- `evals/deterministic/test_coding_workspace.py`：路径隔离、隐藏文件、读取和 API 行为测试。
