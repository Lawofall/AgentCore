"""Git tool policy: allowlists, write detection, argument validators, schema."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agentcore.runtime.safety_breaker import (
    git_forbidden_subcommands,
    git_protected_branches,
)

from .results import _error

if TYPE_CHECKING:
    from agentcore.tools.protocol import ToolResult

_ALLOWED_SUBCOMMANDS = frozenset(
    {
        "status",
        "diff",
        "log",
        "fetch",
        "show",
        "blame",
        "add",
        "commit",
        "branch",
        "checkout",
        "push",
        "pull",
        "init_baseline",
        "clone",
        "stash",
        "merge",
        "rebase",
        "cherry-pick",
        "tag",
        "remote",
        "create_pr",
    }
)
# Always-mutating verbs (approval + CEO ban + write ensure_repo).
_ALWAYS_WRITE_SUBCOMMANDS = frozenset(
    {
        "add",
        "commit",
        "branch",
        "checkout",
        "push",
        "pull",
        "init_baseline",
        "clone",
        "merge",
        "rebase",
        "cherry-pick",
        "create_pr",
    }
)
# Action-gated verbs: only listed actions mutate; ``list`` is read-only / no approval.
_ACTION_WRITE_MAP: dict[str, frozenset[str]] = {
    "stash": frozenset({"push", "pop"}),
    "tag": frozenset({"create"}),
    "remote": frozenset({"add"}),
}
_WRITE_SUBCOMMANDS = _ALWAYS_WRITE_SUBCOMMANDS | frozenset(_ACTION_WRITE_MAP)
# Writes that never take ``.git/index.lock``: refs (``branch`` / ``tag``), config
# (``remote add``), or the network alone (``push`` reads refs, ``create_pr`` is REST,
# ``clone`` writes a *new* dest tree — not this workspace's index). They stay
# outside the per-repo serializer (``repo_lock``) so they keep running beside an
# index writer — and so ``push``'s remote round trip cannot park an unrelated
# ``commit`` behind a minute of network. Everything else is serialized by
# subtraction, so a newly allowlisted write queues by default.
_NO_INDEX_LOCK_SUBCOMMANDS = frozenset(
    {"branch", "tag", "remote", "push", "create_pr", "clone"}
)
_INDEX_LOCK_SUBCOMMANDS = _WRITE_SUBCOMMANDS - _NO_INDEX_LOCK_SUBCOMMANDS
# CEO may run these writes: one-shot first baseline, and clone into an empty dest
# (user still approves via gate). Same GRANTABLE posture; neither is always-confirm.
_CEO_ALLOWED_WRITE_SUBCOMMANDS = frozenset({"init_baseline", "clone"})
_NO_REPO_CODE = "no_repo"
# Root ``.git`` exists but git refuses it as a work tree — never a soft ``no_repo``.
_REPO_UNUSABLE_CODE = "repo_unusable"
_DIRTY_SKIP_CODE = "dirty_skip"
_ALREADY_REPO_CODE = "already_repo"
# Another index-mutating git call still holds this repo after the bounded wait —
# nothing ran, so this is queue pressure, never a git / repo fault.
_REPO_BUSY_CODE = "repo_busy"
_INIT_BASELINE_MESSAGE = "Initial commit (AgentCore baseline)"
_INIT_BASELINE_AUTHOR_NAME = "AgentCore"
_INIT_BASELINE_AUTHOR_EMAIL = "agentcore@local"
# Strategy / force knobs rejected on merge / rebase / cherry-pick before argv.
_COLLAB_DANGER_KEYS = frozenset(
    {
        "force",
        "force_with_lease",
        "forceWithLease",
        "hard",
        "interactive",
        "autosquash",
        "strategy",
        "strategy_option",
        "strategyOption",
        "no_ff",
        "no-ff",
        "ff_only",
        "ff-only",
        "squash",
        "continue",
        "abort",
        "skip",
        "onto",
        "root",
        "mainline",
        "no_commit",
        "no-commit",
        "signoff",
    }
)


def git_write_subcommands() -> frozenset[str]:
    """Git subcommand names that *can* mutate (membership); prefer ``git_call_is_write``."""
    return _WRITE_SUBCOMMANDS


def git_call_is_write(arguments: dict[str, Any] | None = None) -> bool:
    """Whether this git tool call mutates repo state (approval / CEO / ensure_repo)."""
    args = arguments or {}
    sub = str(args.get("subcommand", "")).strip().lower()
    if sub in _ALWAYS_WRITE_SUBCOMMANDS:
        return True
    allowed_actions = _ACTION_WRITE_MAP.get(sub)
    if allowed_actions is None:
        return False
    action = str(args.get("action") or "list").strip().lower() or "list"
    return action in allowed_actions


def git_call_needs_repo_lock(arguments: dict[str, Any] | None = None) -> bool:
    """Whether this call must hold the per-repo lock (it will take ``index.lock``).

    Read-only calls never qualify: ``GIT_OPTIONAL_LOCKS=0`` keeps them off the index
    entirely. Action-gated verbs go through ``git_call_is_write`` first, so
    ``stash list`` stays a free read while ``stash push`` queues.
    """
    args = arguments or {}
    if not git_call_is_write(args):
        return False
    return str(args.get("subcommand", "")).strip().lower() in _INDEX_LOCK_SUBCOMMANDS


_FORBIDDEN_PATTERNS = git_forbidden_subcommands()
_PROTECTED_BRANCHES = git_protected_branches()
_DIFF_OUTPUT_LIMIT = 16000
_STATUS_LINE_LIMIT = 200
# blame is line-oriented; reuse status porcelain line budget.
_BLAME_LINE_LIMIT = _STATUS_LINE_LIMIT
# Per-subprocess ceiling. Engine outer = serial_ops × this + kill slack.
_GIT_TIMEOUT = 20.0
_GIT_KILL_SLACK = 5.0
# Bounded wait for the per-repo serializer (``repo_lock``), charged to the engine
# ceiling below for exactly the subcommands that queue — so queue time can never
# push the caller past the outer ``wait_for`` (which would retire the git tool for
# the round, far worse than any inner failure). Exhausting it is an honest
# ``repo_busy``, never a git fault. Sized at one ``_GIT_TIMEOUT``: long enough to
# absorb a sibling local write from the same round, short enough that queueing
# behind a slow ``pull`` fails fast instead of burning the caller's whole budget.
_GIT_REPO_LOCK_WAIT = _GIT_TIMEOUT
# Wider ceiling for the one remote round trip a network subcommand makes.
_GIT_NETWORK_TIMEOUT = 60.0
# Account PAT lookup (DB session + row read + AES decrypt). Unbounded at the
# store, so ``spawn._load_account_git_auth`` bounds it here — fail-soft: a timeout
# means "no PAT", git then fails authentication honestly.
_GIT_CREDENTIAL_TIMEOUT = 10.0
# create_pr resolves a token through PAT → env → ``gh auth token``. One bound over
# the whole chain (PAT lookup + the 8s ``gh`` probe), enforced by
# ``spawn._resolve_pr_token`` — so a looser inner probe can never widen this.
_GIT_TOKEN_RESOLVE_TIMEOUT = 18.0
# One GitHub REST call; create_pr makes at most default-branch GET + create POST.
_GITHUB_API_TIMEOUT = 30.0
_GITHUB_API_CALLS = 2
# Worst-case count of bounded (``_GIT_TIMEOUT``) git subprocesses run back to back
# by one tool call, network round trip excluded. The workspace ``.git`` check costs
# no subprocess, so a plain read is the primary command and nothing else.
_SERIAL_GIT_OPS: dict[str, int] = {
    # branch --show-current + commit + rev-parse --short HEAD
    "commit": 3,
    # branch --show-current (protected-branch refusal) + the primary command
    "merge": 2,
    "rebase": 2,
    "cherry-pick": 2,
    # init + add -A + commit + rev-parse --short + branch --show-current
    "init_baseline": 5,
    # branch --show-current + remote
    "push": 2,
    # remote
    "pull": 1,
    "fetch": 1,
    # clone --single-branch --depth 1 (no repo probe)
    "clone": 1,
    # remote + remote get-url + branch --show-current
    "create_pr": 3,
}
_DEFAULT_SERIAL_GIT_OPS = 1
# Serial I/O a network subcommand does *outside* a git subprocess, at the ceiling
# the code actually enforces. Every entry must be bounded somewhere in the call
# path, or the engine ceiling below is a promise nothing keeps.
_NETWORK_IO_BUDGET: dict[str, float] = {
    # PAT lookup, then the git remote round trip.
    "push": _GIT_CREDENTIAL_TIMEOUT + _GIT_NETWORK_TIMEOUT,
    "pull": _GIT_CREDENTIAL_TIMEOUT + _GIT_NETWORK_TIMEOUT,
    "fetch": _GIT_CREDENTIAL_TIMEOUT + _GIT_NETWORK_TIMEOUT,
    "clone": _GIT_CREDENTIAL_TIMEOUT + _GIT_NETWORK_TIMEOUT,
    # Token resolution, then GitHub REST (default-branch GET + create POST).
    "create_pr": _GIT_TOKEN_RESOLVE_TIMEOUT + _GITHUB_API_CALLS * _GITHUB_API_TIMEOUT,
}
_NETWORK_SUBCOMMANDS = frozenset(_NETWORK_IO_BUDGET)


def git_tool_timeout_seconds(arguments: dict[str, Any] | None = None) -> float:
    """Engine wall-clock ceiling for one ``git`` tool call (must outlive inner ops).

    ``serial_ops × _GIT_TIMEOUT`` covers the bounded subprocesses the subcommand
    runs back to back; ``_NETWORK_IO_BUDGET`` adds the credential / remote /
    GitHub-REST steps, which are I/O rather than git processes but sit on the same
    serial path. Index-mutating calls also queue on the per-repo lock first, so
    ``_GIT_REPO_LOCK_WAIT`` is charged to them — the whole point of bounding the
    wait is that it fits inside the ceiling instead of eating into it.
    ``_GIT_KILL_SLACK`` leaves room to kill / reap before the outer deadline.
    """
    args = arguments or {}
    sub = str(args.get("subcommand", "")).strip().lower()
    budget = _SERIAL_GIT_OPS.get(sub, _DEFAULT_SERIAL_GIT_OPS) * _GIT_TIMEOUT
    budget += _NETWORK_IO_BUDGET.get(sub, 0.0)
    if git_call_needs_repo_lock(args):
        budget += _GIT_REPO_LOCK_WAIT
    return budget + _GIT_KILL_SLACK


GIT_TOOL_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "subcommand": {
            "type": "string",
            "enum": [
                "status",
                "diff",
                "log",
                "fetch",
                "show",
                "blame",
                "add",
                "commit",
                "branch",
                "checkout",
                "push",
                "pull",
                "init_baseline",
                "clone",
                "stash",
                "merge",
                "rebase",
                "cherry-pick",
                "tag",
                "remote",
                "create_pr",
            ],
            # 审批 / 无仓 / CEO 写入策略只在工具描述里写一遍，勿在此复述。
            "description": "子命令；审批 / 无仓 / CEO 写入策略见工具说明。",
        },
        "paths": {
            "type": "array",
            "items": {"type": "string"},
            "description": "status/diff/add/show/blame 路径；add/blame 必填（blame 仅一文件）。",
        },
        "staged": {
            "type": "boolean",
            "description": "diff --cached。",
            "default": False,
        },
        "include_untracked": {
            "type": "boolean",
            "description": "status 含未跟踪。",
            "default": False,
        },
        "max_count": {
            "type": "integer",
            "description": "log 条数（默认 20，上限 100）。",
            "default": 20,
        },
        "oneline": {
            "type": "boolean",
            "description": "log --oneline。",
            "default": True,
        },
        "message": {
            "type": "string",
            "description": "commit 必填；stash push 可选。",
        },
        "branch": {
            "type": "string",
            "description": "branch/checkout 分支名（二者必填）。",
        },
        "create": {
            "type": "boolean",
            "description": "checkout -b。",
            "default": False,
        },
        "remote": {
            "type": "string",
            "description": "fetch/pull/push 远程名（默认 origin）。",
            "default": "origin",
        },
        "set_upstream": {
            "type": "boolean",
            "description": "push --set-upstream。",
            "default": False,
        },
        "object": {
            "type": "string",
            "description": "show 对象（默认 HEAD）。",
            "default": "HEAD",
        },
        "action": {
            "type": "string",
            "enum": ["list", "push", "pop", "create", "add"],
            "description": "stash：list|push|pop；tag：list|create；remote：list|add。默认 list。",
            "default": "list",
        },
        "ref": {
            "type": "string",
            "description": "merge/rebase/cherry-pick 目标引用。",
        },
        "name": {
            "type": "string",
            "description": "tag create 名；remote add 远程名。",
        },
        "url": {
            "type": "string",
            "description": "remote add / clone 的仓库 URL（clone 仅 GitHub http(s)）。",
        },
        "dest": {
            "type": "string",
            "description": "clone 落点（相对工作区根，默认仓名）；非空拒绝。",
        },
        "title": {
            "type": "string",
            "description": "create_pr 标题（必填）。",
        },
        "body": {
            "type": "string",
            "description": "create_pr 正文。",
            "default": "",
        },
        "base": {
            "type": "string",
            "description": "create_pr 目标分支。",
        },
        "head": {
            "type": "string",
            "description": "create_pr 源分支（须已推远程）。",
        },
    },
    "required": ["subcommand"],
}

def _ref_token_error(ref: str, *, label: str, start: float) -> ToolResult | None:
    """Reject empty / option-like refs before they reach argv."""
    if not ref:
        return _error(f"{label} 需要 ref 参数（分支名或 commit）", start)
    if ref.startswith("-"):
        return _error(
            f"{label} 的 ref 不能以 '-' 开头（防止被 git 解析为选项）",
            start,
        )
    if any(ch.isspace() for ch in ref):
        return _error(f"{label} 的 ref 不能包含空白", start)
    return None


def _collab_danger_keys_error(
    arguments: dict[str, Any], *, label: str, start: float
) -> ToolResult | None:
    hit = sorted(k for k in arguments if k in _COLLAB_DANGER_KEYS)
    if not hit:
        return None
    return _error(
        f"{label} 禁止危险/策略旋钮（{', '.join(hit)}）；"
        "冲突时诚实失败，不自动 resolve，不支持 --force 类参数。",
        start,
    )


def _name_token_error(name: str, *, label: str, start: float) -> ToolResult | None:
    if not name:
        return _error(f"{label} 需要 name 参数", start)
    if name.startswith("-"):
        return _error(
            f"{label} 的 name 不能以 '-' 开头（防止被 git 解析为选项）",
            start,
        )
    if any(ch.isspace() for ch in name) or ":" in name:
        return _error(f"{label} 的 name 不能包含空白或 ':'", start)
    return None


def _remote_url_error(url: str, start: float) -> ToolResult | None:
    if not url:
        return _error("remote add 需要 url 参数", start)
    if url.startswith("-"):
        return _error(
            "remote url 不能以 '-' 开头（防止被 git 解析为选项）",
            start,
        )
    if any(ch.isspace() for ch in url):
        return _error("remote url 不能包含空白", start)
    return None



def _validate_add_paths(paths: list[Any], start: float) -> ToolResult | None:
    if not paths:
        return _error("add 需要显式 paths 参数，禁止使用 git add . / -A / --all", start)
    forbidden = {".", "-A", "--all"}
    for raw in paths:
        path = str(raw).strip()
        if not path:
            return _error("add 的 paths 不能包含空路径", start)
        if path in forbidden:
            return _error(
                f"禁止 add 路径 '{path}'：请显式列出文件，不要使用 . / -A / --all",
                start,
            )
        if "*" in path or "?" in path:
            return _error(f"禁止 add 通配符路径 '{path}'：请显式列出文件", start)
    return None


def _normalize_paths(raw_paths: Any) -> list[str]:
    if not raw_paths:
        return []
    return [str(p) for p in raw_paths if str(p).strip()]


def _remote_name_error(remote: str, start: float) -> ToolResult | None:
    """Reject option-like / refspec remote tokens before they reach argv."""
    if remote.startswith("-"):
        return _error("remote 名不能以 '-' 开头（防止被 git 解析为选项）", start)
    if ":" in remote or any(ch.isspace() for ch in remote):
        return _error(
            "remote 仅允许远程名（默认 origin），禁止 refspec 或空白",
            start,
        )
    return None


def _is_ceo_context(context: Any) -> bool:
    """CEO turns carry no worker-only coordination channels."""
    return (
        context.write_coordinator is None
        and context.note_wall is None
        and context.escalation is None
    )
