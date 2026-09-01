"""Last-line heuristic circuit breaker for catastrophic / sensitive tool calls.

This module is a **defense-in-depth blacklist**, not a security boundary. Patterns
are intentionally narrow and honest: they catch common catastrophic shapes
(``rm -rf /``, force-push to protected branches, raw-device writes, etc.) and
gate obvious credential / key-material paths (template allow → credential ask →
key deny). They do **not** intercept every dangerous command — comments, audit
copy, and approval-card hints must not claim otherwise.

Permission presets (including ``full_trust``), kickoff grants, and turn-wide
「本轮放行」never override these rules. Aligns with Claude Code's practice that
bypass mode still trips the circuit breaker.

Git's hard-forbidden subcommand set lives here as the single source of truth;
``tools.builtin.git_ops`` keeps its boundary behavior by importing that set.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

# ── Git hard-ban (single source for git_ops + breaker) ───────────────────────

# Ordinary ``push`` is allowlisted (approval + CEO-delegate); force / protected
# targets stay hard-denied below. reset/clean remain banned. G2 collaboration
# verbs (stash/merge/rebase/cherry-pick/tag/remote) are allowlisted in git_ops
# with execution-layer guards — not in this hard-ban set.
GIT_FORBIDDEN_SUBCOMMANDS: frozenset[str] = frozenset({"reset", "clean"})
GIT_PROTECTED_BRANCHES: frozenset[str] = frozenset({"main", "master"})


def git_forbidden_subcommands() -> frozenset[str]:
    """Subcommands the git tool hard-rejects (not grantable, not mode-dependent)."""
    return GIT_FORBIDDEN_SUBCOMMANDS


def git_protected_branches() -> frozenset[str]:
    return GIT_PROTECTED_BRANCHES


# ── Verdicts ────────────────────────────────────────────────────────────────


class BreakerVerdict(StrEnum):
    """Outcome of evaluating a tool call against the circuit breaker."""

    FORCE_APPROVAL = "force_approval"
    """Destructive / irreversible shape — always ask a human; grants do not apply."""

    DENY = "deny"
    """Key material / non-grantable shape — refuse and steer the model away."""


@dataclass(frozen=True, slots=True)
class BreakerHit:
    verdict: BreakerVerdict
    rule_id: str
    """Stable id for tests / audit (e.g. ``destructive.rm_root``)."""

    reason: str
    """Chinese explanation for humans and model backfill — honest, not absolute."""


# ── Destructive command heuristics ──────────────────────────────────────────
#
# Matched against shell/command/code text. Keep patterns specific so ordinary
# ``rm -rf build/`` or ``git push`` to a feature branch do not trip the breaker.

# Targets that are catastrophic when passed to recursive delete — not ordinary
# workspace paths like ``/tmp/build`` or ``./dist``.
_RM_CATASTROPHIC_TARGET = (
    r"(?:"
    r"/(?:\s|$|[;&|'\"`])"  # bare root ``/``
    r"|/\*(?:\s|$|[;&|'\"`])"  # ``/*``
    r"|~(?:/|\s|$|[;&|'\"`])"  # home
    r"|\$\{?HOME\}?(?:/|\s|$|[;&|'\"`])"
    r"|\%USERPROFILE\%(?:\\|/|\s|$|[;&|'\"`])?"
    r"|[A-Za-z]:\\?(?:\s|$|[;&|'\"`])"  # bare Windows drive root
    r")"
)

_DESTRUCTIVE_RULES: tuple[tuple[str, re.Pattern[str], str], ...] = (
    (
        "destructive.rm_root",
        re.compile(
            r"(?i)(?:^|[\s;&|`(])"
            r"(?:sudo\s+)?"
            r"rm\b[^\n;|&]*?"
            r"(?:-rf\b|-fr\b|--recursive\b[^\n;|&]*?--force\b|--force\b[^\n;|&]*?--recursive\b)"
            r"[^\n;|&]*?"
            + _RM_CATASTROPHIC_TARGET
        ),
        "检测到疑似删除根目录/家目录的命令（启发式兜底，并非完整拦截）。需人工确认后才能执行。",
    ),
    (
        "destructive.format_device",
        re.compile(
            r"(?i)(?:^|[\s;&|`(])"
            r"(?:mkfs(?:\.\w+)?\b"
            r"|format\s+[A-Za-z]:"
            r"|diskpart\b"
            r"|dd\b[^\n;|&]*\bof\s*=\s*/dev/"
            r"|\\\\\?\\PhysicalDrive"
            r"|\\\\\.\\PhysicalDrive)"
        ),
        "检测到疑似格式化或写入块设备的命令（启发式兜底，并非完整拦截）。需人工确认后才能执行。",
    ),
    (
        "destructive.git_force_push_protected",
        re.compile(
            r"(?i)git\s+push\b[^\n;|&]*"
            r"(?:--force(?:-with-lease)?\b|(?<![-\w])-f(?![-\w]))"
            r"[^\n;|&]*\b(?:main|master)\b"
            r"|"
            r"git\s+push\b[^\n;|&]*\b(?:main|master)\b[^\n;|&]*"
            r"(?:--force(?:-with-lease)?\b|(?<![-\w])-f(?![-\w]))"
        ),
        "检测到疑似向 main/master 强制推送的命令（启发式兜底，并非完整拦截）。"
        "已硬拒，不可由权限模式或本轮放行放开；请改用功能分支或在本机终端手动处理。",
    ),
    (
        "destructive.shutdown",
        re.compile(
            r"(?i)(?:^|[\s;&|`(])"
            r"(?:shutdown\b|poweroff\b|reboot\b|halt\b"
            r"|Stop-Computer\b|Restart-Computer\b)"
        ),
        "检测到疑似关机/重启主机的命令（启发式兜底，并非完整拦截）。需人工确认后才能执行。",
    ),
)

# host(action=shell) fuse (tools.builtin.host) already hard-denies these families.
# Breaker upgrades them to DENY on the host shell path so approval cards do not
# promise a run that fuse will still refuse. git force is intentionally absent —
# fuse does not scan git; shell text force→main|master is DENY via the scanner
# itself (aligned with structured git), not via this fuse⊆DENY set.
# Keep this set in lockstep with host fuse overlap (see fuse⊆DENY tests).
FUSE_ALIGNED_DENY_RULE_IDS: frozenset[str] = frozenset(
    {
        "destructive.rm_root",
        "destructive.format_device",
        "destructive.shutdown",
    }
)

# Shell/command text rules that hard-deny (not FORCE_APPROVAL). Distinct from
# fuse⊆DENY: applies on terminal / code_execute / test_run / host(action=shell) alike.
_TEXT_DENY_RULE_IDS: frozenset[str] = frozenset(
    {"destructive.git_force_push_protected"}
)

_HOST_SHELL_FUSE_DENY_REASON = (
    "检测到疑似毁灭性命令，且与 host(action=shell) 执行侧熔断重叠（启发式兜底，并非完整拦截）。"
    "已硬拒，不可由权限模式或本轮放行放开；请缩小命令范围或改用结构化 host action。"
)


def fuse_aligned_deny_rule_ids() -> frozenset[str]:
    """Destructive rule ids that host shell fuse covers → breaker DENY on host+shell."""
    return FUSE_ALIGNED_DENY_RULE_IDS

# ── Sensitive path heuristics (allow / ask / deny) ───────────────────────────
#
# Templates (``.env.example`` …) → allow. Credential/env files → ask on read
# (FORCE_APPROVAL entrance; turn grant allowed for same tool after human approve).
# Key material / ``.ssh`` → hard DENY on read. Writes to ask|deny paths stay DENY
# (no plaintext secret scaffolding).


class SensitivePathClass(StrEnum):
    """Heuristic class for a filesystem path (defense-in-depth, not complete)."""

    NONE = "none"
    ASK = "ask"
    DENY = "deny"


_ASK_BASENAME_EXACT: frozenset[str] = frozenset(
    {
        ".env",
        ".env.local",
        ".env.development",
        ".env.production",
        ".env.test",
        ".env.staging",
        "credentials.json",
        "credentials.yml",
        "credentials.yaml",
        "secrets.json",
        "secrets.yml",
        "secrets.yaml",
        ".npmrc",
        ".pypirc",
        "netrc",
        ".netrc",
        "pgpass",
        ".pgpass",
    }
)

_DENY_BASENAME_EXACT: frozenset[str] = frozenset(
    {
        "id_rsa",
        "id_dsa",
        "id_ecdsa",
        "id_ed25519",
        "id_rsa.pub",
        "id_ed25519.pub",
        "authorized_keys",
        "known_hosts",
    }
)

_ASK_BASENAME_PREFIXES: tuple[str, ...] = (".env.",)
_DENY_BASENAME_SUFFIXES: tuple[str, ...] = (
    ".pem",
    ".key",
    ".p12",
    ".pfx",
    ".jks",
)
# Committed templates / samples — never treat as credentials.
_TEMPLATE_BASENAME_SUFFIXES: tuple[str, ...] = (
    ".example",
    ".sample",
    ".template",
    ".dist",
)
_ASK_PATH_SEGMENTS: frozenset[str] = frozenset({".aws", ".azure", ".gcloud"})
_DENY_PATH_SEGMENTS: frozenset[str] = frozenset(
    {".ssh", ".gnupg", "private-keys", "private_keys"}
)

_SENSITIVE_ASK_READ_REASON = (
    "该路径疑似凭据或环境配置文件。读入后内容会进入模型上下文"
    "（启发式兜底，并非完整拦截）。需人工确认后才能读取；"
    "也可改用设置页凭据、本机环境变量，或只告知无密钥的配置键名。"
)

_SENSITIVE_DENY_READ_REASON = (
    "该路径疑似私钥/密钥材料，默认拒绝读取（启发式兜底，并非完整拦截）。"
    "请改用本机密钥代理或设置页凭据；不要把密钥内容贴进对话。"
)

_SENSITIVE_WRITE_DENY_REASON = (
    "该路径疑似凭据/密钥类敏感文件，默认拒绝写入（启发式兜底，并非完整拦截）。"
    "【禁止】把 API Key 写入工作区明文；请用不含密钥的脚手架 + 用户本机环境变量。"
)


def classify_sensitive_path(path: str) -> SensitivePathClass:
    """Classify ``path`` as NONE / ASK / DENY (heuristic; templates → NONE)."""
    raw = (path or "").strip()
    if not raw or raw in {".", "./", ".\\"}:
        return SensitivePathClass.NONE
    # Normalize separators; PurePosixPath keeps drive letters oddly, so try both.
    candidates = [PurePosixPath(raw.replace("\\", "/"))]
    if "\\" in raw or re.match(r"^[A-Za-z]:", raw):
        candidates.append(PureWindowsPath(raw))
    worst = SensitivePathClass.NONE
    for p in candidates:
        parts = [part for part in p.parts if part not in {"/", ".", ""}]
        if not parts:
            continue
        for part in parts[:-1]:
            low = part.lower()
            if low in _DENY_PATH_SEGMENTS:
                return SensitivePathClass.DENY
            if low in _ASK_PATH_SEGMENTS:
                worst = SensitivePathClass.ASK
        name_class = _basename_sensitive_class(parts[-1])
        if name_class is SensitivePathClass.DENY:
            return SensitivePathClass.DENY
        if name_class is SensitivePathClass.ASK:
            worst = SensitivePathClass.ASK
    return worst


def is_sensitive_path(path: str) -> bool:
    """True when ``path`` is ASK or DENY (credential / key heuristic)."""
    return classify_sensitive_path(path) is not SensitivePathClass.NONE


def _basename_sensitive_class(name: str) -> SensitivePathClass:
    lower = name.lower()
    if any(lower.endswith(suffix) for suffix in _TEMPLATE_BASENAME_SUFFIXES):
        return SensitivePathClass.NONE
    if lower in _DENY_BASENAME_EXACT or name in _DENY_BASENAME_EXACT:
        return SensitivePathClass.DENY
    if any(lower.endswith(suffix) for suffix in _DENY_BASENAME_SUFFIXES):
        return SensitivePathClass.DENY
    if lower in _ASK_BASENAME_EXACT or name in _ASK_BASENAME_EXACT:
        return SensitivePathClass.ASK
    if any(lower.startswith(prefix) for prefix in _ASK_BASENAME_PREFIXES):
        return SensitivePathClass.ASK
    # Globs that clearly target credential / key basenames (``.env*``, ``*.pem``).
    if "*" in name or "?" in name:
        approx = re.sub(r"[*?]+", "", name)
        if approx:
            approx_class = _basename_sensitive_class(approx)
            if approx_class is not SensitivePathClass.NONE:
                return approx_class
        if lower.endswith(".pem") or lower.endswith(".key") or "*.pem" in lower:
            return SensitivePathClass.DENY
        if lower.startswith(".env") or lower.endswith(".env") or ".env." in lower:
            return SensitivePathClass.ASK
    return SensitivePathClass.NONE


_TOP_TREE_REASON = (
    "检测到疑似删除工作区顶层整项目目录的命令（启发式兜底，并非完整拦截）。"
    "误伤面：非常规顶层目录名的合法清理也会弹确认。"
    "需人工确认后才能执行；白名单清理目录（node_modules/.venv 等）不拦截。"
)

_NO_BASELINE_REASON = (
    "检测到破坏性删除形，且本回合尚无可用的 Local zip 基线（启发式兜底，并非完整拦截）。"
    "无法保证可回滚，需人工确认后才能执行；或先确保回合基线已落盘。"
)


def scan_destructive_text(text: str) -> BreakerHit | None:
    """Scan free-form command/code text for catastrophic patterns."""
    if not text or not text.strip():
        return None
    for rule_id, pattern, reason in _DESTRUCTIVE_RULES:
        if pattern.search(text):
            verdict = (
                BreakerVerdict.DENY
                if rule_id in _TEXT_DENY_RULE_IDS
                else BreakerVerdict.FORCE_APPROVAL
            )
            return BreakerHit(
                verdict=verdict,
                rule_id=rule_id,
                reason=reason,
            )
    return None


def scan_workspace_top_tree(text: str) -> BreakerHit | None:
    """P2: top-level whole-project-tree delete → FORCE_APPROVAL (whitelist skipped)."""
    from agentcore.workspace.destructive_fs import (
        requires_top_level_tree_gate,
        scan_destructive_fs,
    )

    hit = scan_destructive_fs(text)
    if not requires_top_level_tree_gate(hit):
        return None
    return BreakerHit(
        verdict=BreakerVerdict.FORCE_APPROVAL,
        rule_id="destructive.workspace_top_tree",
        reason=_TOP_TREE_REASON,
    )


def no_turn_baseline_hit() -> BreakerHit:
    """P0a: destructive path on Local with no usable zip baseline."""
    return BreakerHit(
        verdict=BreakerVerdict.FORCE_APPROVAL,
        rule_id="destructive.no_turn_baseline",
        reason=_NO_BASELINE_REASON,
    )


def command_text_for_tool(tool_name: str, arguments: dict[str, Any]) -> str:
    """Public alias of the breaker command/code text extractor (engine baseline gate)."""
    return _command_text_for_tool(tool_name, arguments)


def _command_text_for_tool(tool_name: str, arguments: dict[str, Any]) -> str:
    if tool_name == "run":
        action = str(arguments.get("action") or "").strip().lower()
        if action in {"read", "stop", "list"}:
            return ""
        return str(arguments.get("command") or "")
    if tool_name == "host":
        from agentcore.tools.builtin.host import host_call_is_shell

        if not host_call_is_shell(arguments):
            return ""
        # Same command field as terminal; scanned for force→main etc. (Host axis
        # still covers ordinary push). Fuse hard-denies stay in host.py / desktop.
        return str(arguments.get("command") or "")
    if tool_name == "code_execute":
        return str(arguments.get("code") or "")
    if tool_name == "test_run":
        # Whitelisted argv builder; still scan filter + any leaked command fields.
        parts = [
            str(arguments.get("filter") or ""),
            str(arguments.get("command") or ""),
            str(arguments.get("code") or ""),
        ]
        return "\n".join(p for p in parts if p)
    if tool_name == "git":
        # Extra surface if shell wrappers somehow call through — primary ban is
        # still the allowed-subcommand list in git_ops.
        sub = str(arguments.get("subcommand") or "").strip().lower()
        branch = str(arguments.get("branch") or "")
        return f"git {sub} {branch}".strip()
    return ""


def _path_args_for_tool(tool_name: str, arguments: dict[str, Any]) -> list[str]:
    if tool_name == "file_read":
        return [str(arguments.get("path") or "")]
    if tool_name in {"file_write", "file_append", "str_replace"}:
        return [str(arguments.get("path") or "")]
    if tool_name == "grep":
        paths = [str(arguments.get("path") or "")]
        glob = str(arguments.get("glob") or "").strip()
        if glob:
            paths.append(glob)
        return paths
    if tool_name == "code_search":
        return [str(arguments.get("path_prefix") or "")]
    return []


def _write_content_for_secret_scan(tool_name: str, arguments: dict[str, Any]) -> str:
    """Body about to land on disk (heuristic secret gate; 案 image-gen B)."""
    if tool_name in {"file_write", "file_append"}:
        return str(arguments.get("content") or "")
    if tool_name == "str_replace":
        return str(arguments.get("new_string") or "")
    return ""


def _truthy_flag(value: Any) -> bool:
    if value is True:
        return True
    if isinstance(value, (int, float)) and value != 0:
        return True
    return isinstance(value, str) and value.strip().lower() in {"1", "true", "yes", "on"}


def _git_push_breaker_hit(args: dict[str, Any]) -> BreakerHit | None:
    """DENY structured git push when force-like or protected-branch target is requested.

    Ordinary feature-branch push returns ``None`` so approval / execute can proceed.
    Current-branch main/master is also hard-rejected inside ``git_ops._cmd_push``.
    """
    force_tokens = {"-f", "--force", "--force-with-lease"}
    remote = str(args.get("remote") or "").strip()
    branch = str(args.get("branch") or "").strip()
    refspec = str(args.get("refspec") or "").strip()
    force_like = (
        _truthy_flag(args.get("force"))
        or _truthy_flag(args.get("force_with_lease"))
        or _truthy_flag(args.get("forceWithLease"))
        or remote in force_tokens
        or branch in force_tokens
    )
    protected_target = branch.lower() in GIT_PROTECTED_BRANCHES
    if refspec:
        # Custom refspec is never an ordinary push (blocks ``feature:main`` bypass).
        force_like = True
        rhs = refspec.rsplit(":", 1)[-1].strip().lower()
        # Strip common heads/ prefix noise.
        rhs_name = rhs.rsplit("/", 1)[-1]
        if rhs_name in GIT_PROTECTED_BRANCHES:
            protected_target = True
    if force_like or protected_target:
        return BreakerHit(
            verdict=BreakerVerdict.DENY,
            rule_id="git.push_force_or_protected",
            reason=(
                "Git push 禁止 force（含 --force-with-lease），且禁止以 main/master"
                " 为推送目标（硬拒，不可由权限模式或本轮放行放开）。"
                "普通功能分支 push 需用户授权；无凭据/无 remote 时会失败。"
            ),
        )
    return None


def evaluate_tool_call(tool_name: str, arguments: dict[str, Any] | None) -> BreakerHit | None:
    """Evaluate a tool call; return a hit when the circuit breaker should intervene.

    Returns ``None`` when the call is not matched (normal approval / execution path).
    """
    args = arguments or {}
    name = (tool_name or "").strip()

    # Sensitive path reads: templates allow; credentials ASK; key material DENY.
    if name in {"file_read", "grep", "code_search"}:
        for path in _path_args_for_tool(name, args):
            kind = classify_sensitive_path(path)
            if kind is SensitivePathClass.DENY:
                return BreakerHit(
                    verdict=BreakerVerdict.DENY,
                    rule_id="sensitive.path_read",
                    reason=_SENSITIVE_DENY_READ_REASON,
                )
            if kind is SensitivePathClass.ASK:
                return BreakerHit(
                    verdict=BreakerVerdict.FORCE_APPROVAL,
                    rule_id="sensitive.path_read_ask",
                    reason=_SENSITIVE_ASK_READ_REASON,
                )

    # Sensitive writes + pasted-key content (案 20260803-image-gen-byok-egress-boundary B).
    if name in {"file_write", "file_append", "str_replace"}:
        for path in _path_args_for_tool(name, args):
            if classify_sensitive_path(path) is not SensitivePathClass.NONE:
                return BreakerHit(
                    verdict=BreakerVerdict.DENY,
                    rule_id="sensitive.path_write",
                    reason=_SENSITIVE_WRITE_DENY_REASON,
                )
        from agentcore.core.secrets import SECRET_WRITE_DENY_REASON, contains_secret

        body = _write_content_for_secret_scan(name, args)
        if contains_secret(body):
            return BreakerHit(
                verdict=BreakerVerdict.DENY,
                rule_id="sensitive.secret_write",
                reason=SECRET_WRITE_DENY_REASON,
            )

    # Git hard-ban at the breaker layer (git_ops still enforces at execute).
    if name == "git":
        sub = str(args.get("subcommand") or "").strip().lower()
        if sub in GIT_FORBIDDEN_SUBCOMMANDS or any(
            pat in sub for pat in GIT_FORBIDDEN_SUBCOMMANDS
        ):
            return BreakerHit(
                verdict=BreakerVerdict.DENY,
                rule_id="git.forbidden_subcommand",
                reason=(
                    f"Git 子命令 '{sub}' 被硬禁清单拒绝（reset/clean 等不可由"
                    "权限模式或本轮放行放开）。请改由用户在本机终端手动完成。"
                ),
            )
        action = str(args.get("action") or "").strip().lower()
        # G2: destructive stash/tag/remote actions stay DENY (not grantable).
        if sub == "stash" and action in {"drop", "clear"}:
            return BreakerHit(
                verdict=BreakerVerdict.DENY,
                rule_id="git.forbidden_stash_destructive",
                reason=(
                    "禁止 git stash drop/clear（不可由权限模式或本轮放行放开）。"
                    "请改用 list/push/pop，或由用户在本机终端手动处理。"
                ),
            )
        if sub == "tag" and action in {"delete", "remove", "rm"}:
            return BreakerHit(
                verdict=BreakerVerdict.DENY,
                rule_id="git.forbidden_tag_delete",
                reason=(
                    "禁止删除 tag（不可由权限模式或本轮放行放开）。"
                    "仅允许 list / create（轻量标签）。"
                ),
            )
        if sub == "remote" and action in {"remove", "rm", "delete"}:
            return BreakerHit(
                verdict=BreakerVerdict.DENY,
                rule_id="git.forbidden_remote_remove",
                reason=(
                    "禁止 git remote remove（不可由权限模式或本轮放行放开）。"
                    "仅允许 list / add。"
                ),
            )
        # Ordinary push may proceed to approval; force / protected-branch target DENY.
        if sub == "push":
            push_hit = _git_push_breaker_hit(args)
            if push_hit is not None:
                return push_hit

    # Destructive text on execution / terminal / host(action=shell) surfaces.
    # host shell: fuse-aligned families → DENY (方案 C). git force→main|master
    # text → DENY on all shell paths (scanner; fuse still does not scan git).
    # Ordinary push stays on the Host GRANTABLE axis. Other catastrophic shapes
    # stay FORCE_APPROVAL on terminal / code_execute / test_run.
    # P2 top-level workspace tree: FORCE_APPROVAL (whitelist cleanup skipped).
    # Do not stack a second card when fuse-aligned DENY already applies.
    from agentcore.tools.builtin.host import host_call_is_shell

    is_host_shell = name == "host" and host_call_is_shell(args)
    if name == "run" or is_host_shell:
        if name == "run":
            action = str(args.get("action") or "").strip().lower()
            if action in {"read", "stop", "list"}:
                return None
        command_text = _command_text_for_tool(name, args)
        if is_host_shell:
            from agentcore.tools.builtin.host import shell_silent_install_blocks

            silent = shell_silent_install_blocks(command_text)
            if silent:
                return BreakerHit(
                    verdict=BreakerVerdict.DENY,
                    rule_id="host.silent_install",
                    reason=silent,
                )
        hit = scan_destructive_text(command_text)
        if hit is not None:
            if is_host_shell and hit.rule_id in FUSE_ALIGNED_DENY_RULE_IDS:
                return BreakerHit(
                    verdict=BreakerVerdict.DENY,
                    rule_id=hit.rule_id,
                    reason=_HOST_SHELL_FUSE_DENY_REASON,
                )
            return hit
        # P2 narrow gate (after catastrophic rules so fuse⊆DENY stays single-card).
        top_hit = scan_workspace_top_tree(command_text)
        if top_hit is not None:
            return top_hit

    return None
