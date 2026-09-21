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
- **加新视图时的坑**：`render()` 只在**真正切换页面**时才重建 DOM。
  凡是自己持有状态的视图（阅读器的文件框和 URL 输入框、各种编辑器），
  都不能每 5 秒重建一次，否则用户正在输入或正在选的内容会被清掉。
  加视图时照着 `render()` 里已有的分支写。
- 前端改了 `.js`/`.css` 刷新浏览器即可；**改了 `.py` 必须重启 dashboard**。
- **已知失败的 3 个测试**（动手前先看一眼，别把它们算到自己头上）：
  `test_delegate_env.py` ×2、`test_packaging.py::test_the_bundled_skills_are_findable`。
  干净工作区就失败，与前端无关。当前基线：**586 passed / 3 failed / 62 skipped**。
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
