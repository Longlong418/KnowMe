"""The model catalog — what you can actually run, and the shortlist you curated.

Two related jobs, both feeding the settings model picker:

1. **What exists.** `list_models()` asks a provider what it can serve. There is
   no single way to ask: some endpoints publish an explicit catalog URL (kimi
   chats on the Anthropic wire but lists on its OpenAI-compatible one), most
   OpenAI-compatible endpoints answer `GET {base_url}/models`, and some — the
   Anthropic wire among them — have no listing at all, so we fall back to that
   provider's own known defaults. Cached 5 minutes; failures are cached ~1
   minute WITH the reason, so an unreachable catalog can't stall the
   dashboard's 5-second poll and still tells you why.

2. **What you chose.** `.knowme/models.json` holds an ordered `provider:model`
   shortlist. The chat switcher shows exactly these — the built-in defaults are
   a starting point, never the menu. The first pinned model for a provider is
   that provider's default when you switch to it.

Writing the shortlist lives here (`save_pinned`); the pin/unpin HTTP action
lives in settings_api, because its reply is a whole settings payload. That
keeps the dependency pointing one way: settings_api -> catalog, never back.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from knowme.config import load_settings
from knowme.ops.pricing import remember_context, remember_price

_models_cache: dict[str, tuple[float, list]] = {}


def _known_default_ids(prov, out: dict, is_active: bool) -> list[dict]:
    """Best-effort model list when the live catalog is unreachable: the provider's
    flagship + fast + loop/gate defaults — so the showcase model (e.g. opus-4.8)
    is offered too, not just the two loop defaults — plus the active model when
    this is the active provider."""
    ids = [*(prov.default_pair() if prov else []),
           prov.model if prov else "", prov.small_model if prov else ""]
    if is_active:
        ids = [out.get("model"), out.get("small_model"), *ids]
    return [{"id": m} for m in dict.fromkeys(m for m in ids if m)]


def list_models(provider: str | None = None, *, use_cache: bool = True) -> dict:
    """Model ids available on a provider, for the settings model picker — the
    defaults are starting points, never the menu. Pass `provider` to list ANY
    provider's catalog (the "Your models" add-row picks a provider first);
    without it, the ACTIVE provider is used. Three sources: an explicit
    Provider.catalog_url (anthropic, kimi), GET {base_url}/models on
    OpenAI-compatible endpoints (OpenRouter, Gemini, any KNOWME_BASE_URL), or the
    two known defaults when no catalog exists. OpenRouter entries carry free /
    tool-support / context metadata so the picker can surface the $0
    tool-capable models. Cached 5 minutes."""
    from knowme.core import custom_providers
    from knowme.core.models import PROVIDERS

    s = load_settings()
    # An explicit provider overrides the active one (and its custom base_url:
    # KNOWME_BASE_URL only applies to the provider it was set for).
    name = provider or s.provider
    prov = PROVIDERS.get(name)
    base = ((s.base_url if name == s.provider else None)
            or (prov.configured_base_url() if prov else None))
    out = {
        "provider": name,
        "model": s.model or (prov.model if prov else ""),
        "small_model": s.small_model or (prov.small_model if prov else ""),
        "endpoint": base or name,
    }
    # Where can this provider's models be listed? An explicit catalog_url wins
    # (kimi chats on the anthropic wire but lists on its OpenAI-compatible API;
    # anthropic itself has GET /v1/models); otherwise openai-wire endpoints get
    # {base_url}/models; a USER-DEFINED anthropic-wire endpoint gets
    # {base_url}/v1/models — the same path the official one answers on, and the
    # only listing convention its wire has; otherwise fall back to the two known
    # defaults.
    catalog_url = prov.catalog_for(base) if prov is not None else None
    if catalog_url:
        url = catalog_url
    elif prov is not None and base and prov.kind == "openai":
        url = base.rstrip("/") + "/models"
    elif prov is not None and base and custom_providers.is_custom(name):
        url = base.rstrip("/") + "/v1/models"
    else:
        # No catalog endpoint: fall back to the provider's own known defaults
        # (flagship + fast + loop/gate), not just the active model.
        return {**out, "listed": False,
                "models": _known_default_ids(prov, out, name == s.provider)}

    # Use this provider's own key; s.api_key only holds the ACTIVE provider's.
    key = ((s.api_key if name == s.provider else "") or os.getenv(prov.key_env, "")).strip()
    models, error = _fetch_models(url, key, prov.key_env, use_cache=use_cache)
    if error:
        # still offer the provider's known defaults so the picker isn't empty
        return {**out, "listed": False,
                "models": models or _known_default_ids(prov, out, name == s.provider),
                "error": error}
    return {**out, "listed": True, "models": models}


def _fetch_models(url: str, key: str, key_label: str = "API key",
                  *, use_cache: bool = True) -> tuple[list[dict], str]:
    """Ask ONE url for its model list: (models, error). `error` is "" when the
    endpoint answered, and on failure `models` is empty with the reason second —
    the caller decides what to show instead of an empty list.

    Cached 5 minutes; a failure is cached ~1 minute WITH the reason, so an
    unreachable catalog can't stall the dashboard's 5-second poll for 10s and a
    cache hit still tells you why it is empty. `key_label` only names the
    variable in the bad-key message.
    """
    import time
    import urllib.request

    cached = _models_cache.get(url) if use_cache else None
    if cached and time.time() - cached[0] < 300:
        _ts, cmodels, cerr = cached          # cerr None on a real listing
        return cmodels, cerr or ""
    # HTTP headers must be latin-1; a key with a stray non-ASCII char (a smart
    # arrow/quote or a line-break from a bad paste) would otherwise crash the
    # whole listing with an opaque codec error and silently drop back to two
    # defaults. Catch it here with a message that actually says how to fix it.
    # Not cached: it is a fact about the input, not about the endpoint.
    try:
        key.encode("latin-1")
    except UnicodeEncodeError:
        return [], (f"{key_label} contains a non-ASCII character — re-paste the key "
                    f"(no spaces, line breaks, or arrows).")
    # send both auth styles — Bearer for OpenAI-compatible catalogs, x-api-key +
    # version for Anthropic's; each server reads the header it knows.
    # Set a browser-like User-Agent: some OpenAI-compatible proxies (e.g.
    # opencode.ai) block Python-urllib/3.x with a 403 / error code 1010.
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {key}",
        "x-api-key": key, "anthropic-version": "2023-06-01",
        "User-Agent": "Mozilla/5.0 (compatible; KnowMe)",
    })
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
    except Exception as exc:
        # Surface the server's actual reason (e.g. xAI's 403 "no credits"), not
        # just "HTTP Error 403" — an HTTPError carries the body on .read().
        msg = str(exc)
        try:
            msg = f"{msg} — {exc.read().decode()[:160]}"
        except Exception:
            pass
        _models_cache[url] = (time.time() - 240, [], msg)
        return [], msg
    models = []
    for m in data.get("data", []):
        mid = m.get("id", "")
        if not mid:
            continue
        pricing = m.get("pricing") or {}
        params = m.get("supported_parameters")
        entry = {
            "id": mid,
            "free": mid.endswith(":free") or pricing.get("prompt") == "0",
            # None means the endpoint doesn't say (only OpenRouter reports this)
            "tools": ("tools" in params) if params is not None else None,
            # reasoning models spend tokens thinking out loud, which breaks the
            # gate's tiny budget: the UI steers them away from the gate slot
            "reasoning": ("reasoning" in params) if params is not None else None,
            "context": m.get("context_length"),
        }
        try:
            # OpenRouter prices are $/token strings; keep $/M for display + cost
            pin, pout = float(pricing["prompt"]) * 1e6, float(pricing["completion"]) * 1e6
            remember_price(mid, pin, pout)
            entry["price_in"], entry["price_out"] = round(pin, 3), round(pout, 3)
        except (KeyError, TypeError, ValueError):
            pass
        # Where the endpoint publishes a context window, hand it to the same
        # kind of process-lifetime cache the price uses — micro_compact reads it
        # through pricing.context_for() and must not fetch anything mid-turn.
        remember_context(mid, entry["context"])
        models.append(entry)
    models.sort(key=lambda x: (not x["free"], x["tools"] is False, x["id"]))
    _models_cache[url] = (time.time(), models, None)   # None error = a real listing
    return models, ""


def models_at(base_url: str, kind: str = "openai", key: str = "") -> dict:
    """The model list at an endpoint that is NOT saved yet — the add-provider
    dialog's 「获取模型列表」 button, so the model field offers what the endpoint
    actually serves instead of asking you to type an id from memory.

    Same fetch and same parsing as `list_models` (`_fetch_models`), with one
    deliberate difference: there is no provider to fall back to. Nothing is known
    about an unsaved endpoint, so a listing that fails reports its reason rather
    than a made-up default list — the reason IS the answer to "why is this empty".
    """
    base = (base_url or "").strip().rstrip("/")
    if not base:
        return {"ok": False, "error": "先填 Base URL", "models": []}
    # The same listing convention list_models resolves to, minus the providers
    # that carry an explicit catalog_url (an unsaved endpoint has none).
    url = base + ("/v1/models" if kind == "anthropic" else "/models")
    models, error = _fetch_models(url, (key or "").strip(), "API 密钥")
    if error:
        return {"ok": False, "error": error, "models": [], "url": url}
    return {"ok": True, "models": models, "url": url}


def _models_json() -> Path:
    return load_settings().home / "models.json"


def default_pinned_specs() -> list[str]:
    """Starter shortlist before the user has curated their own: flagship + fast
    for every provider that has a key set (so the switcher only shows models you
    can actually use). Flagship comes first, so it's that provider's default."""
    from knowme.core.models import PROVIDERS

    specs = []
    for name, prov in PROVIDERS.items():
        if os.getenv(prov.key_env):
            specs += [f"{name}:{m}" for m in prov.default_pair()]
    return specs


def pinned_specs() -> list[str]:
    """The user's curated 'provider:model' shortlist (ordered), from
    .knowme/models.json. The chat switcher shows exactly these. Before they've
    saved anything, fall back to the flagship+fast defaults."""
    p = _models_json()
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8")).get("pinned", [])
        except (json.JSONDecodeError, OSError):
            pass
    return default_pinned_specs()


def default_model_for(provider: str) -> str:
    """A provider's default model = the FIRST one the user pinned for it.
    Empty string means 'use the provider's built-in default'."""
    for spec in pinned_specs():
        p, _, m = spec.partition(":")
        if p == provider and m:
            return m
    return ""


def save_pinned(specs: list[str]) -> None:
    """Persist the curated shortlist, in order. The ONLY writer of models.json —
    keep it that way so the file has one shape and one owner."""
    path = _models_json()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"pinned": specs}, indent=1))


def pin_default(provider: str, model: str) -> None:
    """Make `provider:model` the model that provider uses.

    It takes over the row the provider already has in the shortlist (so the menu
    keeps its provider order, and that provider keeps exactly one "this is the
    model" row); a model that is already pinned somewhere in that provider's rows
    is promoted to the top of them instead of replacing anything, and a provider
    with no row at all gets one appended.

    Two callers mean the same thing by this — the Models page's 设为默认 button and
    saving a model for a provider — and both must land in the same row, because
    `default_model_for()` (read when you switch TO that provider) reads exactly
    it. Updating only KNOWME_MODEL on a save is how the header chip, the 默认 tag
    and the model you get on the next switch drifted apart."""
    spec = f"{provider}:{model}"
    specs = pinned_specs()
    others = [s for s in specs if s != spec]
    head = next((i for i, s in enumerate(others) if s.partition(":")[0] == provider), None)
    if head is None:                            # no row for this provider yet
        pinned = [*others, spec]
    elif spec in specs:                         # already one of its rows: promote, drop nothing
        pinned = [*others[:head], spec, *others[head:]]
    else:                                       # take over its row — the model it had is no longer it
        pinned = [*others[:head], spec, *others[head + 1:]]
    if pinned != specs:
        save_pinned(pinned)
