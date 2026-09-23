"""DETERMINISTIC EVAL — the dashboard's JavaScript actually parses.

Written the moment it was needed. A `title=` attribute added to a button
contained the word knowme wrapped in backticks, inside a template literal:

    title="... the `knowme` partition is never named ..."

The backticks closed the template literal early and the file stopped parsing.
Not the button, not the panel — the whole dashboard went blank, and the only
symptom was a page that looked like it had not reloaded.

Python has ruff and 580 tests standing between a typo and a broken web.
The JavaScript, which is most of what a user actually touches, had nothing.
`node --check` is a syntax check and no more — it will not catch a wrong
selector or a bad fetch — but this class of bug turns the entire page off,
which is the loudest possible failure for the cheapest possible test.

Skips when node is absent rather than failing: a contributor without node
should still get a green suite, and CI has it.
"""

from __future__ import annotations

import pathlib
import re
import shutil
import subprocess

import pytest

JS_DIR = pathlib.Path(__file__).resolve().parents[2] / "knowme" / "ops" / "static" / "js"
SCRIPTS = sorted(JS_DIR.glob("*.js"))


def test_there_are_scripts_to_check():
    """A guard whose glob silently matches nothing passes forever and protects
    nothing. If the frontend moves, this fails and says so."""
    assert SCRIPTS, f"no .js found under {JS_DIR} — did the frontend move?"


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
@pytest.mark.parametrize("script", SCRIPTS, ids=[s.name for s in SCRIPTS])
def test_every_dashboard_script_parses(script: pathlib.Path):
    result = subprocess.run(  # noqa: S603 — fixed argv, path from our own glob
        [shutil.which("node"), "--check", str(script)],
        capture_output=True, text=True, timeout=30, check=False,
    )
    assert result.returncode == 0, (
        f"{script.name} does not parse — the dashboard will render nothing.\n"
        f"{result.stderr.strip()}"
    )


# --- the check that `node --check` per file CANNOT make ---------------------
# bootstrap.js concatenates these files and evaluates them as ONE
# `new Function(source)` body, so they share a single top-level scope. A name
# declared twice — `const foo` in two files, or a `let` colliding with a
# `function` — is a SyntaxError for the WHOLE bundle, and the dashboard comes up
# blank with no error the user can see. `node --check` runs per file, so it sees
# each file alone and every file is individually fine. bootstrap.js already pulls
# the list of loadable files out of its own FEATURES array; read it from there,
# so this checks the bundle that actually ships rather than a guess at it.

BOOTSTRAP = JS_DIR / "bootstrap.js"


def _bundle_files() -> list[str]:
    source = BOOTSTRAP.read_text(encoding="utf-8")
    listed = re.search(r"const FEATURES = \[(.*?)\]", source, re.DOTALL)
    assert listed, "bootstrap.js no longer declares a FEATURES list — did loading change?"
    return re.findall(r'"([^"]+)"', listed.group(1))


def _declarations(source: str) -> list[tuple[str, str]]:
    """(kind, name) for every top-level declaration, by bootstrap's own idea of
    what one is — the same two regexes it uses to decide what to export."""
    out = [(m.group(1), m.group(2)) for m in
           re.finditer(r"^(const|let|var)\s+([A-Za-z_$][\w$]*)\s*=", source, re.MULTILINE)]
    out += [("function", m.group(1)) for m in
            re.finditer(r"^(?:async\s+)?function\s+([A-Za-z_$][\w$]*)", source, re.MULTILINE)]
    return out


def test_every_script_is_in_the_bundle():
    """A file nobody loads is a file nobody runs. Adding deepresearch.js to the
    directory and forgetting FEATURES is a page that can never appear."""
    loaded = set(_bundle_files())
    on_disk = {p.name for p in SCRIPTS} - {BOOTSTRAP.name}
    assert on_disk - loaded == set(), (
        f"these scripts exist but nothing loads them: {sorted(on_disk - loaded)}"
    )
    assert loaded - on_disk == set(), (
        f"FEATURES names scripts that do not exist: {sorted(loaded - on_disk)}"
    )


def test_no_two_bundled_scripts_declare_the_same_name():
    """The app-wide parse error. Two `var`s are legal and nothing here uses any;
    anything else colliding is a blank page."""
    seen: dict[str, list[tuple[str, str]]] = {}
    for name in _bundle_files():
        path = JS_DIR / name
        assert path.is_file(), f"FEATURES names {name}, which is not on disk"
        for kind, declared in _declarations(path.read_text(encoding="utf-8")):
            seen.setdefault(declared, []).append((kind, name))
    clashes = {n: where for n, where in seen.items()
               if len(where) > 1 and not all(k == "var" for k, _ in where)}
    assert not clashes, (
        "these names are declared by more than one bundled script, which makes the"
        " whole dashboard a syntax error (one shared `new Function` scope):\n  "
        + "\n  ".join(f"{n}: {where}" for n, where in sorted(clashes.items()))
    )


def test_the_collision_check_can_actually_see_a_collision():
    """Reverse check: a checker whose regex matches nothing reports a clean
    bundle forever. `esc` is a top-level const in util.js."""
    assert ("const", "esc") in _declarations((JS_DIR / "util.js").read_text(encoding="utf-8"))
    assert _bundle_files(), "FEATURES parsed as empty"
