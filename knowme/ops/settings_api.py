"""The small Settings surface left after Connections owns integrations.

Provider credentials, models, memory backends, search and gateways are managed
by :mod:`knowme.integrations`. This module retains the Experimental and
Graph-workflows toggles and the model pin action; catalog remains the sole
owner of pin persistence.
"""

from __future__ import annotations

import shutil

from knowme import integrations
from knowme.config import load_settings
from knowme.core import custom_providers
from knowme.core.models import PROVIDERS, role_runs_on
from knowme.ops import catalog

# The two side jobs that may each run on their own provider and model.
# NOT triage: that one is not a role here, it keeps using KNOWME_SMALL_MODEL
# (it is off unless KNOWME_GRAPH_WORKFLOWS is on).
ROLES = ("gate", "summary")


def custom_provider_action(payload: dict) -> dict:
    """Add, remove or pre-flight a user-defined provider — the Models page's ＋ card.

    Adding registers the spec and then hands off to the SAME apply_provider the
    edit modal uses, so a key typed into the add form is probed and written
    exactly like a key typed anywhere else, force-retry included.

    Removing runs in an order that matters: integrations.remove_custom_provider
    needs the provider still in PROVIDERS (that is where its two .env variable
    names come from) and has to redirect the active selection before the entry
    disappears. Only then is the spec forgotten.

    probe_models is the odd one out: it answers a question ("what does this
    endpoint serve?") about a provider that does not exist yet, so it touches
    neither providers.json nor .env. It lives here because this is the dialog
    that asks it.
    """
    action = payload.get("action")
    provider = str(payload.get("id") or payload.get("provider") or "").strip().lower()
    if action == "probe_models":
        # 「获取模型列表」 in the add form, before anything is saved: the endpoint
        # is still just a base URL + key + wire kind. Nothing is registered and
        # nothing is written — this only answers "what can this thing serve?".
        return catalog.models_at(str(payload.get("base_url") or ""),
                                 str(payload.get("kind") or "openai"),
                                 str(payload.get("key") or ""))
    if action == "add_custom":
        spec = custom_providers.spec_from(payload)
        try:
            custom_providers.register(load_settings().home, spec)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        result = integrations.apply_provider(
            spec["id"], key=payload.get("key") or None,
            model=spec["model"], small_model=spec["small_model"],
            base_url=spec["base_url"], force=bool(payload.get("force")),
            activate=bool(payload.get("activate", True)),
        )
        if not result.ok:
            # The spec stays registered on purpose: the card with a red dot is
            # how the user sees what did not save, and re-sending this same
            # action with force=true is the 仍然保存 retry.
            return {"ok": False, "error": result.error, "can_force": result.can_force}
        # Defining a provider is a statement that you mean to use it, so its main
        # model joins the curated shortlist — that list is exactly what the agent
        # page's model switcher shows, so without this the new provider would be
        # current and *still* invisible there until pinned by hand on this page.
        # catalog.save_pinned stays the only writer of models.json.
        pinned = catalog.pinned_specs()
        if (spec_name := f"{spec['id']}:{spec['model']}") not in pinned:
            catalog.save_pinned([*pinned, spec_name])
        return {"ok": True, **settings_info()}
    if action == "remove_custom":
        if not custom_providers.is_custom(provider):
            return {"ok": False, "error": "只有自定义服务商可以删除"}
        integrations.remove_custom_provider(provider)
        custom_providers.unregister(load_settings().home, provider)
        # A pinned model of a provider that no longer exists would sit in the
        # chat switcher forever. catalog.save_pinned stays the only writer.
        catalog.save_pinned([spec for spec in catalog.pinned_specs()
                             if spec.split(":", 1)[0] != provider])
        return {"ok": True, **settings_info()}
    return {"ok": False, "error": f"unknown action {action}"}


def pin_action(payload: dict) -> dict:
    """Manage the curated model shortlist: pin / unpin / make-default."""
    action = payload.get("action")
    provider, model = payload.get("provider", ""), payload.get("model", "")
    if not provider or not model:
        return {"error": "provider and model required"}
    spec = f"{provider}:{model}"
    if action == "default":
        # move to the front of its provider's group -> becomes that provider's default
        catalog.pin_default(provider, model)
        return {"ok": True, **settings_info()}
    specs = [s for s in catalog.pinned_specs() if s != spec]
    if action == "pin":
        specs.append(spec)
    elif action != "unpin":
        return {"error": f"unknown action {action}"}
    catalog.save_pinned(specs)
    return {"ok": True, **settings_info()}


def roles_info(s) -> dict:
    """What each role runs on: the raw choice, and where it actually lands.

    Both halves are needed by the page. The raw value is what the role's two
    <select>s must show, because "" means 「跟随当前服务商」 — showing the
    effective model there instead would turn "follow" into a pinned choice the
    moment anyone presses save without touching it. The effective pair is what
    the line under them prints, so the page can say what "follow" currently
    means rather than leaving the user to guess.

    `degraded` is the honest bit, and it comes from the SAME function the turn
    uses (core/models.py:role_runs_on) — a role whose provider has no key is not
    what will run; it quietly falls back to the main provider, because that path
    must never raise from inside a turn. Computed here by a second copy of that
    rule, the page would eventually print a provider the turns do not use.
    """
    out = {}
    for role in ROLES:
        name, model, degraded = role_runs_on(s, role)
        out[role] = {
            "provider": getattr(s, f"{role}_provider", ""),
            "model": getattr(s, f"{role}_model", ""),
            "effective_provider": name,
            "effective_model": model,
            "degraded": degraded,
        }
    return out


def settings_info() -> dict:
    """Current provider/model + which keys are set — masked to last-4, never
    the full key. `pinned` is the user's curated model shortlist (the chat
    switcher shows exactly these, across providers)."""
    s = load_settings()
    # the curated shortlist, in order; the first pinned model per provider is
    # that provider's default (used when you switch providers).
    pinned, seen = [], set()
    for spec in catalog.pinned_specs():
        p, _, m = spec.partition(":")
        if m:
            pinned.append({"provider": p, "model": m, "default": p not in seen})
            seen.add(p)
    # Group by provider for display (so all of one lab's models sit together,
    # e.g. a late-added claude-fable-5 joins the other anthropic rows). A STABLE
    # sort by provider's first-appearance order keeps each provider's own order —
    # so its default (first pinned) stays on top and the 'default' flags above
    # still line up.
    prov_order: dict = {}
    for row in pinned:
        prov_order.setdefault(row["provider"], len(prov_order))
    pinned.sort(key=lambda row: prov_order[row["provider"]])
    # Resolve the model the same way the loop does. `knowme/loop/models.py` fills a
    # blank KNOWME_MODEL from the provider's default at build time, so the agent is
    # always running SOMETHING — but this dict is what the nav pill and the Models
    # page render, and reporting "" made a fresh install display `anthropic ·`,
    # a trailing separator with no model name. The display must not claim less
    # than the agent actually has.
    prov = PROVIDERS.get(s.provider)
    return {
        "provider": s.provider,
        "model": s.model or (prov.model if prov else ""),
        "small_model": s.small_model or (prov.small_model if prov else ""),
        # gate/summary: each may be its own provider+model (see roles_info).
        "roles": roles_info(s),
        "base_url": s.base_url or "",
        "custom_key_set": bool(s.api_key),
        # Ids of providers the user disabled in the Models grid; the frontend
        # derives each card's status (unconfigured / configured / enabled) and
        # hides disabled providers from the chat switcher.
        "disabled_providers": sorted(s.disabled_providers),
        "pinned": pinned,
        "providers": [{"name": name} for name in PROVIDERS],
        # experimental tools (delegate_task -> pi). The ARENA can switch this on
        # per-race, but the chat agent reads it from the environment — so without
        # a toggle here, the sidebar chat could never delegate. See settings_save.
        "experimental": s.experimental,
        "pi_installed": bool(shutil.which("pi")),
        # graph workflows (triage-first turns) — same toggle contract as
        # experimental: the UI renders it, apply_settings writes it.
        "graph_workflows": s.graph_workflows,
    }


def apply_settings(payload: dict) -> dict:
    """Save the global Settings: the two toggles, and the two role models.

    Connection fields and provider changes deliberately live in integrations —
    but the roles do NOT belong there. A role is not a property of a provider;
    it is a global setting that POINTS AT one (that is why the same four field
    names work no matter who is active), so it is saved here.
    """
    import os

    from dotenv import find_dotenv, set_key

    if "episodic_store" in payload:
        return {"error": "episodic_store is managed in Connections"}
    env_path = find_dotenv(usecwd=True) or ".env"
    # NOT `if value:` — clearing a field sends "", which is falsy. Absent (None)
    # means "don't touch"; "" means "clear it" (toggle off, or a role back to
    # following the active provider). Same rule for both blocks below.
    toggles = (("experimental", "KNOWME_EXPERIMENTAL"), ("graph_workflows", "KNOWME_GRAPH_WORKFLOWS"))
    for field, env_name in toggles:
        value = payload.get(field)
        if value is not None:
            value = "1" if str(value).strip() else ""
            set_key(env_path, env_name, value)
            os.environ[env_name] = value
    # The role pairs (gate/summary × provider/model). Both halves are optional;
    # what an empty half falls back to is decided in ONE place, by the pure
    # function that also reads them back: core/models.py:role_target.
    for role in ROLES:
        for half in ("provider", "model"):
            field = f"{role}_{half}"
            if field not in payload:
                continue
            value = str(payload.get(field) or "").strip()
            env_name = f"KNOWME_{field.upper()}"
            set_key(env_path, env_name, value)
            os.environ[env_name] = value
    # ...and now make it true for the agent that is already running. KnowMe keeps
    # a Settings snapshot from construction and resolves role clients ONCE
    # (app.py:41), so writing os.environ takes effect at the next restart unless
    # the agent is rebuilt — and "生效于下一条消息" would simply be false.
    # Only when one already exists: a CLI-only process must not have an agent
    # conjured up by a settings save (same guard as integrations.apply_provider).
    from knowme.ops import browser_agent

    live = browser_agent.current() is not None or browser_agent.current_agents()
    # A failed rebuild does NOT undo the write, so this must not be reported as
    # "save failed" — the user would go looking for a problem in the value they
    # just set. Say what is actually true: saved, restart needed.
    if live and (error := browser_agent.rebuild()):
        return {"ok": False, "error": f"已保存，但重建失败，要重启才生效：{error}"}
    return {"ok": True, **settings_info()}
