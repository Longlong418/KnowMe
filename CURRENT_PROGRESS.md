# KnowMe 实现进度更新

## Phase 16: 右侧提问报错 + 阅读器三处小毛病（2026-09-22）
- ✅ **右侧提问报 `'LoopResult' object has no attribute 'meta'`**：根因在**后端**——`respond()` 改成 `as_loop_result()` 之后，这个翻译函数把 `meta` 静默丢掉了，前端就一个字都答不出来
- ✅ `LoopResult` 补上 `meta` 字段，`as_loop_result()` 带上它；老测试只验"历史能画出来"，没验"刚答完推的和落库的是同一个"，所以补了 `test_turn_meta.py` 断言**实时 `done` 的 steps == 落库的 steps**
- ✅ 反向验证：去掉 `meta=self.meta`，这条测试立刻 FAIL
- ✅ **「收起提问」点不动**：面板状态变了、视图却没重建——`render()` 里"阅读器不重建"的守卫（防轮询清掉文件框和选中文字）顺手吞掉了这次点击；改成先清 `activeView` 再 `render()`，这一次重建、轮询照旧安静
- ✅ **左侧切换文档高亮不跟着走**：`renderLibrary()` 的 stamp 里没带"当前打开的文档 id"，切文档时 stamp 没变就直接 return 了；stamp 必须覆盖所有影响这段 HTML 的东西
- ✅ **侧边栏那个单独的 Reader agent 删掉了**：只删入口，`reader` profile、它的笔记和历史、`ASK_AGENT = "reader"` 全都不动——它现在只活在阅读器右边的提问面板里
- ✅ **PDF 支持 `ctrl+滚轮` 单独缩放**：拦下浏览器的整页缩放（`devicePixelRatio` 不变），缩放乘在已有的"适配窗宽"比例上（`zoom===1` 时逐像素不变）
- ✅ 缩放**锚在鼠标位置**（记下指针在哪一页的哪个高度比例，重画后把滚动位置调回去），重绘防抖 160ms，且**保留已「继续加载」的页数**
- ✅ 滚轮监听器用 `dataset.zoomWired` 做一次性标记（`#rc-content` 在重绘中不被替换，不然会越挂越多）
- ✅ `.zoomed` 把居中改成左对齐：可滚动容器里居中的 flex 元素一旦溢出就只能往右滚、滚不回左边
- ✅ `test_reader_frontend.py` 加了 `testPdfZoom` 分组（8 页 → 继续加载 12 页 → 只挂一次监听 → `ctrl+滚轮` 阻止页面缩放且页宽变大 → 页数没丢 → 普通滚轮不受影响 → 到上限停住）
- ✅ 真实 Chrome 实测 16 项全过：页宽 605px → 920px 而 `devicePixelRatio` 1 → 1（放大的是 PDF 不是网页）；右侧提问拿到真实回答和完整时间线，无「错误」
- ✅ 基线 **614 passed / 3 failed / 62 skipped**（3 个失败仍是既有的），ruff 干净
- ✅ 提交：`d823312`、`6926b48`、`66dc4b6`

## Phase 15: 知识库改成"一个知识库"（2026-09-22）
- ✅ 知识库显示**所有 Agent** 的笔记，不再只显示当前 Agent 的（停在 default 曾经是空的）
- ✅ 每张卡片标出来源 Agent（`kb-note-agent` 标签），文件夹筛选跨 Agent 取并集
- ✅ 看得到就打得开、存得下：`get/update/delete/search/links` 都跨 Agent；只有 `create` 还用当前 Agent
- ✅ **编辑不改变归属**：在 default 视图里编辑 learning 的笔记，它仍然是 learning 的
- ✅ **Agent 自己的工具完全没动**，依然只能看自己的笔记——隔离是"人 vs 模型"两个视角，不是取消隔离
- ✅ 反链也跨 Agent 了（能点过去的链接，另一边就该校验得到）
- ✅ localStorage 键从 `knowme_kb_note_<agent>` 收敛成 `knowme_kb_note`（一个知识库，一个记忆）
- ✅ 旧测试 `test_dashboard_api_keeps_crud_in_the_selected_agent_scope` 改名并改断言，新名字带上了"工具仍然隔离"
- ✅ 反向验证：把旧过滤加回去，6 条断言 FAIL 且消息可读；真机 Chrome 实测通过
- ✅ 基线 **613 passed / 3 failed / 62 skipped**（3 个失败仍是既有的），ruff 干净

## Phase 14: 三个 bug 的根因，其实只有一个（2026-09-22）
- ✅ 找到真正的元凶：`main.js` 的 `render()` 每次都在给一个已经被删掉的侧边栏计数位写数字，直接抛异常
- ✅ 异常发生在函数中段，所以它下面的「恢复阅读器文档」「恢复知识库笔记」从来没被执行过——三个 bug 是同一个原因
- ✅ 计数位改成 `setCount()`：元素不在就跳过，绝不连累整轮渲染
- ✅ 顺手修好 PDF：之前检查的是 `Uint8Array.fromBase64`，那是静态方法、不是原型方法，所以任何浏览器上的任何 PDF 都会被拒
- ✅ 顺手修好 arXiv 论文：URL 结尾 `.03762` 被当成扩展名，PDF 被存成了纯文本；现在按文件头字节判断，并会自动修正历史记录
- ✅ 新增 `test_dashboard_render_frontend.py`（21 条断言）和 5 条文档库测试；基线 **613 passed / 3 failed / 62 skipped**（3 个失败仍是既有的）
- ✅ 真实 Chrome 实测：arXiv 论文渲染出 8 页 canvas（以前是纯文本兜底）

## Phase 13: 刷新页面后回到刚才的位置（2026-09-22）
- ✅ 阅读器把打开的文档 id 记在浏览器里，重新加载页面后自动回到这份文档（已删掉则不做任何事）
- ✅ 知识库按 Agent 记住打开的是哪条笔记；点「新建」/「关闭」/删除时清掉这个记忆
- ✅ 修掉「创建、保存、删除后界面不更新」：以前赋同一个 `#knowledge` hash 不会触发任何刷新
- ✅ 保存后左侧笔记列表会重建（不再显示旧标题），详情区重新从服务器读取这条笔记
- ✅ 知识库首次打开不再同时显示「从一条笔记开始」和「创建新笔记」两个面板
- ✅ 新增 `evals/deterministic/test_knowledge_frontend.py`；基线 606 passed / 3 failed / 62 skipped

## Phase 11: Reader 上传链路修复（2026-09-22）
- ✅ 保持现有“两栏布局”：左侧 Agent/应用，右侧对话或当前应用，不做大规模重构
- ✅ 修复 Dashboard 空响应和非法 JSON 导致的 `Unexpected end of JSON input`
- ✅ 文档库上传、列表、搜索、打开、删除失败时显示可读错误和重试入口
- ✅ PDF 原始文件接口继续返回真实 PDF，Reader 仍使用 pdf.js 渲染，文本提取用于搜索和 Agent
- ✅ 删除左侧重复的“记忆数据”入口，避免和“记忆管理”显示同一页
- ✅ 相关改动已提交：`6ee4900`、`e270b53`

## Phase 12: 知识库第一轮可用化（2026-09-22）
- ✅ 保持现有两栏布局，只优化知识库应用内部
- ✅ 笔记列表、文件夹筛选、标题/正文搜索和新建入口
- ✅ 笔记编辑、Markdown 实时预览和 `[[WikiLink]]` 点击跳转
- ✅ 显示反向链接，知道哪些笔记引用了当前笔记
- ✅ 编辑中的笔记避开 5 秒轮询，草稿不会被刷新覆盖
- ✅ 相关前端与后端回归测试通过

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
