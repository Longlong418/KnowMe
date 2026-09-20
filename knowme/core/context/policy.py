"""ContextPolicy — the compressors as one chain, run in one place.

app.py used to run the three prompt-side compressors as three hard-coded ifs,
and that was fine for one agent. A second agent that wants a different chain
(no summarising, say, or a lower trigger) would have needed a second copy of
_run_full_turn. So the chain is data now: an ordered tuple of stages, each one
a function over (history, FitContext), and a policy is the thing an AgentSpec
points at.

WHAT DID NOT CHANGE. default_policy() is the exact chain app.py ran, in the
exact order, with the exact trigger rule:

    tool_budget      always            per-turn pointer + excerpt
    micro_compact    if over trigger   older results become bare pointers
    state_summary    if STILL over     the conversation becomes a summary

"over" is re-measured before every gated stage against the CURRENT sent copy,
which is what the old code did by calling prompt_tokens twice. Measurement
stays in micro_compact.prompt_tokens; this module only decides when to ask.

snip_compact is NOT a stage. It bounds the STORED conversation and runs in
Session.add_exchange, on the way to disk; these stages rewrite a COPY on the
way to the model. Different side of the line, so it stays where it is.

state_summary is the one stage whose output must ALSO become the stored
history (persists=True): once the conversation has been replaced by a summary,
the next turn has to start from the summary, not from the archived transcript.
FitResult.persisted carries that back to the caller; every other stage leaves
it None.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from knowme.config import Settings
from knowme.core.context import micro_compact, state_summary, tool_budget


@dataclass
class FitContext:
    """Everything a stage may need, handed to all of them alike."""

    system: str
    prompt: str
    full_history: list[dict]   # the stored conversation, untouched — for archiving
    home: Path
    conn: Any
    session_id: str
    client: Any
    small_model: str
    settings: Settings
    limit: int           # the model's real window, tokens (ops/pricing.context_for)
    trigger: float       # fraction of it that counts as "too full"

    def over(self, sent: list[dict]) -> bool:
        messages = sent + [{"role": "user", "content": self.prompt}]
        return micro_compact.prompt_tokens(self.system, messages) > self.trigger * self.limit


StageFn = Callable[[list[dict], FitContext], "list[dict] | None"]


@dataclass(frozen=True)
class Stage:
    name: str
    fn: StageFn                          # returns the new history, or None for "no change"
    when: Literal["always", "over"] = "always"
    persists: bool = False               # True → the result is also the new stored history


@dataclass
class FitResult:
    sent: list[dict]                     # what goes to the model
    persisted: list[dict] | None = None  # what the session must adopt, if a stage said so
    ran: list[str] | None = None         # stage names that changed something, in order


@dataclass(frozen=True)
class ContextPolicy:
    stages: tuple[Stage, ...]

    def fit(self, history: list[dict], ctx: FitContext) -> FitResult:
        sent = history
        result = FitResult(sent=sent, ran=[])
        for stage in self.stages:
            if stage.when == "over" and not ctx.over(sent):
                continue
            out = stage.fn(sent, ctx)
            if out is None or out is sent:
                continue
            sent = out
            result.ran.append(stage.name)
            if stage.persists:
                result.persisted = out
        result.sent = sent
        return result


# ---- the three stages, as thin adapters over the modules that do the work

def _tool_budget(sent: list[dict], ctx: FitContext) -> list[dict]:
    return tool_budget.fit_history(
        sent, ctx.home, ctx.settings.tool_result_budget, ctx.settings.tool_result_cap)


def _micro_compact(sent: list[dict], ctx: FitContext) -> list[dict]:
    return micro_compact.compact(
        sent, ctx.home, ctx.settings.micro_keep, ctx.settings.micro_min_chars)


def _state_summary(sent: list[dict], ctx: FitContext) -> list[dict] | None:
    # Summarises the FITTED copy (so tool pointers survive) but archives the
    # FULL history — that is why it needs both, and why the full history rides
    # on the context rather than being the `sent` argument.
    return state_summary.summarize(
        ctx.full_history, sent, ctx.home, ctx.conn, ctx.session_id,
        ctx.client, ctx.small_model, ctx.settings.summary_max_tokens)


def default_policy() -> ContextPolicy:
    return ContextPolicy((
        Stage("tool_budget", _tool_budget, when="always"),
        Stage("micro_compact", _micro_compact, when="over"),
        Stage("state_summary", _state_summary, when="over", persists=True),
    ))
