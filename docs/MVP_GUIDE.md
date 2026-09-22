# KnowMe Workspace MVP 使用与开发说明

这份文档只描述现在已经能用、已经验证的部分。没有完成的功能会明确写在最后，避免把路线图当成成品。

## 现在能做什么

启动 Web 后，左侧可以切换四个 Agent：

- **General**：通用助理，可以使用当前配置里全部已启用工具。
- **Coding**：面向代码理解和编码任务。只有显式允许的工具；实验性的 `delegate_task` 没开启时不会出现。
- **Learning**：配合 Reader 解释材料、回答选区问题、做笔记。
- **Research**：检索和比较资料，强调来源与结论的区分。

知识库笔记也按当前 Agent 隔离。Learning Agent 创建的笔记不会出现在 Coding Agent 的列表中；Agent 通过工具创建、搜索、更新和删除笔记时也遵守同一个边界。

Web 的 Memory、聊天、Trace 和数据库预览也按当前 Agent 拉取；切换左侧 Agent 会重新请求对应范围的数据。

Memory Manager 支持编辑、删除和合并事实。合并时保留目标事实、删除来源事实，且只能操作当前 Agent 的记录。

它们不是四套重复代码。四个 Agent 都运行同一个 `AgentRuntime` 和同一个 Agent Loop，区别只是：

```text
Agent = Prompt + Tools + Memory + Context Policy + Model
```

每个 Agent 有独立的：

- 对话列表和当前会话；
- 语义记忆与情景记忆；
- 工具白名单；
- Reader 当前文档和选区上下文。

## 最短启动步骤

项目会读取仓库根目录的 `.env`，不需要把 key 复制进代码。

```powershell
cd D:\LLM\Agent\knowme-agent
.\.venv\Scripts\python.exe -m knowme.ops.web
```

终端会打印实际地址。Windows 默认从 `http://127.0.0.1:8888` 开始；端口被占用时会顺延查找。

## 一条完整的 MVP 使用流程

1. 在左侧选择 **Learning Agent**。
2. 打开 **阅读器**，加载 `.md`、`.txt`、`.py`、`.json`、`.csv` 或 `.html` 文件。
3. 直接在右侧问“这篇文章的核心论点是什么？”——当前文档会自动进入这一轮上下文。
4. 在正文里选中一段文字，点击“发送到聊天”，再追问它的含义。
5. 展开聊天卡片中的统计，可以看到：
   - 上下文实际发送了多少条历史消息；
   - 是否附带 Application 上下文；
   - 记忆门控选择了跳过还是检索；
   - 调用了哪些工具；
   - 是否发生上下文压缩；
   - 迭代次数、模型和耗时。
6. 切换到 **记忆管理**，只能看到当前 Agent 自己的事实和情景。一个 Agent 无法编辑或删除另一个 Agent 的事实。
7. 打开 **知识库**，创建一条带 `[[另一条笔记]]` 的笔记。标题、正文、文件夹、搜索、反向链接和删除都通过同一个 Agent 范围保存。

## 代码从哪里读

建议按这个顺序：

1. `knowme/agents/catalog.py`：四个 Agent 到底有什么区别。
2. `knowme/core/runtime.py`：一轮对话如何组装上下文、执行 loop、保存结果和发 trace。
3. `knowme/core/loop.py`：最核心的 observe → reason → act 循环。
4. `knowme/applications/context_bridge.py`：Reader 页面状态如何安全进入当前 Agent。
5. `knowme/ops/browser_agent.py`：浏览器如何按需创建 Agent，并保持各自会话。
6. `knowme/ops/web/`：本地 HTTP API 和 SSE 流式事件；实现拆在 `web/runtime.py`、`web/data.py`、`web/server.py`。
7. `knowme/ops/static/`：无构建步骤的 HTML/CSS/JavaScript 前端。

## 为什么 MVP 继续使用原生前端

当前页面没有复杂的客户端数据模型，原生 HTML/CSS/JavaScript 已经能清楚地完成导航、SSE 流式聊天和 Reader。
此时迁移 Vue 3 会增加构建链和重写成本，却不会让核心流程更可靠，所以 MVP 暂不迁移。等 Application 组件和跨页面状态明显增多时，再评估 Vue 3。

## 验证命令

```powershell
.\.venv\Scripts\python.exe -m ruff check knowme
.\.venv\Scripts\python.exe -m pytest `
  evals\deterministic\test_multi_agent_workspace.py `
  evals\deterministic\test_application_context_bridge.py `
  evals\deterministic\test_run_turn_extra_context.py `
  evals\deterministic\test_browser_agent.py `
  evals\deterministic\test_session_resume.py `
  evals\deterministic\test_session_rotation.py `
  evals\deterministic\test_dashboard_routes.py `
  evals\deterministic\test_static_assets.py -q
```

## 明确留到后续的内容

- Coding Workspace 的编辑器、终端和变更审阅（当前 MVP 已支持安全只读文件树和文件上下文注入，详见 `docs/CODING_WORKSPACE_MVP.md`）；
- 独立的知识库导入、搜索、关联和批量整理界面；
- Agent 通过 Application 专用工具实时修改浏览器里的高亮和笔记；
- PDF / EPUB 的完整解析与阅读进度；
- URL 阅读的服务端抓取和网页正文清洗（当前版本使用浏览器 `fetch`，目标站点需要允许跨域读取）；
- 可视化创建自定义 Agent；
- Scheduler、Multi-Agent 协作、Connector 市场和远程访问；
- 前端框架迁移。

这些功能不会混进当前 MVP 的完成标准。当前标准是：核心 loop 可运行、Agent 能隔离、Reader 上下文能到达 Agent、执行过程可见、数据能持久化。
