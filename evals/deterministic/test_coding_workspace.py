"""Coding Workspace MVP: safe browsing and Context-ready file reads."""

from pathlib import Path

from knowme.applications.coding_workspace import (
    list_entries,
    read_file,
    workspace_action,
)


def test_workspace_lists_text_files_but_not_secrets_or_generated_dirs(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print('ok')", encoding="utf-8")
    (tmp_path / ".env").write_text("API_KEY=secret", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("secret", encoding="utf-8")
    (tmp_path / "image.bin").write_bytes(b"\x00\x01")

    paths = {entry["path"] for entry in list_entries(tmp_path)}
    assert "src" in paths
    assert "src/main.py" in paths
    assert ".env" not in paths
    assert ".git/config" not in paths
    assert "image.bin" not in paths


def test_workspace_read_is_confined_and_reports_file_metadata(tmp_path):
    (tmp_path / "README.md").write_text("# hello", encoding="utf-8")
    result = read_file("README.md", tmp_path)
    assert result["content"] == "# hello"
    assert result["path"] == "README.md"
    assert result["truncated"] is False

    try:
        read_file("../outside.txt", tmp_path)
    except ValueError as exc:
        assert "outside" in str(exc)
    else:
        raise AssertionError("path traversal should be rejected")


def test_workspace_action_returns_json_friendly_errors(tmp_path, monkeypatch):
    monkeypatch.setenv("KNOWME_PROJECT_ROOT", str(tmp_path))
    (tmp_path / "app.py").write_text("main()", encoding="utf-8")
    listed = workspace_action({"action": "list", "agent_id": "coding"})
    assert listed["ok"] is True
    assert listed["root"] == str(Path(tmp_path).resolve())
    opened = workspace_action({"action": "read", "path": "app.py", "agent_id": "coding"})
    assert opened["file"]["content"] == "main()"
    unknown = workspace_action({"action": "terminal", "agent_id": "coding"})
    assert unknown["ok"] is False


def test_dashboard_workspace_action_validates_the_selected_agent(tmp_path, monkeypatch):
    from knowme.ops import dashboard

    monkeypatch.setenv("KNOWME_PROJECT_ROOT", str(tmp_path))
    assert dashboard.workspace_action({"action": "list", "agent_id": "coding"})["ok"] is True
    try:
        dashboard.workspace_action({"action": "list", "agent_id": "not-an-agent"})
    except ValueError as exc:
        assert "unknown agent" in str(exc)
    else:
        raise AssertionError("unknown agents must not access the workspace")


def test_opened_code_uses_the_same_context_bridge_contract():
    from knowme.applications.context_bridge import ApplicationContextBridge

    bridge = ApplicationContextBridge()
    bridge.publish(
        agent_id="coding", session_id="s-coding",
        application="coding", resource="src/main.py",
        content="print('ok')", selection="",
    )
    rendered = bridge.render("coding", "s-coding")
    assert "Application: coding" in rendered
    assert "Current resource: src/main.py" in rendered
    assert "print('ok')" in rendered
