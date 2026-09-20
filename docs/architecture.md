# 架构 —— 白板图,刷新版

和前几期视频里那两张白板图是同一个系统(通用的 Harness/Loop/Memory/LLM-Ops 那张,
以及 Hermes 专用的那张),只是现在**每个框上都标了文件路径**。

```mermaid
flowchart TB
    subgraph GW["Gateway 入口层 — knowme/gateway/"]
        CLI["cli.py(默认)"]
        WEB["ops/dashboard.py(网页)"]
    end

    subgraph RUN["一次性的 Agent 运行 —— 这里的一切每轮对话都重建"]
        WM["工作记忆 — runtime/session.py<br/>SOUL.md + 记忆上下文 + 聊天历史"]
        subgraph LOOP["循环 — loop/agent.py"]
            LLM["调用 LLM<br/>(loop/models.py)"]
            TOOLS["工具 — tools/<br/>create_event · save_note · send_message"]
            LLM -->|工具调用| TOOLS -->|结果| LLM
        end
        WM --> LLM
        GUARD["结束循环的两道护栏:<br/>没有工具调用即退出 · 最大迭代次数"]
    end

    GW --> WM
    LLM -->|回复| GW

    subgraph MEM["记忆 — knowme/memory/"]
        GATE{{"retrieval_gate.py<br/>'这一轮需要记忆吗?'"}}
        PROC["procedural/ — SKILL.md<br/>该怎么做"}
        SEM["semantic/ — 事实(FTS5,<br/>或 Supabase pgvector)"]
        EPI["episodic/ — 带日期的事件"]
        CONS{{"consolidation.py<br/>'只在积累了 N 条新对话之后'"}}
        DB[("state.db — 一个 SQLite 文件")]
    end

    WM -.->|每一轮| GATE
    GATE -->|只在需要时| SEM & EPI
    PROC -->|关键词命中时| WM
    GW -->|保存消息| DB
    CONS -->|蒸馏成事实| SEM
    CONS -->|一条情景| EPI
    SEM & EPI --- DB

    subgraph OPS["LLM Ops — knowme/ops/ + evals/"]
        TRACE["tracing.py — 每次运行一条 trace<br/>JSONL 永远落盘 · OTel → Phoenix/Langfuse"]
        DET["evals/deterministic — 0/1<br/>'该触发的工具触发了吗?'"]
        JUDGE["evals/judge — 打分 %<br/>'这条回复好吗?'"]
        RGATE{{"release_gate.py"}}
        TRACE --> DET & JUDGE --> RGATE -->|评测通过| SHIP["发布:新的提示词/<br/>模型/配置版本"]
    end

    RUN -.->|每个事件| TRACE
```

## 值得抄走的设计决策

- **检索之前先过一道门**(而不是每轮都检索):用一个便宜模型判断「这条消息需要用户的记忆吗」——
  省延迟,而更重要的是,避免无关记忆去干扰回答。
- **巩固是批量的**(「攒够 N 条对话之后」),异步于回复路径,而且**丢得起**:如果摘要模型失败,
  聊天记录保持未巩固状态,不会损坏。
- **确定性评测和裁判评测永不混用。** 一个是单元测试,一个是带分数的意见。发布闸门要求前者 100% 通过、
  后者过阈值。
- **每一层都有一个无聊的默认值和一个有文档的升级路径** —— FTS5 → pgvector、mock 日历 → Google Calendar、
  JSONL → Phoenix/Langfuse。默认值永远是零注册的。
- **图包住循环,从不替换循环。** 当一轮对话需要固定结构(并行步骤、显式路由)时,一个可选的图工作流
  (`knowme/graph/`)会围绕那个**未被改动的**循环来编排节点 —— `full_agent` 节点**就是** `run_loop`。
  路由是读模型写下的状态的普通代码;任何失败都 fail open 回落到朴素循环;仪表盘从引擎自己的 `describe()`
  渲染拓扑,所以那张图不可能和实际漂移。参见 [loop-vs-graph.md](loop-vs-graph.md)。

## 这个项目故意不是什么

不是框架,不是多智能体,不是生产级。(即使有了图工作流也依然不是多智能体:图里的 `agent_node` 就是同一个循环
被当作一个步骤调用 —— 没有智能体之间的点对点消息,执行严格沿着边确定性地走。)它是那份**可读的蓝图** ——
OpenClaw 和 Hermes 是产品;这是照着读的那个下午。
