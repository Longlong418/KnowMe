"""ContextPolicy — the chain runs in order, and the gated stages re-measure.

The old app.py code was three ifs: budget always; micro if over; summary if
STILL over after micro. This pins that the policy reproduces each branch, using
a fake token measure so no real compressor has to be provoked.
"""

from __future__ import annotations

from knowme.config import Settings
from knowme.core.context import policy as pol
from knowme.core.context.policy import ContextPolicy, FitContext, Stage


def _ctx(tmp_path, over: bool) -> FitContext:
    settings = Settings(home=tmp_path / "home")
    settings.ensure_home()
    ctx = FitContext(system="s", prompt="p", full_history=[], home=settings.home, conn=None,
                     session_id="t", client=None, small_model="m", settings=settings,
                     limit=100, trigger=0.8)
    return ctx


def _stage(name, log, *, change, when="always", persists=False):
    def fn(sent, ctx):
        log.append(name)
        return sent + [{"role": "assistant", "content": name}] if change else None
    return Stage(name, fn, when=when, persists=persists)


def _chain(log, changes):
    return ContextPolicy((
        _stage("tool_budget", log, change=changes[0]),
        _stage("micro_compact", log, change=changes[1], when="over"),
        _stage("state_summary", log, change=changes[2], when="over", persists=True),
    ))


def test_under_the_line_only_the_budget_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(pol.micro_compact, "prompt_tokens", lambda s, m: 10)
    log = []
    fit = _chain(log, (True, True, True)).fit([], _ctx(tmp_path, over=False))
    assert log == ["tool_budget"]
    assert fit.ran == ["tool_budget"] and fit.persisted is None


def test_micro_that_brings_it_under_stops_the_summary(tmp_path, monkeypatch):
    # over until micro_compact has run once, then under
    calls = {"n": 0}

    def measure(system, messages):
        calls["n"] += 1
        return 1000 if calls["n"] == 1 else 10
    monkeypatch.setattr(pol.micro_compact, "prompt_tokens", measure)
    log = []
    fit = _chain(log, (False, True, True)).fit([], _ctx(tmp_path, over=True))
    # tool_budget is always INVOKED (it is the cheap check); it changed nothing,
    # so it does not appear in `ran`. The summary was never reached.
    assert log == ["tool_budget", "micro_compact"]
    assert fit.ran == ["micro_compact"] and fit.persisted is None


def test_still_over_after_micro_runs_the_summary_and_persists(tmp_path, monkeypatch):
    monkeypatch.setattr(pol.micro_compact, "prompt_tokens", lambda s, m: 1000)
    log = []
    fit = _chain(log, (True, True, True)).fit([], _ctx(tmp_path, over=True))
    assert log == ["tool_budget", "micro_compact", "state_summary"]
    assert fit.persisted is fit.sent
    assert fit.sent[-1]["content"] == "state_summary"


def test_summary_failure_leaves_history_alone(tmp_path, monkeypatch):
    """state_summary returning None (the call failed) must not persist anything."""
    monkeypatch.setattr(pol.micro_compact, "prompt_tokens", lambda s, m: 1000)
    log = []
    history = [{"role": "user", "content": "x"}]
    fit = _chain(log, (False, False, False)).fit(history, _ctx(tmp_path, over=True))
    assert log == ["tool_budget", "micro_compact", "state_summary"]
    assert fit.sent is history and fit.persisted is None and fit.ran == []


def test_default_policy_is_the_old_chain():
    assert [(s.name, s.when, s.persists) for s in pol.default_policy().stages] == [
        ("tool_budget", "always", False),
        ("micro_compact", "over", False),
        ("state_summary", "over", True),
    ]
