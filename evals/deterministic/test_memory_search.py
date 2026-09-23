"""DETERMINISTIC EVAL — can memory be searched in the language it was stored in?

`_fts_query` built the FTS5 query from `[a-zA-Z0-9]`, but the index behind
`facts_fts` and `episodes_fts` is unicode61 (FTS5's default — neither table
declares a tokenizer), which keeps every Unicode alphanumeric and folds
diacritics. The two disagreed, and the index was not the side that was wrong:
"München" is stored as `munchen`, "Сергей" as `сергей`.

Everything outside ASCII was dropped on the way in, two different ways:

    "Müller"  -> `ller`   a fragment that matches nothing
    "Сергей"  -> ``       the empty string

The empty string is the worse half, because it isn't a no-op —
`SqliteEpisodeStore.search()` reads it as "just give me the recent ones". So a
Russian user asking about Сергей got an unrelated English episode handed to the
model under the heading "Relevant memory". Not *no* memory: someone else's.
That's the over-interpretation bias hero 1 exists to prevent, arriving through
the back door.

The five cases at the bottom pass both before and after. They're the ones to
read first in review: they pin that this widening didn't cost anything —
"car" still must not match "carpet", and English results stay byte-identical.

The second half, added 2026-09-22, is the SAME fused-run failure seen from the
other end: unicode61 makes a whole CJK sentence one term, so even a query in the
right language only reached a fact that *begins* with the words asked for. See
`_substring_terms`.

Offline, no model, no new dependency: real SQLite in tmp_path.
"""

from __future__ import annotations

import pytest

from knowme.db import connect
from knowme.memory.episodic.store import SqliteEpisodeStore
from knowme.memory.semantic.store import SqliteFactStore, _fts_query, _substring_terms


@pytest.fixture
def facts(tmp_path):
    return SqliteFactStore(connect(tmp_path))


@pytest.fixture
def episodes(tmp_path):
    return SqliteEpisodeStore(connect(tmp_path))


# ---------- the bug: the query builder threw away everything but ASCII


@pytest.mark.parametrize(
    "query,expected",
    [
        # accented Latin — truncated to a fragment, so it matched nothing
        ("Müller", "müller"),
        ("Wann treffe ich Müller?", "wann OR treffe OR ich OR müller"),
        # non-Latin scripts — reduced to "", which the episode store reads as
        # "give me the recent ones"
        ("Сергей", "сергей"),
        ("Γιώργος", "γιώργος"),
        ("محمد", "محمد"),
        ("שרה", "שרה"),
    ],
)
def test_a_query_keeps_the_alphanumerics_the_index_keeps(query, expected):
    """`[^\\W_]` is the same set unicode61 keeps, so the query and the index
    finally agree on what a word is."""
    assert _fts_query(query) == expected


@pytest.mark.parametrize("query,expected", [("阿历克斯", "阿历克斯*"), ("東京", "東京*")])
def test_unsegmented_scripts_are_searched_as_prefixes(query, expected):
    """unicode61 puts no boundary inside CJK, so 「阿历克斯喜欢游泳」 is indexed as
    ONE term and an exact match on the name can never hit it."""
    assert _fts_query(query) == expected


def test_an_accented_name_finds_the_fact_that_mentions_it(facts):
    facts.add("müller", "Müller prefers meetings in München before noon")
    assert facts.search("Müller"), "the fact is there; the query couldn't reach it"


def test_a_diacritic_is_folded_the_same_way_on_both_sides(facts):
    """unicode61 stores "München" as `munchen`. The query is folded by the same
    tokenizer, so it has to survive as a whole word to be folded at all."""
    facts.add("müller", "Müller prefers meetings in München before noon")
    assert facts.search("München")


def test_a_cyrillic_name_finds_its_fact(facts):
    facts.add("сергей", "Сергей любит плавание по утрам")
    assert facts.search("Сергей")


def test_a_cjk_name_finds_its_fact(facts):
    facts.add("阿历克斯", "阿历克斯喜欢游泳")
    assert facts.search("阿历克斯"), "an exact match can't hit an unsegmented run"


def test_a_non_ascii_query_no_longer_returns_an_unrelated_episode(episodes):
    """The worst symptom, stated directly. An empty FTS query fell through to
    recent(), so a question about Сергей was answered with whatever happened
    most recently — under the heading "Relevant memory"."""
    episodes.add("Planned the Acme demo with Alex", "2026-08-02")

    assert episodes.search("Сергей") == [], "silence is correct; someone else's memory is not"


# ---------- regression guards: these pass before AND after


def test_english_search_is_unchanged(facts):
    facts.add("alex", "Alex prefers morning meetings")
    assert facts.search("Alex") == ["[alex] Alex prefers morning meetings"]
    assert _fts_query("when am I meeting Alex?") == "when OR am OR meeting OR alex"


def test_car_must_not_match_carpet(facts):
    """The reason only unsegmented scripts get a `*`. Prefixing everything would
    look like it worked while quietly widening every English search."""
    facts.add("shopping", "the carpet is red")
    assert facts.search("car") == []


def test_punctuation_and_single_letters_are_still_dropped():
    assert _fts_query("!!! ??? ...") == ""
    assert _fts_query("a b c") == ""


def test_an_unsearchable_query_still_means_no_facts_not_all_facts(facts):
    """Facts fail closed on an empty query, and must keep doing so."""
    facts.add("alex", "Alex prefers morning meetings")
    assert facts.search("!!!") == []


def test_underscore_is_a_separator_here_because_it_is_one_in_the_index():
    """unicode61 splits on `_`, so `knowme_agent` is two terms in the index and
    has to be two terms here too, or it matches nothing."""
    assert _fts_query("knowme_agent") == "knowme OR agent"


# ---------- the other half of the same failure: the word is in the MIDDLE

def test_a_chinese_fact_is_reachable_by_a_word_inside_it(facts):
    r"""Live report: 记忆里有「在大连海事大学读硕士研究生」, agent 被问「我在哪读书」时
    说不知道 — 而这条事实一直在表里，记忆管理页面看得见。

    unicode61 indexes that sentence as ONE term each side of the punctuation
    (`在大连海事大学`, `读硕士研究生` — read with fts5vocab on a real database),
    so the prefix form `大连海事大学*` matches nothing. Only the sentence's own
    first characters were ever reachable, and a fact never begins with the word
    you are asking about.
    """
    facts.add("user", "在大连海事大学读硕士研究生（Dalian Maritime University）。")
    assert facts.search("大连海事大学"), "整串就在句子里，前缀匹配却够不到"
    assert facts.search("大连 硕士"), "两个词都落在 token 中间"


def test_the_query_the_agent_actually_tried_finds_the_fact(facts):
    """From the trace of that turn: `manage_memory search "上学 学校 大学"` →
    "no matching facts". This is the exact string, against the exact fact."""
    facts.add("user", "在大连海事大学读硕士研究生。")
    found = facts.search_with_ids("上学 学校 大学")
    assert found, "工具路径和 turn 路径共用一次查询，两条都得通"
    assert "大连海事大学" in found[0]["content"]


def test_a_chinese_episode_is_reachable_by_a_word_inside_it(episodes):
    episodes.add("今天定了去大连海事大学读硕士", "2026-09-20")
    assert episodes.search("大连海事大学")


def test_a_phrase_yields_its_two_character_words():
    """A run longer than a word is a phrase, and the fact it should reach is
    phrased differently — so it is cut into fragments too, whole run first
    (more precise), deduped, in order."""
    assert _substring_terms("大连海事大学") == [
        "大连海事大学", "大连", "连海", "海事", "事大", "大学"]


def test_a_long_cjk_query_does_not_become_hundreds_of_scans():
    """A search query is a few words; a pasted paragraph must not turn into one
    LIKE scan per fragment."""
    assert len(_substring_terms("阿" * 200)) <= 12


def test_a_chinese_query_that_matches_nothing_still_returns_nothing(facts):
    """Widening the search must not turn it into "return everything": that is
    the same failure wearing the opposite coat."""
    facts.add("user", "在大连海事大学读硕士研究生。")
    assert facts.search("养猫") == []


def test_the_substring_pass_reaches_another_agents_fact(facts):
    """The LIKE pass is a second query, so it carries its own WHERE clause —
    and now that clause is empty, like the FTS one. A CJK fact recorded by one
    Agent has to be reachable from another, or half of the shared memory would
    only be shared for Latin text."""
    facts.add("user", "在大连海事大学读硕士研究生。")
    assert SqliteFactStore(facts.conn, agent_id="research").search("大连海事大学") == [
        "[user] 在大连海事大学读硕士研究生。"
    ]


def test_the_gate_query_carries_the_fact_into_the_turn(tmp_path):
    """One step short of the prompt, and the step the user actually saw fail:
    门控选词 → 两个库检索 → 事实进到模型拿到的那段文字里."""
    from evals.helpers import ScriptedClient, response, text_block
    from knowme.config import load_settings
    from knowme.memory import Memory

    settings = load_settings()
    settings.ensure_home()
    gate = response([text_block(
        '{"retrieve": true, "query": "上学 学校 大学", "reason": "where the user studies"}')])
    mem = Memory(connect(tmp_path), settings, ScriptedClient([gate]))
    mem.facts.add("user", "在大连海事大学读硕士研究生。")

    assert "大连海事大学" in mem.gated_retrieve("我在哪读书？")
