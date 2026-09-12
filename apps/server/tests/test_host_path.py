"""Classify host vs workspace paths for file tools (no mount FC tool)."""

from agentcore.workspace.host_path import (
    classify_tool_path,
    is_forbidden_host_root,
    mode_covers,
    split_host_parent,
    workspace_rel_under_disk_root,
)


def test_relative_and_dot_are_workspace():
    assert classify_tool_path("docs/a.md").kind == "workspace"
    assert classify_tool_path(".").kind == "workspace"
    assert classify_tool_path("Downloads").kind == "workspace"


def test_workspace_root_alias_is_not_host():
    assert classify_tool_path("/").kind == "workspace"
    assert classify_tool_path("/workspace/research/x.md").kind == "workspace"
    assert classify_tool_path("/workspace/research/x.md", root_label="conv:x").kind == "workspace"
    assert classify_tool_path("/conv:x/a.md", root_label="conv:x").kind == "workspace"


def test_external_namespace_passthrough():
    assert classify_tool_path("external/desk/a.md").kind == "external_ns"
    assert classify_tool_path("external").kind == "external_ns"


def test_well_known_home_prefix():
    got = classify_tool_path("~/Downloads/foo.pdf")
    assert got.kind == "host"
    assert got.well_known == "downloads"
    assert got.target_name == "foo.pdf"
    assert got.remainder == ""

    nested = classify_tool_path("~/Desktop/咨询/report.md")
    assert nested.kind == "host"
    assert nested.well_known == "desktop"
    assert nested.target_name == "咨询"
    assert nested.remainder == "report.md"

    root = classify_tool_path("~/Documents")
    assert root.kind == "host"
    assert root.well_known == "documents"
    assert root.target_name is None


def test_windows_userprofile_well_known():
    got = classify_tool_path("%USERPROFILE%\\Downloads\\a.pdf")
    assert got.kind == "host"
    assert got.well_known == "downloads"
    assert got.target_name == "a.pdf"


def test_other_tilde_is_forbidden():
    got = classify_tool_path("~/secret")
    assert got.kind == "forbidden"
    assert got.forbidden_reason == "home_not_well_known"


def test_absolute_windows_and_posix():
    win = classify_tool_path(r"D:\资料\报告.md")
    assert win.kind == "host"
    assert win.abs_path == r"D:\资料\报告.md"
    posix = classify_tool_path("/Users/me/Downloads/a.pdf")
    assert posix.kind == "host"


def test_forbidden_drive_and_unix_root():
    assert is_forbidden_host_root("/")
    assert is_forbidden_host_root("C:\\")
    assert is_forbidden_host_root("C:/")
    assert classify_tool_path("/").kind == "workspace"
    assert classify_tool_path("C:\\").kind == "forbidden"


def test_split_host_parent():
    parent, name = split_host_parent(r"D:\资料\报告.md")
    assert name == "报告.md"
    assert "资料" in parent
    p2, n2 = split_host_parent("/tmp/foo.pdf")
    assert n2 == "foo.pdf"
    assert p2 == "/tmp"


def test_workspace_rel_under_disk_root(tmp_path):
    child = tmp_path / "src" / "a.md"
    child.parent.mkdir()
    child.write_text("x", encoding="utf-8")
    assert workspace_rel_under_disk_root(str(tmp_path), tmp_path) == "."
    assert workspace_rel_under_disk_root(str(child), tmp_path) == "src/a.md"
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    assert workspace_rel_under_disk_root(str(outside), tmp_path) is None
    assert workspace_rel_under_disk_root("docs/a.md", tmp_path) is None
    assert workspace_rel_under_disk_root("~/Downloads", tmp_path) is None


def test_mode_covers_rank():
    assert mode_covers("readonly", "readonly")
    assert not mode_covers("readonly", "organize")
    assert mode_covers("organize", "organize")
    assert not mode_covers("organize", "attach_rw")
    assert mode_covers("attach_rw", "organize")
    assert not mode_covers(None, "organize")
