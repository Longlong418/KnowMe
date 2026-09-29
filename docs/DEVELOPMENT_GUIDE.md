# 开发指南

KnowMe 是一个无框架的 Personal Agent Platform。Agent Loop、会话、上下文、记忆和工具系统都由项目自己实现；不同 Agent 与 Application 在这套公共运行时上组合能力。

这份文档只说明当前代码如何运行和扩展，不记录开发过程日志。

## 本地环境

项目需要 Python 3.11+ 和 [uv](https://docs.astral.sh/uv/getting-started/installation/)。

```bash
uv sync --extra dev
uv run knowme
```

模型服务商和 API Key 可以在 Web 的“模型”页面配置。`.env`、`.knowme/`、运行轨迹和本机凭证都不能提交。

## 代码阅读顺序

1. `knowme/core/spec.py`：一个 Agent 可以声明哪些差异。
2. `knowme/agents/catalog.py`：内置 Agent 如何组合提示词和工具。
3. `knowme/core/runtime.py`：一次完整 Turn 如何装配。
4. `knowme/core/loop.py`：模型与工具之间的原生循环。
5. `knowme/core/session.py`：会话历史、恢复和持久化。
6. `knowme/core/context/`：工具结果预算、裁剪和状态摘要。
7. `knowme/memory/`：记忆门控、存储、整理和技能。
8. `knowme/applications/context_bridge.py`：Application 如何把页面状态交给 Agent。
9. `knowme/ops/web/` 与 `knowme/ops/static/`：Web API、SSE 和原生前端。

## 新增 Agent

Agent 是 `AgentSpec`，不是一套新的运行时代码。通常只需要在 `knowme/agents/catalog.py` 中增加一个 `AgentProfile`：

```python
AgentProfile(
    id="planner",
    name="Planner",
    icon="◇",
    description="把复杂目标拆成可执行计划。",
    spec=AgentSpec(
        name="planner",
        system_prompt="You are a planning agent...",
        tools=frozenset({"search_web", "save_note"}),
    ),
)
```

运行时会自动处理模型调用、会话、上下文策略、记忆和轨迹。需要注意：

- `id` 必须稳定，因为会话和运行记录会使用它。
- `tools` 是白名单；不要给 Agent 暴露不需要的能力。
- 如果默认上下文策略不合适，可以在 `AgentSpec.context_policy` 中提供另一条 `ContextPolicy`。
- 新增 Profile 后要覆盖列表接口、页面切换、工具边界和会话恢复测试。

## 新增 Application

Application 是带状态的工作界面。Reader 会发布当前文档和选区，Coding Workspace 会维护项目、文件预览和修改记录；它们最终都把需要的现场信息交给同一个 Agent Runtime。

新增 Application 时按下面的边界组织：

1. 在 `knowme/applications/` 中实现领域逻辑和数据操作。
2. 在 `knowme/ops/web/server.py` 与 `data.py` 中增加明确的 API。
3. 在 `knowme/ops/static/js/` 中增加页面和交互，并在 `bootstrap.js` 中登记模块。
4. 需要 Agent 理解当前页面时，通过 `ApplicationContextBridge.publish()` 发布有界的纯文本快照。
5. 发起对话时，用 `ApplicationContextBridge.render()` 的结果作为 `extra_context`，不要把前端对象直接传进 Core。
6. 为页面状态隔离、上下文长度边界和 API 行为补确定性测试。

Application 的持久数据应该进入自己的存储；Context Bridge 只保存当前页面的临时状态。

## 新增 Tool

1. 在 `knowme/tools/` 中实现工具，保持输入和输出容易测试。
2. 在 `knowme/tools/__init__.py` 中注册 `Tool` 和参数 Schema。
3. 如果工具只属于某些 Agent，把名称加入对应的工具白名单。
4. 给成功、失败和权限边界补确定性测试。
5. 如果工具会访问外部服务，在 `SECURITY.md` 中说明配置和数据流向。

## 前端约定

Web 前端目前是原生 HTML、CSS 和 JavaScript，没有 Node 构建链。`knowme/ops/static/js/bootstrap.js` 按顺序加载各功能模块；新增模块时必须显式加入 `FEATURES`。

修改静态文件后刷新页面即可看到结果；修改 Python 后端后需要重启 `uv run knowme`。

## 验证

```bash
uv run ruff check knowme evals
uv run python -m pytest -q evals/deterministic
```

确定性测试使用 `ScriptedClient`，不能依赖网络、真实 API Key 或本机已有的 `.knowme` 数据。需要真实模型判断的测试位于 `evals/judge/`。

提交前再确认：

- README 中的命令、链接和截图能在干净 checkout 中使用。
- 没有提交 `.env`、API Key、OAuth Token、`.knowme/` 或 `traces/`。
- 修改没有顺手重构无关代码。
- lint 和确定性测试都通过。
