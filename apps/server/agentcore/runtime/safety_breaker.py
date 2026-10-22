"""Last-line heuristic circuit breaker for catastrophic / sensitive tool calls.

This module is a **defense-in-depth blacklist**, not a security boundary. Patterns
are intentionally narrow and honest: they catch common catastrophic shapes
(``rm -rf /``, force-push to protected branches, raw-device writes, etc.) and
gate obvious credential / key-material paths (template allow → credential ask →
key deny). They do **not** intercept every dangerous command — comments, audit
copy, and approval-card hints must not claim otherwise.

``full`` skips FORCE cards and git-protection DENY (push to main, force push,
reset / clean). Fuse-aligned destruction, private-key file reads/writes, silent
install, and secret writes stay DENY. Delegation grants and turn-wide
「本轮放行」never override remaining DENY.

Git force / protected-branch / reset / clean rules live in the command-text
deny table below. There is no structured git tool.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath, PureWindowsPath
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agentcore.core.types import WorkspaceBoundary

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
# FORCE card chrome already says the heuristic is incomplete. Reasons only
# name what fired — do not repeat that disclaimer or 「需人工确认」.

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
        "疑似删除根目录或家目录。",
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
        "疑似格式化或写入块设备。",
    ),
    (
        "destructive.shutdown",
        re.compile(
            r"(?i)(?:^|[\s;&|`(])"
            r"(?:shutdown\b|poweroff\b|reboot\b|halt\b"
            r"|Stop-Computer\b|Restart-Computer\b)"
        ),
        "疑似关机或重启主机。",
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
_GIT_PROTECTION_DENY_RULE_IDS: frozenset[str] = frozenset(
    {
        "destructive.git_force_push_protected",
        "destructive.git_force_push",
        "destructive.git_reset_or_clean",
        "destructive.git_push_protected",
    }
)
_TEXT_DENY_RULE_IDS: frozenset[str] = _GIT_PROTECTION_DENY_RULE_IDS

_FULL_FUSE_DENY_REASON = (
    "检测到疑似毁灭性命令（启发式兜底，并非完整拦截）。"
    "完全权限也不放行格式化磁盘、删系统或关机。"
)

_HOST_SHELL_FUSE_DENY_REASON = (
    "检测到疑似毁灭性命令，且与 host 执行侧熔断重叠（启发式兜底，并非完整拦截）。"
    "已硬拒，不可由权限模式或本轮放行放开；请缩小命令范围。"
)


def fuse_aligned_deny_rule_ids() -> frozenset[str]:
    """Destructive rule ids that host shell fuse covers → breaker DENY on host+shell."""
    return FUSE_ALIGNED_DENY_RULE_IDS


def git_protection_deny_rule_ids() -> frozenset[str]:
    """Git protection DENY ids. ``full`` lifts these; other boundaries do not."""
    return _GIT_PROTECTION_DENY_RULE_IDS


def apply_boundary_to_breaker(
    hit: BreakerHit | None,
    axes: WorkspaceBoundary | None,
) -> BreakerHit | None:
    """Apply conversation boundary to a breaker hit.

    ``full`` skips FORCE cards (``.env`` / 整树删除) and git-protection DENY.
    Fuse-aligned destruction stays DENY even when it arrived as FORCE on ``run``.
    Keys, silent install, and other DENY stay.
    """
    if hit is None or axes is None or not axes.skips_confirm:
        return hit
    if hit.verdict is BreakerVerdict.FORCE_APPROVAL:
        if hit.rule_id in FUSE_ALIGNED_DENY_RULE_IDS:
            return BreakerHit(
                verdict=BreakerVerdict.DENY,
                rule_id=hit.rule_id,
                reason=_FULL_FUSE_DENY_REASON,
            )
        return None
    if (
        hit.verdict is BreakerVerdict.DENY
        and hit.rule_id in _GIT_PROTECTION_DENY_RULE_IDS
    ):
        return None
    return hit

# ── Sensitive path heuristics (allow / ask / deny) ───────────────────────────
#
# Templates (``.env.example`` …) → allow. Credential/env files → ask on read
# (FORCE_APPROVAL entrance; turn grant allowed for same tool after human approve).
# Private-key basenames / ``.gnupg`` → hard DENY on read and write. Public files
# under ``.ssh`` (``*.pub``, ``known_hosts``, ``config``) are not keys. Writes to
# ASK paths (``.env`` …) follow the session file-write boundary — no extra deny.
# Key-shaped body is DENY on ordinary files; ASK destinations are the place for
# project secrets (Cursor-aligned).


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
    }
)
_SSH_PUBLIC_BASENAMES: frozenset[str] = frozenset(
    {
        "known_hosts",
        "known_hosts.old",
        "config",
        "authorized_keys",
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
    {".gnupg", "private-keys", "private_keys"}
)

_SENSITIVE_ASK_READ_REASON = (
    "读入后内容会进入对话。也可改用设置页凭据或本机环境变量。"
)

_SENSITIVE_DENY_READ_REASON = (
    "该路径疑似私钥/密钥材料，默认拒绝读取（启发式兜底，并非完整拦截）。"
    "请改用本机密钥代理或设置页凭据；不要把密钥内容贴进对话。"
)

_SENSITIVE_WRITE_DENY_REASON = (
    "该路径疑似私钥/密钥材料，默认拒绝写入（启发式兜底，并非完整拦截）。"
    "请改用本机密钥代理或设置页凭据；不要把密钥文件写进工作区。"
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
            if low == ".ssh":
                name_low = parts[-1].lower()
                if name_low.endswith(".pub") or name_low in _SSH_PUBLIC_BASENAMES:
                    break
                return SensitivePathClass.DENY
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
    "疑似删除工作区顶层整棵目录。非常规顶层目录名的合法清理也会弹这张卡。"
)

_NO_BASELINE_REASON = (
    "破坏性删除，且本回合尚无可用的 Local zip 基线，无法保证可回滚。"
)


def scan_destructive_text(text: str) -> BreakerHit | None:
    """Scan free-form command/code text for catastrophic patterns."""
    if not text or not text.strip():
        return None
    from agentcore.runtime.command_policy import git_command_deny

    denied = git_command_deny(text)
    if denied is not None:
        return BreakerHit(
            verdict=BreakerVerdict.DENY,
            rule_id=denied.rule_id,
            reason=denied.reason,
        )
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
    return ""


def _path_args_for_tool(tool_name: str, arguments: dict[str, Any]) -> list[str]:
    if tool_name in {"read", "write", "edit"}:
        return [str(arguments.get("file_path") or "")]
    if tool_name == "grep":
        paths = [str(arguments.get("path") or "")]
        glob = str(arguments.get("glob") or "").strip()
        if glob:
            paths.append(glob)
        return paths
    return []


def _write_content_for_secret_scan(tool_name: str, arguments: dict[str, Any]) -> str:
    """Body about to land on disk (heuristic secret gate; 案 image-gen B)."""
    if tool_name == "write":
        return str(arguments.get("content") or "")
    if tool_name == "edit":
        return str(arguments.get("new_string") or "")
    return ""


def evaluate_tool_call(tool_name: str, arguments: dict[str, Any] | None) -> BreakerHit | None:
    """Evaluate a tool call; return a hit when the circuit breaker should intervene.

    Returns ``None`` when the call is not matched (normal approval / execution path).
    """
    args = arguments or {}
    name = (tool_name or "").strip()

    # Sensitive path reads: templates allow; credentials ASK; key material DENY.
    if name in {"read", "grep"}:
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

    # Sensitive writes: private-key paths DENY. ASK paths (``.env`` …) write like
    # any other project file (session boundary). Key-shaped body DENY except when
    # the destination is ASK — that is the designated secret file.
    if name in {"write", "edit"}:
        ask_dest = False
        for path in _path_args_for_tool(name, args):
            kind = classify_sensitive_path(path)
            if kind is SensitivePathClass.DENY:
                return BreakerHit(
                    verdict=BreakerVerdict.DENY,
                    rule_id="sensitive.path_write",
                    reason=_SENSITIVE_WRITE_DENY_REASON,
                )
            if kind is SensitivePathClass.ASK:
                ask_dest = True
        from agentcore.core.secrets import SECRET_WRITE_DENY_REASON, contains_secret

        body = _write_content_for_secret_scan(name, args)
        if contains_secret(body) and not ask_dest:
            return BreakerHit(
                verdict=BreakerVerdict.DENY,
                rule_id="sensitive.secret_write",
                reason=SECRET_WRITE_DENY_REASON,
            )

    # Destructive text on run / host command surfaces.
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
