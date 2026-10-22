"""Mint a session directory grant for a host OS path.

Transport stays the existing ClientTool event; the model never sees a mount
tool. Server still stores only alias/root_id/mode — never abs. Working-root
grants rewrite to a relative remainder; extras keep the internal
``external/<alias>/`` router (listing shows OS abs when the engine has it).
"""

from __future__ import annotations

from agentcore.core.logging import get_logger
from agentcore.db.sidecar_tickets import sidecar_narrow_tickets_bound
from agentcore.desktop.channel import ExternalMountError
from agentcore.tools.protocol import ToolContext
from agentcore.workspace import grant_store
from agentcore.workspace.external_mounts import parse_external_path
from agentcore.workspace.host_path import (
    ClassifiedPath,
    GrantMode,
    mode_covers,
    split_host_entry_for_mount,
)
from agentcore.workspace.hot_attach import attach_grants_to_backend
from agentcore.workspace.working_root import internal_tool_path

logger = get_logger(__name__)

_RESOLVE_REASONS = frozenset({"not_found", "not_directory", "ambiguous", "invalid"})

_MOUNT_NOT_DIRECTORY = (
    "这是文件不是文件夹；请选它所在的目录，或把内容放进当前工作区。"
)
_MOUNT_NOT_FOUND = (
    "找不到该目录。"
    "授权只接受文件夹；若给的是安装包/文件，请改选它所在目录或放进工作区。"
)
_NO_DESKTOP = (
    "读取或整理本机路径需要桌面回填通道：当前无在线桌面客户端。"
    "请如实说明须用桌面客户端，勿假装已能读本机目录。"
)
_CLOUD_ATTACH_RW = (
    "云对话不能把本机目录加成可覆盖写根。"
    "本回合不能改该文件夹原件。"
    "拷入新文件：先写工作区，再 file_batch copy（须用户确认整理授权、不覆盖）。"
)
_FORBIDDEN_ROOT = "不能授权整盘或系统根；请给出具体文件夹。"
_HOME_NOT_WELL_KNOWN = (
    "家目录下只接受 ~/Desktop、~/Downloads、~/Documents（或其下路径）；"
    "其它 ~ 路径请改用本机绝对路径。"
)
_CANCELLED = "用户拒绝授权。勿用相同路径盲重试。"


class HostPathDeniedError(Exception):
    """Structured refusal that is not a desktop resolve failure."""

    def __init__(self, message: str, *, reason: str | None = None) -> None:
        super().__init__(message)
        self.reason = reason


def format_external_mount_error(exc: ExternalMountError) -> str:
    """Model-facing error: stable reason copy + reason tag when present."""
    reason = (exc.reason or "").strip() or None
    if reason == "cancelled":
        return _CANCELLED
    if reason == "not_directory":
        detail = _MOUNT_NOT_DIRECTORY
    elif reason == "not_found":
        detail = _MOUNT_NOT_FOUND
    else:
        detail = (str(exc).strip() or "找不到该目录").replace(
            "，无法挂载", ""
        ).replace("无法挂载", "")
        detail = detail.strip(" ，") or "找不到该目录"
    if not reason:
        return detail
    parts = [f"{detail}（reason={reason}）"]
    if reason in _RESOLVE_REASONS:
        parts.append("勿用相同参数盲重试。")
    return " ".join(parts)


def format_host_path_denied(exc: HostPathDeniedError) -> str:
    return str(exc).strip()


async def _mint(
    context: ToolContext,
    *,
    path: str | None,
    well_known: str | None,
    target_name: str | None,
    remainder: str,
    grant_mode: GrantMode,
    root_id: str | None = None,
) -> str:
    channel = context.desktop_channel
    if channel is None:
        raise HostPathDeniedError(_NO_DESKTOP, reason="no_desktop")

    logger.info(
        "desktop.external_mount_request",
        run_id=context.run_id,
        conversation_id=context.conversation_id,
        has_path=bool(path),
        well_known=well_known,
        has_target_name=bool(target_name),
        has_root_id=bool(root_id),
        mode=grant_mode,
    )
    value = await channel.request_external_mount(
        path=path,
        well_known=well_known,
        target_name=target_name,
        mode=grant_mode,
        root_id=root_id,
    )
    root_id = str(value.get("root_id") or "").strip()
    label = str(value.get("label") or "").strip()
    alias_hint = str(value.get("alias") or "").strip() or None
    display_label = str(value.get("display_label") or "").strip() or None
    namespace_from_desktop = str(value.get("namespace") or "").strip() or None
    if not root_id:
        raise HostPathDeniedError("桌面回填缺少 root_id，无法登记授权")

    if sidecar_narrow_tickets_bound():
        # Desktop already POSTed cloud ``external-grants`` and hot-pushed abs.
        # Tickets, not ``location=local``: cloud local-binding still uses grant_store.
        alias = alias_hint or ""
        if not alias:
            raise HostPathDeniedError("桌面回填缺少 alias，无法登记授权")
        await attach_grants_to_backend(
            context.backend,
            context.conversation_id,
            desktop_channel=channel,
            workspace_channel=context.workspace_channel,
        )
        live = (getattr(context.backend, "_mounts", None) or {}).get(alias)
        mount = live
        _ = display_label
        _ = namespace_from_desktop
        if mount is None:
            from agentcore.workspace.external_mounts import ExternalMount, normalize_mount_mode

            mount = ExternalMount(
                alias=alias,
                root_id=root_id,
                label=label or alias,
                abs_path=None,
                mode=normalize_mount_mode(grant_mode),
            )
        return internal_tool_path(mount, remainder)

    mount = await grant_store.add_grant(
        context.conversation_id,
        root_id=root_id,
        label=label or alias_hint or "external",
        alias_hint=alias_hint or label or None,
        mode=grant_mode,
    )
    await attach_grants_to_backend(
        context.backend,
        context.conversation_id,
        desktop_channel=channel,
        workspace_channel=context.workspace_channel,
    )
    live = (getattr(context.backend, "_mounts", None) or {}).get(mount.alias)
    if live is not None:
        mount = live
    _ = display_label
    _ = namespace_from_desktop
    return internal_tool_path(mount, remainder)


async def ensure_host_path(
    classified: ClassifiedPath,
    context: ToolContext,
    *,
    as_directory: bool,
    grant_mode: GrantMode,
) -> str:
    """Return a backend path for a classified host path.

    A file or a name being created (``as_directory=False``) mints the parent
    folder and keeps the last segment as remainder. Listing a directory mints
    the path as given. The desktop still only locates an existing folder.
    """
    if classified.kind == "forbidden":
        if classified.forbidden_reason == "home_not_well_known":
            raise HostPathDeniedError(_HOME_NOT_WELL_KNOWN, reason="invalid")
        raise HostPathDeniedError(_FORBIDDEN_ROOT, reason="invalid")

    location = getattr(context.backend, "location", None)
    if grant_mode == "attach_rw" and location == "server":
        raise HostPathDeniedError(_CLOUD_ATTACH_RW, reason="cloud_attach_rw")

    path = classified.abs_path
    well_known = classified.well_known
    target_name = classified.target_name
    remainder = classified.remainder
    if not as_directory:
        path, well_known, target_name, remainder = split_host_entry_for_mount(
            path=path,
            well_known=well_known,
            target_name=target_name,
            remainder=remainder,
        )

    return await _mint(
        context,
        path=path,
        well_known=well_known,
        target_name=target_name,
        remainder=remainder,
        grant_mode=grant_mode,
    )


def _lookup_mount(context: ToolContext, alias: str):
    """Live backend mounts as same-turn fallback when the grant store is empty."""
    backend_mounts = getattr(context.backend, "_mounts", None) or {}
    return backend_mounts.get(alias)


async def ensure_external_upgrade(
    path: str,
    context: ToolContext,
    *,
    grant_mode: GrantMode,
) -> str:
    """Upgrade an already-mounted ``external/<alias>/…`` path when mode is short.

    Server never stores abs; desktop upgrades the same session root by
    ``root_id``. Already-sufficient mode is a no-op (return the original path).
    """
    parsed = parse_external_path(path)
    if parsed is None:
        return path
    alias, remainder = parsed
    cid = (context.conversation_id or "").strip()
    mount = _lookup_mount(context, alias)
    if mount is None and cid and not sidecar_narrow_tickets_bound():
        mounts = await grant_store.grants_as_dict(cid)
        mount = mounts.get(alias)
    if mount is None:
        return path
    if mode_covers(mount.mode, grant_mode):
        return path

    location = getattr(context.backend, "location", None)
    if grant_mode == "attach_rw" and location == "server":
        # Cloud cannot grow attach_rw. Leave the mount so backend policy
        # (readonly / organize deny) is the model-facing reason — not a
        # "can't attach" missive for a path that is already granted.
        return path

    if not cid:
        return path

    root_id = str(mount.root_id or "").strip()
    if not root_id:
        raise HostPathDeniedError("已授权目录缺少 root_id，无法升级授权", reason="invalid")

    await _mint(
        context,
        path=None,
        well_known=None,
        target_name=None,
        remainder=remainder,
        grant_mode=grant_mode,
        root_id=root_id,
    )
    return path
