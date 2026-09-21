# KnowMe 开发文档

## Phase 11：先把 Reader 的上传链路做稳（2026-09-22）

这一轮没有改 Dashboard 的两栏布局。左边仍然是 Agent 和应用入口，右边仍然显示当前 Agent 的对话，或者当前应用（阅读器、知识库、记忆等）。这次只处理 Reader 真正影响使用的问题。

### 这次修了什么

- 修复了浏览器端请求遇到空响应时的错误提示。以前 `postJSON()` 不管响应状态，直接调用 `response.json()`；服务端返回空的 404 或连接中断时，浏览器只会显示 `Unexpected end of JSON input`，看不出到底是哪一个接口失败。现在会先读取响应文本，再显示 HTTP 状态或服务端返回的错误。
- Dashboard 收到无效 JSON 时不再直接断开连接，而是返回一个带状态码的 JSON 错误。不存在的 POST API 也会返回 JSON，而不是空响应。
- 去掉了左侧导航里两个都跳到 `#memory` 的入口。现在只保留“记忆管理”；需要看原始记忆数据时，进入已有的“数据库”页面，不会再出现点两个菜单却看到同一页的情况。

### Reader 的正确数据流

1. 浏览器读取文件，转成 data URL。
2. `/api/library` 解码文件并调用 `applications/reader.py` 提取文字。
3. `applications/library.py` 同时保存原始文件和提取后的文字：原始文件用于 PDF 渲染，文字用于搜索和给 Agent 阅读。
4. 打开文档时，接口返回提取文字和 `/api/library/file?id=...`。
5. Markdown/文本直接渲染；PDF 使用内置 pdf.js 渲染原始文件，扫描版 PDF 没有文字层时仍然可以显示页面，只是不能搜索和引用其中的文字。

### 调试上传失败时先看这三件事

- 确认浏览器连接的是刚启动的 Dashboard 端口。旧的 Dashboard 进程如果还占着端口，旧版本可能没有 `/api/library`，这时 PDF 会表现为 `Failed to fetch`。
- 打开浏览器 Network 面板，检查 `POST /api/library` 的状态和响应 JSON；现在即使失败也会返回可读错误，不会再只显示 JSON 解析错误。
- PDF 仍然打不开时，直接访问 Network 里返回的 `/api/library/file?id=...`。如果它返回 `application/pdf`，说明上传和保存成功，问题只在浏览器端 PDF 渲染；如果返回 404，说明连接的不是包含文档库路由的 Dashboard 实例。

### 还没有做的事情

这轮没有引入 Vue，也没有重做整体布局。先确保“添加文件 → 文档库出现 → 文本/PDF 能打开 → 选中文本能交给 Agent”稳定，再继续完善知识库和更丰富的 Reader 功能。

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

### Phase 10：文档库 + 真的能看 PDF（2026-09-21）

**改了什么**：阅读器从「一个文本预览框」变成**文档库**：左边是文档列表 + 搜索，
右边是渲染好的正文，最右边是可以随时提问的 Reader Agent。

**三个原始投诉，各自的根因**：
1. **「上传文件他不给我保存」** —— 文档只存在浏览器变量里，页面一刷新就没了。
   现在存在服务端的文档库里（`applications/library.py`）。
2. **「markdown 也渲染不了」** —— 原来的代码把文本按空行切开、每段包一个 `<p>`，
   markdown 变成了「关于它自己的散文」。现在调 `renderMarkdown()`，
   而且**必须包在 `<div class="r">` 里**——`style.css` 里所有 `.md*` 规则都挂在 `.r` 下面，
   少一层包裹，标题/列表/表格就全部没有样式，看起来就是「没渲染」。
3. **「PDF 也不支持」** —— `pypdf` 是个**没有声明的可选依赖**，
   所以 `reader.py` 的 PDF 分支在任何没碰巧装上它的环境里都报「请先安装 pypdf」。
   现在它是 `pyproject.toml` 的基础依赖，并且 vendor 了 pdf.js 做真正渲染。

**每份文档存两样东西（这是有意的）**：
- **原文**放在 `<home>/documents/<uuid4><后缀>`。浏览器要渲染 PDF 就得拿到真文件
  （插图、排版、可选中的文字层），抽出来的文本给不了这些。
  存盘**绝不用上传的文件名**——文件名是攻击者可控的输入，不能由它决定文件落在哪。
- **抽出的文本**存 SQLite，搜索和 Agent 读的都是它，不用每轮重新解析 PDF。

**搜索结果为什么是 LIKE 而不是 FTS5**（这是实测推翻的设计）：
先按 `db.py` 的 `facts_fts` 那套做了 FTS5 索引，然后量了一下：

| 分词器 | 「检索门控」| 「门控」| 英文 |
|---|---|---|---|
| `unicode61`（FTS5 默认）| 0 命中 | 0 命中 | ✅ |
| `trigram` | 1 命中 | 0 命中 | ✅（≥3 字符）|

`unicode61` 没有中文词边界，**一整句中文是一个 token**，句子里任何查询都匹配不到；
`trigram` 修好了但要求三个字符，于是所有两字中文词（门控、记忆、文档）静默匹配不到。
**搜索框悄悄搜不到东西，比慢 20ms 糟糕得多。** 所以改用 LIKE 扫描，
语言无关、任意长度都正确，和 `tools/knowledge.py` 的 `search_notes` 一致，
还去掉了 FTS 表和三个触发器——这个模块比之前更短了。
摘要的裁剪放在 SQL 里做，4MB 的文档不会为了显示 80 个字符而整个进 Python。

**扫描版 PDF 会被存下来**：没有文字层的 PDF 不是错误，只是里面是文字的图像。
拒绝它等于「扫描件不支持」——而它渲染得好好的，只是搜索和引用看不见内容。
所以：存下来，并在列表里标「无文字层」、在阅读区说明清楚。真正解析不了的文件才会报错。

**Reader Agent**（`agents/catalog.py`）：`list_documents` / `search_documents` /
`open_document` / `fetch_document` 四个工具。核心约束是**窗口**：
`open_document` 一次返回约 `settings.tool_result_budget` 字符，并把下一个 `offset` 一起给它，
所以 50 万字的书可以分段读、永远不会整个进上下文。这个数字选得刚好——
它同时也是「后续轮次里工具结果被压缩」的阈值。
**故意没有 `search_web`**：它的本分是你面前的材料，能跑去上网的 Agent 就会从别处回答。
**故意没有 `get_current_document`**：Context Bridge 已经把打开的文档和它的 id
放进这一轮了（`Application state: doc_id=…`），Agent 直接用那个 id 调 `open_document` 就能往下读。

**内嵌问答面板**：把 `sendChat` 从「写死四个全局变量」改成
`sendChatTo(target, input)`，target 是 `{chat, agentId, repaint}`——
消息放哪、谁回答、怎么重绘。主对话是一个 target，阅读器的问答面板是另一个。
两个容易忽略的冲突都做了双向检查：
- 面板的日志 class 是 `.asklog`，**不能是 `.chatlog`**，因为 `syncChatLogs()` 按 class 扇出，
  同名会把**主对话**的消息画进阅读区。`body.no-tele` 也要为它补一条对应规则。
- 面板用自己的 `#amsg`/`#asend`，复用 `#dmsg`/`#dsend` 会让两个输入框抢同一个节点。

**pdf.js 的浏览器下限**（`static/vendor/pdfjs/README.md` 有完整说明）：
它用 `Uint8Array.prototype.toHex` 算文档指纹，那是 `getDocument` 做的第一件事，
所以缺这个 API 的浏览器**每个 PDF 都会失败**，而且抛的是 `n.toHex is not a function`。
这些 API 是 2025 年才 Baseline 的（Chrome/Edge 140、Firefox 133、Safari 18.2）。
`reader.js` 在下载 1.7MB 渲染器**之前**先检测，直接说明缺什么、哪个浏览器有。
如果真有人卡在下限以下，换成 legacy 构建即可（README 里写了怎么换、代价是什么）。

**验证方式**（这轮学到的）：起了真实的 dashboard 用 curl 验，比只跑测试多抓到两个问题。
改 `.py` 之后**必须重启**才生效（README 早就写了，这次亲自踩到）。
无头 Chrome 在本机沙箱里跑不起来，所以 **PDF 在浏览器里的实际渲染我没能在这里执行验证**——
能验证到的是：vendored 的 pdf.js 在 node 里加载正常、`getDocument`/`TextLayer` 都在、
`/api/library/file` 返回的字节和上传的**完全一致**且 Content-Type 是 `application/pdf`、
`.mjs` 以 `text/javascript` 送达。

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
├── js/
│   ├── main.js          # render/refresh 循环 + 启动（必须最后加载）
│   ├── views.js         # 视图函数
│   ├── chat.js          # 对话视图（VIEWS.agent）+ 会话/历史/模型芯片
│   ├── trace.js         # 每轮的步骤时间线
│   ├── reader.js        # 文档库 + 阅读区 + 内嵌问答面板
│   ├── knowledge.js     # 知识库
│   └── ...              # 其他模块
└── vendor/pdfjs/        # vendor 进来的 pdf.js（唯一不是自己写的代码）
```
注：`main.js` 是**循环**，不是放应用代码的地方——各个应用的辅助函数放各自的文件。
`VIEWS.*` 如果在两个文件里都定义，**后加载的那个会静默胜出**，这正是
`test_reader_frontend.py` 在防的事。

## 使用示例

```bash
# 启动 Dashboard
cd D:\LLM\Agent\knowme-agent
.\.venv\Scripts\python.exe -m knowme.ops.dashboard
```

访问 http://localhost:8888

### MVP 流程
1. 点左侧 **阅读器 · 文档库** → 「+ 添加文件」选一份 md 或 PDF（或粘贴 URL）
2. 正文渲染出来；用左边的搜索框搜内容，点列表切换文档
3. 右边「问 Agent」面板直接针对这份材料提问（Reader Agent 看得到打开的文档）
4. 在正文里选中文本 → 「发送给 Agent」→ 填进对话输入框
5. 左侧点某个 Agent → 右侧全宽对话；展开回合头上的时间线看这轮经历了什么步骤

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
  干净工作区就失败，与前端无关。当前基线：**605 passed / 3 failed / 62 skipped**。
- **改完 `.py` 一定要重启 dashboard**——静态文件（`.js`/`.css`/`html`）每次请求都从磁盘读，
  硬刷新就能看到；但 `dashboard.py` 及其 import 的一切都在内存里。
  （2026-09-21 亲自踩到：改了 `library.py` 后接口还是旧行为，以为改错了。）
- **改了接口/后端，建议起真实服务用 curl 验一遍**，比只跑测试多抓到问题——
  文档库那两个 bug（拒绝扫描件、`.mjs` 的 MIME）都是这样发现的。
  端口被系统保留时换一个高的：`KNOWME_DASHBOARD_PORT=31236`。
- `evals/*` 被 gitignore，但开发中新增的回归锁测试要 `git add -f` 进库。
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
