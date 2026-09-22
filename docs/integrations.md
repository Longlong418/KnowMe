# 把它接到你的生活里

这里所有东西都是手动开启的,各自有独立的 extra。**没有任何一项会改动了循环** ——
网关负责把文本搬进搬出,集成只是智能体**可能**调用的一个工具。四大支柱在这一切都没安装的情况下照样工作。

这些内容是从 README 里搬出来的 —— 那个 README 已经涨到 569 行,对一个承诺「一个下午能读完」的项目来说太长了。
内容一字未改。

## 汇报我这一周(Apple Calendar + Mail)

```bash
KNOWME_APPLE_TOOLS=1 make brief      # 仅 macOS;第一次会弹权限请求,同意一次即可
```

KnowMe 会读你**真实的** Calendar.app(包括别人通过邮件邀请你的日程)和最近的 Apple Mail,
交叉比对你的记忆,写出一份「先讲重点」的简报,里面的链接可以直接点开(`message://`)。
可以挂到 cron 上当作早晨的问候:

```
30 7 * * *  cd ~/knowme-agent && make brief
```

它走的是正常的运行框架,所以它会在仪表盘上像任何一轮对话那样动起来。

## 把创建的事件镜像到 Google Calendar

本地 SQLite 数据库和 `calendar.ics` **仍然是权威来源**。如果还想把 `create_event` 的结果也写进
Google Calendar,装上游离的 extra 并配置
[应用默认凭据(ADC)](https://cloud.google.com/docs/authentication/provide-credentials-adc):

```bash
pip install -e '.[gcal]'
# 下载的 client 文件要放在仓库外面 —— 它只是 gcloud 的输入,
# gcloud 会把生成的凭据存到 ~/.config/gcloud/。
gcloud auth application-default login \
  --client-id-file=~/.config/knowme/gcal-client.json \
  --scopes=https://www.googleapis.com/auth/calendar.events
KNOWME_GOOGLE_CALENDAR=1 knowme
```

**任何密钥都不需要放进仓库**:client 文件只被 `gcloud` 读一次,它铸造出的凭据落在 `~/.config/gcloud/`。
(`.gitignore` 另外还挡掉了 `credentials.json` 和 `*token*.json`,作为第二道防线。)

目标日历默认是登录用户的 `primary`;要指定别的日历就设 `KNOWME_GOOGLE_CALENDAR_ID`。
`list_events` 读的仍然是本地数据库。**Google 那边失败永远不会回滚本地事件**,
而且会抑制参会人通知(`sendUpdates=none`)。

## 接入 MCP 服务器

```bash
pip install -e '.[mcp]'
```

创建 `.knowme/mcp.json`,任何 Model Context Protocol 服务器的工具就会出现在智能体面前,
命名空间为 `<server>_<tool>`(也会出现在仪表盘的 Tools ▸ MCP 标签页里):

```json
{"servers": [{"name": "fs", "command": "npx",
  "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]}]}
```

**免 Node 的演示** —— 仓库里带了一个极小的、自包含的 Python MCP 服务器:

```bash
cp examples/mcp.demo.json .knowme/mcp.json   # 指向 examples/mcp_demo_server.py
make web                               # demo_word_count / demo_reverse_text 出现在 Tools 里
```

同样的模式可以扩展到任何服务器,你自己写的或厂商提供的 —— **不需要改 KnowMe 一行代码**。
