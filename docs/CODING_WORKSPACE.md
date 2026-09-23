# Coding Workspace 开发说明

> 这份文档讲的是 **Coding 这一块**（页面 + KnowMe 自己的编码工具）。整个项目的进度和每一轮
> 改了什么，看 `docs/DEVELOPMENT.md`；这里只讲「这块东西怎么用、代码在哪、边界在哪」。

## 这块东西是干什么的

一开始它只是个「文件浏览器 + 一个把任务甩给本机 CLI 的按钮」，所以用起来像传声筒：
你说话，CLI 干活，KnowMe 只负责转述，改了哪里你看不见。

现在分两层：

**第一层 —— KnowMe 自己的手**（`knowme/tools/coding.py`）。这些是给模型用的工具，它自己就能
读文件、搜代码、写文件、跑命令，不必非要把活外包出去：

| 工具 | 干什么 | 要不要开总开关 |
|---|---|---|
| `list_files` | 列一个目录（目录在前、文件在后） | 不用，随便读 |
| `read_file` | 带行号读文件，默认前 400 行、最多 2000 行 | 不用 |
| `search_files` | 在这个项目里逐行搜文本，返回 `路径:行号: 内容` | 不用 |
| `git_status` | `git status --porcelain --shortstat` | 不用 |
| `git_diff` | `git diff HEAD`（可以只看某个文件） | 不用 |
| `write_file` | 新建或覆盖一个文件 | **要** |
| `edit_file` | 精确替换一段文本（找不到、匹配到多处都会明确报错，**而且一个字节都不改**） | **要** |
| `run_command` | 在项目根里跑一条命令，默认 60 秒超时 | **要** |

**第二层 —— 本机 CLI**（`knowme/tools/experimental.py` 的 `delegate_task`）。大范围重构、要跑很久
的活，还是交给 `pi` / Claude Code / Codex 更合适。KnowMe 现在可以**自己判断**要不要用它，而不是
每次都必须用。这一层由「允许 Coding Agent 调用本机 CLI」这个开关管（背后是配置里的
`experimental`，也就是 `KNOWME_EXPERIMENTAL`）。

**第三层 —— 收据**（`knowme/applications/coding_runs.py`）。不管上面哪一层动的项目，每次改动都会
落一行：改了哪个文件或跑了什么命令、`+多少行 −多少行`、patch 存在哪。这就是页面上「改动」那一页。

## 三条边界（重要）

**1. 出不去项目根目录。** 所有路径都走 `applications/coding_workspace.py` 里现成的那套守卫
（`_relative_path`、`SKIP_FILES`、`SKIP_DIRECTORIES`）—— 页面和 agent 的手**共用同一套规则**，
不存在「页面上看不见、agent 能改」的文件。`../` 和指向外面的绝对路径都会被拒。

**2. `.env`、`.git/`、`.knowme/` 读不到。** 搜索也搜不出来（搜索不会变成读密钥的后门）。
`run_command` 还会把带 `API_KEY` / `TOKEN` / `SECRET` / `PASSWORD` 的环境变量摘掉再交给子进程，
否则一条 `env` 就把 `read_file` 不给你看的密钥打印出来了。这是**卫生**，不是安全边界。

**3. 黑名单挡的是手滑，不是沙箱。** `run_command` 会拒绝 `rm -rf /`、`git push`、`git reset --hard`、
`sudo`、`shutdown`、`curl … | sh` 这类命令，但**别把它当安全机制**：它拦的是"手一抖"，不是"有人存心"。
真要跑不可信的代码，请自己在容器或虚拟机里跑。这句话同时写在工具描述和
`knowme/tools/coding.py` 的黑名单函数旁边，模型自己看得见。

## 总开关：`allow_write`

写文件和跑命令由 `coding.json` 里的 `allow_write` 管，**默认关**。

- 在 `#coding` 页面的状态带上点一下就能开关，**立刻生效**——工具每次调用都重新读一遍
  `coding.json`，不需要重启、不需要 rebuild。
- 关着的时候工具不是"消失"，而是**回一句人话**告诉模型「开关是关的，请让用户去 Coding
  Workspace 页面上打开」。模型因此知道该来请你开，而不是以为自己没手。
- 读文件、搜索、看 git **不受这个开关影响**。所以开关关着时你也可以让它「看看这个 bug 在哪」。

## 配置保存在哪里

```text
.knowme/coding.json
```

只存后端偏好和总开关，**不存任何 API Key 或登录信息**：

```json
{
  "default_backend": "pi",
  "enabled": { "pi": true, "claude": false, "codex": false },
  "allow_write": false
}
```

| 字段 | 含义 | 页面上在哪 |
|---|---|---|
| `allow_write` | 写文件 / 跑命令的总开关 | 状态带上那个开关 |
| `default_backend` | 交给 CLI 时优先用哪个 | 任务卡上的「后备 CLI」下拉 |
| `enabled` | 每个 CLI 允不允许用 | 「本机 Coding Agent 设置」折叠区 |

「允许 Coding Agent 调用本机 CLI」**不在这个文件里**，它对应配置里的 `experimental`
（`KNOWME_EXPERIMENTAL`），和 `delegate_task` 一起开关。关掉之后这一页仍然能存后端偏好，
只是不会执行任何本机 CLI。

## 页面怎么走的

**`#coding`** 从上到下就是人干活的顺序：

1. **状态带** —— 项目根目录、`读 ✓`、总开关、本机 CLI 状态、有几个文件被改过。
2. **任务卡** —— 一个大输入框 + 一个「让它去做」按钮 + 一个「后备 CLI」下拉。按下去之后，
   它带着项目目录去 `#agent/coding` 那个对话里干活。任务内容会存进浏览器本地
   （`localStorage.knowme_coding_task`），刷新不丢。
3. **下面** —— 左边是能折叠的文件树（原生 `<details>`，零状态），右边两个 tab：
   `项目文件` / `改动`，地址分别是 `#coding` 和 `#coding/changes`，链接可以直接分享。

**`#agent/coding`** 顶上多一条细带，写着项目根、「只读 / 可以改文件」、改了 N 个文件，
以及一个跳去 `#coding/changes` 的链接。普通对话页和它长得一样的时候，你分不清它正在你项目里干嘛。

**「改动」页** 一行一条：`时间 · 动作 · 文件 · +2 −0`。点一行展开那个 patch，`+` 绿 `−` 红。
patch 是**文件内容**，属于不可信文本，每一行都过 `esc()` 再上屏——写个含 `<b>` 的文件进去，
页面上看到的必须是字面的 `<b>`，不是粗体。

## 收据是怎么记的

表 `coding_runs`（惰性建表，**不动 `db.py` 的 SCHEMA**，老库零迁移）。五种行：
`baseline`、`write`、`edit`、`command`、`delegate`。

- **patch 全文不进 SQLite**，写在 `<home>/coding_runs/<32位十六进制>.diff`。表里只存文件名，
  读回来之前先按 `^[0-9a-f]{32}$` 校验，客户端给的 id 穿不了目录。
- **`baseline` 是懒的**：不是一打开页面就记，而是**某个会话第一次真要动项目时才记**，而且记的
  是**改动之前**的状态。这就是下一轮「一键撤销」要回滚到的那个点。
- **不是 git 仓库 / 没装 git → 整块收据静默降级**，绝不因为记账失败而让写入失败。
- **没有提交按钮，也不会自动提交 Git。** 自己看 diff、自己决定要不要提交。

## 相关代码

| 文件 | 负责什么 |
|---|---|
| `knowme/tools/coding.py` | KnowMe 自己的八个工具（读 / 写 / 跑），路径守卫、总开关、黑名单 |
| `knowme/applications/coding_runs.py` | 收据：表、patch 文件、baseline |
| `knowme/applications/coding_workspace.py` | 目录与文件模型、路径守卫、CLI 检测、`coding.json` |
| `knowme/tools/experimental.py` | 本机 Coding CLI 的调度（`delegate_task`） |
| `knowme/ops/coding_eval.py` | 跑 git 命令的小工具（argv 写死、不过 shell） |
| `knowme/ops/web/data.py` | Coding 的数据与保存接口，payload 里带 `coding_runs` |
| `knowme/ops/static/js/coding.js` | 整个 `#coding` 页面和它的重画逻辑 |
| `knowme/ops/static/js/views.js` | `#coding` 的三段式布局 |
| `knowme/ops/static/js/chat.js` | `#agent/coding` 顶上那条 `.coding-strip` |
| `knowme/ops/static/style.css` | 这些块的全部样式 |
| `evals/deterministic/test_coding_tools.py` | 工具的确定性测试（范围 / 开关 / 收据） |
| `evals/deterministic/test_coding_frontend.py` | 前端锁：轮询不重建页面、真树、patch 转义、收据行可点 |

## 当前限制

- **没有一键撤销**（`baseline` 行已经为它准备好了），也没有完整的并排 diff 面板
  （行号、折叠、左右对照）。
- **自动验收命令还没接上**：`knowme/ops/coding_eval.py::run_suite` 写好了但没人调用。
  现在「跑测试」是模型自己想起来就跑。
- 页面上的文件预览**本身还是只读的**——你改不了文件；能改的是 agent（在总开关打开时）。
- Claude Code 和 Codex 的流式事件不像 `pi` 那样逐条解析，跑完给一段完整摘要。
- 三个 CLI 的认证和权限各管各的，用之前自己确认登录状态和目录权限。
