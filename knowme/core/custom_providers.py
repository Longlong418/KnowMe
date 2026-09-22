"""User-defined AI providers — what the 模型 page's "＋ 添加服务商" writes.

A custom provider is not a new concept in KnowMe: it is one more entry in
``knowme.core.models.PROVIDERS``, the table everything else already reads — the
model page's cards (``integrations``), the live catalog (``ops.catalog``), where
a key gets written (``integrations.apply_provider``) and the client the loop
builds (``core.models.get_client``). Registering it here is therefore the whole
feature; no downstream code needs a second path for it.

Two deliberate limits:

* **No secrets live here.** ``.knowme/providers.json`` holds the SHAPE of the
  provider (id, wire format, endpoint, default models). The API key lives in
  ``.env`` under ``KNOWME_CUSTOM_<ID>_API_KEY``, exactly like every built-in
  provider — so there is still one place secrets live and one place they don't.
* **Nothing is read at import time.** ``PROVIDERS`` is read at import time by
  the ``.env.example`` generator and by parametrized tests, and a provider table
  that depends on whose ``providers.json`` happens to exist would make the test
  suite depend on the developer's machine. The single lazy call lives in
  ``config.load_settings()`` — the one function every entry point (web, CLI,
  brief, gather) already goes through.

Ids are restricted to ``[a-z][a-z0-9_]*`` because the id becomes part of two
environment variable names, and ``.env`` is meant to stay sourceable by a shell.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from knowme.core.models import PROVIDERS, Provider

ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,31}$")
KINDS = ("openai", "anthropic")

# Which ids THIS process registered, and for which home — so switching to
# another home (every test, and any CLI run from a different directory) can take
# the table back to built-ins instead of stacking user providers forever.
_INJECTED: list[str] = []
_LOADED_HOME: Path | None = None


def spec_path(home: Path) -> Path:
    return Path(home) / "providers.json"


def key_env(provider_id: str) -> str:
    """The .env variable holding this provider's API key."""
    return f"KNOWME_CUSTOM_{provider_id.upper()}_API_KEY"


def base_url_env(provider_id: str) -> str:
    """Its endpoint, in a variable of its own.

    Provider-scoped on purpose: the global KNOWME_BASE_URL belongs to whichever
    provider it was set for, so two custom providers sharing it would overwrite
    each other's endpoint on every switch.
    """
    return f"KNOWME_CUSTOM_{provider_id.upper()}_BASE_URL"


def is_custom(name: str) -> bool:
    """Was *name* loaded from providers.json in this process?"""
    return name in _INJECTED


def specs(home: Path) -> list[dict]:
    """The saved specs, in order. A damaged file reads as "no providers" rather
    than taking the dashboard down — this is a convenience file, not a database."""
    try:
        raw = json.loads(spec_path(home).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    items = raw.get("custom", []) if isinstance(raw, dict) else []
    return [item for item in items if isinstance(item, dict)]


def _save(home: Path, items: list[dict]) -> None:
    path = spec_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")        # same atomic write as connections_health.json
    # ensure_ascii=False: a 显示名 typed in Chinese should read as Chinese in the
    # file, not as a row of \uXXXX escapes.
    temp.write_text(json.dumps({"custom": items}, indent=1, ensure_ascii=False), encoding="utf-8")
    temp.replace(path)


def spec_from(payload: dict) -> dict:
    """The stored shape, built from the add-provider form's payload."""
    def text(name: str) -> str:
        return str(payload.get(name) or "").strip()

    provider_id = text("id").lower()
    return {
        "id": provider_id,
        "label": text("label") or provider_id,
        "kind": text("kind") or "openai",
        "base_url": text("base_url").rstrip("/"),
        "model": text("model"),
        "small_model": text("small_model"),
    }


def validate(spec: dict) -> str:
    """Why this spec cannot be registered — or "" when it can.

    Checked in two places: the add-provider form, so the reason reaches whoever
    typed it, and :func:`load`, so a hand-edited file cannot put a broken entry
    into the table. An id that is already taken is a collision in both cases —
    except for a custom provider's own id, which the retry after a failed probe
    re-sends verbatim (see settings_api.custom_provider_action).
    """
    provider_id = spec.get("id", "")
    if not ID_PATTERN.match(provider_id):
        return "ID 只能用小写字母、数字、下划线，字母开头，2-32 位（它会成为环境变量名的一部分）"
    if provider_id in PROVIDERS and not is_custom(provider_id):
        return f"已有同名服务商：{provider_id}（内置服务商不能被覆盖）"
    if spec.get("kind") not in KINDS:
        return "接口类型必须是 openai 或 anthropic"
    if not str(spec.get("base_url") or "").strip():
        return "Base URL 必填"
    if not str(spec.get("model") or "").strip():
        return "主模型必填"
    return ""


def _provider(spec: dict) -> Provider:
    """The PROVIDERS entry a saved spec stands for.

    base_url is filled in as well as base_url_env: the env var is the live value
    the user saved, and the field is the fallback that keeps the provider usable
    when it has not been saved yet (a fresh clone, a test home).

    label and small_model read with .get() because validate() does not require
    them, and this runs from load_settings() — a hand-edited file missing one
    optional key must yield a slightly plainer card, not a process that cannot
    start up.
    """
    provider_id = spec["id"]
    return Provider(
        kind=spec["kind"],
        key_env=key_env(provider_id),
        base_url=spec["base_url"],
        model=spec["model"],
        small_model=spec.get("small_model") or spec["model"],
        label=spec.get("label") or provider_id,
        base_url_env=base_url_env(provider_id),
    )


def _inject(spec: dict) -> None:
    PROVIDERS[spec["id"]] = _provider(spec)
    if spec["id"] not in _INJECTED:
        _INJECTED.append(spec["id"])


def load(home: Path) -> None:
    """Register this home's custom providers. Called from load_settings()."""
    global _LOADED_HOME
    home = Path(home)
    if home == _LOADED_HOME:
        return                              # same home, already loaded — the common case
    unload()
    _LOADED_HOME = home
    for spec in specs(home):
        if not validate(spec):
            _inject(spec)


def unload() -> None:
    """Undo :func:`load` — for a home switch, and for tests."""
    global _LOADED_HOME
    for name in _INJECTED:
        PROVIDERS.pop(name, None)
    _INJECTED.clear()
    _LOADED_HOME = None


def register(home: Path, spec: dict) -> None:
    """Save *spec* and make it live in this process.

    Raises ValueError whose message is meant for the person who typed it.
    """
    if reason := validate(spec):
        raise ValueError(reason)
    _save(home, [item for item in specs(home) if item.get("id") != spec["id"]] + [spec])
    _inject(spec)


def unregister(home: Path, provider_id: str) -> None:
    """Forget a custom provider: its spec, and its entry in the table.

    Its credentials in ``.env`` are :func:`integrations.remove_custom_provider`'s
    job — this module never touches secrets. A built-in id cannot be reached from
    here on purpose, so a mistyped call can never delete one.
    """
    _save(home, [item for item in specs(home) if item.get("id") != provider_id])
    if provider_id in _INJECTED:
        _INJECTED.remove(provider_id)
        PROVIDERS.pop(provider_id, None)
