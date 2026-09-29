# KnowMe

KnowMe is a local-first personal AI assistant: chat, memory, tools, and run history are managed on your own machine. It is a small Python project you can read end to end, not a black-box agent framework.

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)](pyproject.toml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Offline tests](https://img.shields.io/badge/tests-offline%20suite-2f855a)](evals/deterministic)

[简体中文](README.md)

> Local-first does not mean fully offline: model requests go to the provider selected in `.env`; state and run data stay on your machine by default.

![KnowMe chat page](docs/images/chat.jpg)

## What it does

- Runs General, Coding, and Research agents in a local web app. The Reader brings the current document into a conversation.
- Stores semantic memory, episodic memory, and on-demand `SKILL.md` procedures in SQLite.
- Connects tools for web search, GitHub, calendars, documents, notes, files, and MCP servers.
- Runs repeatable workflows for briefings, gathering, and deep research, with a replayable view of each step.
- Switches model providers from the UI. Built-in providers use their official SDKs; custom OpenAI- and Anthropic-compatible endpoints are supported.

## Quickstart

You need Python 3.11+ and an API key from one model provider.

```bash
git clone <your-repository-url>
cd knowme-agent
python -m venv .venv
```

Activate the environment:

```powershell
# Windows PowerShell
.\.venv\Scripts\Activate.ps1
```

```bash
# macOS / Linux
source .venv/bin/activate
```

Install the project and create your local configuration:

```bash
python -m pip install -e .
```

```powershell
# Windows PowerShell
Copy-Item .env.example .env
```

```bash
# macOS / Linux
cp .env.example .env
```

Edit `.env` with a provider and its key. For example:

```ini
KNOWME_PROVIDER=deepseek
DEEPSEEK_API_KEY=your-api-key
```

Start the web app:

```bash
knowme
```

KnowMe prints the actual URL in the terminal. The default port is `8888` on Windows and `7777` elsewhere. If the port is busy, it tries the next ports; set `KNOWME_WEB_PORT` to choose the starting port.

Try this on the first run:

1. Send “Remember that I usually avoid meetings on Friday afternoons.”
2. Restart KnowMe.
3. Ask “Am I free on Friday afternoon?”

Local data is stored under `.knowme/`; `state.db` is a SQLite database. These files are ignored by Git and are never part of a source checkout.

## Common commands

| Command | Purpose |
| --- | --- |
| `knowme` or `knowme web` | Start the local web app |
| `knowme connections` | Check integration configuration and health |
| `knowme brief` | Generate a calendar, mail, and memory briefing |
| `knowme gather` | Gather several sources in parallel and summarize them |
| `knowme deep_research <topic>` | Run a deep-research workflow and save its report |
| `knowme skill install <url>` | Install a skill into `.knowme/skills/` |

## Screenshots

The overview page shows the current runtime and its statistics:

![KnowMe overview](docs/images/overview.jpg)

The memory page lets you edit, merge, and delete facts:

![KnowMe memory management](docs/images/memory.jpg)

## Project layout

```text
knowme/
  core/          runtime, sessions, tool registration, and context management
  memory/        semantic / episodic / procedural memory and retrieval gating
  tools/         search, calendar, documents, notes, coding, and MCP tools
  graph/         gather, triage, and deep-research workflows
  agents/        agent roles and tool scopes
  applications/  Reader, knowledge base, Coding Workspace, and other apps
  ops/           local web app, CLI, traces, and release checks
evals/           deterministic tests and model-judged tests
skills/          skills shipped with the project
sql/             Supabase schema
docs/            user and contributor documentation
```

The main turn looks like this:

```text
message → memory gate → build context → model → optional tool calls → reply → save memory
```

For a guided code reading order, start with `knowme/core/runtime.py`, `knowme/core/loop.py`, and `knowme/memory/`. The loop/graph boundary is explained in [`docs/loop-vs-graph.md`](docs/loop-vs-graph.md).

## Tests and development

Install development dependencies:

```bash
python -m pip install -e ".[dev]"
```

Run the usual checks:

```bash
make lint
make eval
```

Without `make`:

```bash
python -m ruff check knowme evals
python -m pytest -q evals/deterministic
```

The deterministic suite uses a scripted client instead of a real model, so it needs no API key or network access. See [`docs/DEVELOPMENT_GUIDE.md`](docs/DEVELOPMENT_GUIDE.md) for the code map, test conventions, and how to add a tool.

## Documentation

- [`docs/integrations.md`](docs/integrations.md): optional calendar, mail, Notion, and MCP integrations
- [`docs/CODING_WORKSPACE.md`](docs/CODING_WORKSPACE.md): Coding Workspace permissions and boundaries
- [`docs/memory-backends-playbook.md`](docs/memory-backends-playbook.md): switching to Supabase, mem0, or Zep
- [`docs/loop-vs-graph.md`](docs/loop-vs-graph.md): choosing between loops and graph workflows
- [`SECURITY.md`](SECURITY.md): default permissions, data locations, and security notes

## Contributing

Issues and pull requests are welcome. Please read [`docs/DEVELOPMENT_GUIDE.md`](docs/DEVELOPMENT_GUIDE.md) first, and run lint plus the deterministic tests before opening a PR.

## License

KnowMe is released under the [MIT License](LICENSE).
