"""Procedural memory — SKILL.md files: how to act, loaded only when relevant.

Official Anthropic Agent Skills format: YAML frontmatter with `name` and
`description` (the description doubles as the trigger — no custom `triggers:`
field, which launch-agent-skills used before the spec settled).

Progressive disclosure, the part that matters — four levels, and the MODEL
drives the bottom three:
  1. the system prompt only says that skills EXIST (constant size, so it never
     grows with the number of skills installed and never disturbs the cache
     prefix)
  2. catalog() — name + description of every skill, and only when the model
     asks for them through the `skill` tool
  3. find() — one skill's BODY, loaded only when the model names it
  4. files a skill references are only read if the model asks for those

This used to stop at level 1.5: a keyword-overlap matcher picked the "top 2"
skills and their full bodies were injected into every turn that scored a hit.
It fired reliably but understood nothing (two shared words of 3+ letters was the
whole test), and it paid full price for a skill the model never used. The
harness now only says "there are skills"; which one matters is a judgment the
model is better at than a word-overlap count.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Skill:
    name: str
    description: str
    body: str
    path: Path


def _parse_text(text: str, path: Path) -> Skill | None:
    """Validate SKILL.md content (used by the loader AND the create_skill tool)."""
    match = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.DOTALL)
    if not match:
        return None
    front, body = match.groups()
    fields = {
        k.strip(): v.strip().strip("'\"")
        for k, _, v in (line.partition(":") for line in front.splitlines() if ":" in line)
    }
    if "name" not in fields or "description" not in fields:
        return None
    return Skill(fields["name"], fields["description"], body.strip(), path)


def _parse(path: Path) -> Skill | None:
    return _parse_text(path.read_text(encoding="utf-8"), path)


class SkillLoader:
    """Scans skill directories: the repo's skills/ (built-in + community) and
    KNOWME_HOME/skills (installed or agent-authored). Re-scans automatically
    when any SKILL.md changes, so a skill created mid-session is live next turn."""

    def __init__(self, dirs: list[Path]):
        self.dirs = dirs
        self.skills: list[Skill] = []
        self._sig: tuple = ()
        self.refresh()

    def _scan_sig(self) -> tuple:
        sig = []
        for d in self.dirs:
            if d.is_dir():
                for f in sorted(d.rglob("SKILL.md")):
                    sig.append((str(f), f.stat().st_mtime))
        return tuple(sig)

    def refresh(self) -> None:
        self.skills = []
        for d in self.dirs:
            if not d.is_dir():
                continue
            for f in sorted(d.rglob("SKILL.md")):
                skill = _parse(f)
                if skill:
                    self.skills.append(skill)
        self._sig = self._scan_sig()

    def _reload_if_changed(self) -> None:
        """A skill added or edited on disk becomes live without a restart. This
        used to be buried in match(); every reader has to do it now, so it gets
        one home instead of three chances to forget."""
        if self._scan_sig() != self._sig:
            self.refresh()

    def catalog(self) -> str:
        """Every skill as one `name: description` line — level 1 of progressive
        disclosure, and the ONLY part a skill costs until it is used.

        The model asks for this; the harness no longer pushes it. Nothing here
        is injected automatically, so an installed-but-unused skill costs zero
        tokens. Sorted by path (see refresh), so repeated calls return identical
        bytes — which keeps the result harmless to leave in the history."""
        self._reload_if_changed()
        if not self.skills:
            return "No skills are installed."
        return "\n".join(f"- {s.name}: {s.description}" for s in self.skills)

    def find(self, name: str) -> Skill | None:
        """One skill by name — level 2, the body the model explicitly asked for."""
        self._reload_if_changed()
        wanted = (name or "").strip().lower()
        for skill in self.skills:
            if skill.name.lower() == wanted:
                return skill
        return None
