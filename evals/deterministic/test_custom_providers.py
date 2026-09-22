"""Providers the user adds themselves — the Models page's ＋ card.

A custom provider is not a new mechanism: it is one more entry in the PROVIDERS
table, so these tests are about the three places that could quietly go wrong —
the id becoming an environment variable name, the key ending up in the wrong
file, and REMOVAL leaving the loop pointing at a provider that no longer exists.
"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path

import pytest

from knowme import integrations
from knowme.config import load_settings
from knowme.core import custom_providers
from knowme.core.models import PROVIDERS
from knowme.ops import catalog
from knowme.ops.settings_api import custom_provider_action, settings_info

MY_LAB = {"id": "my_lab", "label": "我的实验室", "kind": "openai",
          "base_url": "https://api.mylab.dev/v1", "model": "my-model-large",
          "small_model": "my-model-small"}


@pytest.fixture
def home(monkeypatch, tmp_path):
    """A throwaway .env + .knowme and no network — the isolation the other
    provider tests use. Returns the .knowme directory the spec file lands in,
    created the same way a real run creates it."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("KNOWME_HOME", str(tmp_path / ".knowme"))
    for name in ("KNOWME_PROVIDER", "KNOWME_MODEL", "KNOWME_SMALL_MODEL", "KNOWME_BASE_URL",
                 "KNOWME_DISABLED_PROVIDERS", "KNOWME_API_KEY",
                 custom_providers.key_env("my_lab"), custom_providers.base_url_env("my_lab")):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(integrations, "_provider_probe", lambda values: None)

    from knowme.ops import browser_agent

    monkeypatch.setattr(browser_agent, "rebuild", lambda: None)
    monkeypatch.setattr(browser_agent, "current", lambda: None)
    monkeypatch.setattr(browser_agent, "current_agents", lambda: {})
    return load_settings().ensure_home()


def _add(home: Path, **overrides) -> dict:
    return custom_provider_action(
        {"action": "add_custom", "key": "sk-mylab", **MY_LAB, **overrides})


def _fresh_process_reads(home: Path) -> None:
    """Read the spec file the way a new process does.

    load() is a no-op for a home it has already loaded — that is what keeps
    load_settings() cheap on every request, and it also means an edit made
    behind the running dashboard's back is picked up on the next start, not
    immediately.
    """
    custom_providers.unload()
    custom_providers.load(home)


def test_adding_a_provider_table_entry_derives_everything_from_the_spec(home):
    custom_providers.register(home, MY_LAB)

    provider = PROVIDERS["my_lab"]
    assert provider.kind == "openai"
    assert provider.key_env == "KNOWME_CUSTOM_MY_LAB_API_KEY"
    assert provider.base_url_env == "KNOWME_CUSTOM_MY_LAB_BASE_URL"
    assert provider.configured_base_url() == "https://api.mylab.dev/v1"
    assert (provider.model, provider.small_model) == ("my-model-large", "my-model-small")
    assert custom_providers.is_custom("my_lab")
    assert not custom_providers.is_custom("anthropic")


def test_a_provider_with_no_small_model_falls_back_to_the_main_one(home):
    """A blank small model would otherwise send model="" on the gate call."""
    custom_providers.register(home, {**MY_LAB, "small_model": ""})

    assert PROVIDERS["my_lab"].small_model == "my-model-large"


def test_the_models_page_shows_it_as_editable_with_a_text_base_url(home):
    """A custom endpoint has no regional list to choose from, so its Base URL
    must render as a plain field — and models.js finds that field by name, which
    is also how the edit modal keeps it changeable."""
    custom_providers.register(home, MY_LAB)

    view = next(v for v in integrations.list_providers() if v.key == "my_lab")
    assert (view.name, view.custom) == ("我的实验室", True)
    fields = {field.name: field for field in view.fields}
    assert fields["KNOWME_CUSTOM_MY_LAB_API_KEY"].secret
    base = fields["KNOWME_CUSTOM_MY_LAB_BASE_URL"]
    assert base.kind is integrations.FieldKind.TEXT
    assert base.value == "https://api.mylab.dev/v1"

    built_in = next(v for v in integrations.list_providers() if v.key == "minimax")
    assert built_in.custom is False


def test_adding_persists_the_shape_and_loads_it_back(home):
    """The file lives beside every other KnowMe runtime file, in the home."""
    custom_providers.register(home, MY_LAB)

    assert json.loads((home / "providers.json").read_text(encoding="utf-8")) == {"custom": [MY_LAB]}

    custom_providers.unload()
    assert "my_lab" not in PROVIDERS

    custom_providers.load(home)
    assert PROVIDERS["my_lab"].label == "我的实验室"
    assert custom_providers.is_custom("my_lab")


def test_loading_a_different_home_takes_the_providers_back_out(home, tmp_path):
    """Otherwise every CLI run from another directory would stack another copy."""
    custom_providers.register(home, MY_LAB)

    custom_providers.load(tmp_path / "somewhere-else")

    assert "my_lab" not in PROVIDERS
    assert not custom_providers.is_custom("my_lab")


def test_loading_and_registering_twice_do_not_stack_duplicates(home):
    """load_settings() runs on every request, and the submit button can be
    pressed twice — neither may produce a second entry."""
    custom_providers.register(home, MY_LAB)
    custom_providers.register(home, {**MY_LAB, "label": "改名了"})
    custom_providers.load(home)
    custom_providers.load(home)

    assert custom_providers.specs(home) == [{**MY_LAB, "label": "改名了"}]
    assert PROVIDERS["my_lab"].label == "改名了"


def test_the_key_goes_to_env_and_never_to_the_spec_file(home):
    """Secrets have one home. providers.json is editable and shareable; .env is
    the file that already holds every other provider's key."""
    result = _add(home)

    assert result["ok"] is True
    assert os.environ["KNOWME_CUSTOM_MY_LAB_API_KEY"] == "sk-mylab"
    assert (home.parent / ".env").read_text(encoding="utf-8").count("sk-mylab") == 1
    assert "sk-mylab" not in (home / "providers.json").read_text(encoding="utf-8")


def test_adding_saves_the_key_endpoint_and_models_and_switches_to_it(home):
    result = _add(home)

    assert (result["provider"], result["model"], result["small_model"]) == (
        "my_lab", "my-model-large", "my-model-small")
    assert os.environ["KNOWME_PROVIDER"] == "my_lab"
    assert os.environ["KNOWME_MODEL"] == "my-model-large"
    assert os.environ["KNOWME_CUSTOM_MY_LAB_BASE_URL"] == "https://api.mylab.dev/v1"
    assert "KNOWME_CUSTOM_MY_LAB_API_KEY='sk-mylab'" in (
        home.parent / ".env").read_text(encoding="utf-8")


def test_adding_a_provider_puts_its_model_in_the_chat_switcher(home):
    """The agent page's model menu shows the curated shortlist and NOTHING else,
    so a provider that was never pinned is current and still invisible there —
    which read as "I added it and the agent page doesn't show it".

    It only looks fine on a fresh install, where pinned_specs() falls back to
    default_pinned_specs() (derived from whichever keys are set) and picks the
    new provider up by accident. Anyone who has ever pinned a model has a
    models.json, and for them the fallback no longer runs — so this asserts on a
    shortlist that already exists.
    """
    catalog.save_pinned(["deepseek:deepseek-v4-pro"])

    _add(home)

    assert "my_lab:my-model-large" in catalog.pinned_specs()
    assert [p["model"] for p in settings_info()["pinned"] if p["provider"] == "my_lab"] == [
        "my-model-large"]


def test_adding_the_same_provider_twice_does_not_pin_it_twice(home):
    _add(home)
    _add(home, label="改名了")

    assert catalog.pinned_specs().count("my_lab:my-model-large") == 1


def test_adding_without_switching_leaves_the_current_provider_alone(home):
    _add(home, activate=False)

    assert os.environ.get("KNOWME_PROVIDER", "") == ""
    assert settings_info()["provider"] == "anthropic"


@pytest.mark.parametrize(("kind", "expected"), [("openai", "/models"), ("anthropic", "/v1/models")])
def test_the_catalog_asks_the_custom_endpoint(home, monkeypatch, kind, expected):
    captured = {}

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    def fake_urlopen(req, timeout=10):
        captured["url"] = req.full_url
        return Response(json.dumps({"data": [{"id": "my-model-large"}]}).encode())

    custom_providers.register(home, {**MY_LAB, "kind": kind, "base_url": "https://api.mylab.dev"})
    monkeypatch.setenv("KNOWME_PROVIDER", "my_lab")
    monkeypatch.setenv(custom_providers.key_env("my_lab"), "sk-mylab")
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    catalog._models_cache.clear()

    result = catalog.list_models("my_lab", use_cache=False)

    assert result["listed"] is True
    # The OpenAI wire lists beside the chat endpoint; the Anthropic wire has one
    # listing convention and it is /v1/models.
    assert captured["url"] == "https://api.mylab.dev" + expected


def test_removing_hands_the_selection_back_to_a_builtin_before_it_disappears(home):
    """get_client reads PROVIDERS[settings.provider] directly, so a selection
    left pointing at a removed provider raises KeyError on the next message
    instead of falling back."""
    _add(home)
    catalog.save_pinned(["my_lab:my-model-large", "anthropic:claude-opus-4-8"])

    result = custom_provider_action({"action": "remove_custom", "provider": "my_lab"})

    assert result["ok"] is True
    assert result["provider"] == "anthropic"
    assert result["model"] == PROVIDERS["anthropic"].model
    assert os.environ["KNOWME_MODEL"] == ""
    assert os.environ["KNOWME_SMALL_MODEL"] == ""


def test_removing_leaves_nothing_behind(home):
    _add(home)
    integrations.apply_provider_disabled("my_lab", disabled=True)
    # A sentinel that must survive, and a pinned model of the provider being
    # removed: a shortlist entry for a provider that no longer exists would sit
    # in the chat switcher forever.
    catalog.save_pinned(["my_lab:my-model-large", "anthropic:claude-opus-4-8"])

    custom_provider_action({"action": "remove_custom", "provider": "my_lab"})

    assert "my_lab" not in PROVIDERS
    assert not custom_providers.is_custom("my_lab")
    assert json.loads((home / "providers.json").read_text(encoding="utf-8")) == {"custom": []}
    assert os.environ.get("KNOWME_CUSTOM_MY_LAB_API_KEY") is None
    assert os.environ.get("KNOWME_CUSTOM_MY_LAB_BASE_URL") is None
    assert "KNOWME_CUSTOM_MY_LAB" not in (home.parent / ".env").read_text(encoding="utf-8")
    # A stale id here would make the next provider to take it be born disabled.
    assert os.environ.get("KNOWME_DISABLED_PROVIDERS", "") == ""
    assert catalog.pinned_specs() == ["anthropic:claude-opus-4-8"]


def test_removing_one_provider_leaves_the_others(home):
    _add(home)
    custom_providers.register(home, {**MY_LAB, "id": "other_lab", "label": "另一个"})

    custom_provider_action({"action": "remove_custom", "provider": "my_lab"})

    assert "other_lab" in PROVIDERS


def test_a_builtin_provider_cannot_be_removed_or_overwritten(home):
    assert custom_provider_action({"action": "remove_custom", "provider": "anthropic"}) == {
        "ok": False, "error": "只有自定义服务商可以删除"}
    assert "anthropic" in PROVIDERS

    result = custom_provider_action({**MY_LAB, "action": "add_custom", "id": "anthropic"})
    assert result["ok"] is False and "已有同名服务商" in result["error"]
    assert PROVIDERS["anthropic"].key_env == "ANTHROPIC_API_KEY"


@pytest.mark.parametrize("bad_id", ["My-Lab", "9lab", "a", "lab-x", "lab x", ""])
def test_an_id_that_cannot_be_an_env_variable_name_is_refused(home, bad_id):
    result = custom_provider_action({**MY_LAB, "action": "add_custom", "id": bad_id})

    assert result["ok"] is False
    assert "ID 只能" in result["error"]
    assert not custom_providers.specs(home)


def test_a_failed_probe_offers_the_force_retry_instead_of_a_dead_end(home, monkeypatch):
    """An endpoint that will not list its models must still be saveable on
    purpose — the same 仍然保存 escape hatch the Connections page has."""
    def failing_probe(values):
        raise ValueError("模型列表不可用")

    monkeypatch.setattr(integrations, "_provider_probe", failing_probe)

    failed = _add(home)
    assert failed["ok"] is False and failed["can_force"] is True
    assert "模型列表不可用" in failed["error"]
    assert os.environ.get("KNOWME_CUSTOM_MY_LAB_API_KEY") is None

    # The retry re-sends the same action: the id is already registered, which
    # must not read as a collision.
    saved = _add(home, force=True)
    assert saved["ok"] is True
    assert os.environ["KNOWME_CUSTOM_MY_LAB_API_KEY"] == "sk-mylab"


def test_a_damaged_spec_file_reads_as_no_providers(home):
    """It is a convenience file, not a database — it must not take the
    dashboard down."""
    (home / "providers.json").write_text("{ not json", encoding="utf-8")

    assert custom_providers.specs(home) == []
    _fresh_process_reads(home)
    assert not custom_providers.is_custom("my_lab")


def test_a_hand_edited_spec_with_a_bad_id_is_skipped(home):
    (home / "providers.json").write_text(
        json.dumps({"custom": [{"id": "Bad-Id", "kind": "openai", "base_url": "x", "model": "m"},
                               MY_LAB]}), encoding="utf-8")

    _fresh_process_reads(home)

    assert "Bad-Id" not in PROVIDERS
    assert "my_lab" in PROVIDERS


def test_a_spec_missing_an_optional_key_still_loads(home):
    """load() runs from load_settings(), so a KeyError here is not a broken card
    — it is a process that cannot start. Missing optional keys fall back."""
    (home / "providers.json").write_text(
        json.dumps({"custom": [{"id": "bare", "kind": "openai",
                                "base_url": "https://api.bare.dev", "model": "only-one"}]}),
        encoding="utf-8")

    _fresh_process_reads(home)

    assert PROVIDERS["bare"].label == "bare"
    assert PROVIDERS["bare"].small_model == "only-one"


def test_the_add_card_is_wired_to_the_backend():
    """No JS test runner (no build step, on purpose), so the form's contract with
    the route is pinned here: the action names, the field ids submitAddProvider
    reads, and the marker that puts 删除 on custom cards only."""
    source = (Path(__file__).resolve().parents[2] / "knowme" / "ops" / "static"
              / "js" / "models.js").read_text(encoding="utf-8")

    assert 'action: "add_custom"' in source
    assert 'action: "remove_custom"' in source
    for field in ("ap-id", "ap-label", "ap-kind", "ap-base-url", "ap-key",
                  "ap-model", "ap-small-model", "ap-activate"):
        assert f'id="{field}"' in source, f"the add form lost {field}"
    assert "${p.custom ?" in source, "删除 would show on built-in cards too"
