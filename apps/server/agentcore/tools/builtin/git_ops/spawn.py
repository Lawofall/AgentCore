"""Git subprocess spawn, reap, bounded auth lookups, failure attribution.

ServerWorkspace / Sidecar: same-process ``git`` under ``backend.root``.
LocalWorkspace (no Path.root): route via ``WorkspaceOp.GIT_RUN`` on the bound
desktop channel — same allowlisted argv surface, executed on the user machine.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import os
import signal
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agentcore.core.logging import get_logger
from agentcore.tools.protocol import ToolContext, ToolResult
from agentcore.workspace.channel import WorkspaceOp
from agentcore.workspace.protocol import WorkspaceError

from . import policy as policy_mod
from .phases import PHASE_CREDENTIALS, PHASE_LOCAL, report_phase
from .policy import (
    _GIT_CREDENTIAL_TIMEOUT,
    _GIT_KILL_SLACK,
    _GIT_TIMEOUT,
    _GIT_TOKEN_RESOLVE_TIMEOUT,
    _PROTECTED_BRANCHES,
)
from .results import _error, _ok

if TYPE_CHECKING:
    from agentcore.workspace.git_credentials import GitAuthMaterial

logger = get_logger(__name__)

# Bound for the duration of one GitTool.execute when LocalWorkspace has no Path.root.
_git_channel: contextvars.ContextVar[Any] = contextvars.ContextVar(
    "git_ops_channel", default=None
)
_git_backend: contextvars.ContextVar[Any] = contextvars.ContextVar(
    "git_ops_backend", default=None
)

_NO_CHANNEL_MSG = (
    "本机工作区未连接桌面通道，无法执行 Git。"
    "请确认桌面在线且已绑定本地项目后再试。"
)


def _resolve_git_cwd(context: ToolContext) -> str | None:
    """Return the absolute workspace root for git, when available."""
    root = getattr(context.backend, "root", None)
    if root is not None:
        # Always absolute so GIT_CEILING_DIRECTORIES matches the process cwd.
        return str(Path(root).resolve())
    return None


@contextlib.contextmanager
def git_transport_scope(context: ToolContext) -> Iterator[str | None]:
    """Bind channel transport for LocalWorkspace; yield cwd or ``None`` if unusable.

    Yields:
      - absolute Path cwd when ``backend.root`` is set (subprocess path);
      - ``""`` when channel-backed (cwd unused; desktop uses ``root_id``);
      - ``None`` when neither root nor ``workspace_channel`` is available.
    """
    cwd = _resolve_git_cwd(context)
    channel = None
    backend = None
    if cwd is None:
        channel = context.workspace_channel
        if channel is None:
            yield None
            return
        backend = context.backend
        cwd = ""
    token_ch = _git_channel.set(channel)
    token_be = _git_backend.set(backend)
    try:
        yield cwd
    finally:
        _git_channel.reset(token_ch)
        _git_backend.reset(token_be)


def _workspace_has_local_git(cwd: str) -> bool:
    """True only when ``.git`` exists *inside* the workspace root (no parent climb)."""
    return (Path(cwd) / ".git").exists()


async def _workspace_has_git_meta(cwd: str) -> bool:
    """Root ``.git`` probe — Path when local, ``backend.exists`` when channel-backed."""
    backend = _git_backend.get()
    if backend is not None:
        exists = getattr(backend, "exists", None)
        if exists is None:
            return False
        return bool(await exists(".git"))
    return _workspace_has_local_git(cwd)


def _git_spawn_kwargs() -> dict[str, Any]:
    """POSIX: new session so timeout can ``killpg`` the whole tree (sandbox pattern)."""
    return {} if sys.platform == "win32" else {"start_new_session": True}


async def _reap_git_process(proc: asyncio.subprocess.Process) -> None:
    """Kill the git child and descendants, then reap (best-effort, never raises)."""
    if proc.returncode is not None:
        return
    pid = proc.pid
    if sys.platform == "win32":
        with contextlib.suppress(Exception):
            await asyncio.to_thread(
                subprocess.run,
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
    else:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(pid, signal.SIGKILL)
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
    with contextlib.suppress(Exception):
        await asyncio.wait_for(proc.wait(), timeout=5.0)


_AUTH_FAILURE_MARKERS = (
    "authentication failed",
    "could not read username",
    "invalid username or password",
    "access denied",
    "permission denied (publickey)",
    "authentication required",
    "fatal: could not read",
    "http basic: access denied",
    "repository not found",
    "the requested url returned error: 401",
    "the requested url returned error: 403",
    "terminal prompts disabled",
)

_AUTH_FAILURE_HINT = (
    "需要凭据：请到「设置 → Git 凭据」配置账户级 PAT（云工作区私仓），"
    "或打开已配置 OS credential helper / gh auth 的本地仓库后再试。"
)


def _looks_like_auth_failure(blob: str) -> bool:
    lower = blob.lower()
    return any(m in lower for m in _AUTH_FAILURE_MARKERS)


# Root ``.git`` is there but git will not operate it: dangling gitdir pointer, bare
# repo, corrupt index / objects. Attribution for the optimistic path — the primary
# command surfaces these instead of a pre-flight probe.
_UNUSABLE_REPO_MARKERS = (
    "not a git repository",
    "not a work tree",
    "must be run in a work tree",
    "index file corrupt",
    "bad index file",
    "is corrupt",
    "corrupt loose object",
)

_UNUSABLE_REPO_HINT = (
    "工作区根有 .git，但 git 无法把它当作可用工作树"
    "（gitdir 指向失效 / 裸仓 / 仓库损坏）——这不是「无仓库」，请人工检查后再试。"
)


def _looks_like_unusable_repo(blob: str) -> bool:
    lower = blob.lower()
    return any(m in lower for m in _UNUSABLE_REPO_MARKERS)


def _is_cloud_backend(context: ToolContext) -> bool:
    """Cloud ServerWorkspace only — Local inherits OS / gh auth, do not inject PAT."""
    return getattr(context.backend, "location", None) == "server"


async def _load_account_git_auth(
    user_id: str, *, timeout: float = _GIT_CREDENTIAL_TIMEOUT
) -> GitAuthMaterial | None:
    """Bounded, fail-soft account PAT lookup (DB session + row read + decrypt).

    The store is fail-soft on errors but unbounded in time, and it sits on the
    serial path the engine ceiling budgets. A timeout is treated exactly like
    "no PAT configured": git runs without injected credentials and fails
    authentication honestly rather than the whole tool call dying here.
    """
    from agentcore.workspace.git_credentials import load_git_auth_for_user

    try:
        return await asyncio.wait_for(load_git_auth_for_user(user_id), timeout)
    except TimeoutError:
        logger.warning(
            "git.credential_lookup_timeout", scope="account_pat", timeout_seconds=timeout
        )
        return None


async def _resolve_pr_token(
    user_id: str | None, *, timeout: float = _GIT_TOKEN_RESOLVE_TIMEOUT
) -> tuple[str | None, bool]:
    """Bounded GitHub token resolution; returns ``(token, timed_out)``.

    One bound over the whole PAT → env → ``gh auth token`` chain. Unlike push,
    create_pr cannot proceed without a token, so the caller reports the timeout
    instead of silently blaming missing credentials.
    """
    from agentcore.workspace.github_pr import resolve_github_token

    try:
        token = await asyncio.wait_for(resolve_github_token(user_id=user_id), timeout)
    except TimeoutError:
        logger.warning(
            "git.credential_lookup_timeout", scope="pr_token", timeout_seconds=timeout
        )
        return None, True
    return token, False


async def _cloud_network_extra_env(context: ToolContext) -> dict[str, str] | None:
    """Credential helper env for cloud push/fetch/pull/clone when an account PAT exists."""
    if not _is_cloud_backend(context) or not context.user_id:
        return None
    # After the guards: a local workspace inherits OS / gh auth and looks nothing up,
    # so it must never claim this phase.
    report_phase(PHASE_CREDENTIALS)
    auth = await _load_account_git_auth(context.user_id)
    if auth is None:
        return None
    # Inline credential helper via GIT_CONFIG_* (no disk write of the token).
    helper = (
        f'!f() {{ echo "username={auth.username}"; echo "password={auth.token}"; }}; f'
    )
    return {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "credential.helper",
        "GIT_CONFIG_VALUE_0": helper,
    }


async def _run_git_via_channel(
    args: list[str],
    *,
    channel: Any,
    timeout: float,
) -> tuple[str, str, int]:
    """Desktop ``git_run`` — returns stdout/stderr/exit_code (never raises WorkspaceError).

    When the bound backend has a non-empty ``base_subpath`` (Local D1a), pass it as
    ``cwd`` so desktop git runs in the project subdirectory — same baseline as
    ``backend.exists(".git")`` / file ops (G1+G2). Empty / missing → container root
    (open-folder: root is the project).
    """
    payload: dict[str, Any] = {"argv": list(args)}
    if timeout != _GIT_TIMEOUT:
        payload["timeout_seconds"] = float(timeout)
    backend = _git_backend.get()
    base = getattr(backend, "base_subpath", None) if backend is not None else None
    if isinstance(base, str):
        sub = base.strip().strip("/").replace("\\", "/")
        if sub and sub != ".":
            payload["cwd"] = sub
    try:
        value = await channel.request(
            WorkspaceOp.GIT_RUN,
            payload,
            timeout=float(timeout) + _GIT_KILL_SLACK,
        )
    except WorkspaceError as exc:
        return "", str(exc) or exc.__class__.__name__, 1
    except Exception as exc:  # noqa: BLE001 — tool layer wants a process-shaped triple
        return "", f"git_run 通道失败：{exc}", 1
    if not isinstance(value, dict):
        return "", "git_run 返回格式异常", 1
    try:
        code = int(value.get("exit_code") or 0)
    except (TypeError, ValueError):
        code = 1
    return (
        str(value.get("stdout") or ""),
        str(value.get("stderr") or ""),
        code,
    )


def _git_subprocess_env(cwd: str, extra_env: dict[str, str] | None) -> dict[str, str]:
    """Env for a spawned ``git`` — kept identical to desktop ``gitRun``.

    ``GIT_OPTIONAL_LOCKS=0`` stops read-only commands from refreshing the index, so
    they never queue on ``.git/index.lock`` behind another git; ``GIT_CEILING_DIRECTORIES``
    keeps repo discovery from climbing into a parent repo.
    """
    env = {
        **os.environ,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_CEILING_DIRECTORIES": str(Path(cwd).resolve()),
    }
    if extra_env:
        env.update(extra_env)
    return env


async def _run_git(
    args: list[str],
    *,
    cwd: str,
    timeout: float = _GIT_TIMEOUT,
    extra_env: dict[str, str] | None = None,
    phase: str = PHASE_LOCAL,
) -> tuple[str, str, int]:
    """Run git command, return (stdout, stderr, exit_code).

    With a bound ``WorkspaceChannel`` (LocalWorkspace), issues ``git_run`` on the
    desktop. Otherwise spawns local ``git`` under ``cwd``.
    On timeout / cancel, reaps the process tree (Windows ``taskkill /T``).

    ``phase`` is what the waiting UI is told while this command runs; the default
    says「local git is executing」so only a caller that really is on the network leg
    (``PHASE_REMOTE``) can claim it.
    """
    report_phase(phase)
    channel = _git_channel.get()
    if channel is not None:
        # Local inherits OS / gh auth on the desktop — never inject cloud PAT env.
        return await _run_git_via_channel(args, channel=channel, timeout=timeout)

    env = _git_subprocess_env(cwd, extra_env)
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=cwd,
        # Sidecar stdin is the JSON-RPC pipe. Inheriting it lets git (or Windows
        # I/O completion) stall until the tool timeout — desktop ``git_run``
        # already uses ``stdio: ignore`` for this reason.
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
        **_git_spawn_kwargs(),
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError:
        await _reap_git_process(proc)
        return "", f"git 操作超时（{' '.join(args)}）", 1
    except asyncio.CancelledError:
        await _reap_git_process(proc)
        raise
    return (
        stdout_b.decode("utf-8", errors="replace"),
        stderr_b.decode("utf-8", errors="replace"),
        proc.returncode or 0,
    )


async def _refuse_on_protected_branch(
    cwd: str, *, label: str, start: float
) -> ToolResult | None:
    branch = await _current_branch(cwd)
    if branch in _PROTECTED_BRANCHES:
        return _error(
            f"禁止在 main/master 上直接 {label}，请先 checkout 到功能分支",
            start,
        )
    return None


_NO_LOCAL_REPO_MSG = (
    "当前工作区内没有 Git 仓库（仅识别工作区根下的 .git；不会上溯到父目录仓库）"
)


def _no_repo_ok(start: float) -> ToolResult:
    """Read-only no-repo: success with machine-readable code (never fake a clean tree)."""
    return _ok(
        _NO_LOCAL_REPO_MSG,
        start,
        metadata={"code": policy_mod._NO_REPO_CODE},
    )


async def _ensure_git_repo(
    cwd: str, start: float, *, write: bool
) -> ToolResult | None:
    """Decide the no-repo fork; never spawn git — the primary command is the probe.

    Only the root ``.git`` check lives here, because it alone splits read-only soft
    success (``no_repo``) from a write hard error, and it costs no subprocess. When
    ``.git`` is present the caller runs optimistically: a broken repo fails on the
    real command and ``_git_failure`` attributes it (``repo_unusable``), so a
    corrupt repo can never be reported as "no repo".
    """
    if await _workspace_has_git_meta(cwd):
        return None
    if write:
        return _error(_NO_LOCAL_REPO_MSG, start)
    return _no_repo_ok(start)


async def _current_branch(cwd: str) -> str:
    stdout, _, code = await _run_git(["branch", "--show-current"], cwd=cwd)
    if code != 0:
        return ""
    return stdout.strip()


def _parse_status_sb(stdout: str) -> tuple[str, str]:
    """Parse ``git status -sb`` into (branch_line, body)."""
    lines = stdout.splitlines()
    if not lines:
        return "(无)", ""
    first = lines[0]
    if first.startswith("## "):
        # ``## main...origin/main [ahead 1]`` → branch token before ``...`` / space.
        rest = first[3:].strip()
        branch = rest.split("...", 1)[0].split(" ", 1)[0].strip() or "(无)"
        body = "\n".join(lines[1:]).rstrip()
        return branch, body
    return "(无)", stdout.rstrip()
