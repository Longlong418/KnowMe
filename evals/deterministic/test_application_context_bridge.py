"""The Application Context Bridge is bounded, isolated and easy to inspect."""

from knowme.applications import ApplicationContextBridge


def test_reader_snapshot_renders_for_the_matching_agent_and_session():
    bridge = ApplicationContextBridge()
    bridge.publish(
        agent_id="learning",
        session_id="s-1",
        application="reader",
        resource="essay.md",
        content="The whole essay",
        selection="a useful sentence",
        metadata={"page": 3},
    )

    rendered = bridge.render("learning", "s-1")
    assert "Application: reader" in rendered
    assert "Current resource: essay.md" in rendered
    assert "The whole essay" in rendered
    assert "Current selection:\na useful sentence" in rendered
    assert "page=3" in rendered
    assert bridge.render("coding", "s-1") == ""
    assert bridge.render("learning", "s-2") == ""


def test_snapshot_is_bounded_before_it_reaches_the_model():
    bridge = ApplicationContextBridge(max_content_chars=5, max_selection_chars=3)
    state = bridge.publish(
        agent_id="default",
        session_id="default",
        application="reader",
        content="abcdefgh",
        selection="12345",
    )

    assert state.content == "abcde"
    assert state.selection == "123"
    assert "fgh" not in bridge.render("default", "default")


def test_clear_removes_only_one_conversations_transient_state():
    bridge = ApplicationContextBridge()
    for session_id in ("one", "two"):
        bridge.publish(
            agent_id="default",
            session_id=session_id,
            application="reader",
            selection=session_id,
        )

    assert bridge.clear("default", "one") is True
    assert bridge.render("default", "one") == ""
    assert "two" in bridge.render("default", "two")
    assert bridge.clear("default", "one") is False
