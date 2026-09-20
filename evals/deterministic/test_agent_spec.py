"""AgentSpec — the default spec IS the agent this repo has always run.

A None field resolves to Settings, and a resolved default produces the same
system prompt byte for byte as the old parameterless build_system did — so
introducing the spec changed nothing about what the model sees.
"""

from __future__ import annotations

from knowme.config import Settings
from knowme.core.session import Session, load_soul
from knowme.core.spec import DEFAULT_SPEC, AgentSpec, resolve
from knowme.core.tools import Tool, ToolRegistry


def _settings(tmp_path) -> Settings:
    s = Settings(home=tmp_path / "home", model="m-big", small_model="m-small",
                 max_iterations=7, max_tokens=999)
    s.ensure_home()
    return s


def test_default_spec_resolves_to_settings(tmp_path):
    r = resolve(DEFAULT_SPEC, _settings(tmp_path))
    assert (r.model, r.small_model, r.max_iterations, r.max_tokens) == ("m-big", "m-small", 7, 999)
    assert r.system_prompt is None and r.tools is None
    assert [s.name for s in r.context_policy.stages] == ["tool_budget", "micro_compact", "state_summary"]


def test_spec_fields_override_settings(tmp_path):
    spec = AgentSpec(name="coder", model="other", max_iterations=2, system_prompt="You code.")
    r = resolve(spec, _settings(tmp_path))
    assert (r.model, r.small_model, r.max_iterations) == ("other", "m-small", 2)
    assert r.system_prompt == "You code."


def test_default_system_prompt_is_byte_identical_to_soul(tmp_path):
    settings = _settings(tmp_path)
    session = Session(settings, memory=None)
    r = resolve(DEFAULT_SPEC, settings)
    expected = "\n".join([
        load_soul(settings),
        (
            f"\nYour model: you are running on '{settings.model}' via the "
            f"'{settings.provider}' provider, inside KnowMe, a local-first "
            "open-source agent harness."
        ),
    ])
    assert session.build_system(r.system_prompt, r.model) == expected
    assert session.build_system() == expected   # the parameterless call still works


def test_persona_replaces_soul_but_keeps_model_line(tmp_path):
    settings = _settings(tmp_path)
    out = Session(settings, memory=None).build_system("You are Coder.", "m-x")
    assert out.startswith("You are Coder.\n")
    assert "running on 'm-x'" in out
    assert "KnowMe, a personal assistant" not in out


def test_registry_subset_shares_tools_and_ignores_unknown():
    reg = ToolRegistry()
    a = Tool("a", "A", {"type": "object"}, lambda: "a")
    b = Tool("b", "B", {"type": "object"}, lambda: "b")
    reg.register(a)
    reg.register(b)
    sub = reg.subset({"b", "zzz"})
    assert [t["name"] for t in sub.schemas()] == ["b"]
    assert sub.execute("b", {}) == "b"
    assert sub.execute("a", {}).startswith("Error: unknown tool")
    assert [t["name"] for t in reg.schemas()] == ["a", "b"]   # the original is untouched


def test_runtime_applies_the_agent_tool_allowlist(tmp_path):
    """A spec is a capability boundary, not just documentation for the UI."""
    from evals.helpers import ScriptedClient, make_knowme, response, text_block

    seen = []

    class Client(ScriptedClient):
        def _create(self, **kwargs):
            seen.append(kwargs)
            return super()._create(**kwargs)

    gate = response([text_block('{"retrieve": false, "query": "", "reason": "x"}')])
    app = make_knowme(
        tmp_path / "home",
        client=Client([gate, response([text_block("ok")])]),
    )
    spec = AgentSpec(name="reader", tools=frozenset({"get_document", "highlight"}))

    result = app.runtime.run_turn(spec, app.session, "read this")

    assert result.reply == "ok"
    assert {tool["name"] for tool in seen[-1]["tools"]} == {"get_document", "highlight"}
