# KnowMe 实现进度更新

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

### 界面优化
1. **Agent 视图**：左侧 Agent 列表，支持分支
2. **SmartReader 风格**：文件上传 + URL 粘贴 + 继续阅读卡片
3. **Sapphire 知识库**：双栏布局，左侧文件夹右侧笔记列表

### 后端改进
- 继续完善知识库功能
- 添加文件树视图
- 改进 PDF/EPUB 解析

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
