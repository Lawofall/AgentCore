"""Git operations tool — read and write git state within the workspace.

ServerWorkspace / Sidecar: thin shell over subprocess git under ``backend.root``.
LocalWorkspace (no Path.root): same allowlisted surface via desktop ``git_run``.
Read subcommands (status / diff / log / fetch / show / blame; stash/tag/remote
``action=list``) run without approval; write subcommands (add / commit / branch /
checkout / push / pull / init_baseline / clone / merge / rebase / cherry-pick /
create_pr; stash push/pop; tag create; remote add) are refused on the CEO path
except ``init_baseline`` / ``clone`` (first baseline or shallow clone into dest;
still approval-gated, not always-confirm) and executed on delegated workers
(mutating ops require user authorization).
Hard-banned at the breaker (``reset`` / ``clean``); force push /
protected-branch targets stay DENY. Push itself is allowlisted but never force;
``create_pr`` is GitHub-only via API (not free ``gh`` shell); pull is always
``--ff-only``; merge / rebase / cherry-pick stop honestly on conflict (no auto
resolve); main/master current branch is hard-rejected for
commit/push/merge/rebase/cherry-pick; missing remote / credentials fail honestly
(``GIT_TERMINAL_PROMPT=0``).

Timeout contract (aligned with ``terminal``): the engine wall-clock ceiling is
``serial_ops × _GIT_TIMEOUT + _NETWORK_IO_BUDGET + repo-lock wait +
_GIT_KILL_SLACK`` — every serial step must be bounded and counted, git subprocess
or not (credential lookup, remote round trip, GitHub REST, queueing behind
another index writer), so kill/reap never races the outer ``asyncio.wait_for``.
Repo state is probed by the primary command itself, and status uses a single
``git status -sb`` (branch + porcelain), so the common read path spawns exactly
one git process.

Concurrency: a round's parallel tool calls hit one repo at once, so
index-mutating subcommands serialize per repo (``repo_lock``). Reads never queue
(``GIT_OPTIONAL_LOCKS=0`` keeps them off ``index.lock``), and the bounded wait is
part of the ceiling above, so queueing can never trip the engine timeout.

Visibility: those same waits are reported as coarse execution phases (``phases``)
— queueing / credentials / remote / local git — so a two-minute call reads as what
it is doing instead of a bare spinner. Transport-only, honest by construction.

Split axes (implementation modules):
- ``policy`` — allowlists, write detection, argument validators, schema, timeouts
- ``spawn`` — subprocess / channel spawn, bounded auth lookups, failure attribution
- ``repo_lock`` — per-repo serialization of index-mutating calls
- ``phases`` — execution-phase reporting for the waiting UI
- ``results`` — ToolResult helpers + truncation
- ``cmds_read`` / ``cmds_local`` / ``cmds_remote`` / ``cmds_collab`` — subcommands
- ``tool`` — GitTool registration + dispatch

Public import path stays ``agentcore.tools.builtin.git_ops``.
"""

from agentcore.tools.builtin.git_ops.phases import (
    GIT_PHASES,
    PHASE_CREDENTIALS,
    PHASE_LOCAL,
    PHASE_QUEUED,
    PHASE_REMOTE,
    phase_scope,
    report_phase,
)
from agentcore.tools.builtin.git_ops.policy import (
    _ALLOWED_SUBCOMMANDS,
    _ALWAYS_WRITE_SUBCOMMANDS,
    _BLAME_LINE_LIMIT,
    _CEO_ALLOWED_WRITE_SUBCOMMANDS,
    _COLLAB_DANGER_KEYS,
    _DIFF_OUTPUT_LIMIT,
    _FORBIDDEN_PATTERNS,
    _GIT_CREDENTIAL_TIMEOUT,
    _GIT_KILL_SLACK,
    _GIT_NETWORK_TIMEOUT,
    _GIT_REPO_LOCK_WAIT,
    _GIT_TIMEOUT,
    _GIT_TOKEN_RESOLVE_TIMEOUT,
    _GITHUB_API_CALLS,
    _GITHUB_API_TIMEOUT,
    _INDEX_LOCK_SUBCOMMANDS,
    _NETWORK_IO_BUDGET,
    _NETWORK_SUBCOMMANDS,
    _NO_REPO_CODE,
    _PROTECTED_BRANCHES,
    _REPO_BUSY_CODE,
    _REPO_UNUSABLE_CODE,
    _SERIAL_GIT_OPS,
    _STATUS_LINE_LIMIT,
    _WRITE_SUBCOMMANDS,
    GIT_TOOL_PARAMETERS,
    _is_ceo_context,
    _normalize_paths,
    _remote_name_error,
    _validate_add_paths,
    git_call_is_write,
    git_call_needs_repo_lock,
    git_tool_timeout_seconds,
    git_write_subcommands,
)
from agentcore.tools.builtin.git_ops.repo_lock import repo_lock_key, repo_write_lock
from agentcore.tools.builtin.git_ops.results import (
    _error,
    _git_failure,
    _ok,
    _truncate_line_output,
    _truncate_status_body,
)
from agentcore.tools.builtin.git_ops.spawn import (
    _AUTH_FAILURE_HINT,
    _AUTH_FAILURE_MARKERS,
    _NO_CHANNEL_MSG,
    _UNUSABLE_REPO_HINT,
    _UNUSABLE_REPO_MARKERS,
    _cloud_network_extra_env,
    _current_branch,
    _ensure_git_repo,
    _git_spawn_kwargs,
    _git_subprocess_env,
    _load_account_git_auth,
    _looks_like_auth_failure,
    _looks_like_unusable_repo,
    _parse_status_sb,
    _reap_git_process,
    _refuse_on_protected_branch,
    _resolve_git_cwd,
    _resolve_pr_token,
    _run_git,
    _workspace_has_git_meta,
    _workspace_has_local_git,
    git_transport_scope,
)
from agentcore.tools.builtin.git_ops.tool import GitTool

__all__ = [
    "GIT_PHASES",
    "GIT_TOOL_PARAMETERS",
    "GitTool",
    "PHASE_CREDENTIALS",
    "PHASE_LOCAL",
    "PHASE_QUEUED",
    "PHASE_REMOTE",
    "_ALLOWED_SUBCOMMANDS",
    "_ALWAYS_WRITE_SUBCOMMANDS",
    "_AUTH_FAILURE_HINT",
    "_AUTH_FAILURE_MARKERS",
    "_BLAME_LINE_LIMIT",
    "_CEO_ALLOWED_WRITE_SUBCOMMANDS",
    "_COLLAB_DANGER_KEYS",
    "_DIFF_OUTPUT_LIMIT",
    "_FORBIDDEN_PATTERNS",
    "_GITHUB_API_CALLS",
    "_GITHUB_API_TIMEOUT",
    "_GIT_CREDENTIAL_TIMEOUT",
    "_GIT_KILL_SLACK",
    "_GIT_NETWORK_TIMEOUT",
    "_GIT_REPO_LOCK_WAIT",
    "_GIT_TIMEOUT",
    "_GIT_TOKEN_RESOLVE_TIMEOUT",
    "_INDEX_LOCK_SUBCOMMANDS",
    "_NETWORK_IO_BUDGET",
    "_NETWORK_SUBCOMMANDS",
    "_NO_CHANNEL_MSG",
    "_NO_REPO_CODE",
    "_PROTECTED_BRANCHES",
    "_REPO_BUSY_CODE",
    "_REPO_UNUSABLE_CODE",
    "_SERIAL_GIT_OPS",
    "_STATUS_LINE_LIMIT",
    "_UNUSABLE_REPO_HINT",
    "_UNUSABLE_REPO_MARKERS",
    "_WRITE_SUBCOMMANDS",
    "_cloud_network_extra_env",
    "_current_branch",
    "_ensure_git_repo",
    "_error",
    "_git_failure",
    "_git_spawn_kwargs",
    "_git_subprocess_env",
    "_is_ceo_context",
    "_load_account_git_auth",
    "_looks_like_auth_failure",
    "_looks_like_unusable_repo",
    "_normalize_paths",
    "_ok",
    "_parse_status_sb",
    "_reap_git_process",
    "_refuse_on_protected_branch",
    "_remote_name_error",
    "_resolve_git_cwd",
    "_resolve_pr_token",
    "_run_git",
    "_truncate_line_output",
    "_truncate_status_body",
    "_validate_add_paths",
    "_workspace_has_git_meta",
    "_workspace_has_local_git",
    "git_call_is_write",
    "git_call_needs_repo_lock",
    "git_tool_timeout_seconds",
    "git_transport_scope",
    "git_write_subcommands",
    "phase_scope",
    "repo_lock_key",
    "repo_write_lock",
    "report_phase",
]
