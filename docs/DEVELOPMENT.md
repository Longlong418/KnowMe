# KnowMe 开发文档

## 项目概览

一个以自研 Agent Core 为底座的个人 Agent 工作平台。支持多Agent记忆隔离，提供Reader应用和Context Bridge。

## 当前状态 ✅ 完成

### Phase 1：Multi-Agent 记忆隔离
- ✅ 所有记忆表（facts, episodes, chat_log）添加 `agent_id` 列
- ✅ SqliteFactStore/EpisodeStore 按 agent_id 过滤
- ✅ Memory、Session、KnowMe 类接受 agent_id 参数
- ✅ consolidation.py 按 agent_id 处理

### Phase 2：Reader 应用与 Context Bridge
- ✅ Reader 工具：get_document(), get_selection(), add_note(), highlight()
- ✅ /api/extras 端点：存储 Application 状态
- ✅ 前端 Reader 视图：文件输入、文档显示、文本选中
- ✅ 自动注入：文档内容自动进入 Agent 上下文

### Phase 3：Knowledge Base MVP
- ✅ SQLite notes 表支持 `agent_id`，旧数据库启动时自动迁移
- ✅ 知识库 CRUD、搜索、文件夹和 `[[双向链接]]`
- ✅ Agent 工具和 Dashboard API 均按当前 Agent 隔离
- ✅ 标题更新、UUID、空标题校验、删除结果和 wiki-link 转义
- ✅ `evals/deterministic/test_knowledge_base.py` 覆盖核心行为

## 四栏布局设计

```
┌──────────────┬───────────────────────┬───────────────┬──────────────────┐
│ Sidebar      │ Main Workspace        │ Agent Panel   │ Chat Dock        │
│              │                       │               │                  │
│ Agents       │ Reader                  │               │                  │
│ Applications │ 知识库                   │               │                  │
│ Manage       │ ...                     │               │                  │
└──────────────┴───────────────────────┴───────────────┴──────────────────┘
```

## 核心文件

### 后端
```
knowme/
├── core/runtime.py      # Agent 运行时
├── core/loop.py         # observe → reason → act 循环
├── tools/reader.py      # Reader 工具
├── applications/context_bridge.py  # Context 桥接
└── ops/dashboard.py     # Dashboard + API
```

### 前端
```
static/
├── index.html           # 主页面
├── style.css            # 样式
└── js/
    ├── main.js          # 主逻辑 + Reader 加载
    ├── views.js         # 视图函数
    ├── dock.js          # 聊天面板
    └── ...              # 其他模块
```

## 使用示例

```bash
# 启动 Dashboard
cd D:\LLM\Agent\knowme-agent
.\.venv\Scripts\python.exe -m knowme.ops.dashboard
```

访问 http://localhost:8888

### MVP 流程
1. 左侧选择 **Learning Agent**
2. 点击 **阅读器** → 加载文档
3. 在右侧提问 → 文档自动进入上下文
4. 选中文本 → "注入到聊天"
5. 查看聊天卡片统计

## 代码维护说明

### git 提交规范
- `feat:` 新增功能
- `fix:` 修复 Bug
- `docs:` 文档更新
- `refactor:` 重构
- `chore:` 杂务

### 调试提示
- 测试文件：`evals/deterministic/`
- 查看日志：Dashboard 5 秒刷新，实时显示 trace
- 数据库：`.knowme/state.db`

## 下期计划

### Phase 4：文件解析增强
- PDF/EPUB 支持
- 文档进度条
- 多格式统一视图

保持开发文档持续更新中...
