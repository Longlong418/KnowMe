"""OFFLINE checks for the two ROLE models: the retrieval gate and the summariser
may each run on their own provider and model, and all four variables empty must
be exactly what KnowMe did before they existed. No network, no real keys.

Why this file exists: the role path is the one place in a turn that resolves a
client other than the loop's, and it MUST NOT raise — the gate fails open by
design, so a role that blows up is reported as a healthy "retrieve" on every
turn instead of as an error. Both halves of that (it resolves, and it degrades)
are pinned here.
"""

from __future__ import annotations

import dataclasses

import pytest
from knowme.loop.models import PROVIDERS, Roles, role_runs_on, roles_for

from evals.helpers import ScriptedClient, make_knowme, response, text_block
from knowme.config import Settings

ROLES = ("gate", "summary")


@pytest.fixture(autouse=True)
def fake_keys(monkeypatch):
    """Every provider has a key, so "which provider" is never answered by
    accident. Tests that need a MISSING key take it away themselves.

    KNOWME_MODEL / KNOWME_SMALL_MODEL are cleared too: knowme/config.py calls
    load_dotenv() at import, so without this the maintainer's .env decides what
    "all four variables empty" means — and for a provider that happens to own the
    model named there (minimax, with KNOWME_SMALL_MODEL=MiniMax-M2.5) the answer
    changes. A test must describe its own world."""
    for provider in PROVIDERS.values():
        monkeypatch.setenv(provider.key_env, "fake-key-for-tests")
    monkeypatch.delenv("KNOWME_API_KEY", raising=False)
    monkeypatch.delenv("KNOWME_BASE_URL", raising=False)
    monkeypatch.delenv("KNOWME_MODEL", raising=False)
    monkeypatch.delenv("KNOWME_SMALL_MODEL", raising=False)
    for role in ROLES:
        monkeypatch.delenv(f"KNOWME_{role.upper()}_PROVIDER", raising=False)
        monkeypatch.delenv(f"KNOWME_{role.upper()}_MODEL", raising=False)


def settings_for(provider: str, **kwargs) -> Settings:
    """A Settings with no explicit credentials unless a test asks for them."""
    kwargs.setdefault("api_key", "")
    kwargs.setdefault("base_url", None)
    return Settings(provider=provider, **kwargs)


# --- 1. all four empty = exactly what it was before ---------------------------

@pytest.mark.parametrize("name", list(PROVIDERS))
def test_no_role_variables_means_the_injected_client_and_the_small_model(name, monkeypatch):
    """The whole promise of this feature's default: nothing set, nothing changes.

    The injected client is reused verbatim on purpose — an eval that swaps in a
    scripted model must have that model serve the gate too, or every offline
    test would quietly start talking to the network.
    """
    sentinel = ScriptedClient([])
    settings = settings_for(name)
    roles = roles_for(settings, sentinel)

    assert isinstance(roles, Roles)
    for role in ROLES:
        assert getattr(roles, f"{role}_client") is sentinel
        assert getattr(roles, f"{role}_model") == PROVIDERS[name].small_model


# --- 2. a role may live on another provider ----------------------------------

def capture_clients(monkeypatch) -> list:
    """Record every client a role builds, whichever wire it speaks.

    The anthropic branch does `import anthropic` inside client_for and calls
    anthropic.Anthropic(**kwargs), so patching the class on the module catches
    both wires with one list. Anything else would test one wire and assume the
    other — and the two providers below are exactly one of each."""
    import anthropic

    from knowme.core import models

    built: list = []

    def fake(api_key, base_url=None, timeout=120.0, **kwargs):
        built.append((api_key, base_url))
        return object()

    monkeypatch.setattr(models, "OpenAICompatClient", fake)
    monkeypatch.setattr(anthropic, "Anthropic", fake)
    return built


# xai speaks the OpenAI wire, kimi the anthropic one — a role must be able to
# run on either, since which wire a provider speaks is not the user's problem.
ROLE_HOSTS = ["xai", "kimi"]


@pytest.mark.parametrize("host", ROLE_HOSTS)
def test_a_role_can_run_on_another_providers_wire(host, monkeypatch):
    """The point of the whole feature: deepseek runs the loop, another lab runs
    the gate — with THAT lab's key and THAT lab's endpoint.

    Captured at the constructor: the arguments a client was built with are the
    only place "it really used that provider" is visible without a network."""
    built = capture_clients(monkeypatch)
    monkeypatch.setenv(PROVIDERS[host].key_env, f"{host}-key")

    settings = settings_for("deepseek", api_key="deepseek-key",
                            base_url="https://deepseek.example/v1",
                            gate_provider=host, gate_model="a-model-of-its-own")
    roles = roles_for(settings, ScriptedClient([]))

    assert roles.gate_model == "a-model-of-its-own"
    assert built == [(f"{host}-key", PROVIDERS[host].configured_base_url())]
    # ...and the main client is untouched: the summary role follows the active
    # provider, which is what makes it reuse the injected client.
    assert roles.summary_client is not built[0]
    assert roles.summary_model == PROVIDERS["deepseek"].small_model


@pytest.mark.parametrize("host", ROLE_HOSTS)
def test_the_active_providers_credentials_are_not_handed_to_a_second_one(host, monkeypatch):
    """KNOWME_API_KEY/KNOWME_BASE_URL are ONE pair belonging to the active
    provider (that is how the Models page writes them). Handing them to a second
    provider posts one lab's endpoint another lab's key."""
    built = capture_clients(monkeypatch)
    monkeypatch.setenv(PROVIDERS[host].key_env, f"{host}-key")

    settings = settings_for("deepseek", api_key="the-active-key",
                            base_url="https://deepseek.example/v1",
                            gate_provider=host)
    roles_for(settings, ScriptedClient([]))

    assert built == [(f"{host}-key", PROVIDERS[host].configured_base_url())]
    assert "the-active-key" not in built[0]
    assert "deepseek.example" not in (built[0][1] or "")


# --- 3. a model id belongs to the provider it was configured for -------------

def test_a_foreign_model_id_is_dropped_for_a_role_too():
    """Same guard as the loop's own model, for the same reason: claude's id at
    xAI is a 400, and the gate reads a 400 as "retrieve anyway" — a permanent
    failure wearing the costume of a healthy decision."""
    settings = settings_for("xai", gate_provider="xai",
                            gate_model="claude-haiku-4-5-20251001")
    name, model = role_runs_on(settings, "gate")[:2]
    assert (name, model) == ("xai", PROVIDERS["xai"].small_model)


def test_a_role_that_follows_the_active_provider_gets_the_resolved_small_model(monkeypatch):
    """The leftover-model case the guard now also covers: switch provider, and a
    KNOWME_GATE_MODEL from the old one must not follow you across."""
    monkeypatch.setenv("KNOWME_GATE_MODEL", "claude-haiku-4-5-20251001")
    settings = settings_for("xai", gate_model="claude-haiku-4-5-20251001")
    assert role_runs_on(settings, "gate")[:2] == ("xai", PROVIDERS["xai"].small_model)


# --- 4. it must never raise --------------------------------------------------

def test_a_role_with_no_key_degrades_instead_of_exiting(monkeypatch):
    """The landmine this feature would otherwise plant. client_for raises
    SystemExit when a provider has no key, and SystemExit is a BaseException —
    the gate's own `except Exception` would let it walk straight through and
    kill EVERY message instead of failing open. One typo in .env would do it."""
    keyless = dataclasses.replace(PROVIDERS["kimi"], key_env="NOT_SET_ANYWHERE")
    monkeypatch.setitem(PROVIDERS, "kimi", keyless)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-key")

    sentinel = ScriptedClient([])
    settings = settings_for("deepseek", gate_provider="kimi", gate_model="kimi-k3")
    roles = roles_for(settings, sentinel)          # must not raise

    assert roles.gate_client is sentinel
    assert roles.gate_model == PROVIDERS["deepseek"].small_model


def test_an_unknown_role_provider_degrades_too(monkeypatch):
    """A provider that was removed, or a typo, must not take the process down
    either — the same rule, reached before any client is built."""
    sentinel = ScriptedClient([])
    settings = settings_for("deepseek", gate_provider="not-a-provider")
    roles = roles_for(settings, sentinel)
    assert roles.gate_client is sentinel
    assert roles.gate_model == PROVIDERS["deepseek"].small_model


def test_the_page_is_told_what_actually_runs(monkeypatch):
    """roles_info is what the Models page prints, and it must agree with the turn
    — through role_runs_on, the same function roles_for uses. A page that showed
    the provider you named while the turns ran on another one would make the
    choice look applied and do nothing."""
    from knowme.ops.settings_api import roles_info

    settings = settings_for("deepseek", gate_provider="kimi")   # kimi HAS a key here
    info = roles_info(settings)["gate"]
    assert info["provider"] == "kimi"
    assert info["effective_provider"] == "kimi"
    assert info["degraded"] is False

    keyless = dataclasses.replace(PROVIDERS["kimi"], key_env="NOT_SET_ANYWHERE")
    monkeypatch.setitem(PROVIDERS, "kimi", keyless)
    info = roles_info(settings)["gate"]
    assert info["provider"] == "kimi", "the raw choice must stay on the select"
    assert info["effective_provider"] == "deepseek"
    assert info["effective_model"] == PROVIDERS["deepseek"].small_model
    assert info["degraded"] is True


# --- 5/6. saving them --------------------------------------------------------

def test_settings_saves_the_roles_and_can_put_them_back_to_follow(tmp_path, monkeypatch):
    """`is not None` and not `if value:`: "" is how you go back to 「跟随当前
    服务商」, and a falsy test would make that a one-way door."""
    from knowme.ops import settings_api

    monkeypatch.chdir(tmp_path)
    for role in ROLES:
        monkeypatch.setenv(f"KNOWME_{role.upper()}_PROVIDER", "SENTINEL")
        monkeypatch.setenv(f"KNOWME_{role.upper()}_MODEL", "SENTINEL")

    settings_api.apply_settings({"gate_provider": "kimi", "gate_model": "kimi-k3",
                                 "summary_provider": "deepseek", "summary_model": ""})

    import os
    assert os.environ["KNOWME_GATE_PROVIDER"] == "kimi"
    assert os.environ["KNOWME_GATE_MODEL"] == "kimi-k3"
    assert os.environ["KNOWME_SUMMARY_PROVIDER"] == "deepseek"
    assert os.environ["KNOWME_SUMMARY_MODEL"] == ""
    written = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "KNOWME_GATE_PROVIDER=" in written and "kimi" in written
    # the real repo .env must not have been touched
    assert os.path.abspath(".env") == os.path.abspath(str(tmp_path / ".env"))

    # back to 「follow」 — "" is a value, not an absence
    settings_api.apply_settings({"gate_provider": "", "gate_model": ""})
    assert os.environ["KNOWME_GATE_PROVIDER"] == ""
    assert os.environ["KNOWME_GATE_MODEL"] == ""
    assert os.environ["KNOWME_SUMMARY_PROVIDER"] == "deepseek", "untouched fields must stay"


def test_a_payload_without_the_roles_leaves_them_alone(tmp_path, monkeypatch):
    """The toggles and the roles share one endpoint; saving one must not clear
    the other."""
    from knowme.ops import settings_api

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("KNOWME_GATE_PROVIDER", "kimi")
    monkeypatch.setenv("KNOWME_GATE_MODEL", "kimi-k3")

    settings_api.apply_settings({"graph_workflows": "1"})

    import os
    assert os.environ["KNOWME_GATE_PROVIDER"] == "kimi"
    assert os.environ["KNOWME_GATE_MODEL"] == "kimi-k3"
    text = (tmp_path / ".env").read_text(encoding="utf-8") if (tmp_path / ".env").exists() else ""
    assert "KNOWME_GATE_PROVIDER" not in text


def test_saving_settings_does_not_conjure_an_agent(tmp_path, monkeypatch):
    """rebuild() BUILDS a default agent when none is live (its documented
    historical behaviour). Without the guard, saving a toggle in a CLI process
    would open a database and an MCP bridge as a side effect."""
    from knowme.ops import browser_agent, settings_api

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(browser_agent, "rebuild",
                        lambda: pytest.fail("a settings save built an agent"))
    assert browser_agent.current_agents() == {}

    settings_api.apply_settings({"experimental": "1"})


# --- 7. the injected client still serves every role in a real turn -----------

def test_a_scripted_turn_serves_both_roles_with_the_injected_client(tmp_path):
    """The seam that keeps every other offline eval honest: a turn runs the gate
    and the consolidation check, and both must go through the injected model.
    Without this, a Memory built with client=None (the dashboard's display-only
    one) could break and nothing would notice."""
    gate_answer = response([text_block('{"retrieve": false, "query": "", "reason": "闲聊"}')])
    chat_answer = response([text_block("你好。")])
    app = make_knowme(tmp_path / "home", client=ScriptedClient([gate_answer, chat_answer]))

    result = app.respond("你好")

    assert result.reply == "你好。"
    assert app.roles.gate_client is app.client
    assert app.memory.roles.gate_model == app.settings.small_model
