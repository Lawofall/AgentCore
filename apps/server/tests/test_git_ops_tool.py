"""Regression tests for GitTool safety guards (``tools/builtin/git_ops``).

Pins the write-path hard rejects that catalog/approval tests do not cover:
forbidden subcommands, protected-branch commits, add-path policy,
and branch/checkout argument handling. Hermetic: throwaway repos under ``tmp_path``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Literal

import pytest

from agentcore.tools.builtin.git_ops import (
    _ALLOWED_SUBCOMMANDS,
    _AUTH_FAILURE_HINT,
    _FORBIDDEN_PATTERNS,
    _PROTECTED_BRANCHES,
    GIT_PHASES,
    PHASE_CREDENTIALS,
    PHASE_LOCAL,
    PHASE_QUEUED,
    PHASE_REMOTE,
    GitTool,
    _cloud_network_extra_env,
    _git_subprocess_env,
    _looks_like_auth_failure,
    _looks_like_unusable_repo,
    _validate_add_paths,
    git_write_subcommands,
)
from agentcore.tools.protocol import ToolContext
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace.git_credentials import GitAuthMaterial
from agentcore.workspace.server import ServerWorkspace
from agentcore.workspace.write_claims import WriteCoordinator

pytestmark = pytest.mark.skipif(not shutil.which("git"), reason="git not installed")

_GIT_ENV = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}


def test_auth_failure_hint_detects_common_markers():
    assert _looks_like_auth_failure("fatal: Authentication failed for 'https://…'")
    assert _looks_like_auth_failure("remote: HTTP Basic: Access denied")
    assert not _looks_like_auth_failure("fatal: not a git repository")
    assert "设置 → Git 凭据" in _AUTH_FAILURE_HINT


def test_unusable_repo_markers_stay_off_ordinary_git_failures():
    assert _looks_like_unusable_repo(
        "fatal: not a git repository (or any of the parent directories): .git"
    )
    assert _looks_like_unusable_repo(
        "fatal: this operation must be run in a work tree"
    )
    assert _looks_like_unusable_repo("error: object file .git/objects/ab/cd is corrupt")
    # Everyday failures keep their own attribution — no repo-corruption claim.
    assert not _looks_like_unusable_repo("fatal: Authentication failed")
    assert not _looks_like_unusable_repo("CONFLICT (content): Merge conflict in a.txt")
    assert not _looks_like_unusable_repo("fatal: bad revision 'HEAD~9'")


def test_git_subprocess_env_disables_optional_locks(tmp_path: Path):
    """Read-only git must not refresh the index — that is what queues on index.lock."""
    env = _git_subprocess_env(str(tmp_path), None)
    assert env["GIT_OPTIONAL_LOCKS"] == "0"
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert env["GIT_CEILING_DIRECTORIES"] == str(tmp_path.resolve())
    merged = _git_subprocess_env(str(tmp_path), {"GIT_CONFIG_COUNT": "1"})
    assert merged["GIT_CONFIG_COUNT"] == "1"
    assert merged["GIT_OPTIONAL_LOCKS"] == "0"


async def test_run_git_spawns_with_optional_locks_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The env helper is the env the child actually gets (desktop gitRun parity)."""
    import asyncio

    # Aliased: this module's own ``_run_git`` helper shadows the tool's spawn.
    from agentcore.tools.builtin.git_ops import _run_git as spawn_run_git

    repo = _init_repo(tmp_path / "repo")
    captured: list[dict[str, str]] = []
    captured_stdin: list[object] = []
    real_exec = asyncio.create_subprocess_exec

    async def _capture(*args: Any, **kwargs: Any):
        captured.append(dict(kwargs.get("env") or {}))
        captured_stdin.append(kwargs.get("stdin"))
        return await real_exec(*args, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _capture)
    _stdout, _stderr, code = await spawn_run_git(["status", "-sb"], cwd=str(repo))
    assert code == 0
    assert captured
    assert captured[0]["GIT_OPTIONAL_LOCKS"] == "0"
    assert captured[0]["GIT_TERMINAL_PROMPT"] == "0"
    # Sidecar stdin is the JSON-RPC pipe; inheriting it stalls git until timeout.
    assert captured_stdin
    assert captured_stdin[0] is asyncio.subprocess.DEVNULL


def _run_git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        env=_GIT_ENV,
    )


def _init_repo(path: Path, *, branch: str = "feature/work") -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _run_git(path, "init", "-b", branch)
    _run_git(path, "config", "user.email", "tester@example.com")
    _run_git(path, "config", "user.name", "Tester")
    (path / "README.md").write_text("hello\n", encoding="utf-8")
    _run_git(path, "add", "README.md")
    _run_git(path, "commit", "-m", "init")
    return path


def _attach_bare_origin(repo: Path, bare: Path, *, branch: str) -> None:
    """Init bare remote, push ``branch``, point bare HEAD so clones check out that branch."""
    _run_git(repo.parent, "init", "--bare", str(bare))
    _run_git(repo, "remote", "add", "origin", str(bare))
    _run_git(repo, "push", "-u", "origin", branch)
    subprocess.run(
        ["git", "--git-dir", str(bare), "symbolic-ref", "HEAD", f"refs/heads/{branch}"],
        check=True,
        capture_output=True,
        env=_GIT_ENV,
    )


def _clone_from_bare(bare: Path, dest: Path) -> Path:
    _run_git(dest.parent, "clone", str(bare), str(dest))
    _run_git(dest, "config", "user.email", "tester@example.com")
    _run_git(dest, "config", "user.name", "Tester")
    return dest


def _ceo_ctx(workspace: Path) -> ToolContext:
    """CEO path: no worker-only coordination channels."""
    return ToolContext.create(
        execution_id="e",
        run_id="s",
        agent_id="ceo",
        backend=ServerWorkspace(root=workspace, sandbox=SubprocessSandbox()),
        user_id="u",
    )


def _assert_credential_helper_env(extra: dict[str, str] | None, *, username: str, token: str) -> None:
    assert extra is not None
    assert extra["GIT_CONFIG_COUNT"] == "1"
    assert extra["GIT_CONFIG_KEY_0"] == "credential.helper"
    helper = extra["GIT_CONFIG_VALUE_0"]
    assert f"username={username}" in helper
    assert f"password={token}" in helper


def _worker_ctx(
    workspace: Path,
    *,
    location: Literal["server", "local"] = "server",
    user_id: str = "u",
) -> ToolContext:
    """Worker path: write_coordinator present (same execute path as CEO)."""
    return ToolContext.create(
        execution_id="e",
        run_id="s",
        agent_id="worker",
        backend=ServerWorkspace(
            root=workspace, sandbox=SubprocessSandbox(), location=location
        ),
        user_id=user_id,
        write_coordinator=WriteCoordinator(),
    )


# --- allowlist / forbidden patterns ---


def test_forbidden_patterns_disjoint_from_allowlist():
    # Defense-in-depth: every hard-banned verb must stay outside the allowlist so a
    # future allowlist expansion cannot silently re-enable reset/clean.
    assert _FORBIDDEN_PATTERNS.isdisjoint(_ALLOWED_SUBCOMMANDS)
    assert {"reset", "clean"} <= _FORBIDDEN_PATTERNS
    assert {"rebase", "merge", "stash"} <= _ALLOWED_SUBCOMMANDS
    assert {"cherry-pick", "tag", "remote"} <= _ALLOWED_SUBCOMMANDS
    assert "push" not in _FORBIDDEN_PATTERNS
    assert "push" in _ALLOWED_SUBCOMMANDS
    assert "push" in git_write_subcommands()
    assert "pull" in _ALLOWED_SUBCOMMANDS
    assert "pull" in git_write_subcommands()
    assert "create_pr" in _ALLOWED_SUBCOMMANDS
    assert "create_pr" in git_write_subcommands()
    assert "clone" in _ALLOWED_SUBCOMMANDS
    assert "clone" in git_write_subcommands()
    assert "fetch" in _ALLOWED_SUBCOMMANDS
    assert "fetch" not in git_write_subcommands()
    assert {"show", "blame"} <= _ALLOWED_SUBCOMMANDS
    assert git_write_subcommands().isdisjoint({"fetch", "show", "blame"})
    assert {"merge", "rebase", "cherry-pick"} <= git_write_subcommands()
    assert {"stash", "tag", "remote"} <= git_write_subcommands()


@pytest.mark.parametrize("subcommand", sorted(_FORBIDDEN_PATTERNS))
async def test_forbidden_subcommands_are_rejected(tmp_path: Path, subcommand: str):
    result = await GitTool().execute({"subcommand": subcommand}, _ceo_ctx(tmp_path))
    assert result.success is False
    assert result.error
    # Allowlist rejects first today; forbidden-pattern message is the defense-in-depth
    # wording if a name ever lands in both sets. Either path must refuse.
    assert (
        "不在允许列表中" in result.error
        or "被安全策略拒绝" in result.error
    )


async def test_unknown_subcommand_rejected(tmp_path: Path):
    result = await GitTool().execute({"subcommand": "reflog"}, _ceo_ctx(tmp_path))
    assert result.success is False
    assert "不在允许列表中" in (result.error or "")


# --- CEO write: same execute path as worker (not a role deny) ---


@pytest.mark.parametrize(
    "subcommand",
    sorted(s for s in git_write_subcommands() if s not in {"init_baseline", "clone"}),
)
async def test_ceo_context_does_not_role_deny_write_subcommands(
    tmp_path: Path, subcommand: str
):
    _init_repo(tmp_path / "repo")
    args: dict[str, Any] = {"subcommand": subcommand}
    if subcommand == "add":
        args["paths"] = ["README.md"]
    elif subcommand == "commit":
        args["message"] = "x"
    elif subcommand in ("branch", "checkout"):
        args["branch"] = "other"
    elif subcommand in ("merge", "rebase", "cherry-pick"):
        args["ref"] = "other"
    elif subcommand == "stash":
        args["action"] = "push"
    elif subcommand == "tag":
        args["action"] = "create"
        args["name"] = "v0"
    elif subcommand == "remote":
        args["action"] = "add"
        args["name"] = "upstream"
        args["url"] = "https://example.com/repo.git"
    elif subcommand == "create_pr":
        args["title"] = "PR"
    result = await GitTool().execute(args, _ceo_ctx(tmp_path / "repo"))
    err = result.error or ""
    assert "delegate" not in err.lower()
    assert "Git 写入操作需通过" not in err
    if subcommand == "add":
        assert result.success is True


async def test_ceo_context_allows_read_status(tmp_path: Path):
    repo = _init_repo(tmp_path / "repo")
    result = await GitTool().execute({"subcommand": "status"}, _ceo_ctx(repo))
    assert result.success is True
    assert "当前分支" in result.output


async def test_status_refuses_parent_repo_when_workspace_has_no_git(tmp_path: Path):
    """Workspace nested under a parent git tree must not operate the parent repo.

    Reproduces host-path leak class: data_dir / scratch lives under the monorepo
    (e.g. ``C:/Project/...``); without a ceiling, ``git status`` would climb out.
    Read-only returns structured ``no_repo`` (success) — never a fake clean tree.
    """
    parent = _init_repo(tmp_path / "parent", branch="feature/parent")
    nested = parent / "nested_workspace"
    nested.mkdir()
    (nested / "notes.txt").write_text("scratch only\n", encoding="utf-8")
    assert not (nested / ".git").exists()

    # Sanity: plain git *would* see the parent work tree from nested cwd.
    climbed = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=nested,
        check=True,
        capture_output=True,
        text=True,
        env=_GIT_ENV,
    )
    assert Path(climbed.stdout.strip()).resolve() == parent.resolve()

    result = await GitTool().execute({"subcommand": "status"}, _ceo_ctx(nested))
    assert result.success is True
    assert result.metadata.get("code") == "no_repo"
    assert "没有 Git 仓库" in result.output
    # Must not echo parent branch / status as if nested were the repo.
    assert "feature/parent" not in (result.output or "")
    assert "当前分支" not in (result.output or "")
    assert "工作区干净" not in (result.output or "")


async def test_status_uses_workspace_repo_not_parent(tmp_path: Path):
    """When the workspace has its own ``.git``, operate that repo — not a parent."""
    parent = _init_repo(tmp_path / "parent", branch="feature/parent")
    nested = _init_repo(parent / "nested_repo", branch="feature/nested")
    result = await GitTool().execute({"subcommand": "status"}, _ceo_ctx(nested))
    assert result.success is True
    assert "feature/nested" in result.output
    assert "feature/parent" not in result.output


async def test_no_git_anywhere_reports_structured_no_repo(tmp_path: Path):
    bare = tmp_path / "not_a_repo"
    bare.mkdir()
    for sub in ("status", "diff", "log", "fetch", "show", "blame"):
        result = await GitTool().execute({"subcommand": sub}, _ceo_ctx(bare))
        assert result.success is True
        assert result.metadata.get("code") == "no_repo"
        assert "没有 Git 仓库" in result.output
        assert "工作区干净" not in result.output
        assert "无差异" not in result.output
        assert "无提交" not in result.output


async def test_status_timeout_is_hard_error_not_no_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """``.git`` present + the command hangs → error/timeout, never soft ``no_repo``."""
    import agentcore.tools.builtin.git_ops as git_mod

    repo = _init_repo(tmp_path / "repo")

    async def _fake_run(
        args: list[str],
        *,
        cwd: str,
        timeout: float = 20.0,
        extra_env: dict[str, str] | None = None,
        phase: str = PHASE_LOCAL,
    ) -> tuple[str, str, int]:
        # No pre-flight probe may run: the primary command owns the whole budget.
        assert args[0] == "status", f"unexpected git args: {args}"
        return "", f"git 操作超时（{' '.join(args)}）", 1

    monkeypatch.setattr(git_mod.spawn, "_run_git", _fake_run)
    result = await GitTool().execute({"subcommand": "status"}, _ceo_ctx(repo))
    assert result.success is False
    assert result.metadata.get("code") == "timeout"
    assert result.metadata.get("timeout_layer") == "inner"
    assert "超时" in (result.error or "")
    assert "勿原样重试" in (result.error or "")


async def test_status_on_unusable_repo_not_soft_no_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """``.git`` present but not a work tree → hard fail, not soft ``no_repo``."""
    import agentcore.tools.builtin.git_ops as git_mod

    repo = _init_repo(tmp_path / "repo")

    async def _fake_run(
        args: list[str],
        *,
        cwd: str,
        timeout: float = 20.0,
        extra_env: dict[str, str] | None = None,
        phase: str = PHASE_LOCAL,
    ) -> tuple[str, str, int]:
        assert args[0] == "status", f"unexpected git args: {args}"
        return (
            "",
            "fatal: not a git repository (or any of the parent directories): .git",
            128,
        )

    monkeypatch.setattr(git_mod.spawn, "_run_git", _fake_run)
    result = await GitTool().execute({"subcommand": "status"}, _ceo_ctx(repo))
    assert result.success is False
    assert result.metadata.get("code") == "repo_unusable"
    assert "not a git repository" in (result.error or "").lower()


@pytest.mark.parametrize(
    "args",
    [
        {"subcommand": "status"},
        {"subcommand": "add", "paths": ["README.md"]},
    ],
)
async def test_broken_gitdir_pointer_fails_honestly(tmp_path: Path, args: dict[str, Any]):
    """Real unusable repo (``.git`` file → dangling gitdir): honest error on read *and* write.

    Read-only must not soft-succeed here: ``no_repo`` means "no repository", and a
    broken one is a different, human-actionable fact.
    """
    ws = tmp_path / "broken"
    ws.mkdir()
    (ws / "README.md").write_text("x\n", encoding="utf-8")
    (ws / ".git").write_text("gitdir: ../nowhere/.git\n", encoding="utf-8")

    result = await GitTool().execute(args, _worker_ctx(ws))
    assert result.success is False
    assert result.metadata.get("code") == "repo_unusable"
    assert "没有 Git 仓库" not in (result.error or "")
    assert "工作树" in (result.error or "")


async def test_write_without_repo_still_hard_fails(tmp_path: Path):
    bare = tmp_path / "not_a_repo"
    bare.mkdir()
    (bare / "README.md").write_text("x\n", encoding="utf-8")
    result = await GitTool().execute(
        {"subcommand": "add", "paths": ["README.md"]},
        _worker_ctx(bare),
    )
    assert result.success is False
    assert "没有 Git 仓库" in (result.error or "")


# --- add path policy ---


@pytest.mark.parametrize(
    "paths,needle",
    [
        ([], "显式 paths"),
        (["."], "禁止 add 路径"),
        (["-A"], "禁止 add 路径"),
        (["--all"], "禁止 add 路径"),
        (["src/*.py"], "通配符"),
        (["foo?.txt"], "通配符"),
        ([""], "空路径"),
    ],
)
def test_validate_add_paths_rejects_dangerous_inputs(paths: list[str], needle: str):
    err = _validate_add_paths(paths, start=0.0)
    assert err is not None
    assert err.success is False
    assert needle in (err.error or "")


def test_validate_add_paths_accepts_explicit_files():
    assert _validate_add_paths(["src/a.py", "docs/readme.md"], start=0.0) is None


async def test_add_rejects_dot_via_execute(tmp_path: Path):
    repo = _init_repo(tmp_path / "repo")
    result = await GitTool().execute(
        {"subcommand": "add", "paths": ["."]},
        _worker_ctx(repo),
    )
    assert result.success is False
    assert "禁止 add" in (result.error or "")


# --- protected branches ---


@pytest.mark.parametrize("branch", sorted(_PROTECTED_BRANCHES))
async def test_commit_on_protected_branch_rejected(tmp_path: Path, branch: str):
    repo = _init_repo(tmp_path / "repo", branch=branch)
    (repo / "extra.txt").write_text("x\n", encoding="utf-8")
    _run_git(repo, "add", "extra.txt")
    result = await GitTool().execute(
        {"subcommand": "commit", "message": "should not land"},
        _worker_ctx(repo),
    )
    assert result.success is False
    assert "main/master" in (result.error or "")
    # Working tree still has the staged file — commit did not happen.
    status = subprocess.run(
        ["git", "status", "--short"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        env=_GIT_ENV,
    )
    assert "extra.txt" in status.stdout


async def test_commit_on_feature_branch_allowed(tmp_path: Path):
    repo = _init_repo(tmp_path / "repo", branch="feature/ok")
    (repo / "extra.txt").write_text("x\n", encoding="utf-8")
    result = await GitTool().execute(
        {"subcommand": "add", "paths": ["extra.txt"]},
        _worker_ctx(repo),
    )
    assert result.success is True
    result = await GitTool().execute(
        {"subcommand": "commit", "message": "add extra"},
        _worker_ctx(repo),
    )
    assert result.success is True
    assert "已提交" in result.output


# --- branch / checkout args ---


async def test_branch_requires_branch_name(tmp_path: Path):
    repo = _init_repo(tmp_path / "repo")
    result = await GitTool().execute({"subcommand": "branch"}, _worker_ctx(repo))
    assert result.success is False
    assert "branch 需要 branch 参数" in (result.error or "")


async def test_checkout_requires_branch_name(tmp_path: Path):
    repo = _init_repo(tmp_path / "repo")
    result = await GitTool().execute({"subcommand": "checkout"}, _worker_ctx(repo))
    assert result.success is False
    assert "checkout 需要 branch 参数" in (result.error or "")


@pytest.mark.parametrize("branch", ["-f", "--force", "-D"])
async def test_branch_rejects_option_like_names(tmp_path: Path, branch: str):
    # audit 05 P3-1: a ``-``-prefixed branch would be parsed by git as an option
    # (e.g. ``branch -f``); reject before it ever reaches argv.
    repo = _init_repo(tmp_path / "repo")
    result = await GitTool().execute(
        {"subcommand": "branch", "branch": branch},
        _worker_ctx(repo),
    )
    assert result.success is False
    assert "'-' 开头" in (result.error or "")


@pytest.mark.parametrize("branch", ["-f", "--force", "-D"])
async def test_checkout_rejects_option_like_names(tmp_path: Path, branch: str):
    repo = _init_repo(tmp_path / "repo")
    result = await GitTool().execute(
        {"subcommand": "checkout", "branch": branch},
        _worker_ctx(repo),
    )
    assert result.success is False
    assert "'-' 开头" in (result.error or "")


async def test_branch_creates_named_branch(tmp_path: Path):
    repo = _init_repo(tmp_path / "repo")
    result = await GitTool().execute(
        {"subcommand": "branch", "branch": "feature/new"},
        _worker_ctx(repo),
    )
    assert result.success is True
    assert "已创建分支 feature/new" in result.output
    branches = subprocess.run(
        ["git", "branch", "--list", "feature/new"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        env=_GIT_ENV,
    )
    assert "feature/new" in branches.stdout


async def test_checkout_create_switches_to_new_branch(tmp_path: Path):
    repo = _init_repo(tmp_path / "repo", branch="feature/base")
    result = await GitTool().execute(
        {"subcommand": "checkout", "branch": "feature/created", "create": True},
        _worker_ctx(repo),
    )
    assert result.success is True
    assert "已创建并切换到分支 feature/created" in result.output
    current = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        env=_GIT_ENV,
    )
    assert current.stdout.strip() == "feature/created"


async def test_checkout_switches_existing_branch(tmp_path: Path):
    repo = _init_repo(tmp_path / "repo", branch="feature/a")
    _run_git(repo, "branch", "feature/b")
    result = await GitTool().execute(
        {"subcommand": "checkout", "branch": "feature/b"},
        _worker_ctx(repo),
    )
    assert result.success is True
    assert "已切换到分支 feature/b" in result.output
    current = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        env=_GIT_ENV,
    )
    assert current.stdout.strip() == "feature/b"


async def test_commit_requires_message(tmp_path: Path):
    repo = _init_repo(tmp_path / "repo")
    result = await GitTool().execute({"subcommand": "commit"}, _worker_ctx(repo))
    assert result.success is False
    assert "message" in (result.error or "")


# --- push ---


async def test_push_ceo_same_path_as_worker(tmp_path: Path):
    repo = _init_repo(tmp_path / "repo")
    result = await GitTool().execute({"subcommand": "push"}, _ceo_ctx(repo))
    assert "delegate" not in (result.error or "").lower()
    assert "Git 写入操作需通过" not in (result.error or "")
    assert result.success is False
    err = result.error or ""
    assert "remote" in err.lower()
    assert "配置" in err or "凭据" in err


@pytest.mark.parametrize("branch", sorted(_PROTECTED_BRANCHES))
async def test_push_on_protected_branch_rejected(tmp_path: Path, branch: str):
    repo = _init_repo(tmp_path / "repo", branch=branch)
    result = await GitTool().execute({"subcommand": "push"}, _worker_ctx(repo))
    assert result.success is False
    assert "main/master" in (result.error or "")


async def test_push_without_remote_clear_error(tmp_path: Path):
    repo = _init_repo(tmp_path / "repo")
    result = await GitTool().execute({"subcommand": "push"}, _worker_ctx(repo))
    assert result.success is False
    err = result.error or ""
    assert "remote" in err.lower()
    assert "配置" in err or "凭据" in err


async def test_push_rejects_force_and_refspec_args(tmp_path: Path):
    repo = _init_repo(tmp_path / "repo")
    for args in (
        {"subcommand": "push", "force": True},
        {"subcommand": "push", "force_with_lease": True},
        {"subcommand": "push", "refspec": "feature:main"},
        {"subcommand": "push", "branch": "main"},
        {"subcommand": "push", "remote": "--force"},
        {"subcommand": "push", "remote": "origin feature:main"},
    ):
        result = await GitTool().execute(args, _worker_ctx(repo))
        assert result.success is False
        assert result.error


async def test_push_to_local_bare_remote(tmp_path: Path):
    repo = _init_repo(tmp_path / "repo", branch="feature/ship")
    bare = tmp_path / "remote.git"
    _run_git(tmp_path, "init", "--bare", str(bare))
    _run_git(repo, "remote", "add", "origin", str(bare))
    result = await GitTool().execute(
        {"subcommand": "push", "set_upstream": True},
        _worker_ctx(repo),
    )
    assert result.success is True
    assert "已推送 feature/ship → origin" in result.output
    # Remote received the branch.
    listed = subprocess.run(
        ["git", "--git-dir", str(bare), "branch", "--list", "feature/ship"],
        check=True,
        capture_output=True,
        text=True,
        env=_GIT_ENV,
    )
    assert "feature/ship" in listed.stdout


# --- fetch / pull / show / blame (G1) ---


def test_pull_requires_approval_fetch_does_not():
    from agentcore.core.types import ToolApproval
    from agentcore.runtime.approvals import tool_call_requires_approval

    schema_approval = ToolApproval.NEVER
    assert (
        tool_call_requires_approval(
            "git", schema_approval, {"subcommand": "pull", "remote": "origin"}
        )
        is True
    )
    assert (
        tool_call_requires_approval(
            "git", schema_approval, {"subcommand": "fetch", "remote": "origin"}
        )
        is False
    )
    assert (
        tool_call_requires_approval(
            "git", schema_approval, {"subcommand": "show"}
        )
        is False
    )
    assert (
        tool_call_requires_approval(
            "git", schema_approval, {"subcommand": "blame", "paths": ["README.md"]}
        )
        is False
    )


async def test_ceo_allows_fetch_show_blame(tmp_path: Path):
    repo = _init_repo(tmp_path / "repo")
    for sub, args in (
        ("show", {"subcommand": "show"}),
        ("blame", {"subcommand": "blame", "paths": ["README.md"]}),
    ):
        result = await GitTool().execute(args, _ceo_ctx(repo))
        assert result.success is True, f"{sub}: {result.error}"


async def test_fetch_from_local_bare_remote(tmp_path: Path):
    repo = _init_repo(tmp_path / "repo", branch="feature/ship")
    bare = tmp_path / "remote.git"
    _attach_bare_origin(repo, bare, branch="feature/ship")

    # Advance remote with a second clone so fetch has something new.
    other = _clone_from_bare(bare, tmp_path / "other")
    (other / "extra.txt").write_text("from remote\n", encoding="utf-8")
    _run_git(other, "add", "extra.txt")
    _run_git(other, "commit", "-m", "remote advance")
    _run_git(other, "push", "origin", "HEAD")

    result = await GitTool().execute(
        {"subcommand": "fetch", "remote": "origin"},
        _ceo_ctx(repo),
    )
    assert result.success is True
    assert "fetch" in result.output.lower() or "已从 origin" in result.output
    # Tracking ref updated; working tree not merged (fetch ≠ pull).
    assert not (repo / "extra.txt").exists()


async def test_pull_ff_only_succeeds(tmp_path: Path):
    repo = _init_repo(tmp_path / "repo", branch="feature/ship")
    bare = tmp_path / "remote.git"
    _attach_bare_origin(repo, bare, branch="feature/ship")

    other = _clone_from_bare(bare, tmp_path / "other")
    (other / "extra.txt").write_text("ff me\n", encoding="utf-8")
    _run_git(other, "add", "extra.txt")
    _run_git(other, "commit", "-m", "remote ff")
    _run_git(other, "push", "origin", "HEAD")

    result = await GitTool().execute(
        {"subcommand": "pull", "remote": "origin"},
        _worker_ctx(repo),
    )
    assert result.success is True
    assert result.metadata.get("ff_only") is True
    assert (repo / "extra.txt").read_text(encoding="utf-8") == "ff me\n"


async def test_pull_non_ff_fails_honestly(tmp_path: Path):
    repo = _init_repo(tmp_path / "repo", branch="feature/ship")
    bare = tmp_path / "remote.git"
    _attach_bare_origin(repo, bare, branch="feature/ship")

    # Divergent histories: local and remote each add a distinct commit.
    (repo / "local.txt").write_text("local\n", encoding="utf-8")
    _run_git(repo, "add", "local.txt")
    _run_git(repo, "commit", "-m", "local only")

    other = _clone_from_bare(bare, tmp_path / "other")
    (other / "remote.txt").write_text("remote\n", encoding="utf-8")
    _run_git(other, "add", "remote.txt")
    _run_git(other, "commit", "-m", "remote only")
    _run_git(other, "push", "origin", "HEAD")

    result = await GitTool().execute(
        {"subcommand": "pull", "remote": "origin"},
        _worker_ctx(repo),
    )
    assert result.success is False
    err = (result.error or "").lower()
    assert (
        "fast-forward" in err
        or "not possible" in err
        or "diverg" in err
        or "拒绝" in (result.error or "")
        or "冲突" in (result.error or "")
        or "无法" in (result.error or "")
        or "ff" in err
    )
    # Local-only commit must remain; no silent merge.
    assert (repo / "local.txt").exists()
    assert not (repo / "remote.txt").exists()


async def test_pull_rejects_strategy_knobs(tmp_path: Path):
    repo = _init_repo(tmp_path / "repo")
    for args in (
        {"subcommand": "pull", "rebase": True},
        {"subcommand": "pull", "no_ff": True},
        {"subcommand": "pull", "strategy": "recursive"},
    ):
        result = await GitTool().execute(args, _worker_ctx(repo))
        assert result.success is False
        assert "ff-only" in (result.error or "").lower() or "快进" in (result.error or "")


async def test_pull_without_repo_hard_fails(tmp_path: Path):
    bare = tmp_path / "not_a_repo"
    bare.mkdir()
    result = await GitTool().execute(
        {"subcommand": "pull"},
        _worker_ctx(bare),
    )
    assert result.success is False
    assert "没有 Git 仓库" in (result.error or "")


async def test_pull_passes_ff_only_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from agentcore.tools.builtin import git_ops as git_mod

    repo = _init_repo(tmp_path / "repo")
    bare = tmp_path / "remote.git"
    _run_git(tmp_path, "init", "--bare", str(bare))
    _run_git(repo, "remote", "add", "origin", str(bare))

    seen: list[list[str]] = []
    real_run = git_mod.spawn._run_git

    async def _spy(
        args: list[str],
        *,
        cwd: str,
        timeout: float = 20.0,
        extra_env: dict[str, str] | None = None,
        phase: str = PHASE_LOCAL,
    ):
        seen.append(list(args))
        return await real_run(
            args, cwd=cwd, timeout=timeout, extra_env=extra_env, phase=phase
        )

    monkeypatch.setattr(git_mod.spawn, "_run_git", _spy)
    await GitTool().execute(
        {"subcommand": "pull", "remote": "origin"},
        _worker_ctx(repo),
    )
    pull_calls = [a for a in seen if a and a[0] == "pull"]
    assert pull_calls
    assert pull_calls[0][:2] == ["pull", "--ff-only"]
    assert "origin" in pull_calls[0]


# --- cloud PAT → credential.helper injection (UNSURE audit) ---


async def test_cloud_network_extra_env_with_pat(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    auth = GitAuthMaterial(username="x-access-token", token="pat-secret")

    async def _load(_user_id: str) -> GitAuthMaterial | None:
        return auth

    monkeypatch.setattr(
        "agentcore.workspace.git_credentials.load_git_auth_for_user",
        _load,
    )
    ctx = _worker_ctx(tmp_path, location="server", user_id="u1")
    extra = await _cloud_network_extra_env(ctx)
    _assert_credential_helper_env(extra, username="x-access-token", token="pat-secret")


async def test_cloud_network_extra_env_no_pat(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    async def _load(_user_id: str) -> GitAuthMaterial | None:
        return None

    monkeypatch.setattr(
        "agentcore.workspace.git_credentials.load_git_auth_for_user",
        _load,
    )
    ctx = _worker_ctx(tmp_path, location="server", user_id="u1")
    assert await _cloud_network_extra_env(ctx) is None


async def test_cloud_network_extra_env_local_skips_even_with_pat(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    async def _load(_user_id: str) -> GitAuthMaterial | None:
        return GitAuthMaterial(username="x-access-token", token="pat-secret")

    monkeypatch.setattr(
        "agentcore.workspace.git_credentials.load_git_auth_for_user",
        _load,
    )
    ctx = _worker_ctx(tmp_path, location="local", user_id="u1")
    assert await _cloud_network_extra_env(ctx) is None


@pytest.mark.parametrize("subcommand", ["push", "fetch", "pull"])
async def test_network_cmds_inject_extra_env_when_cloud_pat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, subcommand: str
):
    """Prove push/fetch/pull pass GIT_CONFIG_* credential.helper into ``_run_git``."""
    from agentcore.tools.builtin import git_ops as git_mod

    auth = GitAuthMaterial(username="gh-user", token="gh-pat")

    async def _load(_user_id: str) -> GitAuthMaterial | None:
        return auth

    monkeypatch.setattr(
        "agentcore.workspace.git_credentials.load_git_auth_for_user",
        _load,
    )

    repo = _init_repo(tmp_path / "repo")
    bare = tmp_path / "remote.git"
    _run_git(tmp_path, "init", "--bare", str(bare))
    _run_git(repo, "remote", "add", "origin", str(bare))

    seen_extra: list[dict[str, str] | None] = []
    real_run = git_mod.spawn._run_git

    async def _spy(
        args: list[str],
        *,
        cwd: str,
        timeout: float = 20.0,
        extra_env: dict[str, str] | None = None,
        phase: str = PHASE_LOCAL,
    ):
        if args and args[0] == subcommand:
            seen_extra.append(extra_env)
        return await real_run(
            args, cwd=cwd, timeout=timeout, extra_env=extra_env, phase=phase
        )

    monkeypatch.setattr(git_mod.spawn, "_run_git", _spy)
    await GitTool().execute(
        {"subcommand": subcommand, "remote": "origin"},
        _worker_ctx(repo, location="server"),
    )
    assert seen_extra, f"expected a {subcommand} _run_git call"
    _assert_credential_helper_env(seen_extra[0], username="gh-user", token="gh-pat")


async def test_credential_lookup_timeout_is_fail_soft(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """A hung credential store must not fail the push — it means "no PAT", nothing more.

    git then authenticates on its own and fails honestly if it cannot; the tool
    call itself never dies inside the lookup.
    """
    import asyncio

    from agentcore.tools.builtin import git_ops as git_mod

    async def _hang(_user_id: str) -> GitAuthMaterial:
        await asyncio.sleep(60)
        raise AssertionError("credential lookup was not bounded")

    monkeypatch.setattr(
        "agentcore.workspace.git_credentials.load_git_auth_for_user",
        _hang,
    )
    real_load = git_mod.spawn._load_account_git_auth
    assert await real_load("u1", timeout=0.05) is None

    # The caller degrades to "no credential helper env", not to an error.
    async def _fast_bound(user_id: str, *, timeout: float = 0.05):
        return await real_load(user_id, timeout=timeout)

    monkeypatch.setattr(git_mod.spawn, "_load_account_git_auth", _fast_bound)
    ctx = _worker_ctx(tmp_path, location="server", user_id="u1")
    assert await _cloud_network_extra_env(ctx) is None


async def test_pr_token_resolve_timeout_reports_honestly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """create_pr cannot proceed without a token, so say the lookup timed out."""
    import asyncio

    from agentcore.tools.builtin import git_ops as git_mod

    async def _hang(*, user_id: str | None) -> str:
        await asyncio.sleep(60)
        raise AssertionError("token resolution was not bounded")

    monkeypatch.setattr("agentcore.workspace.github_pr.resolve_github_token", _hang)
    real_resolve = git_mod.spawn._resolve_pr_token
    assert await real_resolve("u1", timeout=0.05) == (None, True)

    repo = _init_repo(tmp_path / "repo", branch="feature/pr")
    _run_git(repo, "remote", "add", "origin", "https://github.com/acme/demo.git")

    async def _fast_bound(user_id: str | None, *, timeout: float = 0.05):
        return await real_resolve(user_id, timeout=timeout)

    monkeypatch.setattr(git_mod.cmds_remote, "_resolve_pr_token", _fast_bound)
    result = await GitTool().execute(
        {"subcommand": "create_pr", "title": "Hello"},
        _worker_ctx(repo, location="server"),
    )
    assert result.success is False
    assert result.metadata.get("code") == "unauthenticated"
    assert "超时" in (result.error or "")
    # Must not send the user configuring credentials they may already have.
    assert "设置 → Git 凭据" not in (result.error or "")


@pytest.mark.parametrize("subcommand", ["push", "fetch", "pull"])
async def test_network_cmds_no_extra_env_without_pat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, subcommand: str
):
    from agentcore.tools.builtin import git_ops as git_mod

    async def _load(_user_id: str) -> GitAuthMaterial | None:
        return None

    monkeypatch.setattr(
        "agentcore.workspace.git_credentials.load_git_auth_for_user",
        _load,
    )

    repo = _init_repo(tmp_path / "repo")
    bare = tmp_path / "remote.git"
    _run_git(tmp_path, "init", "--bare", str(bare))
    _run_git(repo, "remote", "add", "origin", str(bare))

    seen_extra: list[dict[str, str] | None] = []
    real_run = git_mod.spawn._run_git

    async def _spy(
        args: list[str],
        *,
        cwd: str,
        timeout: float = 20.0,
        extra_env: dict[str, str] | None = None,
        phase: str = PHASE_LOCAL,
    ):
        if args and args[0] == subcommand:
            seen_extra.append(extra_env)
        return await real_run(
            args, cwd=cwd, timeout=timeout, extra_env=extra_env, phase=phase
        )

    monkeypatch.setattr(git_mod.spawn, "_run_git", _spy)
    await GitTool().execute(
        {"subcommand": subcommand, "remote": "origin"},
        _worker_ctx(repo, location="server"),
    )
    assert seen_extra, f"expected a {subcommand} _run_git call"
    assert seen_extra[0] is None


@pytest.mark.parametrize("subcommand", ["push", "fetch", "pull"])
async def test_network_cmds_local_no_extra_env_even_with_pat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, subcommand: str
):
    from agentcore.tools.builtin import git_ops as git_mod

    async def _load(_user_id: str) -> GitAuthMaterial | None:
        return GitAuthMaterial(username="gh-user", token="gh-pat")

    monkeypatch.setattr(
        "agentcore.workspace.git_credentials.load_git_auth_for_user",
        _load,
    )

    repo = _init_repo(tmp_path / "repo")
    bare = tmp_path / "remote.git"
    _run_git(tmp_path, "init", "--bare", str(bare))
    _run_git(repo, "remote", "add", "origin", str(bare))

    seen_extra: list[dict[str, str] | None] = []
    real_run = git_mod.spawn._run_git

    async def _spy(
        args: list[str],
        *,
        cwd: str,
        timeout: float = 20.0,
        extra_env: dict[str, str] | None = None,
        phase: str = PHASE_LOCAL,
    ):
        if args and args[0] == subcommand:
            seen_extra.append(extra_env)
        return await real_run(
            args, cwd=cwd, timeout=timeout, extra_env=extra_env, phase=phase
        )

    monkeypatch.setattr(git_mod.spawn, "_run_git", _spy)
    await GitTool().execute(
        {"subcommand": subcommand, "remote": "origin"},
        _worker_ctx(repo, location="local"),
    )
    assert seen_extra, f"expected a {subcommand} _run_git call"
    assert seen_extra[0] is None


async def test_show_and_blame_basic(tmp_path: Path):
    repo = _init_repo(tmp_path / "repo")
    shown = await GitTool().execute({"subcommand": "show"}, _ceo_ctx(repo))
    assert shown.success is True
    assert "hello" in shown.output or "init" in shown.output

    blamed = await GitTool().execute(
        {"subcommand": "blame", "paths": ["README.md"]},
        _ceo_ctx(repo),
    )
    assert blamed.success is True
    assert "hello" in blamed.output
    assert blamed.metadata.get("path") == "README.md"


async def test_blame_requires_single_path(tmp_path: Path):
    repo = _init_repo(tmp_path / "repo")
    empty = await GitTool().execute({"subcommand": "blame"}, _ceo_ctx(repo))
    assert empty.success is False
    assert "paths" in (empty.error or "")

    multi = await GitTool().execute(
        {"subcommand": "blame", "paths": ["README.md", "other.txt"]},
        _ceo_ctx(repo),
    )
    assert multi.success is False
    assert "一个文件" in (multi.error or "")


async def test_show_truncates_long_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from agentcore.tools.builtin import git_ops as git_mod

    monkeypatch.setattr(git_mod.policy, "_DIFF_OUTPUT_LIMIT", 80)
    repo = _init_repo(tmp_path / "repo")
    (repo / "big.txt").write_text("x" * 400 + "\n", encoding="utf-8")
    _run_git(repo, "add", "big.txt")
    _run_git(repo, "commit", "-m", "big")
    result = await GitTool().execute(
        {"subcommand": "show", "object": "HEAD"},
        _ceo_ctx(repo),
    )
    assert result.success is True
    assert len(result.output) <= 80 + 50  # truncate_head_tail may use marker
    assert "系统视图截断" in result.output or "……" in result.output


async def test_blame_truncates_long_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from agentcore.tools.builtin import git_ops as git_mod

    monkeypatch.setattr(git_mod.policy, "_BLAME_LINE_LIMIT", 3)
    repo = _init_repo(tmp_path / "repo")
    (repo / "lines.txt").write_text(
        "\n".join(f"line-{i}" for i in range(8)) + "\n", encoding="utf-8"
    )
    _run_git(repo, "add", "lines.txt")
    _run_git(repo, "commit", "-m", "lines")
    result = await GitTool().execute(
        {"subcommand": "blame", "paths": ["lines.txt"]},
        _ceo_ctx(repo),
    )
    assert result.success is True
    assert "已截断" in result.output
    assert result.metadata.get("truncated") is True
    assert (result.metadata.get("blame_lines") or 0) >= 4


# --- timeout contract / status narrowing ---


def test_git_tool_timeout_outlives_inner_ops():
    from agentcore.runtime.engine import resolve_tool_timeout
    from agentcore.tools.builtin.git_ops import (
        _GIT_CREDENTIAL_TIMEOUT,
        _GIT_KILL_SLACK,
        _GIT_NETWORK_TIMEOUT,
        _GIT_REPO_LOCK_WAIT,
        _GIT_TIMEOUT,
        _GIT_TOKEN_RESOLVE_TIMEOUT,
        _GITHUB_API_CALLS,
        _GITHUB_API_TIMEOUT,
        git_tool_timeout_seconds,
    )

    schema = GitTool().schema
    assert schema.timeout_seconds is None
    status_ceiling = git_tool_timeout_seconds({"subcommand": "status"})
    commit_ceiling = git_tool_timeout_seconds({"subcommand": "commit"})
    merge_ceiling = git_tool_timeout_seconds({"subcommand": "merge"})
    pull_ceiling = git_tool_timeout_seconds({"subcommand": "pull"})
    fetch_ceiling = git_tool_timeout_seconds({"subcommand": "fetch"})
    clone_ceiling = git_tool_timeout_seconds({"subcommand": "clone"})
    push_ceiling = git_tool_timeout_seconds({"subcommand": "push"})
    pr_ceiling = git_tool_timeout_seconds({"subcommand": "create_pr"})
    baseline_ceiling = git_tool_timeout_seconds({"subcommand": "init_baseline"})
    # Read path spawns one git process (no repo probe) and never queues on the repo
    # lock — one inner budget + slack, unchanged by serialization.
    assert status_ceiling == _GIT_TIMEOUT + _GIT_KILL_SLACK
    # branch --show-current + commit + rev-parse --short HEAD, behind the repo queue
    assert commit_ceiling == 3 * _GIT_TIMEOUT + _GIT_REPO_LOCK_WAIT + _GIT_KILL_SLACK
    # branch --show-current (protected-branch refusal) + merge, behind the repo queue
    assert merge_ceiling == 2 * _GIT_TIMEOUT + _GIT_REPO_LOCK_WAIT + _GIT_KILL_SLACK
    assert commit_ceiling > status_ceiling
    # Network subcommands also serialize on a bounded PAT lookup before the remote op;
    # only pull touches the index, so only pull pays the queue budget.
    assert fetch_ceiling == (
        _GIT_TIMEOUT + _GIT_CREDENTIAL_TIMEOUT + _GIT_NETWORK_TIMEOUT + _GIT_KILL_SLACK
    )
    # clone matches fetch: PAT + network, no index.lock (new dest tree).
    assert clone_ceiling == fetch_ceiling
    assert pull_ceiling == fetch_ceiling + _GIT_REPO_LOCK_WAIT
    # push never takes index.lock — it stays out of the queue and off its budget.
    assert push_ceiling == (
        2 * _GIT_TIMEOUT
        + _GIT_CREDENTIAL_TIMEOUT
        + _GIT_NETWORK_TIMEOUT
        + _GIT_KILL_SLACK
    )
    # create_pr spends its remote budget on token resolution + two REST calls.
    assert pr_ceiling == (
        3 * _GIT_TIMEOUT
        + _GIT_TOKEN_RESOLVE_TIMEOUT
        + _GITHUB_API_CALLS * _GITHUB_API_TIMEOUT
        + _GIT_KILL_SLACK
    )
    assert baseline_ceiling == (
        5 * _GIT_TIMEOUT + _GIT_REPO_LOCK_WAIT + _GIT_KILL_SLACK
    )
    assert resolve_tool_timeout(schema, {"subcommand": "status"}) == status_ceiling
    assert resolve_tool_timeout(schema, {"subcommand": "commit"}) == commit_ceiling
    assert resolve_tool_timeout(schema, {"subcommand": "pull"}) == pull_ceiling
    # Action-gated verbs must budget the queue exactly as they take it: stash push
    # writes the index, stash list is a plain read.
    assert git_tool_timeout_seconds({"subcommand": "stash", "action": "push"}) == (
        _GIT_TIMEOUT + _GIT_REPO_LOCK_WAIT + _GIT_KILL_SLACK
    )
    assert (
        git_tool_timeout_seconds({"subcommand": "stash", "action": "list"})
        == status_ceiling
    )


def _declared_ceiling(fn: Any) -> float:
    """The ceiling a bounded helper enforces when the caller passes none."""
    import inspect

    return float(inspect.signature(fn).parameters["timeout"].default)


def _budget_probe(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, float]]:
    """Ledger of the bounded I/O one git tool call serializes on, with each ceiling.

    Covers the seams that consume wall clock: the per-repo queue, git subprocesses,
    the account PAT lookup, and create_pr token resolution (GitHub REST is stubbed
    per test and appends its client's ceiling). Each entry is the deadline
    production code actually enforces, read off the call or the helper's own
    default — a ratchet over known seams, so a *new* unbounded step shows up as a
    missing entry rather than as a wrong number.
    """
    import asyncio

    from agentcore.tools.builtin import git_ops as git_mod

    ledger: list[tuple[str, float]] = []
    real_run = git_mod.spawn._run_git
    real_cred = git_mod.spawn._load_account_git_auth
    real_token = git_mod.spawn._resolve_pr_token
    real_lock = git_mod.repo_lock._acquire_repo_lock
    cred_default = _declared_ceiling(real_cred)
    token_default = _declared_ceiling(real_token)
    lock_default = _declared_ceiling(real_lock)

    async def _spy_git(
        args: list[str],
        *,
        cwd: str,
        timeout: float = 20.0,
        extra_env: dict[str, str] | None = None,
        phase: str = git_mod.PHASE_LOCAL,
    ):
        ledger.append((f"git:{' '.join(args)}", float(timeout)))
        return await real_run(
            args, cwd=cwd, timeout=timeout, extra_env=extra_env, phase=phase
        )

    async def _spy_cred(user_id: str, *, timeout: float = cred_default):
        ledger.append(("credential", float(timeout)))
        return await real_cred(user_id, timeout=timeout)

    async def _spy_token(user_id: str | None, *, timeout: float = token_default):
        ledger.append(("token_resolve", float(timeout)))
        return await real_token(user_id, timeout=timeout)

    async def _spy_lock(lock: asyncio.Lock, *, timeout: float = lock_default):
        ledger.append(("repo_lock", float(timeout)))
        return await real_lock(lock, timeout=timeout)

    monkeypatch.setattr(git_mod.spawn, "_run_git", _spy_git)
    monkeypatch.setattr(git_mod.spawn, "_load_account_git_auth", _spy_cred)
    monkeypatch.setattr(git_mod.repo_lock, "_acquire_repo_lock", _spy_lock)
    # create_pr binds the helper at import time, so patch the call site too.
    monkeypatch.setattr(git_mod.cmds_remote, "_resolve_pr_token", _spy_token)
    return ledger


@pytest.mark.parametrize(
    "args,expected_argv",
    [
        ({"subcommand": "status"}, "status -sb --untracked-files=no"),
        ({"subcommand": "diff"}, "diff"),
        ({"subcommand": "log", "max_count": 5}, "log -n5 --oneline"),
        ({"subcommand": "show"}, "show HEAD"),
        ({"subcommand": "blame", "paths": ["README.md"]}, "blame -- README.md"),
    ],
)
async def test_healthy_repo_read_spawns_one_git_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    args: dict[str, Any],
    expected_argv: str,
):
    """No pre-flight probe: a read on a healthy repo is exactly the command itself."""
    from agentcore.tools.builtin.git_ops import _GIT_TIMEOUT

    repo = _init_repo(tmp_path / "repo")
    ledger = _budget_probe(monkeypatch)
    result = await GitTool().execute(args, _ceo_ctx(repo))
    assert result.success is True, result.error
    assert ledger == [(f"git:{expected_argv}", _GIT_TIMEOUT)]


async def test_engine_ceiling_outlives_measured_inner_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Invariant with teeth: outer ≥ Σ(inner ceilings actually used) + kill slack.

    Measures the serial steps each subcommand really performs instead of trusting
    the hand-maintained tables next to the formula. Network subcommands must show
    their PAT lookup here — it is DB I/O rather than a git process, and it is the
    step the budget used to ignore entirely. The per-repo queue is on the same
    footing: whoever waits for it must have budgeted for it, and the ledger says
    who waits (reads and ref-only writes must not).
    """
    from agentcore.tools.builtin.git_ops import _GIT_KILL_SLACK, git_tool_timeout_seconds

    repo = _init_repo(tmp_path / "repo", branch="feature/ship")
    bare = tmp_path / "remote.git"
    _run_git(tmp_path, "init", "--bare", str(bare))
    _run_git(repo, "remote", "add", "origin", str(bare))
    (repo / "extra.txt").write_text("x\n", encoding="utf-8")
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    (fresh / "app.py").write_text("print('hi')\n", encoding="utf-8")

    ledger = _budget_probe(monkeypatch)
    from agentcore.tools.builtin.git_ops import cmds_remote as cmds_remote_mod

    async def _skip_clone_url_policy(_url: str, _start: float):
        return None

    monkeypatch.setattr(
        cmds_remote_mod, "_clone_url_policy_error", _skip_clone_url_policy
    )
    clone_src = _init_repo(tmp_path / "clone_src")
    clone_ws = tmp_path / "clone_ws"
    clone_ws.mkdir()
    cases: list[tuple[dict[str, Any], Path, bool, bool]] = [
        ({"subcommand": "status"}, repo, False, False),
        ({"subcommand": "add", "paths": ["extra.txt"]}, repo, False, True),
        ({"subcommand": "commit", "message": "add extra"}, repo, False, True),
        ({"subcommand": "merge", "ref": "feature/ship"}, repo, False, True),
        ({"subcommand": "push", "set_upstream": True}, repo, True, False),
        ({"subcommand": "fetch", "remote": "origin"}, repo, True, False),
        ({"subcommand": "pull", "remote": "origin"}, repo, True, True),
        ({"subcommand": "init_baseline"}, fresh, False, True),
        (
            {"subcommand": "clone", "url": clone_src.as_uri()},
            clone_ws,
            True,
            False,
        ),
    ]
    for args, workspace, wants_credential, wants_repo_lock in cases:
        ledger.clear()
        await GitTool().execute(args, _worker_ctx(workspace, location="server"))
        assert ledger, args
        labels = [label for label, _ in ledger]
        assert ("credential" in labels) is wants_credential, (args, labels)
        assert ("repo_lock" in labels) is wants_repo_lock, (args, labels)
        inner = sum(ceiling for _, ceiling in ledger)
        outer = git_tool_timeout_seconds(args)
        assert inner + _GIT_KILL_SLACK <= outer, (args, ledger)


async def test_create_pr_ceiling_outlives_measured_inner_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Same invariant for create_pr, whose remote budget is token resolve + REST."""
    import httpx

    from agentcore.tools.builtin.git_ops import (
        _GIT_KILL_SLACK,
        _GITHUB_API_TIMEOUT,
        git_tool_timeout_seconds,
    )

    repo = _init_repo(tmp_path / "repo", branch="feature/pr")
    _run_git(repo, "remote", "add", "origin", "https://github.com/acme/demo.git")

    ledger = _budget_probe(monkeypatch)

    def _client_ceiling(client: httpx.AsyncClient) -> float:
        phases = [
            client.timeout.connect,
            client.timeout.read,
            client.timeout.write,
            client.timeout.pool,
        ]
        assert all(p is not None for p in phases), "GitHub client has no deadline"
        return max(float(p) for p in phases)

    async def _fake_get(self: httpx.AsyncClient, url: str, **_kwargs: Any):
        ledger.append(("github_api", _client_ceiling(self)))
        return httpx.Response(
            200,
            json={"default_branch": "main"},
            request=httpx.Request("GET", url),
        )

    async def _fake_post(self: httpx.AsyncClient, url: str, **_kwargs: Any):
        ledger.append(("github_api", _client_ceiling(self)))
        return httpx.Response(
            201,
            json={
                "html_url": "https://github.com/acme/demo/pull/1",
                "number": 1,
                "title": "Hello",
            },
            request=httpx.Request("POST", url),
        )

    async def _token(*, user_id: str | None) -> str:
        return "tok"

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)
    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_post)
    monkeypatch.setattr("agentcore.workspace.github_pr.resolve_github_token", _token)

    args: dict[str, Any] = {"subcommand": "create_pr", "title": "Hello"}
    result = await GitTool().execute(args, _worker_ctx(repo, location="server"))
    assert result.success is True, result.error

    labels = [label for label, _ in ledger]
    assert "token_resolve" in labels
    # Default-branch GET + create POST, each on an explicit client deadline equal to
    # the number the formula budgets (never httpx's library default).
    api = [ceiling for label, ceiling in ledger if label == "github_api"]
    assert api == [_GITHUB_API_TIMEOUT, _GITHUB_API_TIMEOUT]
    inner = sum(ceiling for _, ceiling in ledger)
    assert inner + _GIT_KILL_SLACK <= git_tool_timeout_seconds(args), ledger


# --- execution phases (工具执行阶段进度) ---


class _PhaseWatch:
    """Live phase feed plus the phase each bounded step actually ran under.

    ``phases`` is what the desktop row would have shown, in order. ``ledger`` pairs
    every bounded step with the phase in effect while it ran, so a test can pin the
    thing that matters: the label never describes a leg other than the running one.
    """

    def __init__(self) -> None:
        self.phases: list[str] = []
        self.ledger: list[tuple[str, str | None]] = []

    def on_phase(self, phase: str) -> None:
        self.phases.append(phase)

    def record(self, label: str) -> None:
        self.ledger.append((label, self.phases[-1] if self.phases else None))

    def reset(self) -> None:
        self.phases.clear()
        self.ledger.clear()


# The phase each bounded step must be running under. Anything unlisted is local git
# work: a step that is not declared remote / credential / queue must report「Running」
# rather than inherit whatever leg ran before it.
_PHASE_BY_OP: dict[str, str | None] = {
    # Nothing has been reported yet when an uncontended acquire returns — the queue
    # only speaks when it genuinely makes the caller wait.
    "repo_lock:free": None,
    "repo_lock:contended": PHASE_QUEUED,
    "credential": PHASE_CREDENTIALS,
    "token_resolve": PHASE_CREDENTIALS,
    "github_api": PHASE_REMOTE,
    "git:push": PHASE_REMOTE,
    "git:pull": PHASE_REMOTE,
    "git:fetch": PHASE_REMOTE,
    "git:clone": PHASE_REMOTE,
}


def _phase_probe(monkeypatch: pytest.MonkeyPatch) -> _PhaseWatch:
    """Watch every bounded step the git tool serializes on, with its live phase.

    Phases are only ever reported at the START of a leg, so the last token emitted
    when a step completes is the one that labelled it for the step's whole duration.
    Reading it on completion therefore works uniformly for legs announced by the
    caller (queue / credentials) and by ``_run_git`` itself (local / remote).
    """
    import asyncio

    from agentcore.tools.builtin import git_ops as git_mod

    watch = _PhaseWatch()
    real_run = git_mod.spawn._run_git
    real_cred = git_mod.spawn._load_account_git_auth
    real_token = git_mod.spawn._resolve_pr_token
    real_lock = git_mod.repo_lock._acquire_repo_lock

    async def _spy_git(
        args: list[str],
        *,
        cwd: str,
        timeout: float = 20.0,
        extra_env: dict[str, str] | None = None,
        phase: str = git_mod.PHASE_LOCAL,
    ):
        try:
            return await real_run(
                args, cwd=cwd, timeout=timeout, extra_env=extra_env, phase=phase
            )
        finally:
            watch.record(f"git:{args[0]}")

    async def _spy_cred(user_id: str, **kwargs: Any):
        try:
            return await real_cred(user_id, **kwargs)
        finally:
            watch.record("credential")

    async def _spy_token(user_id: str | None, **kwargs: Any):
        try:
            return await real_token(user_id, **kwargs)
        finally:
            watch.record("token_resolve")

    async def _spy_lock(lock: asyncio.Lock, **kwargs: Any):
        # Read contention before waiting — the same question production asks when it
        # decides whether this call is allowed to say「排队中」.
        label = "repo_lock:contended" if lock.locked() else "repo_lock:free"
        try:
            return await real_lock(lock, **kwargs)
        finally:
            watch.record(label)

    monkeypatch.setattr(git_mod.spawn, "_run_git", _spy_git)
    monkeypatch.setattr(git_mod.spawn, "_load_account_git_auth", _spy_cred)
    monkeypatch.setattr(git_mod.repo_lock, "_acquire_repo_lock", _spy_lock)
    monkeypatch.setattr(git_mod.cmds_remote, "_resolve_pr_token", _spy_token)
    return watch


def _phase_ctx(workspace: Path, watch: _PhaseWatch) -> ToolContext:
    """Worker ctx carrying a live phase sink, exactly as the engine injects one."""
    from dataclasses import replace

    return replace(
        _worker_ctx(workspace, location="server"), on_phase=watch.on_phase
    )


def test_git_phases_are_declared_on_the_wire():
    """Every git phase must be a known ``ToolPhase``.

    The desktop keys an exhaustive text table off that union, so a token declared
    only here would degrade to the generic「处理中」instead of naming its leg.
    """
    from typing import get_args

    from agentcore.runtime.events.payloads.chat import ToolPhase

    assert set(get_args(ToolPhase)) >= GIT_PHASES
    # The local leg deliberately reuses the shared「Running」token; the waits git
    # invented for itself are the ones that needed new copy.
    assert PHASE_LOCAL == "executing"


async def test_phases_name_the_leg_that_is_actually_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """相位诚实：报的相位 == 当时真在做的事（每个子命令的完整序列 + 每步复核）。

    The sequence is the user-facing contract: a plain read says「Running」and nothing
    else, while a network subcommand walks local → credentials → remote. The ledger
    is the teeth — a credential lookup must never run while the row already reads
    「Contacting remote」, which is exactly the lie this feature exists to prevent.
    """
    repo = _init_repo(tmp_path / "repo", branch="feature/ship")
    bare = tmp_path / "remote.git"
    _run_git(tmp_path, "init", "--bare", str(bare))
    _run_git(repo, "remote", "add", "origin", str(bare))
    (repo / "extra.txt").write_text("x\n", encoding="utf-8")
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    (fresh / "app.py").write_text("print('hi')\n", encoding="utf-8")

    remote_leg = [PHASE_LOCAL, PHASE_CREDENTIALS, PHASE_REMOTE]
    from agentcore.tools.builtin.git_ops import cmds_remote as cmds_remote_mod

    async def _skip_clone_url_policy(_url: str, _start: float):
        return None

    monkeypatch.setattr(
        cmds_remote_mod, "_clone_url_policy_error", _skip_clone_url_policy
    )
    clone_src = _init_repo(tmp_path / "clone_src")
    clone_ws = tmp_path / "clone_ws"
    clone_ws.mkdir()
    cases: list[tuple[dict[str, Any], Path, list[str]]] = [
        ({"subcommand": "status"}, repo, [PHASE_LOCAL]),
        ({"subcommand": "add", "paths": ["extra.txt"]}, repo, [PHASE_LOCAL]),
        ({"subcommand": "commit", "message": "extra"}, repo, [PHASE_LOCAL]),
        ({"subcommand": "init_baseline"}, fresh, [PHASE_LOCAL]),
        ({"subcommand": "fetch", "remote": "origin"}, repo, remote_leg),
        ({"subcommand": "pull", "remote": "origin"}, repo, remote_leg),
        ({"subcommand": "push", "remote": "origin"}, repo, remote_leg),
        (
            {"subcommand": "clone", "url": clone_src.as_uri()},
            clone_ws,
            remote_leg,
        ),
    ]
    watch = _phase_probe(monkeypatch)
    for args, workspace, expected in cases:
        watch.reset()
        await GitTool().execute(args, _phase_ctx(workspace, watch))
        assert watch.phases == expected, (args, watch.phases)
        assert watch.ledger, args
        for label, live in watch.ledger:
            assert live == _PHASE_BY_OP.get(label, PHASE_LOCAL), (
                args,
                label,
                watch.ledger,
            )


async def test_create_pr_reports_credentials_then_github(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """create_pr's 18s token chain must not hide behind「Contacting remote」."""
    import httpx

    repo = _init_repo(tmp_path / "repo", branch="feature/pr")
    _run_git(repo, "remote", "add", "origin", "https://github.com/acme/demo.git")
    watch = _phase_probe(monkeypatch)

    async def _fake_get(self: httpx.AsyncClient, url: str, **_kwargs: Any):
        watch.record("github_api")
        return httpx.Response(
            200,
            json={"default_branch": "main"},
            request=httpx.Request("GET", url),
        )

    async def _fake_post(self: httpx.AsyncClient, url: str, **_kwargs: Any):
        watch.record("github_api")
        return httpx.Response(
            201,
            json={
                "html_url": "https://github.com/acme/demo/pull/1",
                "number": 1,
                "title": "Hello",
            },
            request=httpx.Request("POST", url),
        )

    async def _token(*, user_id: str | None) -> str:
        return "tok"

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)
    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_post)
    monkeypatch.setattr("agentcore.workspace.github_pr.resolve_github_token", _token)

    result = await GitTool().execute(
        {"subcommand": "create_pr", "title": "Hello"}, _phase_ctx(repo, watch)
    )

    assert result.success is True, result.error
    assert watch.phases == [PHASE_LOCAL, PHASE_CREDENTIALS, PHASE_REMOTE]
    for label, live in watch.ledger:
        assert live == _PHASE_BY_OP.get(label, PHASE_LOCAL), (label, watch.ledger)


async def test_queue_phase_fires_only_while_the_repo_is_really_busy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """「Waiting for repo」covers the wait and stops the moment the repo is ours."""
    import asyncio

    from agentcore.tools.builtin import git_ops as git_mod
    from agentcore.tools.builtin.git_ops import repo_lock_key

    repo = _init_repo(tmp_path / "repo")
    (repo / "a.txt").write_text("a\n", encoding="utf-8")
    (repo / "b.txt").write_text("b\n", encoding="utf-8")
    watch = _phase_probe(monkeypatch)

    # Uncontended: the acquire never suspends, so claiming a queue would be a lie.
    first = await GitTool().execute(
        {"subcommand": "add", "paths": ["a.txt"]}, _phase_ctx(repo, watch)
    )
    assert first.success is True, first.error
    assert watch.phases == [PHASE_LOCAL]
    assert ("repo_lock:free", None) in watch.ledger

    ctx = _phase_ctx(repo, watch)
    held = git_mod.repo_lock._get_repo_lock(repo_lock_key(str(repo.resolve()), ctx))
    await held.acquire()
    watch.reset()
    queued = asyncio.create_task(
        GitTool().execute({"subcommand": "add", "paths": ["b.txt"]}, ctx)
    )
    try:
        await asyncio.sleep(0.05)
        # Still parked behind the holder: the row says so, and says nothing else.
        assert watch.phases == [PHASE_QUEUED]
    finally:
        held.release()
    result = await queued

    assert result.success is True, result.error
    # …and flips to the real work the instant the queue clears — never the reverse.
    assert watch.phases == [PHASE_QUEUED, PHASE_LOCAL]


# --- per-repo serialization (repo_lock) ---


def _concurrency_probe(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Track how many git subprocesses are in flight at once; returns [peak].

    Each spawn is padded so genuinely parallel calls overlap in wall clock — the
    peak is then the honest answer to "did these two run at the same time?".
    """
    import asyncio

    from agentcore.tools.builtin import git_ops as git_mod

    real_run = git_mod.spawn._run_git
    state = {"depth": 0}
    peak = [0]

    async def _slow_git(
        args: list[str],
        *,
        cwd: str,
        timeout: float = 20.0,
        extra_env: dict[str, str] | None = None,
        phase: str = git_mod.PHASE_LOCAL,
    ):
        state["depth"] += 1
        peak[0] = max(peak[0], state["depth"])
        try:
            await asyncio.sleep(0.05)
            return await real_run(
                args, cwd=cwd, timeout=timeout, extra_env=extra_env, phase=phase
            )
        finally:
            state["depth"] -= 1

    monkeypatch.setattr(git_mod.spawn, "_run_git", _slow_git)
    return peak


def test_repo_lock_covers_exactly_the_index_writers():
    """Serialization follows ``index.lock``, not「是不是写」— pin both directions."""
    from agentcore.tools.builtin.git_ops import (
        _INDEX_LOCK_SUBCOMMANDS,
        git_call_needs_repo_lock,
        git_write_subcommands,
    )

    for sub in (
        "add",
        "commit",
        "checkout",
        "merge",
        "rebase",
        "cherry-pick",
        "pull",
        "init_baseline",
    ):
        assert git_call_needs_repo_lock({"subcommand": sub}) is True, sub
    # Writes that never take index.lock keep their concurrency — push's remote round
    # trip must not park a sibling commit behind a minute of network.
    for sub in ("push", "create_pr", "branch", "clone"):
        assert git_call_needs_repo_lock({"subcommand": sub}) is False, sub
    for sub in ("status", "diff", "log", "fetch", "show", "blame"):
        assert git_call_needs_repo_lock({"subcommand": sub}) is False, sub
    # Action-gated verbs follow the action, exactly as the approval gate does.
    assert git_call_needs_repo_lock({"subcommand": "stash", "action": "push"}) is True
    assert git_call_needs_repo_lock({"subcommand": "stash", "action": "pop"}) is True
    assert git_call_needs_repo_lock({"subcommand": "stash"}) is False
    assert git_call_needs_repo_lock({"subcommand": "tag", "action": "create"}) is False
    assert git_call_needs_repo_lock({"subcommand": "remote", "action": "add"}) is False
    # The queue may only ever gate writes.
    assert git_write_subcommands() >= _INDEX_LOCK_SUBCOMMANDS


def test_repo_lock_key_is_per_checkout(tmp_path: Path):
    """One key per real ``.git`` — never a global lock, never two keys for one repo."""
    from agentcore.tools.builtin.git_ops import repo_lock_key

    left = tmp_path / "left"
    right = tmp_path / "right"
    left.mkdir()
    right.mkdir()
    ctx = _worker_ctx(left)
    assert repo_lock_key(str(left), ctx) != repo_lock_key(str(right), ctx)
    # Same checkout reached by two conversations shares one queue: they share one
    # ``.git/index.lock`` on disk, so serializing them is the whole point.
    assert repo_lock_key(str(left), _worker_ctx(left)) == repo_lock_key(
        str(left), _worker_ctx(left, user_id="other")
    )
    # Casing follows the platform's own path identity (``os.path.normcase``), because that
    # is what decides whether two spellings name one ``.git``. Windows hands out the same
    # directory under different casing, so both spellings must land on one queue or two
    # callers would race on a single ``index.lock``. On a case-sensitive filesystem (Linux)
    # ``/tmp/left`` and ``/TMP/LEFT`` are two different directories with two different
    # ``.git``: folding them would queue unrelated repos behind each other on a false identity.
    upper = repo_lock_key(str(left).upper(), ctx)
    lower = repo_lock_key(str(left).lower(), ctx)
    if os.path.normcase("A") == "a":
        assert upper == lower
    else:
        assert upper != lower


async def test_same_repo_index_writes_serialize(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Two writes dispatched in one round must not overlap on one repo's index."""
    import asyncio

    repo = _init_repo(tmp_path / "repo")
    (repo / "a.txt").write_text("a\n", encoding="utf-8")
    (repo / "b.txt").write_text("b\n", encoding="utf-8")
    peak = _concurrency_probe(monkeypatch)
    ctx = _worker_ctx(repo)

    results = await asyncio.gather(
        GitTool().execute({"subcommand": "add", "paths": ["a.txt"]}, ctx),
        GitTool().execute({"subcommand": "add", "paths": ["b.txt"]}, ctx),
    )

    assert all(r.success for r in results), [r.error for r in results]
    assert peak[0] == 1
    # Queued, not refused: the second write still ran.
    staged = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        env=_GIT_ENV,
    ).stdout
    assert {"a.txt", "b.txt"} <= set(staged.split())


async def test_different_repos_never_queue_behind_each_other(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The lock is per repo, not global — unrelated workspaces keep full parallelism."""
    import asyncio

    left = _init_repo(tmp_path / "left")
    right = _init_repo(tmp_path / "right")
    (left / "a.txt").write_text("a\n", encoding="utf-8")
    (right / "b.txt").write_text("b\n", encoding="utf-8")
    peak = _concurrency_probe(monkeypatch)

    results = await asyncio.gather(
        GitTool().execute(
            {"subcommand": "add", "paths": ["a.txt"]}, _worker_ctx(left)
        ),
        GitTool().execute(
            {"subcommand": "add", "paths": ["b.txt"]}, _worker_ctx(right)
        ),
    )

    assert all(r.success for r in results), [r.error for r in results]
    assert peak[0] == 2


async def test_reads_do_not_queue_behind_an_index_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Reads keep their concurrency: ``GIT_OPTIONAL_LOCKS=0`` keeps them off the index."""
    import asyncio

    repo = _init_repo(tmp_path / "repo")
    (repo / "a.txt").write_text("a\n", encoding="utf-8")
    peak = _concurrency_probe(monkeypatch)
    ctx = _worker_ctx(repo)

    results = await asyncio.gather(
        GitTool().execute({"subcommand": "add", "paths": ["a.txt"]}, ctx),
        GitTool().execute({"subcommand": "status"}, ctx),
    )

    assert all(r.success for r in results), [r.error for r in results]
    assert peak[0] == 2


async def test_repo_busy_is_reported_honestly_when_the_wait_expires(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A full queue fails as 「仓库正忙」 with nothing executed — never as a git fault."""
    from agentcore.tools.builtin import git_ops as git_mod
    from agentcore.tools.builtin.git_ops import _REPO_BUSY_CODE, repo_lock_key

    repo = _init_repo(tmp_path / "repo")
    (repo / "a.txt").write_text("a\n", encoding="utf-8")
    ctx = _worker_ctx(repo)

    real_acquire = git_mod.repo_lock._acquire_repo_lock

    async def _short_wait(lock: Any, *, timeout: float = 0.05):
        return await real_acquire(lock, timeout=0.05)

    async def _refuse_git(args: list[str], **_kwargs: Any):
        raise AssertionError(f"a busy repo must not spawn git: {args}")

    monkeypatch.setattr(git_mod.repo_lock, "_acquire_repo_lock", _short_wait)
    monkeypatch.setattr(git_mod.spawn, "_run_git", _refuse_git)

    held = git_mod.repo_lock._get_repo_lock(repo_lock_key(str(repo.resolve()), ctx))
    await held.acquire()
    try:
        result = await GitTool().execute(
            {"subcommand": "add", "paths": ["a.txt"]}, ctx
        )
    finally:
        held.release()

    assert result.success is False
    assert result.metadata.get("code") == _REPO_BUSY_CODE
    assert result.metadata.get("subcommand") == "add"
    assert "仓库状态未改变" in (result.error or "")
    # Attribution must not drift into the git-failure vocabulary.
    assert "index.lock" not in (result.error or "")


async def test_repo_lock_wait_is_bounded_and_releases_cleanly():
    """A timed-out waiter gives up on schedule and never strands the lock."""
    import asyncio
    import time as time_mod

    from agentcore.tools.builtin.git_ops.repo_lock import _acquire_repo_lock

    lock = asyncio.Lock()
    await lock.acquire()
    started = time_mod.monotonic()
    assert await _acquire_repo_lock(lock, timeout=0.05) is False
    assert time_mod.monotonic() - started < 5.0
    lock.release()
    assert await _acquire_repo_lock(lock, timeout=0.05) is True
    lock.release()


async def test_status_hides_untracked_by_default(tmp_path: Path):
    repo = _init_repo(tmp_path / "repo")
    (repo / "ghost.txt").write_text("untracked\n", encoding="utf-8")
    result = await GitTool().execute({"subcommand": "status"}, _ceo_ctx(repo))
    assert result.success is True
    assert "ghost.txt" not in result.output
    assert result.metadata.get("include_untracked") is False

    shown = await GitTool().execute(
        {"subcommand": "status", "include_untracked": True},
        _ceo_ctx(repo),
    )
    assert shown.success is True
    assert "ghost.txt" in shown.output
    assert shown.metadata.get("include_untracked") is True


async def test_status_truncates_long_porcelain(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from agentcore.tools.builtin import git_ops as git_mod

    monkeypatch.setattr(git_mod.policy, "_STATUS_LINE_LIMIT", 3)
    repo = _init_repo(tmp_path / "repo")
    for i in range(6):
        (repo / f"f{i}.txt").write_text(f"{i}\n", encoding="utf-8")
        _run_git(repo, "add", f"f{i}.txt")
    result = await GitTool().execute({"subcommand": "status"}, _ceo_ctx(repo))
    assert result.success is True
    assert "已截断" in result.output
    assert result.metadata.get("truncated") is True
    assert (result.metadata.get("status_lines") or 0) >= 4


def test_parse_status_sb_extracts_branch():
    from agentcore.tools.builtin.git_ops import _parse_status_sb

    branch, body = _parse_status_sb("## feature/work...origin/feature/work\n M a.py\n")
    assert branch == "feature/work"
    assert "M a.py" in body
    branch2, body2 = _parse_status_sb("## main\n")
    assert branch2 == "main"
    assert body2 == ""


# --- init_baseline (P3 soft git baseline) ---


async def test_init_baseline_creates_repo_and_first_commit(tmp_path: Path):
    bare = tmp_path / "project"
    bare.mkdir()
    (bare / "app.py").write_text("print('hi')\n", encoding="utf-8")
    assert not (bare / ".git").exists()

    result = await GitTool().execute({"subcommand": "init_baseline"}, _ceo_ctx(bare))
    assert result.success is True
    assert (bare / ".git").exists()
    assert "首提交" in result.output or "baseline" in result.output.lower()
    assert result.metadata.get("sha")
    # Tree is tracked after first commit.
    status = await GitTool().execute(
        {"subcommand": "status", "include_untracked": True}, _ceo_ctx(bare)
    )
    assert status.success is True
    assert "app.py" not in (status.output or "") or "工作区干净" in (status.output or "")


async def test_init_baseline_dirty_existing_repo_skips_commit(tmp_path: Path):
    repo = _init_repo(tmp_path / "repo")
    (repo / "README.md").write_text("dirty\n", encoding="utf-8")
    result = await GitTool().execute({"subcommand": "init_baseline"}, _ceo_ctx(repo))
    assert result.success is True
    assert result.metadata.get("code") == "dirty_skip"
    assert "不代为 commit" in result.output
    # Dirty content must remain uncommitted.
    porcelain = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        env=_GIT_ENV,
    )
    assert porcelain.stdout.strip()


async def test_init_baseline_clean_existing_repo_reports_already(tmp_path: Path):
    repo = _init_repo(tmp_path / "repo")
    result = await GitTool().execute({"subcommand": "init_baseline"}, _ceo_ctx(repo))
    assert result.success is True
    assert result.metadata.get("code") == "already_repo"
    assert "无需 init_baseline" in result.output


def test_init_baseline_in_write_allowlist():
    assert "init_baseline" in _ALLOWED_SUBCOMMANDS
    assert "init_baseline" in git_write_subcommands()


# --- clone (G3 Agent shallow clone under tool cwd) ---


def _patch_clone_url_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hermetic clones use file://; skip http(s)/GitHub/SSRF for the spawn path."""
    from agentcore.tools.builtin.git_ops import cmds_remote as cmds_remote_mod

    async def _skip(_url: str, _start: float):
        return None

    monkeypatch.setattr(cmds_remote_mod, "_clone_url_policy_error", _skip)


def test_clone_in_allowlist_write_approval_and_budget():
    from agentcore.core.types import ToolApproval
    from agentcore.runtime.always_confirm import requires_always_confirm
    from agentcore.runtime.approvals import tool_call_requires_approval
    from agentcore.tools.builtin.git_ops import (
        GIT_TOOL_PARAMETERS,
        git_call_is_write,
        git_call_needs_repo_lock,
        git_tool_timeout_seconds,
    )

    assert "clone" in _ALLOWED_SUBCOMMANDS
    assert "clone" in git_write_subcommands()
    assert "clone" in GIT_TOOL_PARAMETERS["properties"]["subcommand"]["enum"]
    assert git_call_is_write({"subcommand": "clone"}) is True
    assert git_call_needs_repo_lock({"subcommand": "clone"}) is False
    clone_args = {"subcommand": "clone", "url": "https://github.com/o/r.git"}
    assert tool_call_requires_approval("git", ToolApproval.NEVER, clone_args) is True
    assert requires_always_confirm("git", clone_args) is False
    assert git_tool_timeout_seconds({"subcommand": "clone"}) == git_tool_timeout_seconds(
        {"subcommand": "fetch"}
    )


def test_clone_schema_has_no_password_params():
    from agentcore.tools.builtin.git_ops import GIT_TOOL_PARAMETERS

    props = GIT_TOOL_PARAMETERS["properties"]
    forbidden = {"password", "token", "pat", "credential", "access_token", "secret"}
    assert forbidden.isdisjoint(props)
    assert "dest" in props
    assert "clone" in props["subcommand"]["enum"]


async def test_clone_rejects_password_argument(tmp_path: Path):
    result = await GitTool().execute(
        {
            "subcommand": "clone",
            "url": "https://github.com/acme/demo.git",
            "password": "s3cret",
        },
        _ceo_ctx(tmp_path),
    )
    assert result.success is False
    assert "密码" in (result.error or "") or "凭据" in (result.error or "")


async def test_clone_rejects_ssrf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from agentcore.core.net import URLBlock
    from agentcore.workspace import git as gitmod

    async def _blocked(_url: str):
        return URLBlock.BLOCKED_HOST

    monkeypatch.setattr(gitmod, "classify_url", _blocked)
    spawned: list[list[str]] = []

    async def _refuse_git(args: list[str], **_kwargs: Any):
        spawned.append(list(args))
        raise AssertionError(f"SSRF must not spawn git: {args}")

    from agentcore.tools.builtin.git_ops import spawn as spawn_mod

    monkeypatch.setattr(spawn_mod, "_run_git", _refuse_git)
    result = await GitTool().execute(
        {"subcommand": "clone", "url": "https://github.com/owner/repo.git"},
        _ceo_ctx(tmp_path),
    )
    assert result.success is False
    assert spawned == []
    err = result.error or ""
    assert "本地" in err or "内网" in err or "保留" in err


@pytest.mark.parametrize(
    "url",
    [
        "ssh://git@github.com/x/y.git",
        "git@github.com:x/y.git",
        "https://gitlab.com/x/y.git",
        "https://example.com/x/y.git",
    ],
)
async def test_clone_rejects_non_github_and_non_http(tmp_path: Path, url: str):
    result = await GitTool().execute(
        {"subcommand": "clone", "url": url}, _ceo_ctx(tmp_path)
    )
    assert result.success is False
    err = result.error or ""
    assert "http(s)" in err or "GitHub" in err


async def test_clone_empty_dest_shallow_succeeds_as_ceo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _patch_clone_url_policy(monkeypatch)
    src = _init_repo(tmp_path / "demo")
    ws = tmp_path / "ws"
    ws.mkdir()
    result = await GitTool().execute(
        {"subcommand": "clone", "url": src.as_uri()},
        _ceo_ctx(ws),
    )
    assert result.success is True, result.error
    cloned = ws / "demo"
    assert (cloned / ".git").exists()
    text = (cloned / "README.md").read_text(encoding="utf-8").replace("\r\n", "\n")
    assert text == "hello\n"
    shallow = subprocess.run(
        ["git", "rev-parse", "--is-shallow-repository"],
        cwd=cloned,
        check=True,
        capture_output=True,
        text=True,
        env=_GIT_ENV,
    )
    assert shallow.stdout.strip() == "true"
    assert result.metadata.get("shallow") is True
    assert result.metadata.get("dest") == "demo"


async def test_clone_rejects_nonempty_dest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _patch_clone_url_policy(monkeypatch)
    src = _init_repo(tmp_path / "demo")
    ws = tmp_path / "ws"
    (ws / "demo").mkdir(parents=True)
    (ws / "demo" / "f.txt").write_text("occupied\n", encoding="utf-8")
    spawned: list[list[str]] = []

    async def _refuse_git(args: list[str], **_kwargs: Any):
        spawned.append(list(args))
        raise AssertionError(f"nonempty dest must not spawn git: {args}")

    from agentcore.tools.builtin.git_ops import spawn as spawn_mod

    monkeypatch.setattr(spawn_mod, "_run_git", _refuse_git)
    result = await GitTool().execute(
        {"subcommand": "clone", "url": src.as_uri()},
        _ceo_ctx(ws),
    )
    assert result.success is False
    assert spawned == []
    assert "非空" in (result.error or "")


# --- G2 collaboration: stash / merge / rebase / cherry-pick / tag / remote ---


def test_g2_list_actions_skip_approval_writes_require():
    from agentcore.core.types import ToolApproval
    from agentcore.runtime.approvals import tool_call_requires_approval
    from agentcore.tools.builtin.git_ops import git_call_is_write

    schema = ToolApproval.NEVER
    assert tool_call_requires_approval(
        "git", schema, {"subcommand": "stash", "action": "list"}
    ) is False
    assert tool_call_requires_approval(
        "git", schema, {"subcommand": "stash", "action": "push"}
    ) is True
    assert tool_call_requires_approval(
        "git", schema, {"subcommand": "tag", "action": "list"}
    ) is False
    assert tool_call_requires_approval(
        "git", schema, {"subcommand": "tag", "action": "create", "name": "v1"}
    ) is True
    assert tool_call_requires_approval(
        "git", schema, {"subcommand": "remote", "action": "list"}
    ) is False
    assert tool_call_requires_approval(
        "git", schema, {"subcommand": "remote", "action": "add", "name": "u", "url": "https://x"}
    ) is True
    assert tool_call_requires_approval(
        "git", schema, {"subcommand": "merge", "ref": "other"}
    ) is True
    assert git_call_is_write({"subcommand": "stash"}) is False  # default list
    assert git_call_is_write({"subcommand": "stash", "action": "pop"}) is True


async def test_stash_list_push_pop_roundtrip(tmp_path: Path):
    repo = _init_repo(tmp_path / "repo")
    (repo / "README.md").write_text("stashed\n", encoding="utf-8")
    push = await GitTool().execute(
        {"subcommand": "stash", "action": "push", "message": "wip"},
        _worker_ctx(repo),
    )
    assert push.success is True, push.error
    porcelain = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        env=_GIT_ENV,
    )
    assert not porcelain.stdout.strip()

    listed = await GitTool().execute(
        {"subcommand": "stash", "action": "list"}, _ceo_ctx(repo)
    )
    assert listed.success is True
    assert "wip" in listed.output or "stash@{" in listed.output

    pop = await GitTool().execute(
        {"subcommand": "stash", "action": "pop"}, _worker_ctx(repo)
    )
    assert pop.success is True, pop.error
    assert (repo / "README.md").read_text(encoding="utf-8") == "stashed\n"


async def test_stash_drop_clear_rejected(tmp_path: Path):
    repo = _init_repo(tmp_path / "repo")
    for action in ("drop", "clear"):
        result = await GitTool().execute(
            {"subcommand": "stash", "action": action}, _worker_ctx(repo)
        )
        assert result.success is False
        assert "禁止" in (result.error or "")


async def test_merge_succeeds_and_conflict_stops_honestly(tmp_path: Path):
    repo = _init_repo(tmp_path / "repo", branch="feature/base")
    _run_git(repo, "checkout", "-b", "feature/a")
    (repo / "README.md").write_text("A\n", encoding="utf-8")
    _run_git(repo, "add", "README.md")
    _run_git(repo, "commit", "-m", "a")
    _run_git(repo, "checkout", "feature/base")
    _run_git(repo, "checkout", "-b", "feature/b")
    (repo / "README.md").write_text("B\n", encoding="utf-8")
    _run_git(repo, "add", "README.md")
    _run_git(repo, "commit", "-m", "b")

    # Clean merge from a common ancestor with a non-conflicting side branch.
    _run_git(repo, "checkout", "feature/base")
    (repo / "other.txt").write_text("ok\n", encoding="utf-8")
    _run_git(repo, "add", "other.txt")
    _run_git(repo, "commit", "-m", "base advance")
    ok = await GitTool().execute(
        {"subcommand": "merge", "ref": "feature/a"}, _worker_ctx(repo)
    )
    assert ok.success is True, ok.error
    assert "已合并" in ok.output

    # Conflict: merge feature/b into feature/a lineage.
    _run_git(repo, "checkout", "feature/a")
    conflict = await GitTool().execute(
        {"subcommand": "merge", "ref": "feature/b"}, _worker_ctx(repo)
    )
    assert conflict.success is False
    assert conflict.metadata.get("conflict") is True or "冲突" in (conflict.error or "")
    assert "自动 resolve" in (conflict.error or "") or "诚实" in (conflict.error or "")


async def test_merge_rejects_force_knobs(tmp_path: Path):
    repo = _init_repo(tmp_path / "repo")
    result = await GitTool().execute(
        {"subcommand": "merge", "ref": "other", "force": True},
        _worker_ctx(repo),
    )
    assert result.success is False
    assert "禁止" in (result.error or "") or "旋钮" in (result.error or "")


async def test_rebase_onto_upstream(tmp_path: Path):
    repo = _init_repo(tmp_path / "repo", branch="feature/mainline")
    (repo / "base.txt").write_text("base\n", encoding="utf-8")
    _run_git(repo, "add", "base.txt")
    _run_git(repo, "commit", "-m", "basefile")
    _run_git(repo, "checkout", "-b", "feature/topic")
    (repo / "topic.txt").write_text("topic\n", encoding="utf-8")
    _run_git(repo, "add", "topic.txt")
    _run_git(repo, "commit", "-m", "topic")
    _run_git(repo, "checkout", "feature/mainline")
    (repo / "base.txt").write_text("base2\n", encoding="utf-8")
    _run_git(repo, "add", "base.txt")
    _run_git(repo, "commit", "-m", "mainline advance")
    _run_git(repo, "checkout", "feature/topic")
    result = await GitTool().execute(
        {"subcommand": "rebase", "ref": "feature/mainline"},
        _worker_ctx(repo),
    )
    assert result.success is True, result.error
    assert "rebase" in result.output.lower() or "已 rebase" in result.output


async def test_rebase_conflict_stops(tmp_path: Path):
    repo = _init_repo(tmp_path / "repo", branch="feature/mainline")
    (repo / "README.md").write_text("main\n", encoding="utf-8")
    _run_git(repo, "add", "README.md")
    _run_git(repo, "commit", "-m", "main edit")
    _run_git(repo, "checkout", "-b", "feature/topic")
    # Reset topic to before main edit, then diverge.
    _run_git(repo, "reset", "--hard", "HEAD~1")
    (repo / "README.md").write_text("topic\n", encoding="utf-8")
    _run_git(repo, "add", "README.md")
    _run_git(repo, "commit", "-m", "topic edit")
    result = await GitTool().execute(
        {"subcommand": "rebase", "ref": "feature/mainline"},
        _worker_ctx(repo),
    )
    assert result.success is False
    assert result.metadata.get("conflict") is True or "冲突" in (result.error or "")


async def test_cherry_pick_applies_commit(tmp_path: Path):
    repo = _init_repo(tmp_path / "repo", branch="feature/a")
    (repo / "pick.txt").write_text("picked\n", encoding="utf-8")
    _run_git(repo, "add", "pick.txt")
    _run_git(repo, "commit", "-m", "to pick")
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        env=_GIT_ENV,
    ).stdout.strip()
    _run_git(repo, "checkout", "-b", "feature/b")
    _run_git(repo, "reset", "--hard", "HEAD~1")
    result = await GitTool().execute(
        {"subcommand": "cherry-pick", "ref": sha},
        _worker_ctx(repo),
    )
    assert result.success is True, result.error
    assert (repo / "pick.txt").read_text(encoding="utf-8") == "picked\n"


async def test_tag_list_and_create_rejects_delete(tmp_path: Path):
    repo = _init_repo(tmp_path / "repo")
    created = await GitTool().execute(
        {"subcommand": "tag", "action": "create", "name": "v1.0"},
        _worker_ctx(repo),
    )
    assert created.success is True, created.error
    listed = await GitTool().execute(
        {"subcommand": "tag", "action": "list"}, _ceo_ctx(repo)
    )
    assert listed.success is True
    assert "v1.0" in listed.output
    deleted = await GitTool().execute(
        {"subcommand": "tag", "action": "delete", "name": "v1.0"},
        _worker_ctx(repo),
    )
    assert deleted.success is False
    assert "禁止" in (deleted.error or "")


async def test_remote_list_and_add_rejects_remove(tmp_path: Path):
    repo = _init_repo(tmp_path / "repo")
    bare = tmp_path / "remote.git"
    _run_git(tmp_path, "init", "--bare", str(bare))
    added = await GitTool().execute(
        {
            "subcommand": "remote",
            "action": "add",
            "name": "origin",
            "url": str(bare),
        },
        _worker_ctx(repo),
    )
    assert added.success is True, added.error
    listed = await GitTool().execute(
        {"subcommand": "remote", "action": "list"}, _ceo_ctx(repo)
    )
    assert listed.success is True
    assert "origin" in listed.output
    removed = await GitTool().execute(
        {"subcommand": "remote", "action": "remove", "name": "origin"},
        _worker_ctx(repo),
    )
    assert removed.success is False
    assert "禁止" in (removed.error or "")


async def test_reset_and_clean_still_rejected(tmp_path: Path):
    repo = _init_repo(tmp_path / "repo")
    for sub in ("reset", "clean"):
        result = await GitTool().execute({"subcommand": sub}, _worker_ctx(repo))
        assert result.success is False
        assert (
            "不在允许列表中" in (result.error or "")
            or "被安全策略拒绝" in (result.error or "")
        )


async def test_merge_on_protected_branch_rejected(tmp_path: Path):
    repo = _init_repo(tmp_path / "repo", branch="main")
    _run_git(repo, "checkout", "-b", "feature/side")
    (repo / "side.txt").write_text("s\n", encoding="utf-8")
    _run_git(repo, "add", "side.txt")
    _run_git(repo, "commit", "-m", "side")
    _run_git(repo, "checkout", "main")
    result = await GitTool().execute(
        {"subcommand": "merge", "ref": "feature/side"},
        _worker_ctx(repo),
    )
    assert result.success is False
    assert "main/master" in (result.error or "")
