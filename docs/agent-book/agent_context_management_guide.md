# Agent 上下文管理指南

> 目标：让模型在每一次决策前，只看到当前任务最需要的信息。  
> 核心原则：**上下文管理不是“保存全部历史”，而是“构造当前最有价值的工作集”。**

---

## 1. 什么是上下文管理

在 Agent 系统中，模型真正能够直接使用的信息，只来自当前一次调用时传入的上下文。

因此可以把上下文管理理解为：

```text
历史消息
工具结果
长期记忆
任务状态
Skills
外部知识
系统规则
    │
    ▼
Context Manager
    │
    ├─ 选择
    ├─ 检索
    ├─ 排序
    ├─ 压缩
    ├─ 隔离
    └─ 丢弃
    │
    ▼
Final Context
    │
    ▼
LLM
```

Context Manager 的核心问题不是：

> 之前发生了什么？

而是：

> 为了让模型做好下一步决策，它现在最需要看到什么？

---

# 2. Context 不等于 Memory

这是最重要的区分之一。

## Memory

Memory 负责长期保存信息，例如：

- 用户偏好
- 项目背景
- 历史决策
- 学习进度
- 文档内容
- 数据库记录
- 长期知识库
- 过去任务结果

Memory 可以很大，也可以长期增长。

---

## Context

Context 是某一次 LLM 调用时真正放进模型输入窗口的信息。

它应该是：

```text
Context ⊂ Memory + 当前任务状态 + 当前执行轨迹
```

理想的数据流是：

```text
Memory / DB / Files / Vector Store
              │
              │ retrieve
              ▼
       Context Manager
              │
              │ select / rank / compress
              ▼
           Context
              │
              ▼
             LLM
```

不要把整个 Memory 直接塞给模型。

---

# 3. 上下文应该分层管理

推荐把 Agent 上下文分成四层。

---

## 3.1 稳定上下文

生命周期最长。

包括：

- System Prompt
- Agent 核心行为规则
- Tool Definitions
- Output Schema
- 基础安全规则
- Agent 身份与职责

例如：

```text
SYSTEM

你是一个个人学习 Agent。

你的职责：
1. 帮助用户理解知识。
2. 必要时调用搜索工具。
3. 不确定时优先检索，而不是猜测。
4. 所有学习成果都应该结构化记录。
```

### 原则

稳定部分应该：

- 尽量少变化
- 尽量保持前缀一致
- 不放频繁变化的信息

这样可以：

- 提高 Prompt Cache 命中率
- 提高 KV Cache 复用效率
- 减少无意义的上下文变化

---

# 4. 按需加载上下文

很多信息不需要永久放在上下文中。

典型例子：

- Skill
- 代码规范
- 项目文档
- API 文档
- 论文模板
- 特定工具说明
- 特定领域知识

推荐采用：

# Progressive Disclosure

即：

```text
只暴露能力目录
        │
        ▼
模型判断需要某项能力
        │
        ▼
加载对应 Skill
        │
        ▼
必要时继续读取详细资料
```

例如：

```text
Available Skills:

- paper-reader
  用于阅读和分析论文

- github-review
  用于代码仓库分析

- web-research
  用于网络检索

- learning-memory
  用于保存学习记录
```

模型决定调用：

```text
paper-reader
```

此时再加载：

```text
skills/paper-reader/SKILL.md
```

如果 Skill 内引用：

```text
references/evaluation.md
```

只有真正需要时再继续加载。

---

## 为什么不能把所有 Skill 都塞进去

假设有 50 个 Skill，每个 1000 tokens：

```text
50 × 1000 = 50000 tokens
```

但当前任务可能只需要其中 1 个。

大量无关规则会产生：

- Token 浪费
- 注意力竞争
- 指令冲突
- 模型行为不稳定

因此：

> **能力应该常驻索引，而不是常驻全文。**

---

# 5. 短期工作上下文

这一层是 Agent 当前正在执行任务时真正使用的信息。

包括：

- 最近几轮对话
- 当前计划
- 当前步骤
- 当前 Tool Result
- 当前文件
- 当前错误信息
- 当前任务目标

例如：

```text
User:
分析这个项目为什么测试失败。

Assistant:
我先检查测试日志。

Tool:
pytest ...

Result:
FAILED tests/test_agent.py::test_tool_call
AssertionError ...
```

这些信息非常重要，但是生命周期通常很短。

任务完成之后，大部分都不需要继续保留。

---

# 6. 显式维护 Agent State

不要要求模型每次都从几十轮历史中重新推断：

- 当前做到哪里
- 哪些步骤已经完成
- 哪些步骤失败
- 下一步应该干什么

应该维护一个明确的状态对象。

例如：

```yaml
task: 分析 Agent Context Manager
phase: implementation

completed:
  - 阅读 chapter2
  - 总结 Context Layer
  - 设计压缩机制

pending:
  - 设计 ContextBuilder
  - 编写测试

current_files:
  - src/knowme/agent/context.py

failed_attempts:
  - 直接保存完整 tool result

token_budget:
  remaining: 32000
```

每轮模型调用之前可以注入一个简化版：

```text
<agent_state>

Task:
实现 ContextManager

Current phase:
implementation

Completed:
- Context Layer
- Tool Result Compression

Pending:
- ContextBuilder
- Tests

Current file:
src/knowme/agent/context.py

</agent_state>
```

---

# 7. 为什么 Agent State 很重要

LLM 擅长：

```text
从上下文找到已经明确存在的信息
```

但不应该频繁要求它：

```text
重新阅读几十轮历史
→ 推断任务进度
→ 推断已经完成什么
→ 推断下一步是什么
```

否则容易出现：

- 重复执行
- 忘记步骤
- 已完成任务重新做
- 工具循环
- 状态错乱

因此：

> **任务状态应该由 Harness 管理，而不是依赖模型自己记忆。**

---

# 8. Tool Result 不应该永久保留

Tool Result 是最容易撑爆上下文的东西。

例如一次搜索：

```text
search()
→ 30 个网页
→ 40000 tokens
```

或者：

```text
grep repository
→ 2000 行代码
```

如果所有 Tool Result 永久保留：

```text
Conversation
 + Search Result
 + File Content
 + Logs
 + Stack Trace
 + Search Result
 + File Content
 ...
```

上下文会迅速膨胀。

---

# 9. Tool Result 的生命周期

建议区分三种情况。

## 立即使用型

例如：

```text
pwd
ls
git status
```

执行完并获得结论后，可以很快删除。

---

## 短期工作型

例如：

```text
读取当前修改文件
错误日志
测试结果
```

在当前任务阶段内保留。

阶段结束后压缩。

---

## 长期证据型

例如：

```text
最终测试结果
关键 benchmark
关键路径
重要设计决策
```

不应该保留完整 Tool Result。

应该转换成结构化摘要。

例如：

```yaml
test_result:
  command: pytest
  passed: 42
  failed: 0
  date: 2026-09-19
```

---

# 10. Context Compression

当上下文增长时，需要进行压缩。

但：

> Compression ≠ 普通摘要

上下文压缩必须围绕当前任务。

---

# 11. Task-aware Compression

同一个 Tool Result，在不同任务下应该生成不同摘要。

例如读取一个代码仓库。

---

## 如果任务是找函数

应该保留：

```text
文件路径
函数名
参数
调用位置
```

例如：

```text
login() 位于：

src/auth/service.py:42

调用链：

routes/login.py
→ AuthService.login()
→ token.create_access_token()
```

---

## 如果任务是理解架构

应该保留：

```text
模块职责
依赖关系
数据流
架构模式
```

例如：

```text
auth/
├─ routes.py
├─ service.py
└─ token.py

routes 负责 HTTP
service 负责业务逻辑
token 负责 JWT
```

---

## 如果任务是 Debug

应该保留：

```text
错误信息
触发条件
相关变量
调用栈
已经尝试过的方法
```

---

# 12. 压缩时绝对不能丢的信息

一些信息对 Agent 来说必须精确保留。

包括：

## Identifier

例如：

```text
文件名
路径
URL
commit hash
UUID
IP
端口
数据库字段
函数名
class 名
变量名
Tool Call ID
```

错误示例：

```text
commit: a83f029
```

被摘要成：

```text
commit: a83f...
```

这对人类可能还能理解。

对 Agent 来说可能直接导致下一步工具调用失败。

---

## 决策原因

例如：

```text
选择 SQLite，因为当前项目是单用户本地 Agent。
```

不要只留下：

```text
使用 SQLite。
```

因为之后模型可能重新推翻之前已经讨论过的设计。

---

## Failed Attempts

例如：

```text
尝试方案：

直接把所有 Tool Result 保存在 messages。

失败原因：

长任务中 Context 增长过快。
```

如果删除失败历史，Agent 很可能再次尝试同一个方案。

---

## TODO

例如：

```text
未完成：

- ContextBuilder
- CompressionPolicy
- SubAgentResult
```

---

# 13. Isolation 优于 Compression

这是长任务 Agent 中非常重要的原则。

> **能够隔离的信息，就不要先污染主 Context 再压缩。**

---

# 14. Sub-Agent Context Isolation

例如主 Agent 需要：

```text
找到项目中登录逻辑的位置。
```

最差的方案：

```text
Main Agent
   │
   ├─ grep
   ├─ read file 1
   ├─ read file 2
   ├─ read file 3
   ├─ ...
   ├─ read file 20
   │
   ▼
Context 爆炸
```

更好的方案：

```text
Main Agent
    │
    │ delegate
    ▼
Search Agent
    │
    ├─ grep
    ├─ read
    ├─ search
    ├─ inspect
    │
    ▼
Result

登录逻辑：
src/auth/service.py

入口：
login()

JWT：
src/auth/token.py
```

Main Agent 只看到：

```text
Search Agent Result
```

而不是：

```text
Search Agent 的全部执行历史
```

---

# 15. Sub-Agent 的本质

Sub-Agent 不只是为了：

```text
并行
```

它还有一个非常重要的作用：

# Context Isolation

每个 Sub-Agent 拥有自己的：

```text
context
history
tool results
temporary reasoning workspace
```

任务完成后：

```text
temporary context
→ discard
```

只返回：

```text
structured result
```

---

# 16. 推荐的 Sub-Agent 返回格式

例如：

```json
{
  "summary": "登录逻辑位于 auth/service.py",
  "evidence": [
    "src/auth/routes.py:32",
    "src/auth/service.py:45",
    "src/auth/token.py:18"
  ],
  "confidence": 0.96
}
```

主 Agent 不需要知道：

```text
grep 了多少次
打开了多少文件
中间猜错了多少次
```

---

# 17. 外部化重要信息

长期任务中，不应该把所有状态只存在：

```text
messages
```

而应该把稳定结果写入外部存储。

例如：

```text
docs/
memory/
database/
task_state/
artifacts/
```

可以存：

```text
架构决策
任务进度
学习笔记
实验结果
测试结果
重要结论
```

---

# 18. Context 应该有生命周期

推荐将信息分成四种生命周期。

```text
┌────────────────────────────┐
│ Permanent                  │
│ System / Core Rules        │
└────────────────────────────┘
             ↓

┌────────────────────────────┐
│ Task Scoped                │
│ Skill / Retrieved Memory   │
└────────────────────────────┘
             ↓

┌────────────────────────────┐
│ Working Context            │
│ Recent History / Tool      │
└────────────────────────────┘
             ↓

┌────────────────────────────┐
│ Ephemeral                  │
│ Logs / Search / Raw Output │
└────────────────────────────┘
```

越往下：

```text
生命周期越短
```

也应该：

```text
越大胆删除
```

---

# 19. 推荐的 Context 数据结构

可以定义：

```python
class Context:
    system: list
    skills: list
    memory: list
    recent_messages: list
    tool_results: list
    task_state: dict
```

进一步可以给每条 Context Item 添加元数据：

```python
class ContextItem:
    content: str

    source: str

    priority: int

    created_at: datetime

    expires_at: datetime | None

    compressible: bool

    persistent: bool
```

例如：

```python
ContextItem(
    content="当前测试失败：test_tool_call",
    source="pytest",
    priority=8,
    compressible=True,
    persistent=False,
)
```

---

# 20. Context Priority

可以设计优先级。

例如：

```text
10  System Rule
9   User Requirement
8   Current Task State
7   Current Tool Result
6   Relevant Memory
5   Skill Instructions
4   Recent Conversation
3   Old Conversation
2   Raw Search Result
1   Logs
```

当 Token Budget 不够时：

```text
优先删除低 Priority Context。
```

---

# 21. Context Budget

不要等模型 Context Window 快满了才压缩。

例如模型支持：

```text
128K
```

不代表 Agent 应该使用：

```text
127K
```

可以人为设置：

```text
target_context = 60K
warning_context = 80K
hard_limit = 100K
```

例如：

```python
if tokens > warning_context:
    compress()

if tokens > hard_limit:
    aggressively_prune()
```

这样给：

```text
模型输出
Tool Result
下一轮执行
```

保留空间。

---

# 22. 推荐 Context Builder

每一次调用模型之前，都重新构建 Context。

不要简单：

```python
messages.append(...)
```

然后永远增长。

推荐：

```python
context = ContextBuilder.build(
    system=system_prompt,
    task=current_task,
    state=agent_state,
    skills=skill_manager.select(task),
    memory=memory.retrieve(task),
    recent_history=history.recent(),
    tool_results=tool_buffer.active(),
)
```

然后：

```python
context = rank(context)

context = remove_irrelevant(context)

context = compress(context)

context = enforce_budget(context)
```

最后：

```python
response = llm(context)
```

---

# 23. 一个完整 Context Builder 流程

```text
User Input
    │
    ▼
Understand Current Task
    │
    ├───────────────┐
    ▼               ▼
Retrieve Memory   Select Skills
    │               │
    └───────┬───────┘
            ▼
       Agent State
            │
            ▼
     Recent History
            │
            ▼
    Active Tool Results
            │
            ▼
        Merge
            │
            ▼
    Relevance Ranking
            │
            ▼
      Deduplication
            │
            ▼
       Compression
            │
            ▼
      Token Budget
            │
            ▼
      Final Context
            │
            ▼
           LLM
```

---

# 24. 不推荐的设计

## 设计 1

```python
messages = []

while True:
    messages.append(user)
    messages.append(tool_result)
    messages.append(assistant)
```

问题：

```text
Context 永久增长
```

---

## 设计 2

所有 Skill 永久加载：

```text
system
+ Skill A
+ Skill B
+ Skill C
+ Skill D
+ ...
```

问题：

```text
无关信息过多
```

---

## 设计 3

Memory 全量注入：

```text
SELECT * FROM memories
```

然后全部塞给模型。

问题：

```text
Memory ≠ Context
```

---

## 设计 4

达到 Context Limit 后：

```text
summarize all messages
```

问题：

```text
会丢失 identifier
会丢失 TODO
会丢失失败历史
会丢失决策原因
```

---

# 25. 推荐架构

一个完整 Agent Harness 可以拆成：

```text
Agent
│
├── AgentLoop
│
├── ContextManager
│   ├── ContextBuilder
│   ├── ContextSelector
│   ├── ContextCompressor
│   └── TokenBudget
│
├── MemoryManager
│
├── SkillManager
│
├── ToolManager
│
├── StateManager
│
└── SubAgentManager
```

其中：

```text
MemoryManager
```

负责：

```text
长期保存
```

而：

```text
ContextManager
```

负责：

```text
决定当前给模型看什么
```

---

# 26. 推荐的 ContextManager API

例如：

```python
class ContextManager:

    async def build(
        self,
        task,
        history,
        state,
    ):
        ...

    async def retrieve_memory(
        self,
        task,
    ):
        ...

    async def select_skills(
        self,
        task,
    ):
        ...

    async def compress(
        self,
        context,
    ):
        ...

    async def enforce_budget(
        self,
        context,
    ):
        ...
```

Agent Loop：

```python
while not done:

    context = await context_manager.build(
        task=task,
        history=history,
        state=state,
    )

    response = await llm.generate(context)

    event = await execute(response)

    state.update(event)
```

---

# 27. Context Garbage Collection

可以把 Context Manager 想成一种：

```text
Context GC
```

每轮结束后检查：

```text
哪些内容仍然需要？
哪些内容已经完成使命？
哪些内容可以压缩？
哪些内容应该存入 Memory？
哪些内容可以直接删除？
```

例如：

```text
Raw Search Result
     │
     ├─ useful conclusion
     │       │
     │       ▼
     │     Memory
     │
     └─ raw content
             │
             ▼
           Delete
```

---

# 28. 一个简单的 GC Policy

```python
for item in context:

    if item.persistent:
        keep()

    elif item.is_active:
        keep()

    elif item.can_compress:
        compress()

    elif item.expired:
        delete()
```

---

# 29. Context 与 Agent Loop 的关系

完整运行流程：

```text
User
 │
 ▼
Agent Loop
 │
 ▼
Context Manager
 │
 ├─ Memory Retrieval
 ├─ Skill Selection
 ├─ State Injection
 ├─ History Selection
 └─ Context Compression
 │
 ▼
LLM
 │
 ▼
Tool Call
 │
 ▼
Tool Result
 │
 ▼
State Manager
 │
 ▼
Context GC
 │
 └───────────────→ Next Loop
```

---

# 30. 最重要的设计原则

最后可以浓缩成十条。

## 1

**Context 不是历史记录。**

Context 是当前任务的工作集。

---

## 2

**Memory 不应该直接等于 Context。**

Memory 应该经过 Retrieve。

---

## 3

**不要把所有 Skill 永久加载。**

Skill 应该按需加载。

---

## 4

**Tool Result 默认应该是短生命周期。**

---

## 5

**压缩必须 Task-aware。**

---

## 6

**Identifier 不允许模糊压缩。**

---

## 7

**必须记录失败方案。**

避免 Agent 重复踩坑。

---

## 8

**状态应该显式维护。**

不要让 LLM 自己从历史推断。

---

## 9

**Isolation > Compression。**

能用 Sub-Agent 隔离的信息，不要污染主 Context。

---

## 10

每次调用模型之前，都问：

> **当前这一步，模型真正需要看到什么？**

而不是：

> **之前发生过什么？**

---

# 31. 推荐最终模型

可以把上下文管理理解成：

```text
Context =
    Stable Instructions
  + Current Task
  + Current State
  + Relevant Skills
  + Relevant Memory
  + Recent Working History
  + Active Tool Evidence
```

其中：

```text
Relevant
```

是整个 Context Engineering 最重要的词。

---

# 32. 一句话总结

> **优秀的 Agent 不是记得最多，而是每一步都能看到最合适的信息。**

