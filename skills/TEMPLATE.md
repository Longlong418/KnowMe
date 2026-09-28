---
name: your-skill-name
description: One sentence saying what this skill does AND when to use it — the loader matches user messages against these words, so include the words people actually say.
---

<!--
To use it yourself: drop it at <KNOWME_HOME>/skills/<your-skill-name>/SKILL.md
(that is .knowme/skills/... unless you moved your home), or `knowme skill install
<url-of-this-file>`. The loader needs the frontmatter above — name + description,
the official Anthropic Agent Skills format — because it matches user messages
against those words.

To contribute it instead: put it under the repo's skills/ and open a PR. A test
(evals/deterministic/test_only_tracked_skills_ship.py) requires every skill on
disk to be tracked, so nothing ships by accident. Keep the body under ~60 lines:
skills are loaded into the prompt only when they match, but shorter is better.
-->

## Instructions

Step-by-step guidance for the model. Be concrete: name the tools to call
(`create_event`, `save_note`, `send_message`), the defaults to assume, and
the tone to take.

## Edge cases

| Situation | Do |
|---|---|
| Something ambiguous | Ask one clarifying question |
