"""Configuration — every knob is an env var, documented in .env.example.

No settings framework: a dataclass read once at startup. If you can read this
file, you know everything KnowMe can be configured to do.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import find_dotenv, load_dotenv


def _load_env() -> str:
    """Find the user's .env the way the user expects: from where they ARE.

    Bare `load_dotenv()` searches upward from the file that called it, not from
    the working directory. Inside a git checkout that is invisible — config.py
    lives in the project, so walking up from it lands on the project's .env and
    everything works. Installed from PyPI it walks up from site-packages,
    reaches the filesystem root, and finds nothing: the user stands in a folder
    holding a perfectly good .env and KnowMe reports "No API key". Reported from
    a clean install on 2026-07-31, from inside the repo folder itself.

    usecwd=True is the whole fix. The walk upward is kept on purpose, so
    running `knowme` from a subdirectory of your project still finds the .env at
    its root — the same rule git, npm and pytest already taught everyone.

    Returns the path that was loaded (empty string if none) so `knowme doctor`
    and the first-run error can say WHICH file was read, rather than leaving
    people guessing between three .env files.
    """
    path = find_dotenv(usecwd=True)
    if path:
        load_dotenv(path)
    return path


DOTENV_PATH = _load_env()


@dataclass
class Settings:
    # --- LLM: pick a provider, set its key. See knowme/loop/models.py PROVIDERS.
    provider: str = field(default_factory=lambda: os.getenv("KNOWME_PROVIDER", "anthropic"))
    # Explicit overrides (optional): key, endpoint, and model ids. Left empty,
    # the provider's own key env var and default models are used.
    api_key: str = field(default_factory=lambda: os.getenv("KNOWME_API_KEY", ""))
    base_url: str | None = field(default_factory=lambda: os.getenv("KNOWME_BASE_URL") or None)
    model: str = field(default_factory=lambda: os.getenv("KNOWME_MODEL", ""))
    # Cheap model used by the retrieval gate and the consolidation summarizer.
    small_model: str = field(default_factory=lambda: os.getenv("KNOWME_SMALL_MODEL", ""))
    # Providers the user turned off in the dashboard (comma-separated ids).
    # Disabled providers are hidden from pickers/switchers; the ACTIVE provider
    # can't be disabled (guarded in integrations.apply_provider_disabled).
    disabled_providers: frozenset[str] = field(default_factory=lambda: frozenset(
        p.strip() for p in os.getenv("KNOWME_DISABLED_PROVIDERS", "").split(",") if p.strip()))

    # --- Home: where KnowMe keeps its state (memory DB, calendar, outbox, traces).
    # Defaults to ./.knowme next to where you run it, so you can open every file
    # it writes. Local-first means you can always look.
    home: Path = field(default_factory=lambda: Path(os.getenv("KNOWME_HOME", ".knowme")))

    # --- Loop guardrails
    max_iterations: int = field(default_factory=lambda: int(os.getenv("KNOWME_MAX_ITERATIONS", "10")))
    # Headroom matters for REASONING models (kimi-k3, gpt-5.x, gemini-*-pro):
    # they spend output tokens thinking before the answer, so a low cap makes
    # them hit stop_reason=max_tokens mid-thought and return an EMPTY reply
    # (watched kimi-k3 do exactly that at 2048). 8192 leaves room to think AND
    # answer; it's a ceiling, not a target, so efficient models still cost the same.
    max_tokens: int = field(default_factory=lambda: int(os.getenv("KNOWME_MAX_TOKENS", "8192")))
    # KNOWME_HISTORY_TURNS is GONE (2026-09-20), not deprecated. It capped
    # working memory with history[-N:], which dropped every older turn without a
    # trace — a long thread read to the model as though it had started late. The
    # bound it existed for is now snip_head/snip_tail below, which ARCHIVES what
    # it removes and leaves a marker saying so. The knob was removed rather than
    # left as a no-op: a setting that silently stops applying is worse than one
    # that is missing, because nothing tells you.
    # Tool results are the fastest-growing thing in the history window, and the
    # folded [tools used: ...] line re-sends them on every later turn. Two knobs
    # with two different jobs (see runtime/tool_budget.py):
    #   budget — does this turn get compressed at all? A turn already under it is
    #            returned untouched, so ordinary turns never pay for this.
    #   cap    — how small one compressed result gets, counting the pointer and
    #            the elision marker (not just the excerpt, or every digest would
    #            come out cap + ~90 and a high cap would GROW the turn). Results
    #            already under the cap are skipped, so short ones (create_event,
    #            save_note) keep their exact text.
    # Characters, not tokens: exact, zero-dependency, and monotonic — which is
    # all a threshold needs. At ~3.6 chars/token these are roughly 1.1k and 330
    # tokens. The full text is written to .knowme/tool_results/ either way.
    tool_result_budget: int = field(
        default_factory=lambda: int(os.getenv("KNOWME_TOOL_RESULT_BUDGET", "4000")))
    tool_result_cap: int = field(
        default_factory=lambda: int(os.getenv("KNOWME_TOOL_RESULT_CAP", "1200")))
    # snip_compact (runtime/snip_compact.py): the conversation-level bound that
    # replaced the history_turns sliding window. Over HEAD+1+TAIL messages, the
    # middle is ARCHIVED to .knowme/archives/ and one marker takes its place —
    # nothing is dropped silently, which is what the window did.
    #   head — how many opening messages survive (a conversation that lost its
    #          first messages has lost its subject)
    #   tail — how many recent messages are kept verbatim; also how much room
    #          one snip buys, since the trigger is derived as head + 1 + tail.
    snip_head: int = field(default_factory=lambda: int(os.getenv("KNOWME_SNIP_HEAD", "3")))
    snip_tail: int = field(default_factory=lambda: int(os.getenv("KNOWME_SNIP_TAIL", "46")))

    # micro_compact (runtime/micro_compact.py): the emergency valve. It measures
    # the assembled prompt against the model's real context window and, past
    # `context_trigger` of it, replaces older tool results with a pointer to
    # their full text on disk — no excerpt, because the alternative is a request
    # that does not fit.
    #   context_trigger — fraction of the window that counts as "too full"
    #   micro_keep      — the most recent N tool results are never touched
    #   micro_min_chars — only results longer than this are worth replacing
    context_trigger: float = field(
        default_factory=lambda: float(os.getenv("KNOWME_CONTEXT_TRIGGER", "0.8")))
    micro_keep: int = field(default_factory=lambda: int(os.getenv("KNOWME_MICRO_KEEP", "3")))
    micro_min_chars: int = field(
        default_factory=lambda: int(os.getenv("KNOWME_MICRO_MIN_CHARS", "120")))
    # The last resort (runtime/state_summary.py): if the prompt is STILL over the
    # trigger after every reversible compressor has run, the conversation is
    # summarised and replaced. It runs on the small model — the gate's model —
    # because it is a compression call, not a reasoning one. The summary is the
    # only thing the model keeps, so the prompt that produces it is where the
    # safety margin lives.
    summary_max_tokens: int = field(
        default_factory=lambda: int(os.getenv("KNOWME_SUMMARY_MAX_TOKENS", "4096")))

    @property
    def snip_at(self) -> int:
        """Messages allowed before a snip. Derived so the three numbers cannot
        disagree — 3 + 1 marker + 46 keeps the conversation at 50."""
        return self.snip_head + 1 + self.snip_tail

    # --- Memory
    # Consolidate (distill chats into durable facts) only after N new exchanges.
    consolidate_every: int = field(default_factory=lambda: int(os.getenv("KNOWME_CONSOLIDATE_EVERY", "6")))
    retrieval_top_k: int = field(default_factory=lambda: int(os.getenv("KNOWME_RETRIEVAL_TOP_K", "4")))
    # 'sqlite' (default, zero setup) or 'supabase' (pgvector upgrade path — see launch-rag).
    semantic_store: str = field(default_factory=lambda: os.getenv("KNOWME_SEMANTIC_STORE", "sqlite"))
    # 'sqlite' (default, zero setup) or 'notion' (episodes live in a Notion database).
    episodic_store: str = field(default_factory=lambda: os.getenv("KNOWME_EPISODIC_STORE", "sqlite"))

    # --- Tools
    # Sync created events into Apple Calendar (a dedicated "KnowMe" calendar)
    # via AppleScript. Opt-in because it writes to your real calendar app.
    apple_calendar: bool = field(
        default_factory=lambda: os.getenv("KNOWME_APPLE_CALENDAR", "") in ("1", "true", "yes")
    )
    # Mirror locally-created events to Google Calendar. SQLite + ICS remain the
    # source of truth; this is only an opt-in write target.
    google_calendar: bool = field(
        default_factory=lambda: os.getenv("KNOWME_GOOGLE_CALENDAR", "") in ("1", "true", "yes")
    )
    google_calendar_id: str = field(
        default_factory=lambda: os.getenv("KNOWME_GOOGLE_CALENDAR_ID", "") or "primary"
    )
    # Give the agent read/write access to Apple Calendar, Mail, Reminders, Notes
    # (macOS; first use triggers the system Automation permission prompts).
    apple_tools: bool = field(
        default_factory=lambda: os.getenv("KNOWME_APPLE_TOOLS", "") in ("1", "true", "yes")
    )
    # Read-only GitHub access through the `gh` CLI's own auth (no token here).
    # Off by default and deliberately so: every registered tool ships in every
    # prompt, and reading PRs is maintainer capability, not assistant capability.
    # The gather workflow calls knowme/tools/github.py as a library and does NOT
    # need this on — the switch only decides whether the MODEL can reach it.
    gh_tool: bool = field(
        default_factory=lambda: os.getenv("KNOWME_GH_TOOL", "") in ("1", "true", "yes")
    )
    # owner/name to assume when a call omits it — for when KnowMe runs outside a
    # checkout, where `gh` has no remote to infer from.
    gh_repo: str = field(default_factory=lambda: os.getenv("KNOWME_GH_REPO", ""))
    # Register the experimental tools (delegate_task -> pi sub-agent, ...).
    experimental: bool = field(
        default_factory=lambda: os.getenv("KNOWME_EXPERIMENTAL", "") in ("1", "true", "yes")
    )
    # Route every message through the triage graph workflow first (a small model
    # classifies it; trivial messages get a fast small-model reply, real tasks
    # run the normal loop as a graph node). Any failure anywhere fails open to
    # the plain loop, so this can never make KnowMe worse — only faster/cheaper.
    graph_workflows: bool = field(
        default_factory=lambda: os.getenv("KNOWME_GRAPH_WORKFLOWS", "") in ("1", "true", "yes")
    )

    # --- Tracing (JSONL always; OTel exports if an endpoint is set)
    otel_endpoint: str = field(
        default_factory=lambda: os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "")
    )

    def ensure_home(self) -> Path:
        self.home.mkdir(parents=True, exist_ok=True)
        (self.home / "traces").mkdir(exist_ok=True)
        (self.home / "outbox").mkdir(exist_ok=True)
        # full copies of any tool result too long to keep in history
        (self.home / "tool_results").mkdir(exist_ok=True)
        # snippets of conversation snip_compact removed from working memory
        (self.home / "archives").mkdir(exist_ok=True)
        return self.home


def load_settings() -> Settings:
    return Settings()
