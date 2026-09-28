# KnowMe

**A personal AI assistant that runs on your own machine. Four pillars — Harness · Loop · Memory · Eval — in Python you can read in an afternoon.**

[简体中文](README.md) | [English](README.en.md)

[![Python](https://img.shields.io/badge/python-3.11%2B-2b4a6f)](pyproject.toml)
[![No framework](https://img.shields.io/badge/runtime-stdlib%20%2B%20official%20SDKs-4a5a4a)](knowme/core/loop.py)
[![Memory](https://img.shields.io/badge/memory-one%20SQLite%20file-8a6a2f)](knowme/db.py)
[![Tests](https://img.shields.io/badge/offline%20tests-855%20passed-2e6b3a)](evals/deterministic)

![The chat page — your message on the right, every step it took on the left](docs/images/chat.jpg)

## What this is

KnowMe is a **local-first** personal assistant: it runs a loop on your own machine, remembers
things about you, calls tools, and shows you — step by step — what it actually did on a turn.

It is not a framework and there is no plugin marketplace. The whole project is four things:

| Pillar | What it does | Where to start reading |
|---|---|---|
| **Harness** | The shell around one run: assemble context, attach tools, emit each step | `knowme/core/runtime.py` |
| **Loop** | The skeleton of a turn: model → tools → model, until it can answer | `knowme/core/loop.py` |
| **Memory** | Three kinds of memory plus a gate that decides **whether** to retrieve, and what | `knowme/memory/` |
| **Eval / Ops** | Offline tests, LLM-as-judge, traces, the release gate | `evals/`, `knowme/ops/` |

## What it does

**Memory comes in three kinds, and it decides for itself when to use them.** A gate asks
whether this turn needs memory at all and what to search for, then reports how many entries
matched and which ones — **all of that is written on the page**, not hidden in a log:

- **Semantic**: durable facts ("I don't take meetings on Friday afternoons")
- **Episodic**: what happened on a given day, distilled into one line
- **Procedural**: `SKILL.md` skills. A skill's body enters the prompt **only when it matches**;
  otherwise it costs nothing but a one-line entry in the catalog

**Chat.** Every agent is a page in the local web app (`#agent/<id>`). Three roles — General /
Coding / Research — sharing one set of memory and one SQLite file.

**Graph workflows.** When the shape of the work is known in advance, give it a name:

| Slash command | What it does |
|---|---|
| `/gather` | Morning briefing: four sources in parallel (GitHub / web / calendar / memory), then one digest |
| `/deep_research <topic>` | Split into sub-questions → one sub-agent per question, in parallel → one report with sources |
| `/triage <message>` | The router. It also runs on every message when graph workflows are on; calling it by name lets you watch it choose |
| `/graphs` | List the graph workflows you can run |

**Applications.** Reader + document library (bring in a PDF or a URL, highlight and ask about it),
knowledge base (notes with Chinese word-segmented search), Coding Workspace (it edits code, then
runs your acceptance command itself), deep research.

**Bring your own model.** 11 providers ship (Anthropic / OpenAI / OpenRouter / Gemini / DeepSeek /
MiniMax / Kimi / GLM / xAI / OpenCode Zen / OpenCode Go), one key each. Or add your own: write it
into `.knowme/providers.json` and it shows up on the Model page.

**Tools.** Web search and fetch, GitHub reads, calendars (Google / Apple), the document library,
notes, memory administration, file read/write and command execution, delegating a subtask, MCP
servers — 36 in all, called by the model as needed.

**Every step is visible.** What the gate searched for, which reasoning round it is on, which tool
it called, where a graph is — live, and again when you look back at an old conversation.

## Quickstart

**You need:** Python 3.11+, and an API key from one model provider (pick the cheapest one).

```bash
git clone https://github.com/<your-username>/knowme-agent
cd knowme-agent
python -m venv .venv
```

```bash
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate
```

```bash
pip install -e .
cp .env.example .env
```

Open `.env` and fill in two lines (DeepSeek here; `.env.example` shows all 11 providers):

```ini
KNOWME_PROVIDER=deepseek
DEEPSEEK_API_KEY=your-key
```

```bash
knowme
```

That opens the local web app: **http://localhost:8888 on Windows, http://localhost:7777
elsewhere**. No key yet? It won't white-screen — it tells you which line is missing and which
file to put it in.

**Try it:** *"Remember that I don't take meetings on Friday afternoons."* → quit → start again →
*"Am I free on Friday afternoon?"*

> Your memory is one SQLite file: `.knowme/state.db`. Open it, read it, delete it — it's yours.

### Other entry points

| Command | What it does |
|---|---|
| `knowme` | Open the local web app (this is the default) |
| `knowme web` | The same thing (spelled out for scripts) |
| `knowme connections` | Which integrations are configured, and whether they work |
| `knowme brief` | Morning briefing: calendar + mail + memory, **as a loop** |
| `knowme gather` | The same job, **as a graph**: four sources in parallel, then one digest |
| `knowme deep_research <topic>` | One deep-research run; the report lands in `.knowme/outbox/` and the knowledge base |
| `knowme skill install <url>` | Install a skill into your own `.knowme/skills/` |

`make run / web / brief / gather / eval / eval-judge / gate / lint` are shortcuts for the above.

## What's in the web app

The sidebar holds 10 pages plus 3 agent chat pages. Five runtime pages are deliberately
not listed — you reach them from links elsewhere:

| Group | Pages |
|---|---|
| Agents | General · Coding · Research (3 chat pages) |
| Applications | Reader · Document library / Knowledge base / Coding Workspace / Deep research |
| Management | Overview / Ops / Memory / Behaviour / Models / Connections |
| Runtime (not in the sidebar) | Gateway / Loop / Graph / Tools / Database |

**Overview** draws the architecture as a clickable diagram, with real numbers from this machine:

![Overview — the architecture diagram plus real stats](docs/images/overview.jpg)

**Memory** lets you edit, merge and delete facts directly; the Ops page shows every gate decision:

![Memory management](docs/images/memory.jpg)

**Deep research** — each round of the run on the left, the report it wrote on the right (and you can
go back to earlier runs):

![Deep research](docs/images/deepresearch.jpg)

> The conversation in the screenshots is **a real turn**; the memory, notes and report are demo
> data — that report was written straight into the knowledge base rather than produced by a run,
> so the left column says "this run left no process record". A run you start yourself keeps every
> round, and you can replay it frame by frame after closing the page.

## How it works

One run looks like this:

```
your message → gate (does this turn need memory? what to search for?) → assemble working memory
            → model reasons → if it wants a tool, call it and feed the result back → reply → save to memory
```

A few places worth reading on their own:

- **The gate** (`knowme/memory/retrieval_gate.py`): one cheap model call decides whether memory is
  needed, and what to search for. Its decision, the query, and the entries it hit are shown verbatim
  on the page.
- **Skills are loaded on demand**: a `SKILL.md` body enters the prompt only when it matches; the
  rest of the time all the model sees is a one-line catalog.
- **Loop vs graph**: fixed shapes get a graph, open-ended ones get the loop. Why it's split that way
  is in [`docs/loop-vs-graph.md`](docs/loop-vs-graph.md).
- **Fan-out happens inside a node**: deep research runs its sub-agents in parallel within one node
  rather than splitting into many nodes — otherwise the "go round again" edge gets lost.
- **No framework at runtime**: HTTP is `http.server`, storage is `sqlite3`, models go through each
  vendor's official SDK. The frontend has no build step — 14 JS files are concatenated in a fixed
  order into a single scope.

## Layout

```
knowme/
  core/         runtime: the loop skeleton, orchestration, sessions, tool registry, context compression
  memory/       three kinds of memory + the gate + consolidation (chats distilled into facts)
  tools/        what the model can call: search, calendar, notes, documents, coding, MCP…
  graph/        the graph engine + workflows (gather / triage / deep_research)
  agents/       the three role definitions
  applications/ reader, knowledge base, Coding Workspace, deep research
  ops/          operations surface: web app, tracing, CLI, release gate
    static/js/  the frontend (no build step; concatenated in order)
evals/
  deterministic/  offline pass/fail tests, no API key needed
  judge/          LLM-as-judge, scored
skills/           skills that ship with the package
sql/              the Supabase backend's schema
docs/             documentation for humans
```

## Tests

```bash
make lint    # ruff
make eval    # offline deterministic tests: no key, no network
make gate    # the release gate: deterministic must pass, the scored suite must clear its threshold
```

`make eval` drives the real code paths with a fake client (`ScriptedClient`) standing in for the
model, so it is fast, deterministic and free. Anything that depends on the model's judgement
(for example how accurate the gate is) lives in `evals/judge/` instead.

## Docs

- [`docs/loop-vs-graph.md`](docs/loop-vs-graph.md) — loops and graphs, and which one to reach for
- [`docs/CODING_WORKSPACE.md`](docs/CODING_WORKSPACE.md) — letting it edit code, and how far it's allowed to reach
- [`docs/integrations.md`](docs/integrations.md) — wiring up calendar, mail, Notion, MCP
- [`docs/memory-backends-playbook.md`](docs/memory-backends-playbook.md) — what changes if you move to Supabase / mem0 / Zep
- [`docs/agent-book/agent_context_management_guide.md`](docs/agent-book/agent_context_management_guide.md) — how the context management grew, one step at a time
- [`SECURITY.md`](SECURITY.md) — what it can and cannot do out of the box

## Feedback

Issues and PRs are welcome. This repository is **one person's, and small enough to read** —
please keep changes that size.
