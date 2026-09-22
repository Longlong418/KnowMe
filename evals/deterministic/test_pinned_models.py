"""DETERMINISTIC EVAL — the curated model shortlist ("Your models").

Feature: a user pins models across providers; the chat switcher shows exactly
that shortlist, and the FIRST pinned model per provider is that provider's
default (adopted when you switch to it). Live goal Sean asked for: "a default
model for each api key, the user can choose more models, and the chat switcher
shows the models already selected in settings."

The shortlist lives in .knowme/models.json as {"pinned": ["provider:model", ...]};
these tests drive the same helpers the dashboard's /api/pin route calls."""

from __future__ import annotations

import json
import os

import pytest

from knowme.ops import catalog
from knowme.ops import settings_api as d

PROVIDER_KEYS = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "DEEPSEEK_API_KEY",
                 "MINIMAX_API_KEY", "MOONSHOT_API_KEY", "ZHIPU_API_KEY", "OPENROUTER_API_KEY",
                 "XAI_API_KEY", "OPENCODE_ZEN_API_KEY", "OPENCODE_GO_API_KEY")


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Point every load_settings() at a throwaway home, run from there so
    apply_settings's find_dotenv writes to a throwaway .env, and clear all
    provider keys so the default shortlist is empty unless a test sets one."""
    monkeypatch.setenv("KNOWME_HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("")
    for var in PROVIDER_KEYS:
        monkeypatch.delenv(var, raising=False)
    # apply_settings bypasses monkeypatch and writes directly to os.environ,
    # so KNOWME_PROVIDER must also be tracked to prevent leaking into later
    # tests (test_tool_trigger would inherit a stale provider and crash).
    monkeypatch.delenv("KNOWME_PROVIDER", raising=False)
    return tmp_path


def test_pin_persists_and_marks_first_per_provider_default(home):
    d.pin_action({"action": "pin", "provider": "gemini", "model": "gemini-3.5-flash"})
    d.pin_action({"action": "pin", "provider": "gemini", "model": "gemini-3.5-pro"})
    info = d.pin_action({"action": "pin", "provider": "kimi", "model": "kimi-k3"})

    # persisted to disk in insertion order
    saved = json.loads((home / "models.json").read_text(encoding="utf-8"))["pinned"]
    assert saved == ["gemini:gemini-3.5-flash", "gemini:gemini-3.5-pro", "kimi:kimi-k3"]

    # settings_info() surfaces the shortlist; first-per-provider is the default
    flags = {(p["provider"], p["model"]): p["default"] for p in info["pinned"]}
    assert flags[("gemini", "gemini-3.5-flash")] is True
    assert flags[("gemini", "gemini-3.5-pro")] is False
    assert flags[("kimi", "kimi-k3")] is True


def test_default_model_for_reads_first_pinned(home):
    assert catalog.default_model_for("kimi") == ""          # nothing pinned yet
    d.pin_action({"action": "pin", "provider": "kimi", "model": "kimi-k3"})
    d.pin_action({"action": "pin", "provider": "kimi", "model": "kimi-k2.6"})
    assert catalog.default_model_for("kimi") == "kimi-k3"   # the first one


def test_make_default_moves_model_to_front_of_its_group(home):
    d.pin_action({"action": "pin", "provider": "kimi", "model": "kimi-k3"})
    d.pin_action({"action": "pin", "provider": "kimi", "model": "kimi-k2.6"})
    d.pin_action({"action": "default", "provider": "kimi", "model": "kimi-k2.6"})
    assert catalog.default_model_for("kimi") == "kimi-k2.6"


def test_unpin_removes_and_promotes_next_default(home):
    d.pin_action({"action": "pin", "provider": "gemini", "model": "gemini-3.5-flash"})
    d.pin_action({"action": "pin", "provider": "gemini", "model": "gemini-3.5-pro"})
    info = d.pin_action({"action": "unpin", "provider": "gemini", "model": "gemini-3.5-flash"})
    assert [p["model"] for p in info["pinned"]] == ["gemini-3.5-pro"]
    assert catalog.default_model_for("gemini") == "gemini-3.5-pro"   # survivor is now default


def test_switching_provider_adopts_its_pinned_default(home, monkeypatch):
    """apply_provider on a provider change uses that provider's pinned default,
    never carrying the previous provider's model across endpoints (the live
    kimi->gemini 404)."""
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    monkeypatch.setenv("MOONSHOT_API_KEY", "k")
    (home / "models.json").write_text(json.dumps({"pinned": ["kimi:kimi-k3"]}))
    from knowme import integrations
    from knowme.ops import browser_agent

    monkeypatch.setattr(browser_agent, "rebuild", lambda: None)
    monkeypatch.setattr(browser_agent, "current", lambda: type("A", (), {"tracer": type("T", (), {"event": lambda *args: None})()})())
    monkeypatch.setenv("KNOWME_PROVIDER", "gemini")
    monkeypatch.setenv("KNOWME_MODEL", "gemini-3.5-flash")
    result = integrations.apply_provider("kimi")
    assert result.ok
    assert os.getenv("KNOWME_PROVIDER") == "kimi"
    assert os.getenv("KNOWME_MODEL") == "kimi-k3"  # not gemini's model


def _live_agent(monkeypatch):
    """Stub the browser agent so apply_provider can run without a real turn."""
    from knowme.ops import browser_agent

    monkeypatch.setattr(browser_agent, "rebuild", lambda: None)
    monkeypatch.setattr(
        browser_agent, "current",
        lambda: type("A", (), {"tracer": type("T", (), {"event": lambda *a: None})()})(),
    )


def test_a_model_you_save_is_the_one_the_shortlist_shows(home, monkeypatch):
    """The live bug: change a provider's model in the edit modal and the header
    chip says the new one, but the switcher still lists the old one — because
    KNOWME_MODEL (the .env field the modal writes) and the pinned shortlist were
    two places deciding the same thing, and only the first was updated."""
    monkeypatch.setenv("MOONSHOT_API_KEY", "k")
    (home / "models.json").write_text(json.dumps({"pinned": ["kimi:kimi-k3"]}))
    monkeypatch.setenv("KNOWME_PROVIDER", "kimi")
    monkeypatch.setenv("KNOWME_MODEL", "kimi-k3")
    _live_agent(monkeypatch)
    from knowme import integrations

    assert integrations.apply_provider("kimi", model="kimi-k2.6").ok

    assert os.getenv("KNOWME_MODEL") == "kimi-k2.6"
    # the shortlist follows the save, in place (kimi keeps its slot in the menu)
    assert catalog.pinned_specs() == ["kimi:kimi-k2.6"]
    assert catalog.default_model_for("kimi") == "kimi-k2.6"
    # ...and the chat switcher's 默认 tag agrees with the chip
    rows = [(p["model"], p["default"]) for p in d.settings_info()["pinned"]]
    assert rows == [("kimi-k2.6", True)]


def test_switching_away_and_back_keeps_the_model_you_saved(home, monkeypatch):
    """The second half of the report: after saving a new model, switching to
    another provider and back re-adopted the OLD pin (the '又显示最初配置的 glm'
    step) — apply_provider's switch branch reads default_model_for()."""
    monkeypatch.setenv("MOONSHOT_API_KEY", "k")
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    (home / "models.json").write_text(json.dumps({"pinned": ["kimi:kimi-k3"]}))
    monkeypatch.setenv("KNOWME_PROVIDER", "kimi")
    monkeypatch.setenv("KNOWME_MODEL", "kimi-k3")
    _live_agent(monkeypatch)
    from knowme import integrations

    integrations.apply_provider("kimi", model="kimi-k2.6")
    assert integrations.apply_provider("gemini").ok          # away...
    assert integrations.apply_provider("kimi").ok            # ...and back
    assert os.getenv("KNOWME_MODEL") == "kimi-k2.6"          # not kimi-k3 again


def test_saving_a_model_takes_over_the_providers_row_and_leaves_others_alone(home, monkeypatch):
    """The provider keeps its place in the menu and other providers' models are
    untouched; the row the provider had (its old model) is the one that gives way,
    so a provider never accumulates a row per model you tried."""
    (home / "models.json").write_text(json.dumps({"pinned": [
        "gemini:gemini-3.5-flash", "kimi:kimi-k3", "openai:gpt-5.3"]}))
    monkeypatch.setenv("KNOWME_PROVIDER", "kimi")
    monkeypatch.setenv("KNOWME_MODEL", "kimi-k3")
    _live_agent(monkeypatch)
    from knowme import integrations

    assert integrations.apply_provider("kimi", model="kimi-k2.6").ok
    assert catalog.pinned_specs() == [
        "gemini:gemini-3.5-flash", "kimi:kimi-k2.6", "openai:gpt-5.3"]
    assert catalog.default_model_for("kimi") == "kimi-k2.6"


def test_picking_a_model_that_is_already_pinned_keeps_the_others(home, monkeypatch):
    """Two models pinned for one provider (a deliberate alternative): using the
    second one makes it the provider's model by taking the first's place in the
    group — the first stays in the list, one row down."""
    (home / "models.json").write_text(json.dumps({"pinned": [
        "kimi:kimi-k3", "kimi:kimi-k2.6", "openai:gpt-5.3"]}))
    monkeypatch.setenv("KNOWME_PROVIDER", "kimi")
    monkeypatch.setenv("KNOWME_MODEL", "kimi-k3")
    _live_agent(monkeypatch)
    from knowme import integrations

    assert integrations.apply_provider("kimi", model="kimi-k2.6").ok
    assert catalog.pinned_specs() == ["kimi:kimi-k2.6", "kimi:kimi-k3", "openai:gpt-5.3"]
    assert catalog.default_model_for("kimi") == "kimi-k2.6"


def test_a_provider_with_no_pin_gets_the_saved_model_added(home, monkeypatch):
    """Nothing pinned for that provider yet: the saved model is appended, so the
    switcher shows the model the chip claims (it used to be invisible)."""
    (home / "models.json").write_text(json.dumps({"pinned": ["openai:gpt-5.3"]}))
    monkeypatch.setenv("MOONSHOT_API_KEY", "k")
    monkeypatch.setenv("KNOWME_PROVIDER", "kimi")
    _live_agent(monkeypatch)
    from knowme import integrations

    assert integrations.apply_provider("kimi", model="kimi-k2.6").ok
    assert catalog.pinned_specs() == ["openai:gpt-5.3", "kimi:kimi-k2.6"]


def test_switching_without_naming_a_model_still_leaves_the_shortlist_alone(home, monkeypatch):
    """The switcher's cross-provider click sends a provider and no model — that
    must not rewrite the list it just read from."""
    (home / "models.json").write_text(json.dumps({"pinned": ["kimi:kimi-k3", "gemini:gemini-3.5-flash"]}))
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    monkeypatch.setenv("KNOWME_PROVIDER", "kimi")
    monkeypatch.setenv("KNOWME_MODEL", "kimi-k3")
    _live_agent(monkeypatch)
    from knowme import integrations
    before = (home / "models.json").read_text(encoding="utf-8")

    assert integrations.apply_provider("gemini").ok
    assert os.getenv("KNOWME_MODEL") == "gemini-3.5-flash"   # the pinned default
    assert (home / "models.json").read_text(encoding="utf-8") == before


def test_pinned_are_grouped_by_provider_for_display(home):
    """A model added later (e.g. claude-fable-5) should list WITH its provider's
    other models, not stranded at the bottom — while each provider's default
    (first pinned) stays on top."""
    (home / "models.json").write_text(json.dumps({"pinned": [
        "anthropic:claude-opus-4-8", "openai:gpt-5.3-chat-latest",
        "anthropic:claude-fable-5", "openai:gpt-4.1-mini"]}))
    rows = [(r["provider"], r["model"], r["default"]) for r in d.settings_info()["pinned"]]
    assert rows == [
        ("anthropic", "claude-opus-4-8", True),      # default stays first
        ("anthropic", "claude-fable-5", False),      # grouped with anthropic, not stranded
        ("openai", "gpt-5.3-chat-latest", True),
        ("openai", "gpt-4.1-mini", False),
    ]


def test_no_pins_is_empty_not_error(home):
    info = d.settings_info()
    assert info["pinned"] == []
    assert catalog.default_model_for("anthropic") == ""


def test_default_pair_is_flagship_then_fast(home):
    """Each provider ships a flagship + fast default pair for the switcher."""
    from knowme.loop.models import PROVIDERS

    assert PROVIDERS["anthropic"].default_pair() == ["claude-opus-4-8", "claude-sonnet-5"]
    assert PROVIDERS["gemini"].default_pair() == ["gemini-3.1-pro-preview", "gemini-3.5-flash"]
    assert PROVIDERS["kimi"].default_pair() == ["kimi-k3", "kimi-k2.7-code-highspeed"]
    # a provider that never set flagship/fast falls back to model/small_model
    assert PROVIDERS["minimax"].default_pair() == ["MiniMax-M3", "MiniMax-M2"]


def test_defaults_apply_before_curation_and_only_for_keyed_providers(home, monkeypatch):
    """No models.json yet -> the switcher shows flagship+fast for providers that
    have a key set, flagship first (so it's the default). Providers without a
    key stay out (you can't use them)."""
    monkeypatch.setenv("MOONSHOT_API_KEY", "k")      # only kimi is keyed
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a")     # and anthropic

    info = d.settings_info()
    pairs = [(p["provider"], p["model"], p["default"]) for p in info["pinned"]]
    assert pairs == [
        ("anthropic", "claude-opus-4-8", True), ("anthropic", "claude-sonnet-5", False),
        ("kimi", "kimi-k3", True), ("kimi", "kimi-k2.7-code-highspeed", False),
    ]
    assert catalog.default_model_for("kimi") == "kimi-k3"        # flagship is the default
    assert catalog.default_model_for("gemini") == ""            # unkeyed -> no default


def test_pinning_snapshots_defaults_then_diverges(home, monkeypatch):
    """The first pin action persists the computed defaults + the change, so
    later edits don't keep resurrecting defaults."""
    monkeypatch.setenv("MOONSHOT_API_KEY", "k")      # only kimi keyed -> 2 defaults
    d.pin_action({"action": "unpin", "provider": "kimi", "model": "kimi-k2.7-code-highspeed"})
    assert (home / "models.json").exists()               # now materialized
    assert [p["model"] for p in d.settings_info()["pinned"]] == ["kimi-k3"]


def test_known_catalog_providers_can_list(home):
    """Guard against the 'only 2 models' bug: a provider lists models from an
    explicit catalog_url OR a {base_url}/models endpoint (openai-wire only).
    openai has no base_url by default, so it MUST set catalog_url — without it
    the picker fell back to just its 2 hardcoded defaults.

    glm is anthropic-wire with no verified public /models endpoint, so it
    intentionally shows its curated defaults until we wire and verify one."""
    from knowme.loop.models import PROVIDERS

    CAN_LIST = {"anthropic", "openai", "openrouter", "gemini", "deepseek", "minimax",
                "kimi", "xai", "opencode_zen", "opencode_go"}
    for name in CAN_LIST:
        prov = PROVIDERS[name]
        can_list = bool(prov.catalog_url) or (prov.kind == "openai" and bool(prov.base_url))
        assert can_list, f"{name} lost its catalog source (add catalog_url)"


def test_list_models_honors_provider_override(home, monkeypatch):
    """The add-row picks a provider first, so list_models(provider) must list
    THAT provider's catalog, not the active one. Cache-seeded to avoid network."""
    import time

    from knowme.loop.models import PROVIDERS

    monkeypatch.delenv("MOONSHOT_BASE_URL", raising=False)
    monkeypatch.delenv("KNOWME_BASE_URL", raising=False)
    url = PROVIDERS["kimi"].catalog_url
    # cache tuple is (ts, models, error) — None error means a real listing
    monkeypatch.setattr(catalog, "_models_cache", {url: (time.time(), [{"id": "kimi-k3"}], None)})
    out = catalog.list_models("kimi")
    assert out["provider"] == "kimi"
    assert out["listed"] is True
    assert [m["id"] for m in out["models"]] == ["kimi-k3"]
