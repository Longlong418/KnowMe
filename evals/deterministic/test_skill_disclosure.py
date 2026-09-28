"""DETERMINISTIC EVAL — skills are pulled by the model, never pushed at it.

The design under test, level by level:
  1. the system prompt only says that skills EXIST (constant size, whatever the
     number of skills installed — so it never disturbs the cache prefix)
  2. the `skill` tool with no arguments returns name + description for each
  3. the `skill` tool with a name returns that skill's body
  4. nothing else is ever read

Level 1.5 used to exist: a keyword-overlap matcher picked the "top 2" skills and
injected their full bodies into any turn that scored two shared words. It fired
reliably and understood nothing, and it had no test coverage at all — which is
how it could be deleted without breaking a single test. These are the tests it
never had.
"""

from __future__ import annotations

from types import SimpleNamespace

from knowme.runtime.session import Session

from knowme.config import Settings
from knowme.memory.procedural.loader import SkillLoader
from knowme.tools.memory_admin import make_skill_tool

DESCRIPTION = "how to run the weekly review"
BODY = "STEP ONE: gather the week's notes."


def _memory_with_skills(tmp_path, *names: str):
    """A memory stub holding real SKILL.md files, minus the model calls."""
    skills_dir = tmp_path / "skills"
    for name in names:
        path = skills_dir / name / "SKILL.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"---\nname: {name}\ndescription: {DESCRIPTION}\n---\n\n{BODY}\n",
            encoding="utf-8",
        )
    return SimpleNamespace(
        skills=SkillLoader([skills_dir]),
        gated_retrieve=lambda *a, **k: "",   # keep the gate offline
    )


def test_a_skill_body_is_not_injected_unless_the_model_asks(tmp_path):
    """THE invariant. Levels 2 and 3 are opt-in, so a turn that never mentions
    skills must not contain one byte of them — not the body, not even the
    description. This is what makes an installed-but-unused skill free."""
    settings = Settings()
    settings.home = tmp_path / "home"
    settings.ensure_home()
    session = Session(settings, memory=_memory_with_skills(tmp_path, "weekly-review"))

    prompt = session.build_system() + session.build_turn_context("do my weekly review")

    assert BODY not in prompt, "a skill body was pushed into the prompt"
    assert DESCRIPTION not in prompt, "a skill description was pushed into the prompt"


def test_the_catalog_lists_name_and_description_without_the_body(tmp_path):
    """Level 2: enough to choose from, not enough to cost anything."""
    tool = make_skill_tool(_memory_with_skills(tmp_path, "weekly-review", "standup-notes"))

    listing = tool.fn()

    assert "weekly-review" in listing and "standup-notes" in listing
    assert DESCRIPTION in listing
    assert BODY not in listing, "the catalog leaked a skill body — level 2 is descriptions only"


def test_loading_by_name_returns_the_body(tmp_path):
    """Level 3: the model named it, so it pays for it."""
    tool = make_skill_tool(_memory_with_skills(tmp_path, "weekly-review"))

    loaded = tool.fn(name="weekly-review")

    assert BODY in loaded
    assert "weekly-review" in loaded


def test_an_unknown_name_answers_with_the_menu(tmp_path):
    """A wrong guess is usually a near miss. Returning the list costs nothing
    and saves the follow-up round trip the model would otherwise spend."""
    tool = make_skill_tool(_memory_with_skills(tmp_path, "weekly-review"))

    answer = tool.fn(name="weakly-review")

    assert "No skill named 'weakly-review'" in answer
    assert "weekly-review" in answer, "the menu did not come back with the refusal"


def test_the_catalog_is_byte_stable_across_calls(tmp_path):
    """Deterministic order means this result is safe to leave in the history:
    identical bytes re-sent turn after turn stay inside the cacheable prefix."""
    tool = make_skill_tool(_memory_with_skills(tmp_path, "b-skill", "a-skill"))

    assert tool.fn() == tool.fn()


def test_no_skills_says_so_rather_than_returning_nothing(tmp_path):
    """An empty string reads as a broken tool. The model should learn that it
    has no skills, not that the skill tool failed."""
    loader = SkillLoader([tmp_path / "empty"])
    assert loader.catalog() == "No skills are installed."


def test_a_skill_edited_on_disk_is_picked_up_without_a_restart(tmp_path):
    """The mtime re-scan used to live inside match(). Moving it out must not
    lose it — every reader has to refresh, or an agent-authored skill stays
    invisible until the process restarts."""
    memory = _memory_with_skills(tmp_path)
    loader = memory.skills
    assert "late-skill" not in loader.catalog()

    path = tmp_path / "skills" / "late-skill" / "SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text(
        f"---\nname: late-skill\ndescription: {DESCRIPTION}\n---\n\n{BODY}\n",
        encoding="utf-8",
    )

    assert "late-skill" in loader.catalog()
    assert loader.find("late-skill") is not None
