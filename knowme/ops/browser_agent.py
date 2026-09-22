"""The browser gateway's agent pool — one lazy KnowMe per agent profile.

The web client is a gateway like the CLI or Telegram: it only moves text. But
unlike those it is multi-threaded (a stdlib ThreadingHTTPServer) and long-lived
across page refreshes, so it needs two things the other gateways don't:

  ONE instance per selected profile, built lazily and reused, behind
  `agent_lock` so turns run one at a time.  All instances run the same core;
  only their AgentSpec, session and memory scope differ.

  ONE chat thread per web RUN, dated, resumed on restart. Never the
  eternal "default" session — a returning user should see their conversation,
  not an infinite scroll of every chat they have ever had. `maybe_rotate_session`
  handles the other half of that: come back after an idle gap and you get a new
  thread, with the old one one click away in History. (Live bug this fixes: a
  tester came back days later and their new message landed in a week-old
  32-message thread.) That rotation is for threads you were RESUMED into —
  a thread you picked from History yourself is exempt for the next turn, see
  `pick_session`.

This lives in its own module because several callers share and rebuild the
pool: web.py lazily creates profiles for chat, while settings changes
rebuild every live profile.  Import the module and call its functions rather
than importing a mutable global directly.
"""

from __future__ import annotations

import os
import threading
from datetime import UTC, datetime

from knowme.agents import get_profile
from knowme.config import load_settings
from knowme.db import connect

# ``_agent`` remains the default-profile slot for compatibility with existing
# integrations; additional profiles live in ``_agents``.  Every instance is
# lazy, reused, and guarded by the one browser turn lock.
_agent = None
_agents = {}
agent_lock = threading.Lock()
_dashboard_session = None  # legacy name; this web run's thread
_dashboard_sessions = {}  # legacy name; keyed by Agent id
_picked_sessions = {}  # threads the user chose by hand, keyed by Agent id

# The reading pane gives every document its OWN thread, named doc-<document id>.
# Such a thread belongs to the document, not to "the recent web chat": you are
# reading that document until you close it, however long ago the last question
# was. The two predicates below both have to know that, so the prefix lives here
# once — a second copy of the string is how the rotation and the resume would
# start disagreeing about what a document's thread is.
DOC_PREFIX = "doc-"


def _new_session_id(agent_id: str) -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    # Keep the persisted session prefix for existing history and integrations;
    # the user-facing gateway is now called Web.
    return f"dashboard-{stamp}" if agent_id == "default" else f"dashboard-{agent_id}-{stamp}"


def dash_session(agent_id: str = "default") -> str:
    """The thread new web chats belong to. Resolved once per process:
    RESUME the most recent recent web thread (so a restart keeps the chat
    on screen), else start a fresh dated one. Never the eternal 'default'."""
    global _dashboard_session
    get_profile(agent_id)  # validate before this value is used in SQL or UI
    if agent_id == "default" and _dashboard_session is not None:
        return _dashboard_session
    if agent_id != "default" and agent_id in _dashboard_sessions:
        return _dashboard_sessions[agent_id]
    try:
        conn = connect(load_settings().home)
        session_id = resume_or_new_session(conn, agent_id)
        conn.close()
    except Exception:
        session_id = _new_session_id(agent_id)
    if agent_id == "default":
        _dashboard_session = session_id
    else:
        _dashboard_sessions[agent_id] = session_id
    return session_id


def resume_or_new_session(conn, agent_id: str = "default") -> str:
    """Resume this agent's recent web thread, otherwise make a new one.

    A document's thread (DOC_PREFIX) is not a candidate: the reading pane writes
    its questions with source='web', so without the exclusion the FIRST /api/data
    of a restart adopts the last document you read as the agent's whole thread —
    and get_agent binds that answer for the rest of the process, so the sidebar
    and the panel would both report a document's thread as the recent chat."""
    get_profile(agent_id)
    idle_min = int(os.getenv("KNOWME_SESSION_IDLE_MINUTES", "60"))
    row = conn.execute(
        "SELECT session_id, MAX(created_at) AS last_at FROM chat_log "
        "WHERE source IN ('web', 'dashboard') AND agent_id=? "
        "AND session_id NOT LIKE ? GROUP BY session_id "
        "ORDER BY last_at DESC LIMIT 1",
        (agent_id, DOC_PREFIX + "%"),
    ).fetchone()
    if row and row["last_at"]:
        try:
            last = datetime.strptime(row["last_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
            if idle_min <= 0 or (datetime.now(UTC) - last).total_seconds() <= idle_min * 60:
                return row["session_id"]
        except ValueError:
            pass
    return _new_session_id(agent_id)


def get_agent(agent_id: str = "default"):
    global _agent
    profile = get_profile(agent_id)
    if agent_id == "default" and _agent is not None:
        return _agent
    if agent_id != "default" and agent_id in _agents:
        return _agents[agent_id]

    from knowme.app import KnowMe

    settings = load_settings()
    settings.ensure_home()
    conn = connect(settings.home, check_same_thread=False)
    fresh = KnowMe(settings=settings, conn=conn, spec=profile.spec)
    # The SAME thread dash_session() hands the page, never a second opinion.
    # Asking resume_or_new_session() again here is how the two answers drifted:
    # /api/data decided the stale thread was over and minted one dated id, this
    # call decided the same thing a second later and minted another. The page
    # then published the open document under its id while the turn ran in mine.
    fresh.session.session_id = dash_session(agent_id)
    if agent_id == "default":
        _agent = fresh
    else:
        _agents[agent_id] = fresh
    return fresh


def pick_session(agent_id: str, session_id: str) -> None:
    """The page chose this agent's thread by hand — History, or the inbox.

    Both halves of that fact live here, because they are one fact: which thread
    this agent is talking in.

    * the web run's current thread, so /api/data (and any later dash_session)
      answers with the thread the agent is actually using;
    * a note that the user PICKED it, which is what stops maybe_rotate_session
      from undoing the click a moment later.
    """
    global _dashboard_session
    if agent_id == "default":
        _dashboard_session = session_id
    else:
        _dashboard_sessions[agent_id] = session_id
    _picked_sessions[agent_id] = session_id


def maybe_rotate_session(agent) -> None:
    """Rotate only the supplied agent's thread after the configured idle gap."""
    global _dashboard_session
    agent_id = agent.agent_id
    if agent.session.session_id.startswith(DOC_PREFIX):
        # A document's thread never rotates. The point of the rotation is to get
        # you out of a thread you were RESUMED into; a document's thread was not
        # resumed, it is the one the document on screen is using -- and the
        # picked-thread exemption below only excuses a single turn, so without
        # this a long read would silently move the pane to a fresh thread while
        # the document stayed open.
        return
    idle_min = int(os.getenv("KNOWME_SESSION_IDLE_MINUTES", "60"))
    if idle_min <= 0:
        return
    row = agent.conn.execute(
        "SELECT MAX(created_at) FROM chat_log WHERE session_id=? AND agent_id=?",
        (agent.session.session_id, agent_id),
    ).fetchone()
    if not row or not row[0]:
        return
    try:
        last = datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
    except ValueError:
        return
    if (datetime.now(UTC) - last).total_seconds() > idle_min * 60:
        # …unless you opened this thread yourself a moment ago. The rotation
        # exists to keep you out of the thread you were RESUMED into; clicking
        # one in History is the opposite of that, and rotating it anyway sent
        # your reply to a brand-new thread while the page went on showing it
        # under the thread you had just picked — 那条旧对话于是看起来只能看、
        # 不能接着说. Consumed on use: it excuses the turn you are about to
        # send, not the thread for the rest of the process.
        if _picked_sessions.pop(agent_id, None) == agent.session.session_id:
            return
        session_id = _new_session_id(agent_id)
        agent.session.start_new(session_id)
        if agent_id == "default":
            _dashboard_session = session_id
        else:
            _dashboard_sessions[agent_id] = session_id


def current(agent_id: str = "default"):
    """Return a live profile instance without building it."""
    get_profile(agent_id)
    return _agent if agent_id == "default" else _agents.get(agent_id)


def current_agents() -> dict:
    """A copy of the live pool, useful for status pages and safe iteration."""
    live = dict(_agents)
    if _agent is not None:
        live["default"] = _agent
    return live


def rebuild() -> str | None:
    """Rebuild every live profile after settings change, preserving sessions.

    Historical callers expect ``rebuild()`` with no live agent to create the
    default one, so that compatibility remains.
    """
    global _agent, _dashboard_session, _agents
    with agent_lock:
        old_agents = current_agents()
        ids = list(old_agents) or ["default"]
        fresh_agents = {}
        from knowme.app import KnowMe

        try:
            for agent_id in ids:
                settings = load_settings()
                settings.ensure_home()
                conn = connect(settings.home, check_same_thread=False)
                fresh = KnowMe(settings=settings, conn=conn, spec=get_profile(agent_id).spec)
                old = old_agents.get(agent_id)
                fresh.session.session_id = (
                    old.session.session_id if old is not None
                    else resume_or_new_session(conn, agent_id)
                )
                fresh_agents[agent_id] = fresh
        except (Exception, SystemExit) as exc:   # get_client raises SystemExit
            for fresh in fresh_agents.values():
                fresh.close()
            return str(exc)

        _agent = fresh_agents.pop("default", None)
        _agents = fresh_agents
        if _agent is not None:
            _dashboard_session = _agent.session.session_id
        for agent_id, fresh in _agents.items():
            _dashboard_sessions[agent_id] = fresh.session.session_id
    for old in old_agents.values():
        old.close()
    return None
