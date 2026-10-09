import pytest

from ai_operator.tools import TOOLS, ToolContext, ToolError, get_tool, run_tool


def test_unknown_tool():
    assert get_tool("does_not_exist") is None
    with pytest.raises(ToolError):
        run_tool("does_not_exist", ToolContext(), {})


def test_all_tools_have_valid_permissions():
    for tool in TOOLS.values():
        assert tool.spec.permission in ("read", "reversible_write", "irreversible_write")
        assert tool.spec.risk_level in ("low", "medium", "high")


def test_read_file_cannot_escape_data_dir(tmp_path):
    ctx = ToolContext(base_url="http://x", data_dir=str(tmp_path))
    with pytest.raises(ToolError):
        run_tool("read_file", ctx, {"path": "/etc/hosts"})


def test_read_file_inside_data_dir(tmp_path):
    (tmp_path / "note.txt").write_text("hello")
    ctx = ToolContext(base_url="http://x", data_dir=str(tmp_path))
    assert run_tool("read_file", ctx, {"path": "note.txt"}) == "hello"
