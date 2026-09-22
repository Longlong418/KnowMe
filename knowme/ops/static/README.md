# Web frontend — the map

Plain static files served as-is by `knowme/ops/web/server.py` (a stdlib HTTP
server). **No build step, no framework, no bundler, no dependencies.** Edit these
files to change the UI; edit `knowme/ops/web/` to change the server/API.

- `index.html` — the shell (sidebar nav + one `<main>` pane) + one module entry.
- `style.css` — one flat file, `:root` design tokens at the top, light + dark.
- `js/` — the app, split by concern (below).

Two columns, not three: a conversation is a **route** (`#agent/<id>`), so it
renders into `<main>` like any other view instead of occupying a permanent
side column. `openAgent()` sets the hash; `selectAgent()` is the state switch
and never writes the hash — one direction only, so neither can recurse.

## The files (`js/`)

`bootstrap.js` is the only script referenced by the HTML. It is a native ES
module that fetches the focused feature files and evaluates them inside one
private function scope. The feature files still share their application state,
but that state is not installed as browser globals. Only the names needed by
generated inline handlers are explicitly exported at the boundary.

| file | what lives here |
|------|-----------------|
| `util.js`    | `esc`, markdown renderer, core globals (`D`, `editing`), `postJSON`, `reveal` |
| `memory.js`  | inline Memory / SOUL / skill editing actions |
| `models.js`  | `applyModel` (the one `/api/settings` writer), model picker / catalog / pins |
| `render.js`  | formatters + chat card renderers (`stagesRow`/`teleFooter`) + chatlog + streaming + `sendChat` |
| `trace.js`   | the turn timeline (`turnTimeline`/`pushStep`/`stepsFromTurn`) — what the agent did, in order |
| `diagram.js` | `archSVG` (the architecture chart) **and** its live animation (`STAGE`/`hot`/`pollEvents`) |
| `graph.js`   | graph workflows: data-driven topology chart (`graphSVG` from `d.graph.workflows`), the Overview panel (`graphPanel`), and `animateGraphStage` for `graph_*`/`route` events |
| `views.js`   | subtab/db helpers, SQL console, Memory/Tools sub-views, the `VIEWS` router object |
| `chat.js`    | `VIEWS.agent` + chat sessions/history (`loadThreadInto`), model chip, stats toggle |
| `reader.js`  | `VIEWS.reader`: the document library list, the reading pane (markdown + pdf.js), selection → agent. Also the Coding Workspace helpers |
| `knowledge.js` | the Knowledge Base actions (notes CRUD, preview, `[[links]]`) |
| `main.js`    | `render`/`refresh` loop and resizers |
| `bootstrap.js` | Native module entrypoint, scoped evaluator, and handler boundary |

`vendor/pdfjs/` is the one thing here that is not ours — a pinned pdf.js build,
checked in because there is no package manager. Its README records the version,
why that version, and how to upgrade it. It is loaded by a dynamic `import()`
from `reader.js` (native ES modules — still no bundler), so the `.mjs` MIME type
in `web/server.py::_serve_static` is load-bearing.

`chat.js` was `dock.js` until the chat stopped being a dock — `git log --follow`
across the rename. `main.js` is the LOOP, not a home for app code: an app's
helpers live in its own file, which is why the Reader and the Knowledge Base
moved out of it. If a `VIEWS.*` definition ends up in two files, the one that
loads LATER wins silently — that is what `test_reader_frontend.py` guards.

Data flows one way: `refresh()` (main.js) fetches `/api/data` into the global
`D`, then `render()` writes `VIEWS[hash](D)` into `#view`. Every mutation
(`applyModel`, `pinModel`, `saveFact`, …) calls `refresh()` when it's done.

## Rules that bite (read before editing)

- **Inline handlers are a compatibility boundary.** Buttons use
  `onclick="fn()"` in generated HTML. `bootstrap.js` exports the declared
  handler names explicitly; keep those names stable and do not add unrelated
  state to `window`.
- **`archSVG` is byte-frozen — do not rewrite the architecture chart.** It emits
  `data-node="…"`/`data-edge="…"` ids that the `STAGE` map (same file) drives the
  live animation from. If you ever change a node/edge id, change it in both
  places. (Both are in `diagram.js` precisely so they stay together.)
- **The graph chart is data-driven — never hand-edit a topology.** `graphSVG`
  renders `Graph.describe()` served in `/api/data`, so the picture is provably
  what the engine runs (`test_graph_topology_payload.py` pins it). To change the
  chart's shape, change the workflow in `knowme/graph/workflows/`. Graph ids are
  namespaced `g-<node>` / `g-<src>-<dst>` so they can never collide with archSVG's.
- **No build step / no framework / no new dependencies.** If you reach for one,
  stop — the whole point is that this reads and runs with nothing installed.
- **No emojis in UI** (project rule). Known pre-existing exception: the `★`/`☆`
  pin stars in `models.js` (typographic dingbats, not colour emoji) — left as-is.

## Verifying a change (no JS test runner exists)

Frontend logic is not unit-tested; verify in the browser preview:
`make web` (or the preview tool) → hard-reload `localhost:7777` → click the
sidebar tabs and an agent in the sidebar → check the console shows **zero
errors**. The Python side (`web/server.py` endpoints, `_thread_history`, pins,
session resume) *is* covered by `evals/deterministic/`.

**A running server does not pick up Python changes.** Static files here (`.js`,
`.css`, `index.html`) are read from disk on every request, so a hard-reload shows
them. But `knowme/ops/web/` and everything it imports are held in memory — after
pulling or editing backend code, **restart `make web`**, or the page renders
new markup against stale data (e.g. a new Settings panel that shows nothing because
the old route isn't sending its fields).
