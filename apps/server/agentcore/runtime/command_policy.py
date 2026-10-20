"""Argv rules on ``run`` / ``host`` command text.

Segments come from the same shell split as package install (quotes respected).
Each segment is classified by its argv, not by a regex on the raw line.
Nested ``powershell -c`` / ``bash -c`` strings are not expanded.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from agentcore.tools.builtin.package_install import (
    parse_segment_argv,
    split_shell_segments,
)

_PREFIX = r"(?:^|[\n;&|`(])\s*(?:sudo\s+)?"
_PACKAGE_INSTALL = re.compile(
    _PREFIX + r"(?:winget\s+install\b|brew\s+install\b|apt(?:-get)?\s+install\b)",
    re.IGNORECASE,
)

_GIT_TAKES_VALUE = frozenset(
    {
        "-C",
        "-c",
        "--git-dir",
        "--work-tree",
        "--namespace",
        "--super-prefix",
        "--config-env",
    }
)
_SUDO_TAKES_VALUE = frozenset({"-u", "-g", "-h", "-p", "-C", "-T", "--user", "--group"})
_GH_TAKES_VALUE = frozenset({"-R", "--repo", "-h", "--hostname"})
_PROTECTED_BRANCHES = frozenset({"main", "master"})


def command_text(arguments: dict[str, Any] | None) -> str:
    return str((arguments or {}).get("command") or "")


def is_package_install_command(command: str) -> bool:
    """``winget`` / ``brew`` / ``apt`` install — always confirm on ``host``."""
    return bool(command and _PACKAGE_INSTALL.search(command))


_SYSTEM_PACKAGE_BINS = frozenset(
    {"apt", "apt-get", "dpkg", "apk", "yum", "dnf", "pacman"}
)
_BOOTSTRAP_SHELLS = frozenset({"sh", "bash", "dash", "zsh"})
_FETCH_BINS = frozenset({"curl", "wget"})
_CLOUD_SYSTEM_INSTALL_REASON = (
    "这台云桌的系统工具是固定菜单（只读根），不能 apt / dpkg / 用管道把下载装进系统。"
    "项目依赖请用 pip 或 npm（官方源）。"
    "（按参数判定；不展开 bash -c，并非完整拦截。）"
)


def reject_cloud_system_install(command: str) -> str | None:
    """Cloud ``run``: guest root is immutable — no apt/dpkg/curl|sh."""
    argvs = iter_command_argvs(command)
    programs = [_program_name(argv[0]) for argv in argvs if argv]
    if any(name in _SYSTEM_PACKAGE_BINS for name in programs):
        return _CLOUD_SYSTEM_INSTALL_REASON
    if any(name in _FETCH_BINS for name in programs) and any(
        name in _BOOTSTRAP_SHELLS for name in programs
    ):
        return _CLOUD_SYSTEM_INSTALL_REASON
    return None


def command_receives_git_credentials(command: str) -> bool:
    """Account PAT only for one lone ``git`` / ``gh`` process.

    ``git -C repo push`` qualifies. ``git push && curl …`` does not.
    """
    argvs = iter_command_argvs(command)
    if len(argvs) != 1:
        return False
    return _program_name(argvs[0][0]) in {"git", "gh"}


def is_git_publish_command(command: str) -> bool:
    """``git push`` or ``gh pr create`` — always confirm on ``run`` and ``host``."""
    for argv in iter_command_argvs(command):
        if _program_name(argv[0]) == "git":
            parsed = _git_subcommand(argv)
            if parsed is not None and parsed[0] == "push":
                return True
        elif _is_gh_pr_create(argv):
            return True
    return False


@dataclass(frozen=True)
class GitCommandDeny:
    rule_id: str
    reason: str


_FORCE_PROTECTED_REASON = (
    "检测到向 main/master 强制推送的命令（按参数判定；不展开 powershell -c / bash -c，"
    "并非完整拦截）。非完全权限下已硬拒，不可由本轮放行放开；"
    "请改用功能分支、升到完全权限，或在本机终端手动处理。"
)
_FORCE_REASON = (
    "检测到 git push --force（含 --force-with-lease）的命令"
    "（按参数判定；不展开 powershell -c / bash -c，并非完整拦截）。"
    "非完全权限下已硬拒，不可由本轮放行放开。"
)
_PROTECTED_REASON = (
    "检测到把 main/master 作为 git push 目标的命令"
    "（按参数判定；不展开 powershell -c / bash -c，并非完整拦截）。"
    "非完全权限下已硬拒，不可由本轮放行放开。"
)
_RESET_REASON = (
    "检测到 git reset / git clean 的命令"
    "（按参数判定；不展开 powershell -c / bash -c，并非完整拦截）。"
    "非完全权限下已硬拒，不可由本轮放行放开。"
)


def git_command_deny(command: str) -> GitCommandDeny | None:
    """DENY force-push, protected-branch push, ``reset``, and ``clean``.

    Scanner only — ``full`` lifts these at the tool-exec chokepoint.
    ``git -C`` / ``git.exe`` are visible. A wrapper's inner string is not.
    """
    for argv in iter_command_argvs(command):
        if _program_name(argv[0]) != "git":
            continue
        parsed = _git_subcommand(argv)
        if parsed is None:
            continue
        sub, args = parsed
        if sub in {"reset", "clean"}:
            return GitCommandDeny("destructive.git_reset_or_clean", _RESET_REASON)
        if sub != "push":
            continue
        force = _push_is_force(args)
        protected = _push_targets_protected(args)
        if force and protected:
            return GitCommandDeny(
                "destructive.git_force_push_protected",
                _FORCE_PROTECTED_REASON,
            )
        if force:
            return GitCommandDeny("destructive.git_force_push", _FORCE_REASON)
        if protected:
            return GitCommandDeny("destructive.git_push_protected", _PROTECTED_REASON)
    return None


def iter_command_argvs(command: str) -> list[list[str]]:
    """Simple-command argvs, split on shell separators and a bare ``&``."""
    if not command or not command.strip():
        return []
    out: list[list[str]] = []
    for segment in split_shell_segments(command):
        argv = parse_segment_argv(segment)
        if not argv:
            continue
        for piece in _split_background(argv):
            unwrapped = _unwrap_sudo(piece)
            if unwrapped:
                out.append(unwrapped)
    return out


def _split_background(argv: list[str]) -> list[list[str]]:
    groups: list[list[str]] = []
    current: list[str] = []
    for token in argv:
        if token == "&":
            if current:
                groups.append(current)
            current = []
            continue
        current.append(token)
    if current:
        groups.append(current)
    return groups


def _unwrap_sudo(argv: list[str]) -> list[str]:
    if not argv or _program_name(argv[0]) != "sudo":
        return argv
    i = 1
    while i < len(argv):
        token = argv[i]
        if token == "--":
            return argv[i + 1 :]
        if not token.startswith("-"):
            return argv[i:]
        name = token.partition("=")[0]
        i += 1
        if "=" not in token and name in _SUDO_TAKES_VALUE:
            i += 1
    return []


def _program_name(token: str) -> str:
    base = (token or "").lower().replace("\\", "/").rsplit("/", 1)[-1]
    for suffix in (".exe", ".cmd", ".bat", ".com"):
        if base.endswith(suffix):
            return base[: -len(suffix)]
    return base


def _git_subcommand(argv: list[str]) -> tuple[str, list[str]] | None:
    i = 1
    while i < len(argv):
        token = argv[i]
        if token == "--":
            i += 1
            break
        if token.startswith("-"):
            name, eq, _value = token.partition("=")
            i += 1
            if not eq and name in _GIT_TAKES_VALUE:
                i += 1
            continue
        return token.lower(), argv[i + 1 :]
    if i < len(argv) and not argv[i].startswith("-"):
        return argv[i].lower(), argv[i + 1 :]
    return None


def _is_gh_pr_create(argv: list[str]) -> bool:
    if _program_name(argv[0]) != "gh":
        return False
    i = 1
    while i < len(argv) and argv[i].startswith("-"):
        name, eq, _value = argv[i].partition("=")
        i += 1
        if not eq and name in _GH_TAKES_VALUE:
            i += 1
    return argv[i : i + 2] == ["pr", "create"]


def _push_is_force(args: list[str]) -> bool:
    for token in args:
        if token in {"--force", "-f"} or token.startswith("--force"):
            return True
        if token.startswith("-") and not token.startswith("--") and "f" in token[1:]:
            return True
    return False


def _push_targets_protected(args: list[str]) -> bool:
    for token in args:
        if token.startswith("-"):
            continue
        if _ref_is_protected(token):
            return True
    return False


def _ref_is_protected(token: str) -> bool:
    dest = token[1:] if token.startswith("+") else token
    if ":" in dest:
        dest = dest.rsplit(":", 1)[-1]
    leaf = dest.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
    return leaf in _PROTECTED_BRANCHES
