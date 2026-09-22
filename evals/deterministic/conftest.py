"""Isolation for the whole deterministic suite: it runs against its own home.

``config.load_settings()`` registers whatever is in ``.knowme/providers.json``
(the Models page's ＋ 添加服务商) into the PROVIDERS table, and it is also where
``.knowme/models.json`` and ``connections_health.json`` are read from. Pointing
``KNOWME_HOME`` at a fresh empty directory keeps three things true:

* Several checks here read PROVIDERS as "the providers KnowMe ships with" —
  every one needs a logo, a pricing entry, a key URL, and ``registry()`` must
  total 21 — so a developer who has added a provider of their own would watch
  those go red with nothing actually broken.
* Tests never write into the developer's real ``.knowme``.
* It has to happen in ``pytest_configure``, not in a fixture: ``evals/helpers.py``
  calls ``load_settings()`` at IMPORT time, so the table can already be polluted
  while pytest is still collecting — and a polluted table changes what the
  parametrized provider tests are even parametrized over.

Tests that want their own home still set ``KNOWME_HOME`` themselves (many do);
that overrides this for the duration of the test.
"""

from __future__ import annotations

import os
import tempfile

import pytest

from knowme.core import custom_providers


def pytest_configure(config):
    # Assigned, not setdefault: KNOWME_HOME already being set is exactly the case
    # this has to override, because that is how someone points the app at a real
    # home — including their own.
    os.environ["KNOWME_HOME"] = tempfile.mkdtemp(prefix="knowme-eval-home-")


@pytest.fixture(autouse=True)
def _forget_custom_providers():
    """An id one test registered must not be in the table for the next one.

    Clearing at both ends matters: the first test in the session would
    otherwise inherit whatever ``pytest_configure``'s home had, and a failure
    mid-test would otherwise leave its providers behind for the rest of the run.
    """
    custom_providers.unload()
    yield
    custom_providers.unload()
