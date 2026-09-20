# Personal Agent Workspace 产品方案

## 1. 产品定位

一个以 **自研 Agent Core** 为底座的个人 Agent 工作平台。

平台统一提供 Agent Loop、上下文管理、上下文压缩、工具调用、记忆、会话与执行 Trace；上层可以构建不同类型的 Agent，并通过 Application 为用户提供阅读、知识库、记忆管理、Coding 等具体工作空间。

一句话：

> **让 Agent 不只是聊天，而是能够长期运行、透明执行，并直接操作用户工作空间的个人 AI 平台。**

---

## 2. 核心产品结构

```text
Personal Agent Workspace

├── Agents
│   ├── General Agent
│   ├── Coding Agent
│   ├── Learning Agent
│   └── Research Agent
│
├── Applications
│   ├── Reader
│   ├── Knowledge Base
│   ├── Memory Manager
│   └── Coding Workspace
│
└── Agent Core
    ├── Agent Loop
    ├── Context Manager
    ├── Context Compaction
    ├── Tool Runtime
    ├── Memory
    ├── Session
    └── Event / Trace
```

---

## 3. Agent Core

所有 Agent 共用统一运行时，不重复实现 Loop。

核心能力：

- Agent Loop
- Context Builder / Context Compaction
- Tool Calling
- Memory Injection
- Session Management
- Model Adapter
- Event / Trace System

不同 Agent 主要通过以下内容进行区分：

```text
Agent = Prompt + Tools + Memory + Context Policy + Model
```

---

## 4. Application

Application 是用户与 Agent 共同工作的 Workspace，而不是简单页面。

例如：

### Reader
- 阅读网页 / PDF / Markdown
- 高亮与笔记
- 当前选中文本自动进入 Agent 上下文
- Agent 可解释、总结、检索、生成笔记

### Knowledge Base
- 管理个人知识
- 搜索、关联、整理知识
- Agent 可读取和写入知识库

### Memory Manager
- 查看不同 Agent 的记忆
- 编辑、删除、合并记忆
- 查看记忆来源与使用情况

### Coding Workspace
- 文件树、编辑器、Terminal
- Coding Agent 可直接操作项目

---

## 5. 前端设计

整体采用固定的三栏 Workspace：

```text
┌──────────────┬───────────────────────┬───────────────┐
│ Sidebar      │ Application Workspace │ Agent Panel   │
│              │                       │               │
│ Agents       │ Reader                │ Chat          │
│ Applications │ Knowledge             │ Trace         │
│              │ Memory                │ Tool Calls    │
│              │ Coding                │ Context       │
└──────────────┴───────────────────────┴───────────────┘
```

### Sidebar

上半部分：

```text
AGENTS
General
Coding
Learning
Research
```

下半部分：

```text
APPLICATIONS
Reader
Knowledge
Memory
Coding
```

### Agent Panel

右侧 Agent Panel 为全局组件，可在任何 Application 中打开。

主要包含：

- Chat
- Execution Trace
- Tool Calls
- Context
- Task 状态
- Token / Cost / Duration

Agent 的执行过程保持透明，例如：

```text
Search
 ↓
Read Document
 ↓
Create Note
 ↓
Update Knowledge
 ↓
Answer
```

---

## 6. Agent 与 Application 的连接

Application 向 Agent 暴露两类能力：

### Context

```text
当前 Application
当前文档
当前页面
当前选中文本
当前项目
当前文件
```

### Tools

例如 Reader：

```text
reader.get_document()
reader.get_selection()
reader.highlight()
reader.add_note()
```

通过统一的 **Application Context Bridge** 将 Application 状态注入 Agent，并允许 Agent 操纵 Application。

---


优先打通最核心的一条链路：

> **用户在 Application 中工作 → 调用 Agent → Agent 获取当前上下文 → 调用工具操作 Application → 全程 Trace 可见。**

后续再加入 Scheduler、Multi-Agent、Connector、远程访问等能力。
