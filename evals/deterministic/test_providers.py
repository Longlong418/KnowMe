"""OFFLINE provider-table checks: every PROVIDERS entry must build the right
client, fill its default model ids, and be covered by the dashboard's pricing
and model-listing fallbacks. No network, no real keys (fakes via monkeypatch).

Born from a live regression hunt: adding a provider touches shared paths
(get_client, HAS_KEY, /api/models, PRICING), and nothing offline proved the
other five still worked. Now something does.
"""

from __future__ import annotations

import anthropic
import pytest
from knowme.loop.models import PROVIDERS, OpenAICompatClient, get_client

from knowme.config import Settings


@pytest.fixture(autouse=True)
def fake_keys(monkeypatch):
    for provider in PROVIDERS.values():
        monkeypatch.setenv(provider.key_env, "fake-key-for-tests")
    # a stray custom-endpoint override must not leak into these checks
    monkeypatch.delenv("KNOWME_API_KEY", raising=False)
    monkeypatch.delenv("KNOWME_BASE_URL", raising=False)


@pytest.mark.parametrize("name", list(PROVIDERS))
def test_get_client_builds_the_right_wire(name):
    provider = PROVIDERS[name]
    settings = Settings(provider=name, model="", small_model="", api_key="", base_url=None)
    client = get_client(settings)
    expected = anthropic.Anthropic if provider.kind == "anthropic" else OpenAICompatClient
    assert isinstance(client, expected)
    # defaults must be filled in so the loop never sends model=""
    assert settings.model == provider.model
    assert settings.small_model == provider.small_model


@pytest.mark.parametrize("name", list(PROVIDERS))
def test_missing_key_exits_with_the_key_name(name, monkeypatch):
    monkeypatch.delenv(PROVIDERS[name].key_env, raising=False)
    settings = Settings(provider=name, model="", small_model="", api_key="", base_url=None)
    with pytest.raises(SystemExit, match=PROVIDERS[name].key_env):
        get_client(settings)


def test_unknown_provider_names_the_choices():
    settings = Settings(provider="not-a-provider", model="", small_model="",
                        api_key="", base_url=None)
    with pytest.raises(SystemExit, match="openrouter"):
        get_client(settings)


@pytest.mark.parametrize("name", list(PROVIDERS))
def test_dashboard_pricing_covers_every_provider(name):
    from knowme.ops.pricing import PRICING

    assert name in PRICING


@pytest.mark.parametrize("name", [n for n, p in PROVIDERS.items()
                                  if p.catalog_url is None
                                  and (p.kind == "anthropic" or not p.base_url)])
def test_model_listing_falls_back_without_a_catalog(name, monkeypatch):
    """Providers with no listable catalog still give the picker their defaults
    (and never make a network call to get them)."""
    from knowme.ops import catalog

    monkeypatch.setenv("KNOWME_PROVIDER", name)
    monkeypatch.delenv("KNOWME_MODEL", raising=False)
    monkeypatch.delenv("KNOWME_SMALL_MODEL", raising=False)
    result = catalog.list_models()
    assert result["listed"] is False
    ids = [m["id"] for m in result["models"]]
    assert PROVIDERS[name].model in ids
    # the flagship (showcase) model is offered too, not just the loop default
    if PROVIDERS[name].flagship:
        assert PROVIDERS[name].flagship in ids


def test_bad_key_gives_a_fixable_error_not_a_codec_crash(monkeypatch):
    """A key with a stray non-latin-1 char (a mis-pasted arrow/smart-quote) must
    NOT crash the whole catalog with an opaque codec error — it should return a
    fixable message AND still offer the flagship so opus-4.8/fable-5 aren't lost.
    (Regression: a cloned repo whose ANTHROPIC_API_KEY had a '→' dropped the
    picker to two defaults with a 'latin-1 codec' error.)"""
    from knowme.ops import catalog

    monkeypatch.setenv("KNOWME_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-" + "x" * 100 + "→bad")
    monkeypatch.delenv("KNOWME_MODEL", raising=False)
    catalog._models_cache.clear()
    result = catalog.list_models("anthropic")
    assert result["listed"] is False
    assert "ANTHROPIC_API_KEY" in result["error"] and "non-ASCII" in result["error"]
    assert "claude-opus-4-8" in [m["id"] for m in result["models"]]


def test_catalog_url_is_used_with_both_auth_styles(monkeypatch):
    """kimi chats on the anthropic wire but LISTS models on its OpenAI-compat
    endpoint — catalog_url must win, carrying both auth header styles, so the
    picker offers the real menu instead of two hardcoded defaults."""
    import io
    import json
    import urllib.request

    from knowme.ops import catalog

    captured = {}

    def fake_urlopen(req, timeout=10):
        captured["url"] = req.full_url
        captured["headers"] = {k.lower(): v for k, v in req.header_items()}
        body = io.BytesIO(json.dumps(
            {"data": [{"id": "kimi-k3"}, {"id": "kimi-k2.7"}, {"id": "kimi-k1.5"}]}
        ).encode())
        body.__enter__ = lambda *a: body
        body.__exit__ = lambda *a: None
        return body

    monkeypatch.setenv("KNOWME_PROVIDER", "kimi")
    monkeypatch.setenv("MOONSHOT_API_KEY", "fake-key-for-tests")
    monkeypatch.delenv("MOONSHOT_BASE_URL", raising=False)
    monkeypatch.delenv("KNOWME_BASE_URL", raising=False)
    monkeypatch.delenv("KNOWME_MODEL", raising=False)
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    catalog._models_cache.clear()

    result = catalog.list_models()
    assert captured["url"] == PROVIDERS["kimi"].catalog_url
    assert captured["headers"]["authorization"] == "Bearer fake-key-for-tests"
    assert captured["headers"]["x-api-key"] == "fake-key-for-tests"
    assert result["listed"] is True
    assert "kimi-k3" in [m["id"] for m in result["models"]]
    catalog._models_cache.clear()


def test_price_for_layers_model_over_provider():
    """Receipts correctness: a kimi-k3 run must be priced at K3's $3/$15, not
    the kimi provider's K2.7 rate — and unknown models still fall back to the
    provider estimate. (Live-catalog and :free paths are covered above.)"""
    from knowme.ops.pricing import MODEL_PRICING, PRICING, price_for

    assert price_for("kimi", "kimi-k3") == MODEL_PRICING["kimi-k3"] == (3.0, 15.0)
    assert price_for("kimi", "kimi-k2.7") == (0.95, 4.0)
    assert price_for("kimi", "some-future-model") == PRICING["kimi"]
    assert price_for("openrouter", "whatever:free") == (0.0, 0.0)

    # Regression: within a provider, models diverge hugely — fable-5 is priced at
    # $10/$50, ~2x opus's $5/$25. A provider-level fallback once made fable-5 look
    # CHEAPER than opus on the scoreboard; each must carry its own per-model rate.
    assert price_for("anthropic", "claude-fable-5") == (10.0, 50.0)
    assert price_for("anthropic", "claude-opus-4-8") == (5.0, 25.0)
    fable_in, fable_out = price_for("anthropic", "claude-fable-5")
    opus_in, opus_out = price_for("anthropic", "claude-opus-4-8")
    assert fable_in > opus_in and fable_out > opus_out   # fable is never cheaper


def test_every_priced_model_has_a_knowledge_cutoff():
    """Arena honesty: the Compare arena discloses each model's knowledge cutoff
    so stale world knowledge isn't misread as low capability (gemini-3.1-pro
    confidently denies 2026 models exist — its cutoff is 2025-01). Every model
    in MODEL_PRICING must have a MODEL_CUTOFF entry. None is a valid value
    (vendor hasn't published a cutoff; the UI shows a dash) — a MISSING key
    means someone added a model without deciding, which is what this catches."""
    import re

    from knowme.ops.pricing import MODEL_CUTOFF, MODEL_PRICING, cutoff_for

    missing = set(MODEL_PRICING) - set(MODEL_CUTOFF)
    assert not missing, f"models priced but missing a MODEL_CUTOFF entry: {sorted(missing)}"

    for model, cutoff in MODEL_CUTOFF.items():
        if cutoff is not None:
            assert re.fullmatch(r"20\d\d-(0[1-9]|1[0-2])", cutoff), \
                f"{model}: cutoff {cutoff!r} is not YYYY-MM"

    # The motivating case, plus the unknown-model path (no guessing).
    assert cutoff_for("gemini-3.1-pro-preview") == "2025-01"
    assert cutoff_for("some-future-model") is None


# --- a model name belongs to the provider it was configured for --------------
# Found live: `.venv/bin/python examples/tiny_memory_agent.py` under
# KNOWME_PROVIDER=xai printed "gate failed open (BadRequestError)". KNOWME_SMALL_MODEL
# is global, so anthropic's gate model was sent to xAI, which 400s. The retrieval
# gate fails open on error BY DESIGN, so this did not surface as a failure — it
# surfaced as "retrieve", on every turn, for every non-anthropic model in the
# arena. These pin the rule that stops it.

def test_env_model_names_do_not_leak_into_another_provider(monkeypatch):
    from knowme.loop.models import get_client

    from knowme.config import Settings

    monkeypatch.setenv("KNOWME_PROVIDER", "anthropic")
    monkeypatch.setenv("KNOWME_MODEL", "claude-fable-5")
    monkeypatch.setenv("KNOWME_SMALL_MODEL", "claude-haiku-4-5-20251001")
    monkeypatch.setenv("XAI_API_KEY", "test-key")

    settings = Settings(provider="xai")
    get_client(settings)

    assert settings.model == "grok-4", "anthropic's model was sent to xAI"
    assert settings.small_model == "grok-4-fast", (
        "anthropic's gate model was sent to xAI — the gate 400s and fails open, "
        "so this bug reports itself as a healthy 'retrieve' forever"
    )


def test_an_explicit_model_survives_the_provider_switch(monkeypatch):
    """The rule drops INHERITED env values, not deliberate ones. Without this
    the fix would quietly override the arena's own model picker."""
    from knowme.loop.models import get_client

    from knowme.config import Settings

    monkeypatch.setenv("KNOWME_PROVIDER", "anthropic")
    monkeypatch.setenv("KNOWME_MODEL", "claude-fable-5")
    monkeypatch.setenv("XAI_API_KEY", "test-key")

    settings = Settings(provider="xai", model="grok-4.3")
    get_client(settings)

    assert settings.model == "grok-4.3"


def test_the_env_still_wins_for_its_own_provider(monkeypatch):
    """The whole point of KNOWME_MODEL is to override the default — the fix must
    not break the ordinary single-provider case."""
    from knowme.loop.models import get_client

    from knowme.config import Settings

    monkeypatch.setenv("KNOWME_PROVIDER", "anthropic")
    monkeypatch.setenv("KNOWME_MODEL", "claude-fable-5")
    monkeypatch.setenv("KNOWME_SMALL_MODEL", "claude-haiku-4-5-20251001")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    settings = Settings(provider="anthropic")
    get_client(settings)

    assert settings.model == "claude-fable-5"
    assert settings.small_model == "claude-haiku-4-5-20251001"


def test_the_env_still_wins_for_its_own_deepseek_provider(monkeypatch):
    """The same ordinary case as above, but for a family an AGGREGATOR resells.

    opencode_zen/opencode_go ship `deepseek-v4-flash*`, so `_families` declares
    the family "deepseek" for them too — and sitting later in PROVIDERS they
    OVERWROTE deepseek's own claim to it (the owner dict comprehension keeps the
    last writer, not the true owner). `_belongs_elsewhere("deepseek-flash",
    "deepseek")` then answered True for deepseek's OWN model, so get_client
    dropped the user's KNOWME_MODEL and fell back to the provider default.

    Silent by construction: the override in .env simply had no effect, and every
    cost number afterwards priced a model that wasn't running. Found 2026-09-19
    on a `KNOWME_PROVIDER=deepseek` + `KNOWME_MODEL=deepseek-flash` .env.
    """
    from knowme.loop.models import get_client

    from knowme.config import Settings

    monkeypatch.setenv("KNOWME_PROVIDER", "deepseek")
    monkeypatch.setenv("KNOWME_MODEL", "deepseek-flash")
    monkeypatch.setenv("KNOWME_SMALL_MODEL", "deepseek-flash")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")

    settings = Settings(provider="deepseek")
    get_client(settings)

    assert settings.model == "deepseek-flash", (
        "the env override for deepseek was dropped — an aggregator was mistaken "
        "for the owner of deepseek's own model family"
    )
    assert settings.small_model == "deepseek-flash"


def test_an_aggregator_may_still_use_the_family_it_resells(monkeypatch):
    """The other half of the same rule. Guarding against the naive fix — simply
    dropping aggregators from the owner map — which would turn around and drop
    opencode's own default model, a deepseek id."""
    from knowme.loop.models import get_client

    from knowme.config import Settings

    monkeypatch.setenv("KNOWME_PROVIDER", "opencode_go")
    monkeypatch.setenv("KNOWME_MODEL", "deepseek-v4-flash")
    monkeypatch.setenv("OPENCODE_GO_API_KEY", "test-key")

    settings = Settings(provider="opencode_go")
    get_client(settings)

    assert settings.model == "deepseek-v4-flash"


def test_a_foreign_model_is_still_dropped_for_an_aggregator(monkeypatch):
    """And the short-circuit opens no hole: opencode resells deepseek ids, not
    anthropic ones, so the leak this rule exists to stop must still be stopped."""
    from knowme.loop.models import get_client

    from knowme.config import Settings

    monkeypatch.setenv("KNOWME_PROVIDER", "opencode_go")
    monkeypatch.setenv("KNOWME_SMALL_MODEL", "claude-haiku-4-5-20251001")
    monkeypatch.setenv("OPENCODE_GO_API_KEY", "test-key")

    settings = Settings(provider="opencode_go")
    get_client(settings)

    assert settings.small_model == "deepseek-v4-flash"


def test_a_foreign_gate_model_is_dropped_even_when_the_env_names_the_provider(monkeypatch):
    """The case that was actually live in Sean's .env: KNOWME_PROVIDER=xai with
    KNOWME_SMALL_MODEL still holding anthropic's gate model. Scoping by "did the
    caller switch provider" missed this one — the env agreed with itself and was
    still wrong."""
    from knowme.loop.models import get_client

    from knowme.config import Settings

    monkeypatch.setenv("KNOWME_PROVIDER", "xai")
    monkeypatch.setenv("KNOWME_SMALL_MODEL", "claude-haiku-4-5-20251001")
    monkeypatch.setenv("XAI_API_KEY", "test-key")

    settings = Settings(provider="xai")
    get_client(settings)

    assert settings.small_model == "grok-4-fast"


def test_an_unfamiliar_model_name_is_left_alone(monkeypatch):
    """The check asks "is this positively someone else's?", not "does it look
    like ours?". The second question would silently downgrade any id we don't
    recognise — a preview name, a model released after this code was written."""
    from knowme.loop.models import get_client

    from knowme.config import Settings

    monkeypatch.setenv("KNOWME_PROVIDER", "kimi")
    monkeypatch.setenv("KNOWME_SMALL_MODEL", "moonshot-v1-8k")
    monkeypatch.setenv("MOONSHOT_API_KEY", "test-key")

    settings = Settings(provider="kimi")
    get_client(settings)

    assert settings.small_model == "moonshot-v1-8k", "a deliberate choice was overridden"
