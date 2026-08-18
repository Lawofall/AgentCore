"""Tests for the grep tool (workspace content search) and its path guard.

Filesystem-backed but hermetic: every test builds a throwaway tree under
``tmp_path`` and points the tool's workspace at it, so nothing escapes the
sandbox and no real repo files are read.
"""

from pathlib import Path

from agentcore.tools.builtin.grep import GrepTool
from agentcore.tools.protocol import ToolContext
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace._paths import (
    normalize_glob,
    normalize_workspace_path,
    resolve_safe_path,
    strip_root_label_prefix,
)
from agentcore.workspace.server import ServerWorkspace


def _ctx(workspace: Path) -> ToolContext:
    return ToolContext.create(
        execution_id="e",
        run_id="s",
        agent_id="a",
        backend=ServerWorkspace(root=workspace, sandbox=SubprocessSandbox()),
        user_id="u",
    )


def _seed(root: Path) -> None:
    """A small, representative workspace tree."""
    (root / "app.py").write_text(
        "def add(a, b):\n    return a + b  # TODO: validate\n", encoding="utf-8"
    )
    (root / "util.py").write_text("VALUE = 42\nprint('todo later')\n", encoding="utf-8")
    (root / "notes.md").write_text("# Notes\nSee TODO in app.py\n", encoding="utf-8")
    sub = root / "src"
    sub.mkdir()
    (sub / "main.ts").write_text("const x = 1; // TODO ts\n", encoding="utf-8")
    # noise dir that must be pruned
    nm = root / "node_modules" / "dep"
    nm.mkdir(parents=True)
    (nm / "index.js").write_text("// TODO inside node_modules\n", encoding="utf-8")


# --- validation / failure paths ---


async def test_grep_requires_pattern(tmp_path: Path):
    result = await GrepTool().execute({}, _ctx(tmp_path))
    assert result.success is False
    assert "pattern" in result.error


async def test_grep_access_permission_is_policy_retire():
    """「没有访问权限」→ permission class + retire grep (not transient 2/3)."""
    from agentcore.workspace.protocol import WorkspaceError

    class _DeniedBackend:
        async def grep(self, query):  # noqa: ARG002
            raise WorkspaceError("没有访问权限")

    ctx = ToolContext.create(
        execution_id="e",
        run_id="s",
        agent_id="a",
        backend=_DeniedBackend(),  # type: ignore[arg-type]
        user_id="u",
    )
    result = await GrepTool().execute({"pattern": "TODO"}, ctx)
    assert result.success is False
    assert result.metadata.get("policy_failure") is True
    assert result.metadata.get("error_class") == "permission"
    assert result.metadata.get("permission_kind") == "access"
    assert result.metadata.get("retire_tools") == ["grep"]


async def test_grep_rejects_invalid_regex(tmp_path: Path):
    result = await GrepTool().execute({"pattern": "("}, _ctx(tmp_path))
    assert result.success is False
    err = result.error or ""
    assert "正则" in err
    assert "unclosed group" in err


async def test_grep_literal_newline_pattern_keeps_rg_reason(tmp_path: Path):
    (tmp_path / "a.txt").write_text("foo\nbar\n", encoding="utf-8")
    result = await GrepTool().execute({"pattern": "foo\nbar"}, _ctx(tmp_path))
    assert result.success is False
    err = result.error or ""
    assert "正则" in err
    assert "literal" in err.lower()
    assert "\\n" in err


def test_regex_error_message_keeps_multiline_rg_diagnostic():
    from agentcore.workspace.rg_grep import _regex_error_message

    stderr = (
        "rg: regex parse error:\n"
        "    (?:()\n"
        "    ^\n"
        "error: unclosed group\n"
    )
    msg = _regex_error_message(stderr)
    assert msg is not None
    assert msg.startswith("正则表达式无效：")
    assert "unclosed group" in msg
    assert "^" in msg
    assert msg != "正则表达式无效：rg: regex parse error:"


def test_regex_error_message_keeps_literal_newline_diagnostic():
    from agentcore.workspace.rg_grep import _regex_error_message

    stderr = (
        'rg: the literal "\\n" is not allowed in a regex\n\n'
        "Consider enabling multiline mode with the --multiline flag "
        "(or -U for short).\n"
        "When multiline mode is enabled, new line characters can be matched.\n"
    )
    msg = _regex_error_message(stderr)
    assert msg is not None
    assert 'literal "\\n"' in msg
    assert "multiline" in msg.lower()


def test_grep_schema_forbids_literal_newline_as_regex():
    desc = GrepTool().schema.parameters["properties"]["pattern"]["description"]
    assert "禁止把字面" in desc
    assert "\\n" in desc


async def test_grep_rejects_path_outside_workspace(tmp_path: Path):
    ws = tmp_path / "ws"
    ws.mkdir()
    result = await GrepTool().execute({"pattern": "x", "path": "../"}, _ctx(ws))
    assert result.success is False
    assert "超出了工作区范围" in result.error
    # actionable: names the relative-path fix and gives a concrete example
    assert "工作区相对路径" in result.error
    assert "AgentCore/文档/research/report.md" in result.error


async def test_grep_normalizes_absolute_workspace_path(tmp_path: Path):
    _seed(tmp_path)
    # an absolute /workspace/... scope (rejected before) is normalized to the root.
    result = await GrepTool().execute(
        {"pattern": "TODO", "path": "/workspace"}, _ctx(tmp_path)
    )
    assert result.success is True
    assert "app.py:2: return a + b  # TODO: validate" in result.output


async def test_grep_bare_slash_means_workspace_root(tmp_path: Path):
    """Bare ``/`` (and ``\\``) must mean whole workspace — not OutsideWorkspace."""
    _seed(tmp_path)
    for scope in ("/", "\\"):
        result = await GrepTool().execute(
            {"pattern": "TODO", "path": scope}, _ctx(tmp_path)
        )
        assert result.success is True, scope
        assert "app.py:2: return a + b  # TODO: validate" in result.output


async def test_grep_rejects_true_absolute_escapes(tmp_path: Path):
    _seed(tmp_path)
    result = await GrepTool().execute(
        {"pattern": "TODO", "path": "/etc"}, _ctx(tmp_path)
    )
    assert result.success is False
    assert "超出了工作区范围" in (result.error or "")


async def test_grep_rejects_missing_path(tmp_path: Path):
    result = await GrepTool().execute({"pattern": "x", "path": "nope.txt"}, _ctx(tmp_path))
    assert result.success is False
    assert "不存在" in result.error
    assert "父目录" in result.error or "上级目录也找不到" in result.error
    assert "原样重试" not in result.error
    assert "反复重试" in result.error


async def test_grep_missing_dir_with_parent_gives_landmark(tmp_path: Path):
    """grep 假目录但上级可列：同层样本纠偏（如 apps/server/src → agentcore）。"""
    server = tmp_path / "apps" / "server"
    server.mkdir(parents=True)
    (server / "agentcore").mkdir()
    (server / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    result = await GrepTool().execute(
        {"pattern": "x", "path": "apps/server/src"}, _ctx(tmp_path)
    )
    assert result.success is False
    assert result.error is not None
    assert result.error.startswith("路径不存在：apps/server/src")
    assert "父目录" in result.error
    assert "apps/server/" in result.error
    assert "agentcore" in result.error or "pyproject.toml" in result.error
    assert "反复重试" in result.error


# --- core search behavior ---


async def test_grep_finds_matches_with_path_and_lineno(tmp_path: Path):
    _seed(tmp_path)
    result = await GrepTool().execute({"pattern": "TODO"}, _ctx(tmp_path))
    assert result.success is True
    # ripgrep-style "rel:lineno: text" with forward slashes, sorted by file
    assert "app.py:2: return a + b  # TODO: validate" in result.output
    assert "src/main.ts:1: const x = 1; // TODO ts" in result.output
    # case-sensitive by default: 'todo later' must NOT match 'TODO'
    assert "util.py" not in result.output


async def test_grep_prunes_noise_dirs(tmp_path: Path):
    _seed(tmp_path)
    # Also plant ``.pytest_tmp`` (Windows lock poison) — must be product-ignored.
    pt = tmp_path / ".pytest_tmp" / "x"
    pt.mkdir(parents=True)
    (pt / "hidden.py").write_text("# TODO inside pytest_tmp\n", encoding="utf-8")
    result = await GrepTool().execute({"pattern": "TODO"}, _ctx(tmp_path))
    assert "node_modules" not in result.output
    assert ".pytest_tmp" not in result.output
    assert "hidden.py" not in result.output


async def test_grep_soft_skips_rg_access_denied(monkeypatch, tmp_path: Path):
    """rg exit-2 IO denials become warnings — search still succeeds (no retire)."""
    from agentcore.workspace import rg_grep as rg_mod

    (tmp_path / "ok.py").write_text("TODO here\n", encoding="utf-8")

    async def fake_run_rg(rg, args, *, cwd):
        del rg
        # ``--files`` listing: pretend one path is denied but still emit ok.py
        if "--files" in args:
            return (
                2,
                "ok.py\n",
                "rg: ./poison: Access is denied. (os error 5)\n",
            )
        # content search
        if any(a == "ok.py" or a.endswith("ok.py") for a in args):
            return (0, "ok.py:1:TODO here\n", "")
        return (1, "", "")

    monkeypatch.setattr(rg_mod, "_run_rg", fake_run_rg)
    monkeypatch.setattr(
        rg_mod,
        "resolve_rg_binary",
        lambda: tmp_path / "fake-rg",
    )
    # require_rg_binary uses resolve; also stub file check via require path
    monkeypatch.setattr(rg_mod, "require_rg_binary", lambda: tmp_path / "fake-rg")

    result = await GrepTool().execute({"pattern": "TODO"}, _ctx(tmp_path))
    assert result.success is True
    assert "ok.py" in (result.output or "")
    assert "跳过无权限" in (result.output or "")
    assert result.metadata.get("retire_tools") is None


async def test_grep_glob_filters_by_name(tmp_path: Path):
    _seed(tmp_path)
    result = await GrepTool().execute({"pattern": "TODO", "glob": "*.py"}, _ctx(tmp_path))
    assert "app.py" in result.output
    assert "main.ts" not in result.output
    assert "notes.md" not in result.output


async def test_grep_glob_strips_recursive_prefix(tmp_path: Path):
    _seed(tmp_path)
    result = await GrepTool().execute({"pattern": "TODO", "glob": "**/*.ts"}, _ctx(tmp_path))
    assert "src/main.ts" in result.output
    assert "app.py" not in result.output


async def test_grep_case_insensitive(tmp_path: Path):
    _seed(tmp_path)
    result = await GrepTool().execute({"pattern": "todo", "case_insensitive": True}, _ctx(tmp_path))
    assert "util.py:2" in result.output  # 'todo later'
    assert "app.py:2" in result.output  # 'TODO: validate'


async def test_grep_scopes_to_subdirectory(tmp_path: Path):
    _seed(tmp_path)
    result = await GrepTool().execute({"pattern": "TODO", "path": "src"}, _ctx(tmp_path))
    assert "src/main.ts" in result.output
    assert "app.py" not in result.output


async def test_grep_path_can_be_single_file(tmp_path: Path):
    """``path`` may name a single file (rg PATTERN FILE) — scan just that file."""
    _seed(tmp_path)
    result = await GrepTool().execute({"pattern": "TODO", "path": "app.py"}, _ctx(tmp_path))
    assert result.success is True
    assert "app.py:2: return a + b  # TODO: validate" in result.output
    # scoped to the one file — sibling matches must not leak in
    assert "src/main.ts" not in result.output
    assert "notes.md" not in result.output


async def test_grep_single_file_path_ignores_glob(tmp_path: Path):
    """When ``path`` is a file, ``glob`` is moot — the file is already pinpointed."""
    _seed(tmp_path)
    result = await GrepTool().execute(
        {"pattern": "TODO", "path": "app.py", "glob": "*.ts"}, _ctx(tmp_path)
    )
    assert result.success is True
    assert "app.py:2" in result.output


async def test_grep_files_only_lists_files_with_counts(tmp_path: Path):
    _seed(tmp_path)
    result = await GrepTool().execute({"pattern": "TODO", "files_only": True}, _ctx(tmp_path))
    assert result.success is True
    assert "个文件匹配" in result.output
    assert "app.py: 1" in result.output
    # files_only must not emit individual line bodies
    assert "return a + b" not in result.output


async def test_grep_no_matches(tmp_path: Path):
    _seed(tmp_path)
    result = await GrepTool().execute({"pattern": "zzz_nope"}, _ctx(tmp_path))
    assert result.success is True
    assert "未匹配" in result.output or "没有匹配" in result.output
    assert "可执行下一步" in result.output
    assert "code_search" in result.output
    assert result.metadata["match_count"] == 0


async def test_grep_skips_binary_files(tmp_path: Path):
    (tmp_path / "blob.bin").write_bytes(b"\xff\xfe\x00\x01 needle here \x00")
    (tmp_path / "ok.txt").write_text("needle here\n", encoding="utf-8")
    result = await GrepTool().execute({"pattern": "needle"}, _ctx(tmp_path))
    assert "ok.txt:1" in result.output
    assert "blob.bin" not in result.output


async def test_grep_truncates_at_max_results(tmp_path: Path):
    (tmp_path / "many.txt").write_text("hit\n" * 10, encoding="utf-8")
    result = await GrepTool().execute({"pattern": "hit", "max_results": 3}, _ctx(tmp_path))
    assert "[结果已截断" in result.output
    # 3 matching lines + summary header + truncation note
    body_lines = [ln for ln in result.output.splitlines() if ln.startswith("many.txt:")]
    assert len(body_lines) == 3


async def test_grep_truncation_order_is_stable(tmp_path: Path):
    """Hits are sorted by (path, line) before the result cap — same order both ends."""
    (tmp_path / "b.txt").write_text("hit\n", encoding="utf-8")
    (tmp_path / "a.txt").write_text("hit\nhit\n", encoding="utf-8")
    (tmp_path / "c.txt").write_text("hit\n", encoding="utf-8")
    result = await GrepTool().execute({"pattern": "hit", "max_results": 2}, _ctx(tmp_path))
    assert result.success is True
    assert "[结果已截断" in result.output
    body = [ln for ln in result.output.splitlines() if ":hit" in ln or ln.endswith(": hit")]
    # path-sorted: a.txt lines first
    assert body[0].startswith("a.txt:1:")
    assert body[1].startswith("a.txt:2:")


async def test_grep_missing_rg_binary_fails_explicitly(tmp_path: Path, monkeypatch):
    _seed(tmp_path)
    monkeypatch.setattr(
        "agentcore.workspace.rg_grep.resolve_rg_binary",
        lambda: None,
    )
    monkeypatch.delenv("AGENTCORE_RG_PATH", raising=False)
    result = await GrepTool().execute({"pattern": "TODO"}, _ctx(tmp_path))
    assert result.success is False
    assert "ripgrep" in (result.error or "").lower() or "rg" in (result.error or "").lower()


# --- normalize_glob ---


def test_normalize_glob_reduces_to_name_pattern():
    assert normalize_glob("*.py") == "*.py"
    assert normalize_glob("**/*.py") == "*.py"
    assert normalize_glob("src/**/*.ts") == "*.ts"
    assert normalize_glob("") is None
    assert normalize_glob("   ") is None


# --- resolve_safe_path (workspace boundary) ---


def test_resolve_safe_path_allows_root_and_children(tmp_path: Path):
    assert resolve_safe_path(tmp_path, ".") == tmp_path.resolve()
    child = resolve_safe_path(tmp_path, "a/b.txt")
    assert child is not None
    assert child == (tmp_path / "a" / "b.txt").resolve()


def test_resolve_safe_path_blocks_parent_escape(tmp_path: Path):
    assert resolve_safe_path(tmp_path, "../secret") is None
    assert resolve_safe_path(tmp_path, "../../etc/passwd") is None


def test_resolve_safe_path_blocks_prefix_sibling(tmp_path: Path):
    """A sibling dir that shares the workspace name as a string prefix must not
    be reachable — the bug a naive ``startswith`` check would let through."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (tmp_path / "ws-evil").mkdir()
    assert resolve_safe_path(ws, "../ws-evil") is None
    assert resolve_safe_path(ws, "../ws-evil/loot.txt") is None


# --- strip_root_label_prefix (absolute /workspace/... rescue, pure string) ---


def test_strip_root_label_prefix_rewrites_absolute_label_path():
    assert strip_root_label_prefix("/workspace/foo/bar.md", "workspace") == "foo/bar.md"
    # the root label alone maps to the workspace-root marker
    assert strip_root_label_prefix("/workspace", "workspace") == "."
    assert strip_root_label_prefix("/workspace/", "workspace") == "."


def test_strip_root_label_prefix_leaves_relative_and_other_roots_untouched():
    # genuine relative paths are never rewritten — even when the first segment
    # coincidentally equals the label (a real subdir, not an absolute escape).
    assert strip_root_label_prefix("AgentCore/文档/research/report.md", "workspace") == "AgentCore/文档/research/report.md"
    assert strip_root_label_prefix("workspace/foo", "workspace") == "workspace/foo"
    assert strip_root_label_prefix(".", "workspace") == "."
    # a different absolute root is returned verbatim so the guard still refuses it
    assert strip_root_label_prefix("/etc/passwd", "workspace") == "/etc/passwd"


def test_strip_root_label_prefix_keeps_traversal_for_downstream_guard():
    # normalization must not defuse ``..`` — it only strips the leading label segment.
    assert strip_root_label_prefix("/workspace/../secret", "workspace") == "../secret"


def test_strip_root_label_prefix_honors_custom_label():
    assert strip_root_label_prefix("/proj/a.md", "proj") == "a.md"
    # the default label is NOT special once a custom one is configured
    assert strip_root_label_prefix("/workspace/a.md", "proj") == "/workspace/a.md"


# --- normalize_workspace_path (bare root aliases + label strip) ---


def test_normalize_workspace_path_bare_root_aliases():
    assert normalize_workspace_path("/") == "."
    assert normalize_workspace_path("\\") == "."
    assert normalize_workspace_path("") == "."
    assert normalize_workspace_path(".") == "."


def test_normalize_workspace_path_strips_root_label():
    assert normalize_workspace_path("/workspace/foo.md", root_label="workspace") == "foo.md"
    assert normalize_workspace_path("/workspace", root_label="workspace") == "."
    assert normalize_workspace_path("/etc/passwd", root_label="workspace") == "/etc/passwd"
    assert normalize_workspace_path("a/b", root_label="workspace") == "a/b"


def test_resolve_safe_path_bare_slash_is_workspace_root(tmp_path: Path):
    assert resolve_safe_path(tmp_path, "/", root_label="workspace") == tmp_path.resolve()
    assert resolve_safe_path(tmp_path, "\\", root_label="workspace") == tmp_path.resolve()
    # Without root_label, bare / still maps to root (alias is label-independent).
    assert resolve_safe_path(tmp_path, "/") == tmp_path.resolve()


# --- resolve_safe_path with root_label (normalize then contain) ---


def test_resolve_safe_path_normalizes_absolute_root_label(tmp_path: Path):
    # /workspace/x.md was always rejected before; it now resolves in-tree.
    resolved = resolve_safe_path(tmp_path, "/workspace/x.md", root_label="workspace")
    assert resolved == (tmp_path / "x.md").resolve()
    # /workspace alone points at the root itself.
    root = resolve_safe_path(tmp_path, "/workspace", root_label="workspace")
    assert root == tmp_path.resolve()


def test_resolve_safe_path_normalization_still_blocks_escapes(tmp_path: Path):
    # a non-label absolute path stays rejected
    assert resolve_safe_path(tmp_path, "/etc/passwd", root_label="workspace") is None
    # label-prefixed traversal stays rejected (containment guard is untouched)
    assert resolve_safe_path(tmp_path, "/workspace/../x", root_label="workspace") is None


def test_resolve_safe_path_relative_unchanged_with_root_label(tmp_path: Path):
    # relative paths behave identically whether or not root_label is set
    child = resolve_safe_path(tmp_path, "AgentCore/文档/research/report.md", root_label="workspace")
    assert child == (tmp_path / "AgentCore" / "文档" / "research" / "report.md").resolve()
    assert resolve_safe_path(tmp_path, "../evil", root_label="workspace") is None


def test_resolve_safe_path_without_root_label_is_legacy(tmp_path: Path):
    # opting out (no root_label) preserves the exact prior behavior: an absolute
    # /workspace/... path is still rejected.
    assert resolve_safe_path(tmp_path, "/workspace/x.md") is None


def test_resolve_safe_path_custom_label_normalization(tmp_path: Path):
    resolved = resolve_safe_path(tmp_path, "/proj/a.md", root_label="proj")
    assert resolved == (tmp_path / "a.md").resolve()
    # default "workspace" is not honored when a custom label is configured
    assert resolve_safe_path(tmp_path, "/workspace/a.md", root_label="proj") is None
