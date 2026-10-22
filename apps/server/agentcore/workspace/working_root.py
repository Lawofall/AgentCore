"""Working-root grant: authorization is not a path rename.

The first directory grant on a scratch conversation (no project ``folder_id``)
is the session cwd. ``folder_id`` does not change. Model-facing paths stay
native: relatives under the working root; OS-absolute for extra grants that
have ``abs_path``. Internal routing may still use ``external/<alias>/`` for
extras without abs (cloud worker + desktop channel).

Attachments and ``AgentCore/`` stay on the conversation scratch even when a
working root is set.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from agentcore.workspace.attachments import ATTACHMENTS_DIR
from agentcore.workspace.external_mounts import (
    ExternalMount,
    external_ns,
    is_external_namespace,
)
from agentcore.workspace.stage_dirs import AGENTCORE_ROOT

_PRIMARY_EXACT = frozenset({ATTACHMENTS_DIR, AGENTCORE_ROOT, ".agentcore"})
_PRIMARY_PREFIXES = (
    f"{ATTACHMENTS_DIR}/",
    f"{AGENTCORE_ROOT}/",
    ".agentcore/",
)

HOST_GRANT_ABS_MSG = (
    "干活根和已授权文件夹里的文件用 read / run，不要用 host 拼盘符或 cd 进去。"
)


def working_mount(mounts: dict[str, ExternalMount] | None) -> ExternalMount | None:
    """The unique working-root grant, if any."""
    if not isinstance(mounts, dict) or not mounts:
        return None
    for mount in mounts.values():
        if mount.working_root:
            return mount
    return None


def stays_on_primary(path: str) -> bool:
    """True when ``path`` must keep using the conversation scratch."""
    raw = (path or "").replace("\\", "/").strip().lstrip("/")
    if not raw or raw == ".":
        return False
    if raw in _PRIMARY_EXACT:
        return True
    return any(raw.startswith(prefix) for prefix in _PRIMARY_PREFIXES)


def routes_to_working_root(
    path: str, mounts: dict[str, ExternalMount] | None
) -> bool:
    """True when a non-``external/`` relative should land on the working root."""
    if not working_mount(mounts):
        return False
    if is_external_namespace(path):
        return False
    return not stays_on_primary(path)


def posix_rel(path: str) -> str:
    rel = (path or "").replace("\\", "/").strip().strip("/")
    return rel if rel else "."


def join_host_abs(abs_path: str, rel: str) -> str:
    """Join an OS root and a relative remainder using the host separator."""
    remainder = (rel or "").replace("\\", "/").strip("/")
    if not remainder or remainder == ".":
        return abs_path
    return str(Path(abs_path) / remainder.replace("/", os.sep))


def model_facing_path(mount: ExternalMount, rel: str) -> str:
    """Path the model should see for a file under ``mount``."""
    remainder = posix_rel(rel)
    if mount.working_root:
        return remainder
    if mount.abs_path:
        return join_host_abs(mount.abs_path, remainder)
    return external_ns(mount.alias, remainder)


def model_facing_namespace(mount: ExternalMount) -> str:
    """Grant receipt namespace: ``.`` for the working root, else internal extra."""
    if mount.working_root:
        return "."
    return external_ns(mount.alias)


def out_routed_path(
    path: str,
    alias: str | None,
    mounts: dict[str, ExternalMount] | None,
) -> str | None:
    """Rewrite an internal routed path to the model-facing dialect.

    ``None`` means the caller should keep its primary-workspace ``_out``.
    """
    if alias is None:
        working = working_mount(mounts)
        if working is None or stays_on_primary(path):
            return None
        return model_facing_path(working, path)
    mounts = mounts or {}
    mount = mounts.get(alias)
    if mount is None:
        return external_ns(alias, path)
    return model_facing_path(mount, path)


def internal_tool_path(mount: ExternalMount, remainder: str) -> str:
    """Path file tools hand to the backend after a host-path mint.

    Working root → relative remainder. Extra → ``external/<alias>/…`` so the
    existing mount router still applies (listing rewrites extras to abs).
    """
    rel = posix_rel(remainder)
    if mount.working_root:
        return rel
    if rel in ("", "."):
        return external_ns(mount.alias)
    return external_ns(mount.alias, rel)


def host_command_hits_grant_abs(
    command: str, mounts: dict[str, ExternalMount] | None
) -> bool:
    """True when a host command embeds a granted folder's absolute path."""
    if not command or not mounts:
        return False
    blob = command.replace("/", os.sep)
    blob_alt = command.replace("\\", "/")
    for mount in mounts.values():
        abs_path = (mount.abs_path or "").strip()
        if not abs_path:
            continue
        if abs_path in command or abs_path in blob or abs_path.replace("\\", "/") in blob_alt:
            return True
        folded = abs_path.replace("/", os.sep)
        if folded and folded in blob:
            return True
    return False


def working_cwd_payload(backend: Any) -> tuple[str | None, str | None]:
    """``(cwd_abs, root_id)`` for host / run when a working root is live."""
    mounts = getattr(backend, "_mounts", None)
    mount = working_mount(mounts if isinstance(mounts, dict) else None)
    if mount is None:
        return None, None
    cwd = (mount.abs_path or "").strip() or None
    rid = (mount.root_id or "").strip() or None
    return cwd, rid
