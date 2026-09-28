"""DETERMINISTIC EVAL — the dashboard's static assets hang together.

There is no JS test runner (no build step, on purpose), so these cheap checks
guard the three ways the split frontend silently breaks:
  1. index.html references a <script>/<link> that doesn't exist on disk.
  2. an inline onclick=/oninput=/… handler calls a function that no js/ file
     defines (e.g. a handler was renamed or moved and a call site missed).
  3. a regex in the frontend is coupled to a format the BACKEND writes, and the
     format moved without it.
All render as something visibly wrong with no error anywhere — exactly what a
Python-side check can catch without a browser."""

from __future__ import annotations

import re
from pathlib import Path

from knowme.integrations import INTEGRATIONS

STATIC = Path(__file__).resolve().parents[2] / "knowme" / "ops" / "static"
INDEX = (STATIC / "index.html").read_text(encoding="utf-8")
JS_FILES = sorted((STATIC / "js").glob("*.js"))
JS_SRC = "\n".join(f.read_text(encoding="utf-8") for f in JS_FILES)
CONNECTION_LOGOS = {f"{integration.key}.svg" for integration in INTEGRATIONS}

# JS keywords / builtins / DOM globals an inline handler may call without a js/
# definition. Kept small on purpose — anything else must be a real app function.
ALLOWED = {
    "if", "for", "while", "switch", "return", "typeof", "new", "await", "function",
    "Math", "JSON", "Date", "Number", "String", "Boolean", "Object", "Array",
    "parseInt", "parseFloat", "isNaN", "console", "setTimeout", "setInterval",
    "encodeURIComponent", "decodeURIComponent", "alert", "confirm", "prompt",
    "document", "window", "event", "fetch",
}


def test_referenced_assets_exist():
    """Every /static/... in a src=/href= points to a real file."""
    refs = re.findall(r'(?:src|href)="(/static/[^"]+)"', INDEX)
    assert refs, "expected script/link references in index.html"
    for ref in refs:
        target = STATIC / ref[len("/static/"):]
        assert target.is_file(), f"index.html references missing asset: {ref}"


def test_static_agent_navigation_has_a_module_handler():
    """The shell's Agent buttons must stay wired after inline handlers move out."""
    bootstrap = (STATIC / "js" / "bootstrap.js").read_text(encoding="utf-8")
    assert 'data-agent="default"' in INDEX
    assert 'const shellHandlers = new Set(["openAgent"])' in bootstrap
    assert 'publicHandlers.openAgent(agent.dataset.agent)' in bootstrap


def test_connection_card_logos_are_local_and_complete():
    """Dynamic card image paths are generated in JS, so index.html cannot pin
    them. Keep every registry-backed logo present and valid."""
    logo_dir = STATIC / "logos" / "connections"
    assert {path.name for path in logo_dir.glob("*.svg")} == CONNECTION_LOGOS
    for name in CONNECTION_LOGOS:
        svg = (logo_dir / name).read_text(encoding="utf-8")
        assert svg.startswith("<svg "), f"{name} is not an SVG"
        assert "<title>" in svg, f"{name} needs an accessible title"


def test_connection_display_groups_stay_in_product_order():
    """The order is the reading order of the page, so it is pinned deliberately
    rather than left to whatever the object literal happens to say.

    "Memory", not "Storage": the registry group is called "Memory & Storage" and
    the display map used to keep the wrong half. Notion is the episodic store,
    Supabase the semantic one, and every hosted memory service that joins them
    is semantic too — none of it is generic storage."""
    assert 'const CONNECTION_GROUPS = ["Productivity", "Memory", "Tools"]' in JS_SRC


def test_every_registry_group_has_a_display_name():
    """connectionDisplayGroup falls back to "Tools" for anything unmapped, so a
    new registry group would not error — it would quietly file itself under the
    wrong heading and nobody would notice. Pin the mapping instead of trusting
    the fallback."""
    from knowme import integrations

    mapped = set(re.findall(r'^\s*"([^"]+)":\s*"[^"]+",\s*$', JS_SRC, re.MULTILINE))
    # AI Providers has its own page (Models), so it is deliberately not here.
    groups = {i.group for i in integrations.registry()} - {"AI Providers"}
    missing = groups - mapped
    assert not missing, f"registry groups with no display name, they'd land in Tools: {missing}"


def _defined_names() -> set[str]:
    names = set()
    names |= set(re.findall(r'^(?:async\s+)?function\s+([A-Za-z_$][\w$]*)', JS_SRC, re.MULTILINE))
    names |= set(re.findall(r'^(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=', JS_SRC, re.MULTILINE))
    return names


def _handler_calls(text: str) -> set[str]:
    """Function names called inside inline on*=... handlers (not method calls)."""
    called = set()
    for body in re.findall(r'\bon\w+="([^"]*)"', text) + re.findall(r"\bon\w+='([^']*)'", text):
        # identifier immediately before '(' that isn't a property access (no leading .)
        called |= set(re.findall(r'(?<![.\w$])([A-Za-z_$][\w$]*)\s*\(', body))
    return called


def test_inline_handlers_are_defined():
    """Every function an inline handler calls is defined in a js/ file (or is an
    allowed builtin). Catches renamed/moved handlers before they ship as dead
    buttons. Scans index.html AND the HTML the js files generate."""
    defined = _defined_names()
    called = _handler_calls(INDEX) | _handler_calls(JS_SRC)
    missing = {n for n in called if n not in defined and n not in ALLOWED}
    assert not missing, f"inline handlers call undefined functions: {sorted(missing)}"


def test_the_chat_thread_splits_off_every_shape_of_tool_block():
    """The reply drawn in a bubble must not CONTAIN the stored tool block; it
    has to match TWO shapes.

    chat_log is never rewritten, so rows written before the per-entry format are
    still there next to rows written after it. When the format moved, the regex
    in render.js kept matching only the old one and silently stopped matching
    anything — the dashboard started rendering whole tool outputs inside the
    chat bubble, with no error anywhere. A cross-language coupling with no test
    runner to notice, which is why the pattern is pulled out of the JS and
    applied here to strings the BACKEND actually produces.

    (The block is folded into a collapsed box, not thrown away — that half is
    the node harness in test_dashboard_render_frontend.py. This one is only
    about the pattern covering what the backend writes.)

    The extraction is loose about the function's SHAPE and strict about its
    SUBSTANCE. It used to match `.replace(/…tools used…/, "")` and broke when
    that was reformatted into a named local — a "check broke, code fine" false
    alarm. Anchoring on the one regex the splitter is built from, and carrying
    its flags across, means a reformat is free while a changed pattern fails.
    """
    from knowme.runtime import tool_entries as te

    source = (STATIC / "js" / "render.js").read_text(encoding="utf-8")
    match = re.search(r'const TOOLS_BLOCK = (/\S.*?/[a-z]*);', source)
    assert match, ('render.js no longer defines the TOOLS_BLOCK pattern that '
                   'splitTools() cuts the stored tool block off with')

    literal = match.group(1)
    slash = literal.rfind("/")
    pattern = re.compile(literal[1:slash],           # the JS /…/ body
                         re.IGNORECASE if "i" in literal[slash + 1:] else 0)

    current = te.render("Booked them.", [te.entry("search_web", {"q": "x"}, "lots")])
    many = te.render("Booked them.", [te.entry("search_web", {"q": "x"}, "lots"),
                                      te.entry("create_event", {"t": "y"}, "ok")])
    legacy = "Booked them.\n[tools used: search_web({'q': 'x'}) -> lots]"

    assert pattern.sub("", current).strip() == "Booked them.", "the current block leaks"
    assert pattern.sub("", many).strip() == "Booked them.", "a multi-entry block leaks"
    assert pattern.sub("", legacy).strip() == "Booked them.", "a legacy row leaks"
    # …and it must cut the block OFF, not swallow the reply with it.
    assert pattern.search("No tools this turn.") is None, "a plain reply is left alone"


def test_app_js_is_gone():
    """The monolith was split; index.html must not load the old single file."""
    assert not (STATIC / "app.js").exists(), "stale app.js still present"
    assert "/static/app.js" not in INDEX, "index.html still references app.js"


# The sidebar entries that are deliberately NOT listed: the runtime internals,
# reachable from links inside pages (#tools, #database, …) and by URL. Every
# other entry in index.html must be visible — a page you can only reach by
# typing a URL is a page that does not exist for the person using the dashboard.
DEEP_ONLY = {"#gateway", "#loop", "#graph", "#tools", "#database"}


def _css_rules() -> str:
    """style.css with its comments stripped: a comment QUOTING a selector is not
    a rule, and matching one would fail these tests for the wrong reason."""
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    return re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)


def test_every_sidebar_entry_except_the_deep_pages_is_visible():
    """A page listed in the sidebar must actually be in the sidebar.

    f6a5f8b hid seven operational entries with `display:none` — among them 模型
    and 连接, which are the pages you open the sidebar to change. Nothing
    failed and nothing looked broken: the entries were still in index.html, so
    the sidebar just quietly lost them. Reported as "配置模型的页面现在看不到".
    """
    rule = re.search(r"nav a\[href[^}]*display:none\}", _css_rules())
    hidden = set(re.findall(r'href="([^"]+)"', rule.group(0))) if rule else set()
    assert hidden == DEEP_ONLY, (
        "the CSS hides sidebar entries that must be visible "
        f"({sorted(hidden - DEEP_ONLY)}) or stopped hiding the deep pages "
        f"({sorted(DEEP_ONLY - hidden)}). Update DEEP_ONLY in the same commit "
        "if this was on purpose.")


def test_group_headers_are_not_hidden_by_position():
    """A group header is hidden by name, never by :nth-of-type.

    The header rule counted from the brand div, so when the nav was regrouped it
    hid the wrong headers — 总览/运维/行为 lost theirs and read as children of
    Applications. A group you mean to hide marks itself (`class="grp deep"`).
    """
    css = _css_rules()
    assert not re.search(r"nav > \.grp:nth-of-type", css), \
        "a positional selector hides whichever group lands in that slot"
    assert 'class="grp deep"' in INDEX, "the deep group must say so on itself"
    assert re.search(r"nav > \.grp\.deep\s*\{\s*display:none", css), \
        "the deep group's header is no longer hidden by name"
