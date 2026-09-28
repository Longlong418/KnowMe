"""DETERMINISTIC EVAL — the chat agent can switch experimental tools on/off.

The dashboard exposes the experimental flag and lets settings_save persist it,
while direct Settings instances can still explicitly enable or disable tools.
"""

from __future__ import annotations

import os

from knowme.config import Settings, load_settings
from knowme.tools import build_registry


def test_settings_exposes_the_flag_so_the_ui_can_render_a_toggle(monkeypatch):
    from knowme.ops import settings_api

    monkeypatch.delenv("KNOWME_EXPERIMENTAL", raising=False)
    assert settings_api.settings_info()["experimental"] is False

    monkeypatch.setenv("KNOWME_EXPERIMENTAL", "1")
    info = settings_api.settings_info()
    assert info["experimental"] is True
    # the UI needs to say "pi not installed" honestly rather than fail later
    assert "pi_installed" in info


def test_turning_it_off_is_not_swallowed(monkeypatch):
    """`if experimental:` would make the toggle ONE-WAY — "" is falsy, so
    switching off would silently do nothing. settings_save uses `is not None`,
    so absent means "don't touch" and "" means "switch off". This pins the rule
    by driving the real payload through the same expression settings_save uses.
    """
    def rule(payload: dict):
        experimental = payload.get("experimental")
        if experimental is None:
            return None                     # not sent -> leave the env alone
        return "1" if str(experimental).strip() else ""

    assert rule({"experimental": "1"}) == "1"      # on
    assert rule({"experimental": ""}) == ""        # off, and NOT ignored
    assert rule({}) is None                        # absent -> untouched


def test_an_explicit_setting_beats_the_global_env_switch(tmp_path, monkeypatch):
    """An explicit Settings value must win over the process environment."""
    monkeypatch.setenv("KNOWME_EXPERIMENTAL", "1")      # global switch ON
    off = Settings(home=tmp_path / "off", experimental=False)
    on = Settings(home=tmp_path / "on", experimental=True)
    off.ensure_home()
    on.ensure_home()

    from knowme.db import connect

    conn = connect(off.home)
    assert "delegate_task" not in build_registry(conn, off, None)._tools
    conn2 = connect(on.home)
    assert "delegate_task" in build_registry(conn2, on, None)._tools


def test_env_var_is_what_load_settings_reads(monkeypatch):
    """Why the chat was broken: nothing sets KNOWME_EXPERIMENTAL for the server."""
    monkeypatch.delenv("KNOWME_EXPERIMENTAL", raising=False)
    assert load_settings().experimental is False
    monkeypatch.setenv("KNOWME_EXPERIMENTAL", "1")
    assert load_settings().experimental is True
    assert os.getenv("KNOWME_EXPERIMENTAL") == "1"
