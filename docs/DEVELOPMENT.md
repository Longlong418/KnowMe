# KnowMe 开发文档

## Phase 20：侧栏改成三个模块（2026-09-22）

**你提的**：左侧栏的「总览、运维、行为」单独成为一个模块；另外"配置模型的页面现在看不到"。

### 1. 先查"看不到"——不是布局挤掉了，是被 CSS 藏了

`style.css` 里有一条规则（`f6a5f8b` 那次提交加的）把 7 个页面从侧栏 `display:none`
掉了：网关、循环、图工作流、工具、数据库、**模型**、**连接**。当时的想法写在注释里：
"操作类页面能直达就行，别在侧栏跟四个 Agent、四个应用抢注意力"。

紧接着还有一条按**位置**隐藏组标题的规则：`nav > .grp:nth-of-type(4),:nth-of-type(5)`。
它是从顶部 `brand` 那个 div 开始数的。两条加起来的结果就是：

- `模型`/`连接` 彻底看不见（你只能在地址栏输入 `#models` 才能进去）；
- `总览`/`运维`/`行为` 因为没被点名藏掉、但它们的组标题被按位置藏了，就这么挂在
  Applications 组下面，看着像 Applications 的子项。

你截图里那几条"像 Applications 子项"的条目，就是这个。

**怎么复现的**：本机起了一个用临时 home 的实例，然后用真 Chrome 把 `#nav` 每个子元素的
`getBoundingClientRect()` 打出来——被 CSS 藏掉的元素 `top`/`bottom` 全是 `0`，
可见的那几个位置正常，渲染出的截图和你给的一模一样。所以这**不是**浏览器缓存，
也**不是**窗口太矮。

### 2. 改法

- `index.html`：把原来 `Manage` 和 `设置` 两组合成**一个「系统」组**，放在 Applications
  下面，顺序是 总览 → 运维 → 行为 → 模型 → 连接（先看它跑得怎么样，再看每轮怎么跑，
  最后是配置）。剩下的运行时内部页（网关/循环/图工作流/工具/数据库）收进一个「运行」组。
- `style.css`：要隐藏的那一组在 HTML 里写 `class="grp deep"`，CSS 按**名字**藏
  （`nav > .grp.deep` + 那几条 `href` 规则），不再按位置数。想藏的组自己说明自己，
  以后再加组、换顺序都不会误伤别的组。
- 「运行」这一组维持现状：能从 `#tools`、`#database` 这类链接直达，但侧栏不显示。
  想让它显示，把 `style.css` 里那两条 `display:none` 删掉即可。

**结果**：侧栏现在就是 Agents / Applications / 系统 三组；1280×700 的窗口也不用滚动
就能看全（内容高 623px）。

### 3. 锁

`evals/deterministic/test_static_assets.py` 加了两条：

- 除「运行」组那 5 个页面外，**侧栏里不许有被 CSS 藏掉的条目**（这条直接对应你这次的
  问题：页面老老实实写在 index.html 里，却被 CSS 藏掉，谁都不会发现）；
- **组标题不许按位置隐藏**，要藏就写在自己身上。

反向验证过：把「模型」重新加回隐藏名单，第一条 FAIL；把组标题改回 `nth-of-type`，
第二条 FAIL。另外用真 Chrome 跑了 15 项检查（点「模型」能进、标题对、选中态对、
`#tools`/`#database` 等深层链接仍然能打开、侧栏计数器还在、控制台无报错），15/15 通过。

## Phase 19：对话那一块的几个坑（2026-09-22）

这一轮是从一个现象出发的：对话页面上出现一张卡片，写着
`错误：TypeError: Failed to fetch`，页头停在「实时 · 122 秒前更新」，怎么刷都不动。
顺着查下去，发现是四个各自独立的问题，其中两个都会表现成"页面不动了"，
所以看起来很像是同一件事——也就是"对话管理乱七八糟"。

下面每一条都是：先说现象，再说真正的原因，最后说改了什么。

### 1. 工具返回了一个列表，整个 `/api/data` 就没了（这是主因）

**现象**：对话卡片报 `TypeError: Failed to fetch`，5 秒一次的轮询一直失败，
页头只是停住、不报错。

**原因**：`search_notes`、`list_notes`、`list_folders` 这几个笔记工具返回的是
Python 列表。循环把工具结果原样记进 trace（`output` 直接是数组），而 Web 拼
`/api/data` 的时候要用 `str.lower()` 判断这次调用算成功还是失败——列表没有
`.lower()`，于是 `collect()` 在拼 payload 的中间抛了异常。HTTP 处理器没有兜住，
连接直接被关掉，一个字节都没回；浏览器只能把它翻译成 `TypeError: Failed to fetch`，
而前端又把轮询的异常吞了，所以页头只是停住不动，看不出到底哪里坏了。

**改法**：

- 工具结果的**记录**永远是文本。循环里新增 `tools.as_text()`，把非字符串结果转成
  JSON 文本再写进事件；模型拿到的仍然是工具原本的返回值，只有记录变了。
- Web 读取时也做一次容错，因为磁盘上已经存着旧格式的 trace 行。
- `/api/data` 加了兜底：出错也回一个 JSON `{"error": "..."}`，再也不会用"死连接"回答。
- 页头在后端不健康时直接说人话：「后端没在正常回应 · 具体原因」，不再只是停住不动。

### 2. 切 Agent 的时候，一个迟到的响应把"历史记录"清空了

**现象**：从 General 切到 Learning，历史记录菜单有时会显示「还没有历史对话」，
过 5 秒又自己好了。

**原因**：payload 里的 `sessions_by_agent` **只给被请求的那个 Agent 填列表**，
其他 Agent 一律是空数组；而页面是用自己的 `ACTIVE_AGENT` 去取这个字段的。
切换 Agent 要经过两次 await，所以一次"切换前发出、切换后到达"的轮询响应，
会拿旧 Agent 的数据去画新 Agent 的界面——菜单读到空数组，就报告"没有历史"。

**改法**：payload 里加上 `agent_id`，说清楚"这是谁的数据"；前端在 `refresh()`
里收到不是当前 Agent 的数据就直接丢掉。一个决定点，两个文件里各写一半是不行的，
所以这里是"后端声明 + 前端校验"。

### 3. localStorage 里存着一个已经不存在的 Agent，页面变白板

**现象**：整个页面没有数据，页头空白，点什么都没反应。

**原因**：`ACTIVE_AGENT` 存在 localStorage。如果这个 Agent 后来没了（改过
catalog、改过名字），`/api/data` 每次都会回 `unknown agent: X`；`D` 永远是 null，
而 `render()` 第一行就是 `if (!D) return;`——所以侧栏根本不画，连点别的 Agent 也
救不回来（`selectAgent()` 需要 `D.agents` 才认这个点击）。这是最难受的一种坏法：
没有数据，也没有出路。

**改法**：后端说"unknown agent"时，前端把这个 id 忘掉、退回 `default`，
并把地址栏一起改回 `#agent/default`，然后再拉一次数据。

### 4. 「记忆 ▸ 语义记忆」里点「取消」没反应

**现象**：点「编辑」出现编辑框，点「取消」编辑框不消失，等多久都不消失。

**原因**：那行按钮写的是 `onclick="editing=false;refresh()"`。内联事件是在
window 上求值的，写的是 bootstrap 复制出去的那份 `editing`；而 `render()` 读的是
util.js 里那个闭包变量。所以点完之后 `window.editing` 确实变成了 `false`，
真正决定"要不要重建页面"的那个 `editing` 却还是 `true`。

**改法**：改成调用 `cancelFactEdit()`，在同一个作用域里改那个变量。
（同样是内联事件，`models.js` 里的 `catFilter.q=this.value` 没问题——因为它改的是
对象的属性，复制出去的是同一个对象的引用；会出问题的只有被复制的原始值。）

### 5. 知识库的文件夹筛选，点了要等 5 秒

**现象**：选了文件夹，列表没反应；过 5 秒（下一次轮询）才生效。

**原因**：筛选是烘进视图标记里的，改文件夹必须让侧栏重建一次。原来那段代码结尾写
的是 `location.hash = "#knowledge"`——可当前 hash 已经是 `#knowledge`，
给同一个 hash 赋值是个空操作，不会触发 hashchange（这个坑 Phase 13 就记录过），
所以只能等下一次轮询顺带重建。

**改法**：调用同一个文件里已经为这件事写好的 `refreshKnowledgeView()`。

### 6. 「新建对话」会丢掉你正在看的文档，以及 Notion 后端的那个 NameError

这两条是查上面几个问题时顺手查出来的，都不难解释：

- 阅读器打开文档时会往"上下文桥"里存一份快照，键是 (agent, session)；
  空闲轮换的时候代码**特意**把它带到新线程（有注释、有测试）。但「新建对话」
  这个按钮忘了做同样的事，于是新对话里的 Agent 看不到你还开着的文档。
  现在 `session_action` 的 `new` 分支也调用 `rekey()`。
- `KNOWME_EPISODIC_STORE=notion` 时，两个 Notion 缓存（客户端 + 结果）只定义在
  `runtime.py`，靠包级别的 `_sync_state()` 复制进 `data.py`；而 HTTP 路由是直接
  import `data` 的，从不走那个包装函数——所以真实请求里这两个名字根本不存在，
  `collect()` 抛 NameError。现在缓存放进 `data.py`，就放在读它的代码旁边。

### 怎么验证的

服务端和前端都改过，所以两边都验：

- **真浏览器**（Playwright + 本机 Chrome，跑在 `.knowme` 的副本上，不碰真实数据）：
  对话正常收发；让 `/api/data` 返回错误时页头会说人话并恢复；切 Agent 时历史记录
  不再清空；清空 localStorage 换成不存在的 Agent 后页面能自己退回 default。
- **测试**：`evals/deterministic/test_dashboard_payload_contract.py` 是这一轮新增的
  回归锁。先把改动 `git stash` 掉确认这 5 个测试**全都会失败**，再放回来确认全过——
  不会失败的测试不算锁。
- 全量：`628 passed, 62 skipped`，Ruff 通过。

## Phase 18：核心目录收敛与 Web 入口整理（2026-09-22）

这一轮没有改变 Agent 的行为，专门处理代码组织问题：

- `knowme/core/` 是唯一的核心实现目录。旧的 `knowme/loop/` 和
  `knowme/runtime/` 重复文件已删除；旧 import 由 `knowme/__init__.py`
  集中映射到 `core`，所以历史代码仍然可以运行，但新代码只应使用
  `knowme.core.*`。
- 本地浏览器入口统一叫 **Web**：启动命令是 `knowme web` 或 `make web`。
- 本地浏览器入口只保留 **Web**：启动命令是 `knowme web` 或 `make web`，不再提供旧命令别名。
- 原来的单体服务文件拆为四层：`ops/web/`（兼容门面）、
  `ops/web/runtime.py`（聊天和工作流）、`ops/web/data.py`（数据和写操作）、
  `ops/web/server.py`（HTTP/SSE 传输）。
- 前端由 `static/js/bootstrap.js` 作为唯一的原生 ES Module 入口；
  `index.html` 不再维护一长串脚本加载顺序。现有功能脚本保留稳定的浏览器
  handler 名称，后续可以逐个迁移为显式 `export`，而不必再改服务端 API。

验证：核心布局、旧 import 兼容、Web 路由、Agent 池和静态前端定向测试
`42 passed`，Ruff 检查通过。

## Phase 11：先把 Reader 的上传链路做稳（2026-09-22）

这一轮没有改 Web 的两栏布局。左边仍然是 Agent 和应用入口，右边仍然显示当前 Agent 的对话，或者当前应用（阅读器、知识库、记忆等）。这次只处理 Reader 真正影响使用的问题。

### 这次修了什么

- 修复了浏览器端请求遇到空响应时的错误提示。以前 `postJSON()` 不管响应状态，直接调用 `response.json()`；服务端返回空的 404 或连接中断时，浏览器只会显示 `Unexpected end of JSON input`，看不出到底是哪一个接口失败。现在会先读取响应文本，再显示 HTTP 状态或服务端返回的错误。
- Web 收到无效 JSON 时不再直接断开连接，而是返回一个带状态码的 JSON 错误。不存在的 POST API 也会返回 JSON，而不是空响应。
- 去掉了左侧导航里两个都跳到 `#memory` 的入口。现在只保留“记忆管理”；需要看原始记忆数据时，进入已有的“数据库”页面，不会再出现点两个菜单却看到同一页的情况。

### Reader 的正确数据流

1. 浏览器读取文件，转成 data URL。
2. `/api/library` 解码文件并调用 `applications/reader.py` 提取文字。
3. `applications/library.py` 同时保存原始文件和提取后的文字：原始文件用于 PDF 渲染，文字用于搜索和给 Agent 阅读。
4. 打开文档时，接口返回提取文字和 `/api/library/file?id=...`。
5. Markdown/文本直接渲染；PDF 使用内置 pdf.js 渲染原始文件，扫描版 PDF 没有文字层时仍然可以显示页面，只是不能搜索和引用其中的文字。

### 调试上传失败时先看这三件事

- 确认浏览器连接的是刚启动的 Web 端口。旧的 Web 进程如果还占着端口，旧版本可能没有 `/api/library`，这时 PDF 会表现为 `Failed to fetch`。
- 打开浏览器 Network 面板，检查 `POST /api/library` 的状态和响应 JSON；现在即使失败也会返回可读错误，不会再只显示 JSON 解析错误。
- PDF 仍然打不开时，直接访问 Network 里返回的 `/api/library/file?id=...`。如果它返回 `application/pdf`，说明上传和保存成功，问题只在浏览器端 PDF 渲染；如果返回 404，说明连接的不是包含文档库路由的 Web 实例。

### 还没有做的事情

这轮没有引入 Vue，也没有重做整体布局。先确保“添加文件 → 文档库出现 → 文本/PDF 能打开 → 选中文本能交给 Agent”稳定，再继续完善知识库和更丰富的 Reader 功能。

## Phase 12：知识库第一轮可用化（2026-09-22）

Reader 链路稳定后，开始补自己的知识库。这里也没有改变 Web 的整体两栏布局，只把知识库应用内部整理成更接近笔记工具的工作区：左边看笔记，右边编辑当前笔记。

### 这次加了什么

- 知识库右侧应用改成内部双栏：左边是当前 Agent 的笔记列表，右边是新建页或编辑页。
- 左侧增加标题/正文搜索、文件夹筛选和新建按钮；搜索只在当前已经加载的笔记上过滤，不会每敲一个字都请求服务器。
- 编辑笔记时可以修改标题、文件夹和正文，正文会实时用项目自己的 Markdown 渲染器预览。
- `[[笔记标题]]` 会在预览中变成可点击链接；点击后会在当前 Agent 的笔记中查找并打开目标笔记。
- 打开笔记时额外读取反向链接，编辑器底部会显示“哪些笔记链接到了这里”。
- 所有失败都通过页面提示，不把网络异常留在控制台里；删除仍然需要确认。
- 编辑中的新笔记或旧笔记会避开 Web 的 5 秒刷新，草稿不会因为后台轮询而消失；保存、删除或离开页面后再恢复正常刷新。

### 为什么先做这些

知识库的第一价值不是复杂的图谱，而是“能放进去、能找到、能读懂、能跳转”。文件夹、搜索、Markdown 预览和反向链接把这条最短路径闭环了；向量检索、自动整理和更复杂的图谱可以在真实笔记积累后再决定。

## Phase 13：刷新页面后回到刚才的位置（2026-09-22）

Phase 12 只让"页面不主动刷新"这一条成立了：5 秒轮询不再重建视图，所以草稿不会被后台轮询吃掉。但只要你真的按 F5，或者关掉标签页再回来，正在读的文档和正在写的笔记就都没了——因为那些状态本来只活在内存里。这一轮把它们延长到"下次打开"。

### 这次加了什么

- **阅读器记住打开的是哪一份文档**。`readerOpen()` 把文档 id 写进浏览器的 localStorage；页面重新加载后 `restoreReaderState()` 先加载文档库，再确认这个 id 还在库里，在的话就重新打开它。文档已经被删掉时不会去追一个不存在的 id。
- **知识库记住打开的是哪一条笔记**，并且**按 Agent 分开记**（键名是 `knowme_kb_note_<agent>`）：你在 Learning 里看到的笔记，不会因为切到 Coding 就跳出来。点「新建」、点「关闭」、删除笔记都会把这个记忆清掉。
- **修掉"写完界面不更新"**。以前创建、保存、删除之后是 `location.hash = "#knowledge"`，可你本来就在 `#knowledge` 上——赋一个相同的 hash 不会触发 hashchange，所以什么都不会发生，左侧列表一直显示旧标题。现在改成直接调用 `main.js` 的 `refresh()`，并且先把当前选中清空，让 `render()` 真的重建视图（笔记列表和文件夹筛选是服务端烘进视图 HTML 的），紧接着 `restoreKnowledgeState()` 再把这条笔记从服务器重新读出来打开——所以详情区也不会停留在旧标题上。
- **顺手修掉一个显示问题**：知识库第一次打开时，"从一条笔记开始"和"创建新笔记"表单会同时出现。`#kb-note-detail` 有初始 `display:none`，`#kb-create-editor` 漏了，而两个 JS 函数本来就一直在切换这两个面板的可见性，说明初始状态本该是隐藏的。
- **补上测试**：新增 `evals/deterministic/test_knowledge_frontend.py`（node + DOM 桩，跑的是真实的 `knowledge.js`），锁住"轮询不重建视图""保存后列表重建并重新打开这条笔记""新建不会跳回旧笔记""刷新后回到上次那条笔记"；阅读器的桩补上了 `localStorage`，并锁住"刷新后回到原文"。
- **测试基线**：**606 passed / 3 failed / 62 skipped**（3 个失败仍是既有的 `test_delegate_env.py` ×2 和 `test_packaging.py::test_the_bundled_skills_are_findable`）。

### 为什么没有让轮询去重新拉文档库

`restoreReaderState()` 每一轮都会跑，但如果让它每次都强制重新拉一遍文档库列表，就等于每 5 秒发一次多余的请求，也违背了这块面板"轮询不重拉"的约定（测试里就有这一条）。恢复文档并不需要它：刷新页面后 JS 状态本来就是空的，第一次调用自然会去请求服务器。

## Phase 14：三个 bug，其实只有一个原因（2026-09-22）

你报的是三个问题：阅读器刷新后不显示文档、知识库点开笔记几秒后跳回新建页、PDF 解析不好使。查完之后发现前两个是**同一个 bug**，第三个是我自己之前写错的一行判断。

### 元凶：`render()` 每次调用都抛异常

`main.js` 的 `render()` 是 Web 的渲染主循环，每 5 秒被轮询调用一次。它最后有一段给左侧导航写数字的代码：

```js
document.getElementById("n-mem").textContent = ...;
```

Phase 11 删掉了左侧重复的「记忆数据」入口（就是提交 `6ee4900`），那个 `<span id="n-mem">` 跟着一起没了。于是这一行变成了 `Cannot set properties of null`，**每次调用都抛**。

要命的是异常发生的位置：它在 `render()` 的中段。它下面是这些代码——

```js
if (view === "reader"){ restoreReaderState(); wireChat(); }
if (view === "knowledge"){ restoreKnowledgeState(); }
```

全部**永远执行不到**。所以：

- 阅读器刷新后不显示文档 → 因为 `restoreReaderState()` 从来没跑过。你以为是你必须重新上传，其实是恢复逻辑整个没执行。
- 知识库点开笔记几秒后跳回新建页 → 因为 `restoreKnowledgeState()` 也没跑过。5 秒后轮询重建视图，重建后没人把笔记重新打开，看到的就是「创建新笔记」页。
- 「有时候又会显示」→ 因为轮询落点、当前在哪个视图都会影响你先看到哪一帧，表现就不稳定。

### 怎么修

加了一个 `setCount()`：

```js
// 计数位丢了，就只是这个数字不显示而已，绝不能让整轮渲染跟着挂掉
function setCount(id, value){
  const el = document.getElementById(id);
  if (el) el.textContent = value;
}
```

7 个计数位全部改成走这个函数，同时把 `index.html` 里那个 `<span id="n-mem">` 补回去。**关键是两层保护**：元素在就正常显示，元素不在也不影响别的。

### PDF：我自己写错的一行判断

阅读器里有一段"这台浏览器能不能渲染 PDF"的预检查，写的是：

```js
if (typeof Uint8Array.prototype.fromBase64 !== "function") return "浏览器不支持…";
```

`fromBase64` 是**静态方法**（`Uint8Array.fromBase64`），挂在构造函数上，原型上根本没有它。所以 `Uint8Array.prototype.fromBase64` 永远是 `undefined`，这句判断永远为真——**任何浏览器、任何 PDF 都会被拒**，全都退化成纯文本。这正是你看到的"PDF 解析还是不好使"。

真实需要检查的只有 `Uint8Array.prototype.toHex`（pdf.js 的 worker 里真的用了它）。实测 Chrome 153 下 `toHex` 和 `fromBase64` 都是函数。

**关于浏览器端 PDF 库**：pdf.js（mozilla/pdf.js）就是这件事的标准答案，Chrome、Firefox 内置的 PDF 阅读器都是它，React-PDF、pdfjs-dist 这些包都是它的封装。项目里**早就把它放在 `static/vendor/pdfjs/` 了**，所以不需要换库，也不需要引入新的开源项目——之前打不开只是因为上面那行判断写错了。

### arXiv 论文：扩展名骗了我们

你导入过一篇 arXiv 论文（`1706.03762`，就是《Attention Is All You Need》）。URL 是 `https://arxiv.org/pdf/1706.03762`，取扩展名得到 `.03762`，于是这篇 PDF 被**存成了纯文本**（`kind=text`），阅读器就按文本渲染，整篇论文挤成一大段。

修法是让**文件内容说话**：PDF 前四个字节一定是 `%PDF`，这比文件名靠谱。新增 `kind_and_suffix(name, raw)`，先看字节再退回看文件名。另外加了 `_repair_binary_kinds()`，在列出文档库时顺手检查历史记录里存错的那些行，是 PDF 就把 `kind` 改成 `pdf`（文件本身没存错，不用搬动，`suffix` 和 `path` 本来就是两列）。

> 中途踩了一个坑值得记下来：判断文件头时写的是 `read(5) != b"%PDF"`。`read(5)` 读出来是 5 个字节 `b"%PDF-"`，和 4 个字节的 `b"%PDF"` 比**永远不相等**，所以修复逻辑每次都提前 `continue`，一条也没修上。改成 `read(4)` 就对了。

### 新增的测试

- `evals/deterministic/test_dashboard_render_frontend.py`（新，21 条断言）：把 `render()` 抽出来在 node 里跑，故意让某个计数位的元素返回 `null`，断言 `render()` 不抛异常、`restoreReaderState()` 和 `restoreKnowledgeState()` 照样被调用、其他计数位照样显示；另外锁住 `index.html` 必须提供 `main.js` 用到的每一个计数位 id——**这是当初真正丢掉的那一半**。
- `evals/deterministic/test_document_library.py` 新增 5 条：URL 命名的 PDF 存成 pdf、历史错误行能就地修正且不动文件、只认真正的 PDF（不能把普通文本也改成 pdf）、文件被手工删掉时列表不崩。
- 阅读器和知识库的旧测试桩改成从 `function setCount(` 开始切片——`render()` 现在依赖它，切片起点不跟着动就会 `ReferenceError`。这本身也是个提醒：**测试里的"按形状切片"是会跟着代码一起坏的东西**。

### 为什么没有换 Vue3

你问要不要用 Vue3 重构前端。我的判断是**不需要**，理由是这次的三个问题没有一个是框架能防住的：

1. 第一个是**删 HTML 时漏改了对应的 JS**。Vue 里写 `{{ count }}` 确实不会因此崩，但"删掉的组件里还在引用数据"是任何框架都会遇到的事。
2. 第二个是**用错了 API**（静态方法当原型方法）。这跟用不用框架完全无关。
3. 第三个是**文件名当扩展名**，是后端逻辑。

真正让问题变严重的是**"一个装饰性的数字写不进去，把整个渲染循环炸了"**——这跟框架无关，是"要渲染的东西该不该有能力打断整条链路"的问题。现在加了 `setCount()` 之后，这类问题在这一层就不会再发生了。

另外这个前端是**零构建**的：改完刷新浏览器就能看到效果，不需要 npm/webpack/vite，也不需要 `npm install`。换成 Vue3 就要引入构建步骤和一套工具链，对一个本地优先的个人项目来说，收益不明显、成本很实在。

**所以结论：先不换。** 如果你之后觉得是"界面不够好看"而不是"有 bug"，那才是该聊样式的时候——那种改动也不依赖 Vue，直接改 CSS 就行。

### 需要你决定的一件事

**笔记是按 Agent 隔离的**（`notes.agent_id`，`test_dashboard_agent_scope.py` 就是专门测这个的）。你的笔记现在都属于 `reader` 和 `learning` 两个 Agent，所以在 `default`（默认助手）下面知识库是**空的**。这是设计如此，但很容易被当成"知识库有时候不显示"。

要不要改成"知识库看到所有 Agent 的笔记、只是标注来源"？这属于产品决定，我没有擅自改。你说一声我就动。

### 测试基线

**613 passed / 3 failed / 62 skipped**。3 个失败是既有的、和这轮无关：`test_delegate_env.py` ×2 和 `test_packaging.py::test_the_bundled_skills_are_findable`（缺 `weekly-brief` 这个 skill）。

## Phase 15：知识库改成"一个知识库"，笔记来源标出来（2026-09-22）

你问的那个问题——"知识库有时候不显示"——根因是 Phase 14 那个崩溃，但还有一个**设计上**的原因：笔记是按 Agent 隔离的，而你的笔记分别属于 `reader`、`learning`、`default`。所以停在哪个 Agent，就只能看到那个 Agent 的笔记。停在 `default`（默认落在的那一页）而它一条笔记都没有时，看到的就是空的知识库。

你拍板改成"看所有 Agent 的笔记、只标注来源"。下面是改了什么。

### 关键区分：两个读者，两套规则

这次改动最重要的判断是**分清了两件事**，它们之前被混在一起：

| | 谁在用 | 应该看到什么 |
|---|---|---|
| **Web 知识库** | **你（人）** | 所有 Agent 的笔记。这是你自己的知识库，你有全部所有权 |
| **Agent 自己的工具** | **模型** | 只有它自己的笔记 |

**只有第一行改了。** `make_knowledge_tools()`（模型调用的 `save_note`/`list_notes` 这些工具）**完全没动**——Coding Agent 依然看不到你 Learning 的笔记。这是 Agent 隔离这个功能本身，你没有要求去掉它，去掉会让每个 Agent 都能读到你的私人笔记。

所以这不是"取消隔离"，而是"**浏览器是人的视角，工具调用是 Agent 的视角**"。

### 后端怎么改的

**一个概念：`agent_id=None` 表示"不过滤"。**

`knowme/tools/knowledge.py` 里加了一个小助手，把这个规则收在一处：

```python
def _agent_conds(agent_id):
    """把查询限定到某个 Agent 的条件。

    agent_id=None 表示不限定 —— 所有 Agent 的笔记。Web 的知识库就是
    这么调的，因为看它的人拥有全部笔记。Agent 自己的工具永远不传 None
    （它们闭包持有自己的 id），所以 Agent 依然读不到别的 Agent 的笔记。
    None 是刻意写成"必须显式传"的：这样没有任何现有调用会不小心放宽范围。
    """
    if agent_id is None:
        return [], []
    return ["agent_id = ?"], [agent_id]
```

`get_note / update_note / delete_note / list_notes / list_folders / search_notes / get_linked_notes` 都支持它，条件用一个小 `_where()` 拼，SQL 不再散落七份（顺手抽了 `_COLS` 常量）。

**为什么不是"加一组新的 all_agents_* 函数"**：那会复制一遍 SQL，而且两个函数长得几乎一样，以后改一个忘一个。用一个参数表达"限定/不限定"是一件事，不是两件事。

**默认值仍是 `"default"`**，所以所有老调用行为不变。只有显式写 `agent_id=None` 才会放宽。

**`ops/web/` 包：

- `knowledge_info()` 去掉了 `agent_id` 参数（它已经不用了，留着会误导），列出全部笔记和全部文件夹。
- `knowledge_action()`：`list` / `get` / `update` / `delete` / `search` / `links` 都传 `agent_id=None`；**只有 `create` 还用当前 Agent**——新笔记总得盖上某个 Agent 的章。

### 关于"能不能改别人的笔记"这个决定

能。**只要列表里看得到，就应该打得开、存得下。** 否则点开一条笔记、改完点保存却失败，那是比原来的 bug 更糟的体验——我把"看不见"换成了"看得见但用不了"。

而且编辑**不会改变笔记的归属**：`update_note` 的 SQL 只 `SET title/folder/content/updated_at`，从来不 `SET agent_id`。你在 `default` 视图里编辑一条 `learning` 的笔记，它还是 `learning` 的。

### 前端怎么改的

- **`views.js`**：删掉了那行 `filter(n => (n.agent_id||"default") === ACTIVE_AGENT)`——这就是"知识库是空的"的直接原因。文件夹筛选现在跨 Agent 取并集。
- **每张卡片标出来源**：加了一个 `kb-note-agent` 小标签（灰底描边），显示 `reader` / `learning` / `default`，和彩色的文件夹标签并排。用一个 `kb-note-tags` 包住两个标签——卡片是两列 grid，直接塞第三个 span 会把预览挤到第三行。
- **`knowledge.js`**：读/写/删/搜索/反链的请求里去掉了 `agent_id`（已经不起限定作用了，留着会让人以为它在限定）。**`create` 保留**。
- **localStorage 的键从 `knowme_kb_note_<agent>` 改成 `knowme_kb_note`**：列表现在跟 Agent 无关，却按 Agent 分别记"上次打开哪条"，切 Agent 会莫名其妙换一条笔记。一个知识库，一个记忆。
- **反链也跨 Agent 了**：`[[链接]]` 从一条 Learning 笔记指向 Reader 笔记时，两边都要能看到——点击能跳过去，反向链接却空着，那才是自相矛盾。

### 顺手修掉一个测试里的坑

`evals/deterministic/test_knowledge_frontend.py` 里我那几条新断言，一开始是用 `indexOf("\n  },\n  settings(d){")` 切 `VIEWS.knowledge` 的。这个标记里包含方法自己的收尾 `}`，所以切片少了一个大括号，`eval` 直接 SyntaxError。**另外**：切出来的代码引用了 `ACTIVE_AGENT`，而它在那个 IIFE 里没声明——**故意还原旧代码做反向验证时，它报的是 `ReferenceError` 而不是我想要的"FAIL：只显示了当前 Agent 的笔记"**。所以在 IIFE 里补了 `let ACTIVE_AGENT = "learning"`，让它失败时报的是人话。

反向验证过了：把旧的过滤那行加回去，6 条断言 **FAIL**（消息可读），改回新代码全 PASS。**这个锁是真能抓住回归的，不是摆设。**

### 真机实测（Chrome 153，3 条笔记分属 3 个 Agent）

```
默认 Agent 下的知识库（以前这里是空的）
   [reader]    test  (TEST)
   [default]   test  (TEST)
   [learning]  test  (DEFAULT)
切到 reader 之后：条数 3 → 3（列表不再跟着 Agent 变）
在 default 视图下编辑 learning 那条 → 保存成功，没有报错弹窗
刷新后：详情区还在，标题还在
控制台错误：无
```

数据库里确认了最关键的一条：内容确实写进去了，而 `agent_id` **仍然是 `learning`**。

### 测试基线

**613 passed / 3 failed / 62 skipped**（3 个失败仍是既有的 `test_delegate_env.py` ×2 和 `test_packaging.py`）。ruff 干净。

改动的是断言而不是数量：`test_dashboard_api_keeps_crud_in_the_selected_agent_scope`（它锁的正是被推翻的旧行为）换成了 `test_dashboard_shows_every_agent_but_the_agents_tools_stay_scoped`——**名字里带上了"工具仍然隔离"**，因为那是不许回归的那一半。

## Phase 16：右侧提问报错 + 阅读器三处小毛病（2026-09-22）

你报了 5 件事，其中 4 件是 bug、1 件是要求。按"先说根因、再说改了哪一行"的顺序写。

### 一、右侧提问每轮都报 `'LoopResult' object has no attribute 'meta'`

**这是后端的问题，看起来却像前端的问题**——所以你才会觉得"提问不能使用"。而且旧的历史记录还能正常显示，因为那是从数据库里读出来的，不走这条路径。

**根因是一次重构留下的缺口。** 早先 `e303b01` 给对话加了时间线，时间线读的是 `result.meta` 里的 `steps`。那时 `KnowMe.respond()` 返回的就是 `LoopResult`，`meta` 是有的。后来做 AgentSpec / ContextPolicy / AgentRuntime 那一轮重构，`respond()` 改成返回 `runtime.run_turn(...).as_loop_result()`——而 `as_loop_result()` 是把一轮"运行时结果"翻译成"普通循环结果"的函数，它当时只搬了 `reply / tool_calls / iterations` 三个字段，**`meta` 被当成翻译过程里多余的东西丢掉了**。

于是每一轮提问走到最后、要发 `done` 事件的时候，`result.meta` 就炸了。前端的表现是：回答一个字都没出来，底下写着"— · ? 次迭代"。

**改法（两处，一共四行）：**

- `knowme/core/loop.py`：给 `LoopResult` 补上 `meta: dict[str, Any] = field(default_factory=dict)`，并且写了注释说明**为什么这个字段属于这里**——调用方拿到手的只有 `LoopResult`，运行时知道而普通循环不知道的东西（时间线、门控、耗时、模型名）只能挂在这个字段上，否则就会被翻译层静默吃掉。
- `knowme/core/runtime.py`：`as_loop_result()` 把 `meta=self.meta` 一起带上。

**为什么这里原来没被测试抓到**：老测试验的是"存进数据库的历史能画出来"，没验"刚答完这一轮推给前端的和存进数据库的是不是同一个东西"。所以补了一条 `evals/deterministic/test_turn_meta.py::test_the_live_stream_ends_with_what_a_reload_will_render`——它同时拿两条路径的结果，**断言实时 `done` 事件里的 `steps` 和落库的 `meta["steps"]` 相等**。一句话概括这条测试守的是什么：**一份数据，两条路径，必须长得一样**。

反向验证过：把 `meta=self.meta` 去掉，这条测试立刻 FAIL（`assert []`），也就是说它真的能抓住这次这个回归。

### 二、点「收起提问」没反应

**根因和上一轮（Phase 14/13）是同一类**：面板开合状态存在 `localStorage` 里，而面板本身是**视图 HTML 的一部分**——它要变，只能靠重新渲染整个阅读器。

但 `render()` 里有一句**故意**的守卫：

```js
else if (view === "reader" && !subChanged) { /* 不重建 */ }
```

这个守卫是需要的：每 5 秒一次的轮询如果每次重建阅读器，你正在输入的文件框会没、正选着的文字会被清掉。**问题是它也一起吞掉了这次点击。**

**改法**：`toggleAskPanel()` 里先写 `localStorage`，然后把 `activeView` 清成 `null` 再调 `render()`。

```js
activeView = null;   // 让 render() 认为"换了个视图"，于是这一次会重建
render();
```

`activeView = null` 是 `render()` 本来就认识的一种信号（正常的导航也是这么让它重建的），所以不需要给这个守卫开新口子——**这一次会重建，轮询依然安静**。

### 三、左侧切换文档，高亮不跟着走

也是同一类问题：**画出来的东西依赖一个状态，而那个状态不在"要不要重画"的判断里。**

`renderLibrary()` 原来用 `el.dataset.stamp` 做"内容没变就别重画"的标记，stamp 是 `查询词 | 行 id 列表`。你从文档 A 点开文档 B，**查询词没变、行也没变**，所以 stamp 相同、直接 return，那一行的 `.on` 就还留在 A 上。

**改法**：把当前打开的文档 id 加进 stamp：

```js
const stamp = `${readerQuery}|${currentDoc ? currentDoc.id : ""}|${rows.map(d => d.id).join(",")}`;
```

一句话原则：**stamp 必须覆盖所有会影响这段 HTML 的东西**，漏一个就会出现"数据变了但界面没动"。

### 四、侧边栏里那个单独的 Reader agent：删掉

你说阅读器里的 Agent 只在阅读器右边就够了。改法是从 `index.html` 里删掉那一行侧边栏入口：

```html
<button class="agent-link" data-agent="reader" ...>Reader</button>
```

**只删入口，别的一律不动**：`reader` 这个 profile（`agents/catalog.py` 里）还在、它的笔记和历史都还在、`ASK_AGENT = "reader"` 也还是它。右侧提问面板用的就是这个 Agent。现在界面上没有任何地方能跳到 `#agent/reader`——**"它属于阅读器"这件事是靠入口的位置表达的，不是靠删掉它的身份。**

### 五、PDF 要能单独 `ctrl+滚轮` 缩放，而不是缩放整个网页

浏览器的 `ctrl+滚轮` 默认是缩放整个页面。要让 PDF 自己响应，必须**拦下这个事件**并 `preventDefault()`，否则页面的 `devicePixelRatio` 会变——那也是你不想看到的。

设计上有三个决定值得说：

1. **乘在已有的"适配窗宽"比例上**，不另起一套坐标系。页面本来就是按 `fit = min(2, 窗宽/页宽)` 画出来的，现在画成 `fit * zoom`。`zoom === 1` 时是**逐像素和以前一样**的，所以这个功能不动任何没缩放的场景。
2. **锚点是鼠标位置，不是页首**。缩放前记下鼠标指针落在哪一页的哪个高度比例（`{index, frac}`），重画完再把滚动位置调回去。否则你盯着看的那一行会在缩放时跑到屏幕外。
3. **重绘是防抖的（160ms），而且保留已经加载的页数**。`ctrl+滚轮` 会连着触发十几次事件，逐个重画会卡；而且你点过「继续加载」的那些页不能因为缩放就退回 8 页。

另外两个细节：

- 监听器挂在 `#rc-content` 上，而这个元素**在重绘中是不被替换的**，所以用 `dataset.zoomWired` 做一次性标记，不然每次重绘都会多挂一个监听器。
- CSS 里 `.reader-pdfwrap.zoomed` 把 `align-items` 从 `center` 改成 `flex-start` 并允许横向滚动。**不是审美问题**：在可滚动容器里，一个居中的 flex 元素一旦溢出，你只能往右滚、再也滚不回左边。页面在 `zoom === 1` 时是按窗宽画的，所以这条规则只在显式缩放过之后生效。

### 测试和验证

- **`evals/deterministic/test_reader_frontend.py`**（node 桩）新增了一个 `testPdfZoom` 分组：给 pdf.js 写了最小桩（`getDocument` / `TextLayer` / `GlobalWorkerOptions`），手工造了会记录监听器、`classList`、`children`、`getBoundingClientRect` 的 DOM 元素桩，然后断言：画出 8 页 → 点「继续加载」变 12 页 → 滚轮监听器只挂一次 → `ctrl+滚轮` 阻止了页面缩放且页宽变大 → 已加载的页数没丢 → 不带 `ctrl` 的滚轮不受影响 → 缩放到上限就停住。同一个文件里还加了「收起提问」和「文档高亮跟着走」两组断言，以及一条"侧边栏里没有 Reader 入口"（`!/data-agent="reader"/`）。
- **真实 Chrome（Playwright 驱动本机 Chrome，端口 31236，`KNOWME_HOME` 指向 `.knowme` 的副本）16 项全过**。其中最有说服力的两条：`ctrl+滚轮` 之后页宽 605px → 920px 而 `devicePixelRatio` 保持 1 → 1（**说明放大的是 PDF，不是网页**）；右侧提问真的拿到了回答（"我是阅读助手：…"，时间线 `门控 · skip / 推理 · iter 1 · end_turn / 2.9 秒 · 1 次迭代 · deepseek-flash`），一个「错误」都没有。

### 测试基线

**614 passed / 3 failed / 62 skipped**。3 个失败仍是既有的、和这几轮无关：`test_delegate_env.py` ×2 和 `test_packaging.py::test_the_bundled_skills_are_findable`（缺 `weekly-brief` 这个 skill，干净工作区上也一样）。ruff 干净。

三个提交：`d823312`（后端 meta）、`6926b48`（PDF 缩放）、`66dc4b6`（前面那三个前端 bug）。

## Phase 17：提问看不到当前文档 + 日志被轮询推回底部（2026-09-22）

你报的两件事。两件的症状都在前端，两件的根因都不在"前端那一眼能看到的地方"。

### 一、右侧提问："没有拿到「当前打开文档」的标识"

你自己也试出来了——在阅读器里开着 REACT 那篇问它，回答是"说不上来，这次会话里我没有拿到「当前打开文档」的标识"。

**Context Bridge 是按 (agent, session) 存的。** `application_contexts.publish(agent_id, session_id, …)` 存进一个字典，键是这两样；服务端取的时候用的是 `render(agent.agent_id, agent.session.session_id)`——也就是**这一轮是哪个 Agent 在问，就取它那一格**。

而前端只在 `ACTIVE_AGENT` 那一格写：你停在 `default`（默认落的那一页），文档就存在 `("default", <default 的线程>)` 里。可是右侧面板是以 `reader` 的身份问的，服务端取的是 `("reader", <reader 的线程>)`——两边**从来就不是同一格**，所以永远取到空。

注意这个 bug 的形状：**发布和读取都没写错，错的是它们对不上。** 单看任何一边的代码都是对的，只有把 `publish` 的实参和服务端 `render` 的实参摆在一起才看得出来。

**改法**：一次发布，两个目标。

```js
const targets = [
  [ASK_AGENT, askSessionId()],                      // 右侧面板
  [ACTIVE_AGENT, SESSION || D?.current_sessions?.[ACTIVE_AGENT] || "default"]
];
```

- **`ASK_AGENT`（reader）**：面板自己的那一格，这才是它要看的。
- **`ACTIVE_AGENT`**：主对话那一格，`选中文字 → 发送`（`rcSend`）走的是它——留着，不是多余的两个 POST，而是那条路本来就在用。

顺手把"reader 的线程是谁"收成一个 `askSessionId()`：原来 `loadAskThread()` 里写了一遍同样的表达式，两处各写一遍迟早会漂。现在发布的目标和屏幕上那个线程用的是同一个来源。

返回值改成 `{ok: 两边都成功, results}`：`rcSend` 里判断的是 `res.ok`，让它以后也代表"两边都写进去了"。

### 二、往上翻几秒就自己跳回最后一条

**根因在 `syncLogClass()`**（`render.js`），它原来是无条件的：

```js
el.scrollTop = el.scrollHeight;   // 滚到底部才让流式回答看起来"活着"
```

这句话本身没错——错在**它被调用的时机**。`wireChat()` 每 5 秒的轮询都会重画两个日志（主对话的 `.chatlog` 和右侧的 `.asklog`），所以每 5 秒就有人把你从正在读的旧消息上拽走。你"翻几秒就跳回去"，那个"几秒"就是轮询周期。

**改法**：跟着最新消息这件事，**只在你本来就在底部的时候做**。

```js
// 量在替换 innerHTML 之前：问的是"你当时看的东西"，不是新的那份
const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
el.innerHTML = renderChatLogFor(chat, emptyText);
if (force || atBottom) el.scrollTop = el.scrollHeight;
```

留了 40px 余量，滚轮/触控板很难精确停在第 0 像素。

**`force` 是给"确实有新东西"的调用点的**：你在面板里按了发送（`sendChatTo` 的第一帧）、刚加载完一个线程（`loadAskThread`）。这两种情况下底才是对的，不管你之前翻到哪儿。

**为什么"空日志"要算作在底部**：`render()` 重建视图后，日志元素是**新的空元素**（`chat.js` 里就是一句 `<div class="chatlog"></div>`）。空元素的 `scrollHeight - 0 - clientHeight` 是 0 或负，所以天然算"在底部"→ 跟着走。要是把它当成"你翻走了"，每个会话打开时都会停在最老的一条上——这是这个改动最容易踩的坑。

**主对话那边没被碰到**：它的 `.chatlog` 不是内层滚动条（实测 `scrollHeight == clientHeight`，滚的是 `<main>`），所以那条判断在那边恒为真，行为和以前一模一样。真实 Chrome 里也确认了。

### 验证

**先复现，再修。** 用真实 Chrome + 一个数据副本（`KNOWME_HOME` 指向 `.knowme` 的拷贝，端口 31237）跑 `.claude/verify17.py`：

修复前：
```
FAIL  开文档时把这份材料 publish 给了 reader agent （实际 [('default', 'dashboard-20260922-125333')]）
FAIL  reader agent 拿到了当前打开的文档 application_chars=0
FAIL  轮询没有把你推回底部 （7 秒后 scrollTop=10896）
```
（`application_chars=0` 是服务端自己报的"这一轮我拿到了多少字的应用上下文"，比看模型的措辞可靠。）

修复后 **6 项全过**，其中：

```
PASS  开文档时把这份材料 publish 给了 reader agent （实际 [('default', …), ('reader', …)]）
PASS  reader agent 拿到了当前打开的文档 application_chars=24157
     它这一轮的回答: 看得到了——这次 [application context] 里带上了 Current resource: REACT，
     以及 doc_id=36492580-…（pdf · 110151 字）
PASS  轮询没有把你推回底部 （7 秒后 scrollTop=0）
PASS  按发送之后日志回到最新一条 （scrollTop=17170 底部=17170）
```

有意思的是模型自己把三次的差别说出来了（第一次只有时间时区、第二次有标题和 id、第三次连正文都在）——因为每修一次它拿到的 context 就多一段。

**两个新锁都做了反向验证**（把旧写法放回去，读到的必须是能看懂的 FAIL，不是测试自己炸掉）：

- `test_dashboard_render_frontend.py::test_the_poll_does_not_steal_the_scroll_position`：切片 `syncLogClass`，用一个几何可控的假日志元素驱动。反向验证时读到的是 `FAIL a poll does NOT drag you back to the bottom when you have scrolled up`。
- `test_reader_frontend.py` 里新增的三条：记录每个 `/api/extras` 的 body，断言两格都写了。反向验证时读到的是 `FAIL opening a document publishes it under the READER agent (the panel's own key): default/s1`——**把旧行为的实质直接打出来了**（只发了 default）。

### 测试基线

**616 passed / 3 failed / 62 skipped**（比上轮多 2 条，就是这两个锁）。3 个失败仍是既有的那三个。ruff 干净。

### 一个还没做的选择

**选中文字之后不按发送，Agent 是看不到的**——它只在你点「发送」时（`rcSend`）才知道你选了哪一段。这是现在的行为，我没有动它：要不要"选中即注入"是个产品决定，你说一声我再改。

## Phase 18：选中之后在右边问 + 页面和这一轮必须用同一个线程（2026-09-22）

你报了两件事：选中文字按「发送给 Agent」，人被甩到上面的 agent 视图去了，你要的是在右边栏提问；以及顺着上一轮"看不出我开着哪篇文档"再挖下去，发现还有一个更长引信的同类问题。

### 1. 选中文字 → 在右边问（不再跳走）

以前的 `rcSend()` 是去填主对话那个输入框 `#dmsg`，填不到就 `openAgent()` 导航到 `#agent/<当前 agent>`——你只是引用一句话，人却被从正在读的文档里扔了出去。

现在改成填右侧提问框 `#amsg`，并把焦点放进去，等你按发送。理由很直接：**那个面板本来就贴在文档右边，而"就着正在读的内容提问"正是 reader agent 存在的意义**。选中内容照旧经过 Context Bridge 同时发给 reader 和当前 agent 两个格子，所以主对话那一侧也还是拿得到这段话。

- 按钮和提示语都改成「在右侧提问」/「选中文本后可在右侧提问」。
- `rcSend()` 现在返回 promise（测试要能等它跑完）。面板本来开着就不重建它——重建会把你的选区丢掉，白丢一次；只有关着的时候才需要重建一次把它打开（靠 `setAskPanel`，见上一轮那段注释）。

### 2. 真正的大问题：页面认的线程 ≠ 这一轮跑的线程

上一轮修完"publish 挂错了 agent"，我以为这件事完了。结果在真实 Chrome 上再跑一次，bug 1 又 FAIL 了，日志里写得很清楚：

```
→ agent_id=reader session_id=dashboard-reader-20260922-155717 …
→ agent_id=reader session_id=dashboard-reader-20260922-155727 …
FAIL  reader agent 拿到了当前打开的文档 application_chars=0
```

publish 写进了 `…155717`，这一轮却在 `…155727` 里回答。原因不是轮换，是**同一个问题问了两遍**：

- 页面加载时 `/api/data` 调 `dash_session()`，它调 `resume_or_new_session()` 决定"现在该用哪个线程"；
- 一轮对话开始时 `get_agent()` **又自己调了一次** `resume_or_new_session()`。

当这个 agent 最后一条消息够老（超过 `KNOWME_SESSION_IDLE_MINUTES`），两次调用都会判定"旧线程结束了"，各自 `_new_session_id()` 生成一个带时间戳的新 id。两次发生在不同的秒上（上面就是差了 10 秒），于是页面把打开的文档 publish 到 A，这一轮在 B 里回答，桥里当然是空的。**这不是阅读器独有的问题**：`default` 走的是同一对函数，主对话第一句话同样会落到一个页面不知道的线程里。

修法只有一句话——`get_agent()` 不再自己掷骰子，直接用 `dash_session()` 已经定好的那个线程。谁先问谁决定，后面的人照抄。

顺带补上同症状的另一半：**空闲轮换发生在这一轮开始之后**。你上午打开 dashboard（页面认下线程 A），一直开着读文档（publish 到 A），一小时后才第一次提问——这时候 `maybe_rotate_session()` 才把 A 换成 B，而快照还留在 A 上，你读了一小时的那篇恰好在你问它的那一刻掉线。新增 `ApplicationContextBridge.rekey(agent, 旧, 新)`，在这一轮开始、轮换之后把快照搬过去。判断依据是：**快照描述的是"屏幕上开着什么"，不是"说过什么"**，所以它属于这一轮即将回答的那个线程。

### 复现与验证

- 单元锁：`test_browser_agent.py::test_the_page_and_the_first_turn_agree_on_the_thread`。这里有个坑值得记一笔——`_new_session_id()` 只精确到秒，两次调用在同一秒内会撞出**同一个** id，测试就会白过。所以这个测试把 `_new_session_id` 打桩成一个计数器，让两次调用必然拿到不同的值，模拟真实里"差了几秒"的那种情况。
- 反向验证：把 `get_agent()` 还原成旧写法，读到的是
  `AssertionError: … + dashboard-fresh-02`（页面拿到 01，这一轮跑 02）——失败原因一眼就是这个问题本身。
- 真实 Chrome（`.claude/verify17.py`，副本 + 端口 31237 + `KNOWME_SESSION_IDLE_MINUTES=1`）：**13 项全过**。为了让日志真能滚起来，副本里给 reader 和 default 各灌了 24 条历史消息。关键几条：

```
PASS  reader agent 拿到了当前打开的文档 application_chars=24157
PASS  翻到顶部成功 / 轮询没有把你推回底部 （7 秒后 scrollTop=0）
PASS  按发送之后日志回到最新一条 （scrollTop=2817 底部=2817）
PASS  没有跳到上面的 agent 视图 （hash=#reader）
PASS  选中的文字进了右侧提问框 （'[选中文本] Coding Workspace MVP这次新增的是 Coding'）
PASS  空闲之后真的换了新线程（否则这条检查是空的） （dashboard-reader-seed -> dashboard-reader-20260922-160326）
PASS  换线程之后 reader agent 还是看得到那篇文档 application_chars=2142
```

最后两条是配套的：**先确认真的换了线程，再看文档有没有跟着走**。否则换线程没发生时这条检查等于什么都没验（这正是我一开始跑出来的假 PASS 风险）。`2142` 比 `24157` 小，是因为转线程之前你刚好又选中了另一篇小文档，快照跟的是"现在屏幕上开着的那篇"——这正是我们要的语义。

### 测试基线

**619 passed / 3 failed / 62 skipped**（比上轮多 3 条：页面/线程一致性的锁、轮换搬快照的锁、选中走右侧的锁）。3 个失败仍是既有那三个，另外 26 个 ERROR 是 `evals/judge/` 需要 `deepeval`，本机没装。

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
- ✅ Agent 工具和 Web API 均按当前 Agent 隔离
- ✅ 标题更新、UUID、空标题校验、删除结果和 wiki-link 转义
- ✅ `evals/deterministic/test_knowledge_base.py` 覆盖核心行为

### Phase 4：Coding Workspace 只读 MVP
- ✅ 通过 `KNOWME_PROJECT_ROOT`（默认启动目录）选择项目根目录
- ✅ `/api/workspace` 提供受限文件树和文本读取
- ✅ 路径穿越、`.env`、`.git`、虚拟环境、缓存和二进制文件默认拒绝
- ✅ 打开文件后通过 `application: coding` 写入 Context Bridge
- ✅ 暂不开放任意写入和 Terminal；变更仍通过显式 `delegate_task(cwd=...)` 完成

### Phase 5：Web Agent 隔离收口
- ✅ `/api/data?agent_id=...` 只返回当前 Agent 的 facts、episodes、chat log、Trace 和数据库样本
- ✅ 切换 Agent 后前端重新拉取对应数据，避免只靠浏览器端过滤
- ✅ 未知 Agent 在数据接口和 Workspace API 中都会被拒绝
- ✅ 新增 Web 隔离回归测试

### Phase 6：Memory Manager 合并
- ✅ Semantic facts 可以在同一 Agent 内合并，目标事实保留、来源事实删除
- ✅ Agent 工具 `manage_memory(action="merge")` 和 Web UI 共用同一个 Store 方法
- ✅ 合并操作失败时不会跨 Agent 修改数据

### Phase 7：修复阅读器「选择文件」误报 Bug（2026-09-21）

**现象**：在阅读器里点「选择文件」，选完文件后偶尔弹出「请选择一个文件」。
时好时坏，没有明显规律。

**原因**：Web 每 5 秒整体刷新一次（`refresh()` → `render()`）。以前的
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
└── ops/web/     # Web + API
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
# 启动 Web
cd D:\LLM\Agent\knowme-agent
.\.venv\Scripts\python.exe -m knowme.ops.web
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
- 查看日志：Web 5 秒刷新，实时显示 trace
- 数据库：`.knowme/state.db`
- **加新视图时的坑**：`render()` 只在**真正切换页面**时才重建 DOM。
  凡是自己持有状态的视图（阅读器的文件框和 URL 输入框、各种编辑器），
  都不能每 5 秒重建一次，否则用户正在输入或正在选的内容会被清掉。
  加视图时照着 `render()` 里已有的分支写。
- 前端改了 `.js`/`.css` 刷新浏览器即可；**改了 `.py` 必须重启 Web**。
- **已知失败的 3 个测试**（动手前先看一眼，别把它们算到自己头上）：
  `test_delegate_env.py` ×2、`test_packaging.py::test_the_bundled_skills_are_findable`。
  干净工作区就失败，与前端无关。另外 `evals/judge/` 有 26 个 ERROR，是没装 `deepeval`。
  当前基线：**619 passed / 3 failed / 62 skipped**。
- **改完 `.py` 一定要重启 Web**——静态文件（`.js`/`.css`/`html`）每次请求都从磁盘读，
  硬刷新就能看到；但 `ops/web/` 及其 import 的一切都在内存里。
  （2026-09-21 亲自踩到：改了 `library.py` 后接口还是旧行为，以为改错了。）
- **改了接口/后端，建议起真实服务用 curl 验一遍**，比只跑测试多抓到问题——
  文档库那两个 bug（拒绝扫描件、`.mjs` 的 MIME）都是这样发现的。
  端口被系统保留时换一个高的：`KNOWME_WEB_PORT=31236`（旧的
  `KNOWME_DASHBOARD_PORT` 仍可用）。
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
