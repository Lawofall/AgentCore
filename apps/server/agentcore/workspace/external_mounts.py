"""Session-level external directory mounts (W3 readonly + organize).

Model-facing paths use the relative namespace ``external/<alias>/…`` — absolute
OS paths never enter prompts. File tools route through these mounts; access is
gated by per-alias ``mode`` (readonly | organize). ``resolve_safe_path`` /
pathGuard algorithms are unchanged: each mount is a separate root passed into
the same guard.
"""

from __future__ import annotations

import base64
import hashlib
import re
from dataclasses import dataclass
from typing import Literal

EXTERNAL_PREFIX = "external/"
_ALIAS_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_ALIAS_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")
_READONLY_MSG = (
    "会话授权目录为只读授权，不能改动；如需在此目录交付或整理，"
    "请让用户升级为整理授权（交付：先写工作区，再 `file_copy` 到此目录）"
)
_ORGANIZE_DENY_MSG = (
    "整理授权不允许此操作（仅 list/read/grep/stat + move/copy/mkdir + 回收站删除）"
)
_PERMANENT_EXTERNAL_MSG = "区外目录禁止永久删除；请使用可逆删除（进回收站）"

ExternalMountMode = Literal["readonly", "organize"]

# Op policy for session external mounts. These three sets are the single source of
# truth mirrored by the desktop dispatch gate
# (``apps/desktop/src/main/fs/workspace/sessionRoot.ts``); both ends are whitelists so
# a newly added ``WorkspaceOp`` is denied until classified, never silently allowed.
# ``tests/test_external_op_parity.py`` asserts the mirror **and** exhaustiveness over
# ``WorkspaceOp`` — a new op makes it red on both ends.

# Read-side ops (no mutation): allowed under readonly *and* organize.
READONLY_ALLOWED_OPS: frozenset[str] = frozenset(
    {
        "read",
        "read_bytes",
        "read_head",
        "read_lines",
        "list",
        "exists",
        "list_tree",
        "index_files",
        "grep",
        "diagnostics",  # 内环语言服务只读诊断
        "probe_exec",  # 解释器探测，与绑定根内容无关
        "process_read",
        "process_list",
        "process_stop",
        "git_repo_status",  # 只读 git 摘要（桌面 UI chip）
    }
)

# Mutating ops organize may perform (workspace-layer semantic names).
ORGANIZE_MUTATION_OPS: frozenset[str] = frozenset({"move", "copy", "mkdir", "delete"})

# Desktop / engine op names allowed under organize mode (read + organize mutations).
ORGANIZE_ALLOWED_OPS: frozenset[str] = READONLY_ALLOWED_OPS | ORGANIZE_MUTATION_OPS

# Explicit denials under organize (defense in depth; also absent from ALLOWED).
ORGANIZE_DENIED_OPS: frozenset[str] = frozenset(
    {
        "write",
        "append",
        "write_bytes",
        "replace",
        "execute",
        "process_start",
        "archive",
        "ensure_turn_baseline",
        "git_scm",
        "git_run",
    }
)


@dataclass(frozen=True)
class ExternalMount:
    """One session-scoped directory grant under ``external/<alias>/``.

    ``root_id`` is the desktop authorized-root handle (LocalWorkspace channel /
    cloud ServerWorkspace external bridge). ``abs_path`` is set only where the
    engine has direct Path I/O (sidecar); cloud grants leave it ``None`` and let
    the desktop resolve via per-op ``root_id``. ``mode`` is explicit: never flip a
    bare ``readonly=False`` (that would also open execute / process_start /
    archive on the desktop dispatch path).
    """

    alias: str
    root_id: str
    label: str
    abs_path: str | None = None
    mode: ExternalMountMode = "readonly"

    @property
    def readonly(self) -> bool:
        """True when this mount is read-only (W3). Prefer ``mode`` for new code."""
        return self.mode == "readonly"


@dataclass(frozen=True)
class RoutedExternal:
    mount: ExternalMount
    """Path relative to the mount root (``""`` / ``"."`` = mount root itself)."""
    rel: str


def is_external_namespace(path: str) -> bool:
    """True when ``path`` claims the reserved ``external/`` mount namespace.

    Any such path must be routed (or rejected) — never fall through to the
    primary workspace root (would silently write under ``external/…`` there).
    """
    raw = (path or "").strip().replace("\\", "/").lstrip("/")
    return raw == "external" or raw.startswith(EXTERNAL_PREFIX)


def alias_is_routable(alias: str) -> bool:
    """True when ``alias`` matches the path-router ASCII whitelist."""
    return bool(alias and _ALIAS_RE.match(alias))


def _ascii_fold_digest(s: str) -> str:
    """Stable short base32 digest (lowercase, no padding) for non-ASCII names."""
    digest = hashlib.sha256(s.encode("utf-8")).digest()[:5]
    return base64.b32encode(digest).decode("ascii").rstrip("=").lower()


def sanitize_alias(raw: str) -> str:
    """Derive a stable alias from a folder display name.

    Output always matches ``_ALIAS_RE`` (ASCII only). Non-ASCII display names
    fold to ``[<ascii_slug>_]<base32digest>`` or ``ext_<digest>`` so grant
    storage and path routing share one charset — never store an alias the
    router cannot parse.
    """
    s = (raw or "").strip().replace("\\", "/").rstrip("/")
    if "/" in s:
        s = s.rsplit("/", 1)[-1]
    ascii_part = _ALIAS_SAFE_RE.sub("_", s).strip("._-")
    needs_fold = (not ascii_part) or any(ord(c) > 127 for c in s)
    if needs_fold:
        enc = _ascii_fold_digest(s or "folder")
        alias = f"{ascii_part[:24]}_{enc}" if ascii_part else f"ext_{enc}"
    else:
        alias = ascii_part
    alias = alias.strip("._-") or "folder"
    if alias[0].isdigit():
        alias = f"d_{alias}"
    alias = alias[:64]
    if not _ALIAS_RE.match(alias):
        # Last-resort: pure digest (always ASCII / valid length).
        alias = f"ext_{_ascii_fold_digest(s or 'folder')}"[:64]
    return alias


def uniquify_alias(base: str, taken: set[str]) -> str:
    """Ensure ``base`` is unique within ``taken`` (append ``_2``, ``_3``, …).

    Suffix must not push the result past 64 chars or outside ``_ALIAS_RE``.
    """
    alias = sanitize_alias(base)
    if alias not in taken:
        return alias
    n = 2
    while True:
        suffix = f"_{n}"
        candidate = f"{alias[: 64 - len(suffix)]}{suffix}"
        if candidate[0].isdigit():
            candidate = f"d{candidate[1:]}"[:64]
        if _ALIAS_RE.match(candidate) and candidate not in taken:
            return candidate
        n += 1


def parse_external_path(path: str) -> tuple[str, str] | None:
    """If ``path`` is under ``external/<alias>/…``, return ``(alias, rel)``.

    ``rel`` is ``""`` when the path names the mount root itself.
    Invalid / non-ASCII aliases under ``external/`` return ``None`` — callers
    that see ``is_external_namespace`` must treat that as ``PathNotFound``,
    not as a primary-workspace relative path.
    """
    raw = (path or "").strip().replace("\\", "/").lstrip("/")
    if not raw.startswith(EXTERNAL_PREFIX):
        return None
    rest = raw[len(EXTERNAL_PREFIX) :]
    if not rest:
        return None
    alias, _, rel = rest.partition("/")
    if not alias or not _ALIAS_RE.match(alias):
        return None
    return alias, rel


def route_external(
    path: str, mounts: dict[str, ExternalMount]
) -> RoutedExternal | None:
    """Route an ``external/<alias>/…`` path, or ``None`` when not external."""
    parsed = parse_external_path(path)
    if parsed is None:
        return None
    alias, rel = parsed
    mount = mounts.get(alias)
    if mount is None:
        return None
    return RoutedExternal(mount=mount, rel=rel)


def external_ns(alias: str, rel: str = "") -> str:
    """Build the model-facing path ``external/<alias>[/rel]``."""
    rel = (rel or "").replace("\\", "/").strip("/")
    if not rel or rel == ".":
        return f"{EXTERNAL_PREFIX}{alias}"
    return f"{EXTERNAL_PREFIX}{alias}/{rel}"


def readonly_write_error(path: str) -> str:
    return f"{_READONLY_MSG}（拒绝写入 `{path}`）"


def organize_deny_error(path: str, op: str) -> str:
    return f"{_ORGANIZE_DENY_MSG}（拒绝 `{op}` → `{path}`）"


def permanent_external_error(path: str) -> str:
    return f"{_PERMANENT_EXTERNAL_MSG}（拒绝 `{path}`）"


def normalize_mount_mode(raw: str | None) -> ExternalMountMode:
    text = (raw or "readonly").strip().lower()
    if text == "organize":
        return "organize"
    return "readonly"


_CROSS_COPY_MSG = "不能跨会话授权目录与工作区复制文件"
_CROSS_MOUNT_COPY_MSG = "不能跨会话授权目录复制文件"
_CROSS_MOVE_MSG = "不能跨会话授权目录与工作区移动文件"
_CROSS_MOUNT_MOVE_MSG = "不能跨会话授权目录移动文件"


def cross_root_copy_error(src_alias: str | None, dst_alias: str | None) -> str | None:
    """Deny message for copy across roots, or ``None`` if the direction is allowed.

    Same alias (including both ``None`` = primary workspace) is in-root copy.
    Workspace → external is allowed here; the caller still gates dest with
    ``op="copy"`` so readonly mounts stay denied. Reverse and cross-mount stay
    denied. Does not add ops to the external allow-set.
    """
    if src_alias == dst_alias:
        return None
    if src_alias is None and dst_alias is not None:
        return None
    if src_alias is not None and dst_alias is not None:
        return _CROSS_MOUNT_COPY_MSG
    return _CROSS_COPY_MSG


def cross_root_move_error(src_alias: str | None, dst_alias: str | None) -> str | None:
    """Deny message for move across roots. Cross-root move is never allowed."""
    if src_alias == dst_alias:
        return None
    if src_alias is not None and dst_alias is not None:
        return _CROSS_MOUNT_MOVE_MSG
    return _CROSS_MOVE_MSG


def external_mutation_allowed(
    mount: ExternalMount,
    op: str,
    *,
    path: str = "",
    permanent: bool = False,
) -> str | None:
    """Return an error message when a mutating op is denied on this mount; else None.

    Routing-layer rules:
    - readonly → all mutations denied
    - permanent delete on any external mount → always denied (stricter than workspace)
    - organize → only move / copy / mkdir / non-permanent delete
    """
    label = path or external_ns(mount.alias)
    if permanent:
        return permanent_external_error(label)
    if mount.mode == "readonly":
        return readonly_write_error(label)
    if op in ORGANIZE_DENIED_OPS or op not in ORGANIZE_MUTATION_OPS:
        return organize_deny_error(label, op)
    return None


def external_env_var(alias: str) -> str:
    """Env var name for code_execute injection (absolute path value)."""
    safe = re.sub(r"[^A-Za-z0-9]+", "_", alias).strip("_").upper() or "FOLDER"
    return f"AGENTCORE_EXTERNAL_{safe}"


def build_external_env(mounts: dict[str, ExternalMount]) -> dict[str, str]:
    """Map alias → abs path for code_execute env injection.

    Organize mounts are **excluded** (proposal §五): file tools are the only
    supported external write path under organize. Skips missing abs.
    """
    out: dict[str, str] = {}
    for alias, m in mounts.items():
        if m.mode == "organize":
            continue
        if m.abs_path:
            out[external_env_var(alias)] = m.abs_path
    return out
