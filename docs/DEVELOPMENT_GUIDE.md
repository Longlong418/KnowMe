# 开发指南

这份文档面向第一次打开 KnowMe 代码的人。它记录当前项目怎么运行、从哪里读代码，以及提交改动前要检查什么；不记录某一次开发过程中的临时日志。

## 本地环境

项目需要 Python 3.11+。建议在仓库根目录创建 `.venv`，然后安装开发依赖：

```bash
python -m pip install -e ".[dev]"
```

`.env` 只放本机配置和 API key，不能提交。`.knowme/` 是运行时数据目录，也不应该复制到 Pull Request 中。

## 从哪里读代码

1. `knowme/agents/catalog.py`：Agent 的角色、提示词和工具范围。
2. `knowme/core/runtime.py`：一轮请求如何组装上下文、运行和保存结果。
3. `knowme/core/loop.py`：模型与工具之间的主循环。
4. `knowme/memory/`：记忆存储、检索门控和整理。
5. `knowme/ops/web/`：本地 HTTP API、SSE 事件和网页运行时。
6. `knowme/ops/static/`：无构建步骤的 HTML、CSS 和 JavaScript 前端。

网页前端目前不需要 Node 或 Vue 构建链。修改静态文件后重启 Python 网页服务即可看到结果。

## 常用检查

```bash
make lint
make eval
```

等价的直接命令是：

```bash
python -m ruff check knowme evals
python -m pytest -q evals/deterministic
```

确定性测试使用 `ScriptedClient` 模拟模型，不能依赖网络、真实 API key 或本机已有的 `.knowme` 数据。需要真实模型判断的测试位于 `evals/judge/`，运行前要配置相应服务商。

## 添加一个工具

1. 在 `knowme/tools/` 中选择合适的模块，保持输入和输出都容易测试。
2. 在工具注册表中登记名称、说明和参数，确保模型能看到准确的描述。
3. 给成功、失败和权限边界补确定性测试。
4. 如果工具会访问外部服务，在 `docs/integrations.md` 或 `SECURITY.md` 中说明所需配置和数据流向。

## 提交前检查

- 不要提交 `.env`、API key、OAuth token、`.knowme/`、`traces/` 或本机截图。
- README 中的命令、链接和截图路径要能在干净 checkout 中成立。
- 运行 lint 和确定性测试。
- 提交信息说明“改了什么”，不要把临时调试文件一起带进来。
