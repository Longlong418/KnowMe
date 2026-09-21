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

### Phase 4：Coding Workspace 只读 MVP
- ✅ 通过 `KNOWME_PROJECT_ROOT`（默认启动目录）选择项目根目录
- ✅ `/api/workspace` 提供受限文件树和文本读取
- ✅ 路径穿越、`.env`、`.git`、虚拟环境、缓存和二进制文件默认拒绝
- ✅ 打开文件后通过 `application: coding` 写入 Context Bridge
- ✅ 暂不开放任意写入和 Terminal；变更仍通过显式 `delegate_task(cwd=...)` 完成

### Phase 5：Dashboard Agent 隔离收口
- ✅ `/api/data?agent_id=...` 只返回当前 Agent 的 facts、episodes、chat log、Trace 和数据库样本
- ✅ 切换 Agent 后前端重新拉取对应数据，避免只靠浏览器端过滤
- ✅ 未知 Agent 在数据接口和 Workspace API 中都会被拒绝
- ✅ 新增 Dashboard 隔离回归测试

### Phase 6：Memory Manager 合并
- ✅ Semantic facts 可以在同一 Agent 内合并，目标事实保留、来源事实删除
- ✅ Agent 工具 `manage_memory(action="merge")` 和 Dashboard UI 共用同一个 Store 方法
- ✅ 合并操作失败时不会跨 Agent 修改数据

### Phase 7：修复阅读器「选择文件」误报 Bug（2026-09-21）

**现象**：在阅读器里点「选择文件」，选完文件后偶尔弹出「请选择一个文件」。
时好时坏，没有明显规律。

**原因**：Dashboard 每 5 秒整体刷新一次（`refresh()` → `render()`）。以前的
`render()` 对阅读器视图没有任何保护，每次刷新都把整块 HTML 重新生成一遍——
**包括那个 `<input type="file">`**。于是：

1. 你点「选择文件」，系统弹窗打开，你正在浏览文件（要花几秒）
2. 这期间恰好赶上一次 5 秒刷新，你正在操作的文件框被整个删掉，
   页面上换成了一个全新的、空的同名文件框
3. 你点确定，`readerLoad()` 用 `getElementById` 去页面上找文件框，
   找到的是第 2 步那个新的空框 → 文件没了 → 弹「请选择一个文件」

所以**只有在弹窗打开的那几秒里撞上刷新才会出问题**——这就是"时好时坏"的来源。

**怎么改的**（`knowme/ops/static/js/main.js`）：

1. `render()` 增加一条分支：如果当前就在阅读器、且没有切换页面，**跳过重建**。
   阅读器上没有任何靠轮询更新的内容，重建纯属浪费。
2. `readerLoad(this)`：不再靠 id 去页面上找文件框，直接用触发事件的那个元素读文件。
   就算 DOM 被换掉，文件仍然在事件源上——多一层保险。
3. `renderReaderContent()` 只在文档真的换了才重绘（用元素上的 `dataset.stamp` 做标记）。
   原来每 5 秒无条件重写 innerHTML，会**清掉你正在用鼠标拖选的文本**，
   正好毁掉"选中文本发给 Agent"这个功能。标记放在元素上而不是模块变量，
   所以视图被重建后（新元素没有标记）仍然会正常重绘，不会白屏。

**顺手清理**：`views.js` 里有一个旧的 `VIEWS.reader`，被 `main.js` 的同名定义覆盖，
是死代码——改它没有任何效果。已删除并留下说明，避免以后改错文件。

**回归测试**：`evals/deterministic/test_reader_frontend.py`，用 Node 跑真实前端源码
（配一个最小 DOM 桩）锁住上面三条不变量。需要 node；没装就自动 skip，不影响 `make gate`。

### Phase 8：三栏改两栏——对话变成一个页面（2026-09-21）

**改了什么**：左侧一条侧边栏 + 右侧一块全宽面板。原来固定在右边的聊天栏
（`aside#dock`，380px）整个删掉；点侧边栏里的 Agent，右侧**全宽**显示这个
Agent 的对话，浏览器地址变成 `#agent/coding` 这种形式，**后退键能用**，
刷新后对话还在。Application（阅读器、知识库）也跟着变全宽——读长文档
正是最需要宽度的时候。

**为什么这么改**：
1. 聊天栏永远占一竖条，不管你在不在聊天。读文档、看代码时白白少 380px。
2. 点 Agent 只是换个全局变量，不是导航——没法全屏看某个 Agent 的对话，
   后退键也没反应。
3. `#agent/<id>` 是路由以后，「网关」收件箱点一条对话、历史菜单点一条对话，
   都走同一条路：跳到那个 Agent 的对话页。

**几个关键设计**（改前端时别踩）：

- **路由是单向的，所以不会递归**。`openAgent(id)`（侧边栏按钮调它）只做一件事：
  改 `location.hash`。`selectAgent(id)` 是纯状态切换，**不碰哈希**。`render()`
  是唯一把两者对上的地方：发现哈希和 `ACTIVE_AGENT` 不一致才调 `selectAgent`。
  改完两边一致了，这条链就停——不需要防递归标志。
- **`render()` 多了一条 `view === "agent" && !subChanged` 分支**。和 Phase 7 的
  阅读器是同一类坑：对话面板里有正在输入的草稿（`#dmsg`）、滚动位置、展开的
  `<details>`，每 5 秒重建一次会全清掉。轮询时跳过重建，真正切换页面时照常重建。
- **`.chatlog` 这个 class 不能改名**。`style.css` 里 `body.no-tele .chatlog .tele`
  就是「统计」开关的全部机制——靠它把每轮的门控/耗时/迭代藏起来或显示出来。
- 对话面板的 HTML 不在 `index.html` 里，是 `chat.js` 的 `VIEWS.agent` 生成的；
  生成后由 `wireChat()` 绑定输入框、重绘消息列表（`render()` 每次都会调）。
- `dock.js` 改名成了 `chat.js`（`git mv`，历史还在，`git log --follow` 能看到）。

### Phase 9：每轮对话的「步骤时间线」（2026-09-21）

**改了什么**：原来每个助手回合头上是一排小芯片（`门控 · 工具 · 回复`），
看不出先后、耗时和参数。现在换成一棵**可展开的时间线**：每一步一个圆点，
标签在左、耗时在右，点开「详情」看原文（工具输出、节点写了什么、token 数）。
时间线跟着 `统计` 开关一起显隐。

**步骤从哪来**（三处产同一种形状，一个渲染器通吃）：
- **实时**：`applyStreamEvent`（render.js）边收 SSE 事件边 `pushStep`——
  时间线跟着回合一起长出来。`llm` 事件（第几次迭代、stop_reason、token 进出）
  和 `node_end`（keys/error）以前直接被丢掉，现在都记上——这两样恰恰是
  「Agent 到底干了什么」的核心。
- **历史**：`core/runtime.py` 把同样的步骤存进 `meta.steps`（跟着 chat_log 落库），
  重开一个对话渲染出来的时间线和当时直播的一模一样。有界：最多 40 步、
  每条 detail 截断，图再野也撑不大一行 chat_log。
- **Loop 标签页**：那些行是从当天的 trace 文件重建的，没有 meta.steps，
  `stepsFromTurn()`（trace.js）把 llm_calls/tools/gate 折成同样的步骤。

**为什么用原生 `<details>` 做展开**：`syncChatLogs()` 每收到一个流事件就把整个
消息列表 innerHTML 重写一遍。原生 `<details>` 的开合状态存在 DOM 里，重写后
照样是开的；换成 JS 里存状态就会在重绘时被清掉。

**旧对话怎么办**：steps 之前的回合没法补——trace 文件按天滚动，昨天的今天就
没了。这些回合照旧渲染成原来的芯片行（`legacyTrace`），等它们自然沉底。

**回归测试**：`test_turn_meta.py` 锁步骤的顺序、kind 闭合集合、五字段形状、
40 步上限和截断；`test_knowme_facade.py` 锁 meta 的键集合。基线变成
**588 passed / 3 failed / 62 skipped**（3 个失败仍是原来那三个）。

## 两栏布局设计

```
┌──────────────┬────────────────────────────────────────────┐
│ Sidebar      │ 全宽面板（按路由切换）                        │
│              │                                            │
│ Agents       │  #agent/<id>   → 这个 Agent 的对话 + 时间线  │
│ Applications │  #reader       → 阅读器（现在真的全宽了）     │
│ Manage       │  #knowledge    → 知识库                     │
│ 设置         │  其余          → 原来的诊断页，只是变宽了     │
└──────────────┴────────────────────────────────────────────┘
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
├── index.html           # 主页面（侧边栏 + 一块全宽面板）
├── style.css            # 样式
└── js/
    ├── main.js          # render/refresh 循环 + Reader 加载
    ├── views.js         # 视图函数
    ├── chat.js          # 对话视图（VIEWS.agent）+ 会话/历史/模型芯片
    ├── trace.js         # 每轮的步骤时间线
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
1. 左侧选择 **Learning Agent**（右侧变成它的对话页）
2. 点 **阅读器** → 加载文档（现在全宽）
3. 回到对话页提问 → 文档自动进入上下文
4. 选中文本 → 「发送给 Agent」
5. 展开回合头上的时间线，看这轮到底经历了哪些步骤

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
- **加新视图时的坑**：`render()` 只在**真正切换页面**时才重建 DOM。
  凡是自己持有状态的视图（阅读器的文件框和 URL 输入框、各种编辑器），
  都不能每 5 秒重建一次，否则用户正在输入或正在选的内容会被清掉。
  加视图时照着 `render()` 里已有的分支写。
- 前端改了 `.js`/`.css` 刷新浏览器即可；**改了 `.py` 必须重启 dashboard**。
- **已知失败的 3 个测试**（动手前先看一眼，别把它们算到自己头上）：
  `test_delegate_env.py` ×2、`test_packaging.py::test_the_bundled_skills_are_findable`。
  干净工作区就失败，与前端无关。当前基线：**588 passed / 3 failed / 62 skipped**。
- `test_static_assets.py` 里那个"剥工具块"的测试，会**从 `render.js` 里抽出**
  `stripTools` 用到的正则，再拿去跑后端真实产出的字符串——因为这条逻辑跨了
  JS 和 Python 两边，没有测试看着的话，格式一变就会静默失效（聊天卡片里
  直接显示整段工具输出）。所以：**改那条正则会被它抓到，改函数的写法不会**。
  2026-09-21 已把提取方式从"锚定函数写法"改成"锚定 `tools used` 标记"，
  以后重构 `stripTools` 不会再误报。

## 下期计划

### Phase 4：文件解析增强
- PDF/EPUB 支持
- 文档进度条
- 多格式统一视图

保持开发文档持续更新中...
