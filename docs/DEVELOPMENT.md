# KnowMe开发文档

## 项目概览

一个以自研 Agent Core 为底座的个人 Agent 工作平台。支持多Agent记忆隔离，提供完整的阅读器应用。

### 核心特性
- **Multi-Agent Memory Isolation**：每个Agent拥有独立的记忆空间
- **Reader Application**：文档阅读+选中文本自动注入上下文
- **Context Bridge**：Application层数据的透明注入机制
- **Agent Panel**：实时查看Agent的推理轨迹

---

## 四栏布局说明

```
┌──────────────┬───────────────────────┬───────────────┬──────────────────┐
│ Sidebar      │ Application Workspace │ Agent Panel   │ Dock (Chat)      │
│              │                       │               │                  │
│ 导航菜单     │ 主内容区域            │ 聊天区        │ 聊天栏           │
│              │                       │ + Trace       │                  │
└──────────────┴───────────────────────┴───────────────┴──────────────────┘
```

---

## 关键目录结构

```
knowme/
├── core/              # Agent Core 核心运行时
│   ├── agent.py       # Agent基类
│   ├── memory.py      # 记忆系统
│   ├── context/       # 上下文管理
│   └── runtime.py     # 执行循环
├── tools/             # 可用工具
│   ├── reader.py      # Reader工具（本次新增）
│   └── __init__.py    # 工具注册
├── ops/               # 运维/监控
│   ├── dashboard.py   # Dashboard服务器 + API
│   └── static/        # 前端静态资源
│       ├── index.html # 主页面
│       ├── style.css  # 样式
│       └── js/        # JavaScript
└── memory/            # 记忆存储实现
```

---

## 核心改动说明

### 里程碑 1：Application Context Bridge 真正接入运行时（2026-09-21）

#### 这次解决了什么

此前 Reader 页面虽然会把选中文本发给 `/api/extras`，但聊天入口没有读取这份数据，
所以 Agent 实际上看不到用户正在阅读的内容。现在链路已经完整：

```text
Reader 打开文档 / 选中文字
  → ApplicationContextBridge 保存当前页面快照
  → 发起聊天时按 Agent + 会话读取快照
  → Session 把快照放进本轮临时上下文
  → Agent 回答
  → Trace 显示本轮是否带了 Application 上下文
```

Application 上下文是临时工作状态，不是长期记忆。它只存在于 Dashboard 进程中；
切换 Agent 或会话不会串数据，重启服务后也不会把上次打开的文章错误带入新对话。

#### 新增文件

- `knowme/applications/context_bridge.py`
  - `ApplicationState`：一份可读的页面快照，包括应用名、当前资源、正文、选区和少量元数据。
  - `ApplicationContextBridge`：按 `(agent_id, session_id)` 隔离快照，并负责把它变成模型可读的文本。
  - 正文默认最多 24,000 字符，选区最多 8,000 字符。限制发生在进入运行时之前，避免本地大文件撑爆模型上下文。

#### 运行时改动

- `KnowMe.respond(..., extra_context="")` 现在正式接受 Application 上下文。
- 带 Application 上下文的请求暂时直接进入完整 Agent Loop，不走只接收纯文本的快速分流图。这样不会为了省一次模型调用而丢掉当前文档。
- `AgentSpec.tools` 现在会在每轮执行前转成真正的工具子集。一个 Learning Agent 只声明 Reader 工具，就既看不到也调用不了 Coding 或日历工具。
- Trace 新增三个容易读懂的事件：
  - `turn_start`：谁、在哪个会话、用哪个模型开始执行。
  - `context`：Application 上下文字符数、历史消息数、实际发送条数、运行了哪些压缩阶段。
  - `turn_end`：迭代次数、耗时和工具结果状态。

这些事件只记录结构和计数，不记录完整系统提示词、文档正文或选区内容。

#### `/api/extras` 当前格式

```json
{
  "application": "reader",
  "resource": "notes.md",
  "content": "文档正文",
  "selection": "用户当前选中的段落",
  "agent_id": "default",
  "session_id": "dashboard-20260921-120000"
}
```

清除当前会话的页面状态：

```json
{
  "action": "clear",
  "agent_id": "default",
  "session_id": "dashboard-20260921-120000"
}
```

旧版 `type=selection / text / source` 请求仍然兼容，避免升级服务后已打开的旧页面突然失效。

#### 怎么验证

```bash
.venv/Scripts/python.exe -m pytest \
  evals/deterministic/test_agent_spec.py \
  evals/deterministic/test_application_context_bridge.py \
  evals/deterministic/test_run_turn_extra_context.py -q
```

覆盖内容包括：Agent 工具白名单、上下文隔离、长度上限、清除行为、Facade 透传和透明 Trace 事件。

### Phase 1: 多Agent记忆隔离（2024-09-20）

#### 背景
之前所有Agent共享同一个记忆空间，导致记忆相互干扰。

#### 解决方案
在所有记忆表中添加 `agent_id` 列，计算过程按agent筛选。

#### 关键文件变更

1. **数据库层** (`knowme/db.py`)
   - 为 `facts` 表添加 `agent_id` 列
   - 为 `episodes` 表添加 `agent_id` 列  
   - 为 `chat_log` 表添加 `agent_id` 列

2. **存储层** (`knowme/memory/semantic/store.py`, `episodic/store.py`)
   - `SqliteFactStore`：所有查询WHERE添加 `agent_id = ?`
   - `SqliteEpisodeStore`：所有查询WHERE添加 `agent_id = ?`

3. **Agent层** (`knowme/core/agent.py`)
   - `Memory.__init__(conn, settings, client, episode_store, agent_id="default")`
   - `Session` 接收 `agent_id` 参数
   - `KnowMe(spec)` 传递 `spec.name` 作为 `agent_id`

4. **集成层**
   - `consolidation.py`：按 `agent_id` 处理记忆整理
   - 托管存储（Supabase/Mem0/Zep/LangMem/Notion）：接受 `agent_id` kwarg

#### 使用示例
```python
# 创建隔离的Agent
from knowme.core.agent import Agent, AgentSpec

spec = AgentSpec(name="reader-agent", tools=["get_document"])
agent = Agent(spec)  # 自动使用 "reader-agent" 作为agent_id
```

---

### Phase 2: Reader应用（本次完成）

#### Reader工具 (`knowme/tools/reader.py`)

| 工具 | 作用 | 参数 |
|------|------|------|
| `get_document` | 读取本地文档 | `path`: 文档路径 |
| `get_selection` | 获取当前选中文本 | 无 |
| `add_note` | 为文档添加批注 | `document_path`, `note_text` |
| `highlight` | 标记重要文本 | `text`, `label` |

#### 工具注册
```python
# 在 tools/__init__.py 中
def build_registry(conn, settings, memory=None) -> ToolRegistry:
    # ... 其他工具 ...
    
    # Reader tools - always available
    from knowme.tools.reader import make_reader_tools
    for tool in make_reader_tools().values():
        registry.register(tool)
```

#### Context Bridge

Application层通过 `/api/extras` 端点存储额外上下文，Agent循环读取后注入。

**请求体：**
```json
{
  "type": "selection",
  "text": "选中文本内容",
  "source": "文档路径",
  "session_id": "当前会话ID"
}
```

**存储位置：** `_app_context["{session_id}:selection"]`

---

## 前端开发

### 导航入口（index.html）
```html
<a href="#reader" data-v="reader">阅读器</a>
```

### Reader视图（js/views.js）
```javascript
reader(d){
  // 显示文件输入、文档内容、选中文本面板
}
```

### 文件加载（js/main.js）
```javascript
function readerLoad(){
  // FileReader 读取本地文件，渲染到 #rc-content
}

function rcSend(){
  // 获取选中文本，POST到 /api/extras
}
```

---

## API参考

### GET /api/data
获取仪表盘所需的所有数据

### POST /api/chat/stream
启动一次对话轮次（Server-Sent Events）

### POST /api/extras
存储Application上下文
- **type**: `selection`
- **text**: 选中文本
- **source**: 文档来源
- **session_id**: 会话ID

### GET /api/models
获取可用模型列表

---

## 开发流程

### 本地运行
```bash
# 安装依赖
pip install -e ".[dev]"

# 启动Dashboard
python -m knowme.ops.dashboard

# 访问 http://localhost:7777
```

### 运行测试
```bash
# 运行所有测试
.venv/Scripts/python.exe -m pytest evals/

# 运行确定性测试
.venv/Scripts/python.exe -m pytest evals/deterministic -q
```

### 添加新Agent
1. 在 `spec.name` 中定义唯一名称
2. 在 `spec.tools` 中声明可用工具
3. 记忆自动按 `name` 隔离

---

## 常见问题

### Q: 如何查看某个Agent的记忆？
A: 访问 `/memory` 页面，切换"语义记忆"或"情景记忆"标签，记忆已按agent筛选。

### Q: 选择文本后没反应？
A: 检查控制台是否有错误，确认 `/api/extras` 端点响应正常。

### Q: 新工具不显示在Tools标签页？
A: 确保在 `tools/__init__.py` 的 `build_registry()` 中注册。

---

## 版本历史

### v0.3.0-dev (2026-09-21)
- **修复**：Reader 页面状态现在真正进入 Agent 本轮上下文
- **新增**：按 Agent + 会话隔离的 Application Context Bridge
- **新增**：`turn_start`、`context`、`turn_end` 透明运行事件
- **修复**：`AgentSpec.tools` 工具白名单在运行时生效
- **兼容**：保留旧版 `/api/extras` 选区请求格式

### v0.2.0 (2024-09-20)
- **新增**：多Agent记忆隔离
- **新增**：Reader应用（文档阅读+选中注入）
- **新增**：Context Bridge API (`/api/extras`)
- **优化**：前端三栏布局
- **优化**：工具注册体系化

### v0.1.0 (2024-09-19)
- 初始版本：Agent Loop、记忆系统、工具调用、仪表盘
