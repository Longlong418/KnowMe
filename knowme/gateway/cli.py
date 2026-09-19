"""CLI gateway — the zero-setup way to talk to your KnowMe.

The Gateway Interface box: a gateway only moves text in and out; everything
interesting happens in the loop. The Telegram gateway is the same ~60 lines
with polling instead of input().
"""

from __future__ import annotations

import sqlite3

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from knowme.app import KnowMe

console = Console()


def _memory_snapshot(conn: sqlite3.Connection) -> str:
    """Render a bounded, read-only view of KnowMe's local memory."""
    fact_count = conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
    facts = conn.execute("SELECT subject, content FROM facts ORDER BY id DESC LIMIT 8").fetchall()
    episode_count = conn.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
    episodes = conn.execute(
        "SELECT happened_at, summary FROM episodes ORDER BY happened_at DESC, id DESC LIMIT 5"
    ).fetchall()
    pending = conn.execute("SELECT COUNT(*) FROM chat_log WHERE consolidated = 0").fetchone()[0]

    lines = [f"Semantic facts ({fact_count})"]
    lines.extend(f"- [{row['subject']}] {row['content']}" for row in facts)
    if not facts:
        lines.append("- none yet")

    lines.extend(["", f"Recent episodes ({episode_count})"])
    lines.extend(f"- {row['happened_at']} - {row['summary']}" for row in episodes)
    if not episodes:
        lines.append("- none yet")

    lines.extend(["", f"Unconsolidated chat messages: {pending}"])
    return "\n".join(lines)


def _observer(kind: str, event: dict) -> None:
    """Show the loop's internals live — the video's 'transparent harness' beat."""
    if kind == "tool":
        console.print(f"  [dim]tool · {event['tool']}({event['args']}) → {event['output'][:80]}[/dim]")
    elif kind == "gate":
        console.print(f"  [dim]gate · {event['decision']} — {event.get('reason','')}[/dim]")
    elif kind == "consolidation":
        console.print(f"  [dim]memory · consolidated {event['new_facts']} fact(s) from recent chats[/dim]")


def main() -> None:
    knowme = KnowMe()
    knowme.session.session_id = "terminal"   # its own conversation thread in the inbox
    console.print(Panel.fit(
        "[bold]KnowMe[/bold] — local, yours, transparent.\n"
        f"home: {knowme.settings.home.resolve()}   model: {knowme.settings.model}\n"
        "Commands: /memory · /quit",
        border_style="cyan",
    ))
    while True:
        try:
            user_message = console.input("[bold cyan]you ›[/bold cyan] ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not user_message:
            continue
        if user_message in ("/quit", "/exit"):
            break
        if user_message == "/memory":
            console.print(
                Panel(
                    Text(_memory_snapshot(knowme.conn)),
                    title="Memory snapshot",
                    border_style="cyan",
                )
            )
            continue
        result = knowme.respond(user_message, observer=_observer, source="cli")
        console.print(f"[bold green]knowme ›[/bold green] {result.reply}\n")
    console.print("[dim]bye — your memory stays in state.db[/dim]")


if __name__ == "__main__":
    main()
