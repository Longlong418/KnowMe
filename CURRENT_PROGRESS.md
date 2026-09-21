# KnowMe 实现进度更新

## Phase 11: Reader 上传链路修复（2026-09-22）
- ✅ 保持现有“两栏布局”：左侧 Agent/应用，右侧对话或当前应用，不做大规模重构
- ✅ 修复 Dashboard 空响应和非法 JSON 导致的 `Unexpected end of JSON input`
- ✅ 文档库上传、列表、搜索、打开、删除失败时显示可读错误和重试入口
- ✅ PDF 原始文件接口继续返回真实 PDF，Reader 仍使用 pdf.js 渲染，文本提取用于搜索和 Agent
- ✅ 删除左侧重复的“记忆数据”入口，避免和“记忆管理”显示同一页
- ✅ 相关改动已提交：`6ee4900`、`e270b53`

## Phase 1: Reader 应用增强 ✅ 完成
- ✅ 修复文件上传 bug：currentDoc 变量持久化，5秒刷新不丢失
- ✅ 新增 `renderReaderContent()` 刷新时恢复文档显示
- ✅ 新增 URL 粘贴输入框（`#rc-url-input`）
- ✅ 新增 `handleUrlInput()` 处理 URL/file 路径
- ✅ 新增 `restoreReaderState()` 在 render() 末尾调用
- ✅ 增强 views.js 样式：flex 布局、加载按钮
- ✅ 文档自动进入上下文（Context Bridge 已实现）

## Phase 2: 知识库系统 (Sapphire) ✅ MVP 完成
- ✅ 新增 `knowme/tools/knowledge.py`：
  - `create_note()`, `get_note()`, `update_note()`, `delete_note()`
  - `parse_links()` 提取 `[[wiki-links]]`
  - `list_notes()`, `search_notes()`, `get_linked_notes()`
  - 自动创建 SQLite 表 `notes`
- ✅ 新增 `/api/knowledge` 端点：
  - `knowledge_action(payload)` 处理 CRUD + 搜索 + 反向链接
- ✅ 新增前端 `VIEWS.knowledge()`：
  - 创建笔记表单、笔记列表、编辑器
  - Markdown 预览、文件夹筛选
- ✅ 前端 JS 函数：
  - `createKnowledgeNote()`, `viewKnowledgeNote()`, `saveKnowledgeNote()`
  - `deleteKnowledgeNote()`, `closeKnowledgeDetail()`, `renderKnowledgePreview()`

### MVP 收口修复（2026-09-21）
- ✅ notes 表增加 `agent_id`，旧表启动时自动迁移
- ✅ 知识库 CRUD、搜索、反向链接和 Agent 工具全部按 Agent 隔离
- ✅ 标题更新、UUID、空标题校验、删除结果和 wiki-link 转义
- ✅ 新增 `evals/deterministic/test_knowledge_base.py`

## Phase 3: Coding Workspace 只读 MVP ✅ 完成
- ✅ `KNOWME_PROJECT_ROOT` 选择项目根目录，默认使用启动目录
- ✅ 只读文件树、文本预览和路径穿越保护
- ✅ 打开代码文件后发布 `application: coding` Context Bridge
- ✅ 暂不开放任意写入和 Terminal，保留给后续审批闭环

## Phase 4: 多Agent增强 ⏳ 后续迭代
基于用户分享的 Octopus 界面，计划：

### 本轮隔离收口（2026-09-21）
- ✅ Dashboard 数据按 `agent_id` 查询，Memory、聊天、Trace 和数据库样本不再跨 Agent 返回
- ✅ 切换 Agent 后重新拉取对应 Dashboard 数据
- ✅ 新增 `evals/deterministic/test_dashboard_agent_scope.py`
- ✅ Memory Manager 支持同一 Agent 内合并事实；跨 Agent 合并会被拒绝

### 布局重构 + 轨迹时间线（2026-09-21）
- ✅ 三栏改两栏：删掉右侧聊天列，对话变成路由 `#agent/<id>`，右侧全宽
- ✅ `dock.js` → `chat.js`，对话面板由 `VIEWS.agent` 生成；`openAgent`/`selectAgent`
  单向路由（openAgent 只改哈希，selectAgent 只改状态），后退键可用
- ✅ `render()` 增加 agent 分支：5 秒轮询不重建对话（草稿、滚动、展开状态不丢）
- ✅ 每轮对话渲染步骤时间线：`core/runtime.py` 落库 `meta.steps`（≤40 步、截断），
  SSE `done` 带同样的 steps，`trace.js` 统一渲染；`llm`/`node_end` 事件不再被丢弃
- ✅ 旧回合（无 steps）照旧渲染芯片行，不需要迁移
- ✅ `evals/deterministic/test_turn_meta.py`、`test_knowme_facade.py`、
  `test_reader_frontend.py`（node 桩）锁住行为；基线 588 passed / 3 failed / 62 skipped

### 界面优化
1. **Agent 视图**：左侧 Agent 列表，支持分支 ✅（已完成）
2. **SmartReader 风格**：文件上传 + URL 粘贴 + 继续阅读卡片 ✅（已完成，见 Phase 10）
3. **Sapphire 知识库**：双栏布局，左侧文件夹右侧笔记列表

### 后端改进
- 继续完善知识库功能
- 添加文件树视图
- 改进 PDF/EPUB 解析

## Phase 5: 文档库 + PDF 渲染 + Reader Agent ✅ 完成（2026-09-21）
- ✅ `applications/library.py`：每份文档存**原文**（`home/documents/<uuid4><后缀>`，
  绝不用上传的文件名）和**抽取的文本**（SQLite），sha256 去重
- ✅ 中文可用的搜索：**LIKE 而不是 FTS5**——实测 FTS5 默认分词器对中文静默零命中，
  trigram 又搜不到两字词（详见 DEVELOPMENT.md Phase 10 的对照表）
- ✅ vendor pdf.js 6.3.289（Apache-2.0）进 `static/vendor/pdfjs/`，带 cmaps 和标准字体；
  选 6.x 是因为 4.2.67 以下有 CVE-2024-4367
- ✅ 阅读器重写：文档库列表 + 渲染的正文（Markdown 真的渲染了 / PDF 用 pdf.js 画，
  带文字层所以能选中）+ 内嵌 Reader Agent 问答面板
- ✅ `tools/documents.py`：list / search / **分窗口** open / fetch，整本书不进上下文
- ✅ 扫描版 PDF 存下来并标注「无文字层」，不再当成错误拒绝
- ✅ `pypdf` 升级为基础依赖（之前没声明，所以 PDF 分支永远报「请先安装 pypdf」）
- ✅ 前端拆分：阅读器 → `reader.js`，知识库 → `knowledge.js`，`main.js` 回归纯循环

### 下一轮候选
- EPUB / Office 格式
- 知识库的文件树 + 双栏编辑（Sapphire 风格）
- 阅读器：目录导航、阅读进度、在 PDF 上做笔记

## 代码可读性
- 所有函数都有中文注释
- 前端代码使用清晰的命名
- 文档更新在 docs/DEVELOPMENT.md

## 下一步
启动 Dashboard 测试完整工作流：
1. 创建笔记 → 使用 [[链接]] 语法
2. 加载 Markdown 文档
3. 提问 → 自动注入上下文
4. 查看迭代统计
