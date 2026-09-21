# 前端与 Reader MVP 更新

## 这次改了什么

KnowMe 的主界面现在把侧边栏的重点放回 Agent 和 Application。原来 Gateway、Loop、Graph、Tools、Database、Models、Connections 等内部诊断页面全部平铺在左侧，视觉上像后台菜单，也重复了应用入口。它们仍然可以通过原来的 URL 访问，但不再抢占主导航；Trace 和 Settings 保留为工作台级入口。

Reader 也不再把所有文件都当作纯文本：

- 本地文件会先编码成上传请求，由服务端按文件类型解析。
- Markdown、文本、代码、JSON、CSV、HTML/XML 可以直接阅读。
- HTML 会去掉 script/style，只保留可读正文。
- URL 由服务端读取，绕过浏览器跨域限制，并限制为 http(s) 和 4 MB。
- PDF 只有在环境安装 `pypdf` 时才解析；没有依赖时会明确提示，而不会把 `%PDF` 二进制显示在页面上。
- Reader 内容继续通过 Context Bridge 注入当前 Agent，所以选中文本提问的流程不变。

## 为什么这样设计

Reader 的 UI 只做两件事：带入材料、阅读材料。解析和安全边界放在 `knowme/applications/reader.py`，以后要增加 EPUB、Office 或更强的网页正文抽取，只需要扩展这个模块，不必再把格式判断散落到浏览器脚本里。

## 后续可做

1. ~~将 `pypdf` 做成可选依赖并增加 PDF 页码/目录导航。~~ 依赖已改成基础依赖（Phase 10）；页码导航仍未做，现在有 pdf.js 可以做目录/缩略图。
2. ~~将 Trace 做成按 turn 分组的时间线，默认折叠完整 tool output，只在用户展开时查看原文。~~ 已完成（2026-09-21，Phase 9）：每个助手回合渲染成一棵步骤时间线，`meta.steps` 落库，工具输出默认折叠在原生 `<details>` 里。
3. 给知识库增加类似 Sapphire 的文件树和笔记编辑双栏布局。
4. ~~在 Reader 右侧增加 Agent 对话上下文卡片，显示当前文档、选区和已写入的笔记。~~ 已完成（2026-09-21，Phase 10）：阅读区右侧是内嵌的 Reader Agent 问答面板，`asklog` 显示当前文档标题，选中文本可直接发给它。

## 2026-09-21（Phase 10）：这一版实际落地成了什么

这份文档当初写的 Reader 是「一个文本预览框」。现在是**文档库 + 阅读区 + 问答面板**：

- **文档库**在服务端（`applications/library.py`）：原文存 `home/documents/`（文件名是
  uuid4，永远不是上传的名字），抽取的文本存 SQLite。列表、搜索、删除都在左侧栏。
- **正文按类型渲染**：Markdown 走 `renderMarkdown`（**必须包在 `<div class="r">` 里**，
  否则 `.md*` 样式全部不生效）；PDF 走 vendor 的 pdf.js，画到 canvas 并叠一层文字层，
  所以 PDF 里的字能选中、能发给 Agent。
- **长文档分块渲染**：一次画 6 万字，剩下的一个「继续加载」按钮，避免一次性 innerHTML 卡死标签页。
- **Reader Agent** 是第 5 个 Agent，也能在侧边栏单独打开；在阅读器里它以内嵌面板出现。
  它的工具按窗口读文档，整本书不进上下文。
- **看不见文字层的 PDF（扫描件）**会被存下来并明确标注，不当成错误——这是当时没考虑到的一类。

关于中文搜索为什么从 FTS5 改成 LIKE，见 `DEVELOPMENT.md` 的 Phase 10（有实测对照表）。
