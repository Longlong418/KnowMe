# Coding Workspace 开发说明

## 这次加了什么

Coding 页面现在不只是文件预览，它还提供一个小型的编码任务工作台：

- 显示项目根目录和现有文件树；
- 检测本机是否安装了 `pi`、Claude Code、Codex；
- 允许用户启用或停用每个本地 Coding CLI；
- 选择本次任务使用的默认后端；
- 输入编码任务并发送给 Coding Agent；
- 显示 `delegate_task` 是否已经启用。

文件预览仍然是只读的。实际修改由本机 CLI 在指定项目目录中完成，Coding Agent 负责理解任务、调用后端、汇报结果。

## 配置保存在哪里

页面开关保存在：

```text
.knowme/coding.json
```

这个文件只保存后端开关和默认后端，不保存 API Key 或 CLI 登录信息。例如：

```json
{
  "default_backend": "pi",
  "enabled": {
    "pi": true,
    "claude": false,
    "codex": false
  }
}
```

`Coding 页面 → 允许 Coding Agent 调用本机 CLI` 对应现有的 `KNOWME_EXPERIMENTAL` 设置。打开它会启用 `delegate_task`，关闭后页面仍然可以保存后端偏好，但不会执行本机 Coding CLI。

## 执行流程

1. 用户在 Coding Workspace 输入任务。
2. 页面把项目根目录、任务内容和所选 backend 写进发送给 Coding Agent 的消息。
3. Coding Agent 调用 `delegate_task`。
4. `delegate_task` 根据 `backend` 选择 `pi`、`claude` 或 `codex`。
5. 子进程在指定 `cwd` 中运行，使用超时和过滤后的环境变量。
6. 输出保存到项目工作目录或 `.knowme/outbox/`，摘要返回聊天窗口。

## 后端适配

后端入口在 `knowme/tools/experimental.py`：

- `pi` 继续使用原来的 JSON 事件流和 token 记录；
- Claude Code 使用非交互 prompt 模式；
- Codex 使用 `codex exec --full-auto`；
- 三者都使用 `KNOWME_DELEGATE_TIMEOUT` 控制超时；
- 三者都使用 `KNOWME_DELEGATE_ENV_DENY` 过滤不应传递的环境变量。

Claude Code 和 Codex 使用各自 CLI 的登录状态。KnowMe 不会把 API Key 写入 `coding.json`，也不会为这两个 CLI 额外创建认证文件。

## 相关代码

- `knowme/applications/coding_workspace.py`：项目浏览、后端检测、Coding 配置；
- `knowme/ops/web/data.py`：Coding 数据和保存接口；
- `knowme/ops/web/server.py`：`/api/coding` 路由；
- `knowme/tools/experimental.py`：本地 Coding CLI 调度；
- `knowme/ops/static/js/coding.js`：任务输入和后端开关；
- `knowme/ops/static/js/views.js`：Coding 页面布局；
- `knowme/ops/static/style.css`：工作台样式。

## 当前限制

- 页面目前通过 Coding Agent 对话触发任务，尚未单独做“任务历史”数据库；
- Claude Code 和 Codex 的流式事件不会像 pi 一样逐条解析，完成后返回完整摘要；
- 默认不自动提交 Git，仍然需要用户检查 diff 后提交；
- 本机 CLI 的认证和权限由各自工具管理，使用前应确认它们的登录状态和项目目录权限。
