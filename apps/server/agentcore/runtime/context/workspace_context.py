"""Per-turn ``<工作区>`` — short environment coordinates for CEO and workers.

只写开场工具表看不出来的现场：执行、桌、系统、Git、客户端、未装配缺口、
已挂区外、非空约定文档出口。已装配不报（开场表就是通道）；产物格式 / 出站 HOW /
表格解析 / 通道履约剧本不在这里。

空状态不写。空桌只标「顶层空」。CEO 文件索引仍拼在本块末节（工人不加）。
HOW → ``product_help`` / ``team_delivery_env`` / ``team_local_desk`` / 工具 description / consult。
分层 → docs/03-AI核心/上下文工程.md「提示词设计原则」。
"""

from __future__ import annotations

import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from agentcore.runtime.context.outlet_inventory import (
    OutletDirListing,
    format_outlet_line,
)
from agentcore.workspace.layout import CONV_SEGMENT, INTERNAL_SEGMENT, TREE_SEGMENT
from agentcore.workspace.stage_dirs import (
    DEBATE_DIR,
    DRAFTS_DIR,
    RESEARCH_DIR,
    REVIEWS_DIR,
)

if TYPE_CHECKING:
    from agentcore.core.types import HostAxis, PermissionAxes
    from agentcore.workspace.protocol import WorkspaceBackend

ChannelSurface = Literal["desktop", "web", "mobile", "unknown"]


@dataclass(frozen=True)
class WorkspaceGitFact:
    """Root-``.git`` fact for ``<工作区>`` (same rule as ``git`` tool).

    ``present=None`` = could not probe (exists/root I/O failed).
    Only the workspace root is considered — no nested scan, no parent climb.
    LocalWorkspace without Path.root still probes via ``backend.exists(".git")``.
    """

    present: bool | None
    branch: str | None = None


def _branch_from_git_head(head_text: str) -> str | None:
    line = (head_text or "").strip().splitlines()[0] if head_text else ""
    if line.startswith("ref: refs/heads/"):
        branch = line.removeprefix("ref: refs/heads/").strip()
        return branch or None
    return None


def detect_workspace_git_sync(backend: WorkspaceBackend | None) -> WorkspaceGitFact:
    """Sync probe via ``backend.root`` when available (server / sidecar Local)."""
    if backend is None:
        return WorkspaceGitFact(present=None)
    root = getattr(backend, "root", None)
    if root is None:
        return WorkspaceGitFact(present=None)
    try:
        root_path = Path(root)
        git_meta = root_path / ".git"
        if not git_meta.exists():
            return WorkspaceGitFact(present=False)
        branch: str | None = None
        if git_meta.is_file():
            # Worktree / gitfile pointer — treat as present; branch optional.
            return WorkspaceGitFact(present=True, branch=None)
        head = git_meta / "HEAD"
        if head.is_file():
            branch = _branch_from_git_head(head.read_text(encoding="utf-8", errors="replace"))
        return WorkspaceGitFact(present=True, branch=branch)
    except OSError:
        return WorkspaceGitFact(present=None)


async def detect_workspace_git(backend: WorkspaceBackend | None) -> WorkspaceGitFact:
    """Probe root ``.git`` — sync root first, else ``backend.exists`` (desktop Local)."""
    sync = detect_workspace_git_sync(backend)
    if sync.present is not None:
        return sync
    if backend is None:
        return WorkspaceGitFact(present=None)
    exists = getattr(backend, "exists", None)
    if exists is None:
        return WorkspaceGitFact(present=None)
    try:
        if not await exists(".git"):
            return WorkspaceGitFact(present=False)
    except Exception as exc:
        # Prepare-phase: first channel hang aborts (do not swallow into present=None).
        from agentcore.runtime.pipeline.errors import (
            prepare_local_io_budget_active,
            reraise_prepare_liveness_timeout,
        )

        if prepare_local_io_budget_active():
            reraise_prepare_liveness_timeout(exc)
        return WorkspaceGitFact(present=None)
    branch: str | None = None
    read = getattr(backend, "read", None)
    if read is not None:
        try:
            branch = _branch_from_git_head(await read(".git/HEAD"))
        except Exception:
            branch = None
    return WorkspaceGitFact(present=True, branch=branch)


_OS_LABEL = {"win32": "Windows", "darwin": "macOS", "linux": "Linux"}


def format_workspace_git_line(
    fact: WorkspaceGitFact, *, tool_enabled: bool = True
) -> str:
    """Single git coordinate for ``<工作区>`` (never a kickoff gate).

    Unassembled git is a 缺口, not a Git line — do not name the branch
    when the model does not hold the tool. Repo-policy (``no_repo`` /
    ``init_baseline``) lives on the git tool description.
    """
    if not tool_enabled or fact.present is None:
        return ""
    if fact.present is True:
        return f"Git：{fact.branch}" if fact.branch else "Git：有"
    return "Git：无"

_WEB_SURFACES: frozenset[str] = frozenset({"web", "mobile-web"})
_MOBILE_SURFACES: frozenset[str] = frozenset({"mobile", "android", "ios"})


@dataclass(frozen=True)
class ChannelProfile:
    """Single source for channel capabilities derived from ``X-Client-Platform``.

    Orthogonal to workspace ``location`` (local/server) and to auth audience
    (``parse_client_platform`` also fail-closes on missing / unknown headers).
    Missing / unknown headers fail closed — never pretend the web can drive Host.
    """

    surface: ChannelSurface
    desktop_online: bool
    can_bind_folder: bool

    def for_turn(self, *, member_turn: bool) -> ChannelProfile:
        """Member turns never fulfill desktop tools or advertise bind-local.

        Surface stays (they still opened the desktop app); capability flags drop.
        """
        if not member_turn:
            return self
        return ChannelProfile(
            surface=self.surface,
            desktop_online=False,
            can_bind_folder=False,
        )


def resolve_channel_profile(x_client_platform: str | None) -> ChannelProfile:
    """Map raw ``X-Client-Platform`` → :class:`ChannelProfile` (fail-closed).

    Only explicit ``desktop`` is a fulfillable desktop channel. Absent / blank /
    unknown values → ``surface=unknown``, both capability flags ``False``.
    """
    raw = (x_client_platform or "").strip().lower()
    if not raw:
        return ChannelProfile(surface="unknown", desktop_online=False, can_bind_folder=False)
    if raw == "desktop":
        return ChannelProfile(surface="desktop", desktop_online=True, can_bind_folder=True)
    if raw in _WEB_SURFACES:
        return ChannelProfile(surface="web", desktop_online=False, can_bind_folder=False)
    if raw in _MOBILE_SURFACES:
        return ChannelProfile(surface="mobile", desktop_online=False, can_bind_folder=False)
    return ChannelProfile(surface="unknown", desktop_online=False, can_bind_folder=False)


_FOLDER_INTERNAL_KIND = "folder"


def _is_cloud_folder_desk(backend: WorkspaceBackend) -> bool:
    """True when this server backend is a user Folder (tree), not conv scratch.

    Reads existing backend attrs only — does not rewrite path resolution.
    ``internal/folder/<id>`` or a visible ``tree/`` root → folder desk.
    Missing signals (FakeBackend / hermetic tmp roots) stay scratch.
    """
    internal = getattr(backend, "_internal_root", None)
    if internal is not None:
        try:
            parts = Path(internal).parts
        except (TypeError, ValueError):
            parts = ()
        if INTERNAL_SEGMENT in parts:
            idx = parts.index(INTERNAL_SEGMENT)
            kind = parts[idx + 1] if idx + 1 < len(parts) else ""
            if kind == _FOLDER_INTERNAL_KIND:
                return True
            if kind == CONV_SEGMENT:
                return False
    root = getattr(backend, "root", None) or getattr(backend, "_root", None)
    if root is not None:
        try:
            parts = Path(root).parts
        except (TypeError, ValueError):
            parts = ()
        if TREE_SEGMENT in parts:
            return True
        if CONV_SEGMENT in parts:
            return False
    return False


def _system_line(
    *,
    is_local: bool,
    is_remote_local: bool,
    langs: Sequence[str] | None,
) -> str:
    """OS · shell. Cloud is the guest; remote-local does not use the API host OS."""
    if not is_local:
        return "系统：Linux · bash"
    os_name = None if is_remote_local else _OS_LABEL.get(sys.platform)
    if langs is not None:
        shell = "bash" if "bash" in langs else "PowerShell"
    elif os_name == "Windows":
        shell = "PowerShell"
    elif os_name in ("Linux", "macOS"):
        shell = "bash"
    else:
        shell = None
    if os_name and shell:
        return f"系统：{os_name} · {shell}"
    if shell:
        return f"系统：{shell}"
    if os_name:
        return f"系统：{os_name}"
    return ""


def _desk_line(
    *,
    backend: WorkspaceBackend,
    is_local: bool,
    desk_folder_id: str | None,
    desk_folder_label: str | None,
    root_label: str,
    desk_visibly_empty: bool | None,
) -> str:
    """Which desk this agent sits on. No folder_id, no 出生桌."""
    empty = "；顶层空" if desk_visibly_empty else ""
    if is_local:
        root = getattr(backend, "root", None)
        shown = str(root) if root is not None else (
            (desk_folder_label or "").strip() or root_label
        )
        if empty:
            return f"桌：{shown}（顶层空）"
        return f"桌：{shown}"
    fid = (desk_folder_id or "").strip()
    label = (desk_folder_label or "").strip() or (root_label if fid else "")
    if fid or _is_cloud_folder_desk(backend):
        shown = label or fid or root_label
        return f"桌：{shown}（云端文件夹{empty}）"
    return f"桌：本会话草稿（云端{empty}）"


def _gap_line(flags: Sequence[tuple[str, bool]]) -> str:
    """Only names that are unassembled. Omit the line when none."""
    missing = [name for name, on in flags if not on]
    if not missing:
        return ""
    return "缺口：" + "、".join(missing)


def desktop_client_can_bind(x_client_platform: str | None) -> bool:
    """Thin fail-closed wrapper: folder AskOption actions need a desktop client.

    Covers ``open_local_project`` / ``bind_local_folder`` / ``grant_*``. ``None`` /
    unknown → ``False`` (same fail-closed spirit as auth ``parse_client_platform``,
    which raises rather than inventing a desktop audience).
    """
    return resolve_channel_profile(x_client_platform).can_bind_folder


def build_workspace_context(
    backend: WorkspaceBackend | None,
    *,
    desktop_online: bool,
    run_enabled: bool | None = None,
    browser_enabled: bool | None = None,
    package_install_enabled: bool | None = None,
    git_tool_enabled: bool | None = None,
    exec_languages: list[str] | tuple[str, ...] | None = None,
    host_axis: HostAxis | str | None = None,
    permission_axes: PermissionAxes | None = None,
    mcp_enabled: bool = False,
    mcp_label: str | None = None,
    git_fact: WorkspaceGitFact | None = None,
    outlet_inventory: Mapping[str, OutletDirListing] | None = None,
    desk_folder_id: str | None = None,
    desk_folder_label: str | None = None,
    desk_is_birth: bool = True,
    desk_visibly_empty: bool | None = None,
) -> str:
    """Render the ``<工作区>`` block for this turn's backend + client.

    Always returns a non-empty block when ``backend`` is set (environment is a fact,
    even for an empty cloud scratch). ``backend is None`` → ``""`` (caller omits).

    Gap line uses the same predicates as worker registry assembly
    (``execution_class_enabled_for`` / ``browser_execution_enabled_for``, including
    ``command=ask`` withhold); optional ``*_enabled`` overrides are for tests /
    probes only — not a second truth source. Assembled faces are omitted
    (the opening tool table is the channel).

    ``permission_axes`` folds ask-withhold into ``run`` /
    ``browser`` so the line never contradicts the worker toolset.
    When ``host_axis`` is omitted, it is taken from ``permission_axes.host``.

    ``package_install`` on cloud uses the same predicate as ``run``.
    Local follows execution-class only. Override ``package_install_enabled``
    is tests/probes only.

    ``exec_languages`` is the probed (local/sidecar) or fixed (cloud) language
    surface advertised on ``run``. Incomplete local probes get a short
    ``解释器：`` line; the full set is omitted.

    ``git_fact`` is the root-``.git`` probe. Unassembled git is a gap, not a
    Git line. Repo-policy lives on the git tool description.

    ``outlet_inventory`` lists the four 约定文档出口 dirs. Empty / ``None`` omit
    (layout HOW → ``team_delivery_env``).

    ``desk_folder_id`` / ``desk_folder_label`` name the sitting desk.
    ``desk_is_birth`` is accepted for call-site compatibility; the label is enough.
    ``desk_visibly_empty`` adds「顶层空」on the desk line.
    """
    del desk_is_birth
    if backend is None:
        return ""

    if host_axis is None and permission_axes is not None:
        host_axis = permission_axes.host

    location: Literal["server", "local"] = backend.location
    root_label = (getattr(backend, "root_label", None) or "workspace").strip() or "workspace"
    is_local = location == "local"
    channel = getattr(backend, "_channel", None)
    is_remote_local = is_local and channel is not None

    location_line = "执行：用户本机" if is_local else "执行：云端沙箱"
    desktop_line = "客户端：桌面已连接" if desktop_online else "客户端：未连接"

    mounts = getattr(backend, "_mounts", None) or {}
    mounts_line: str | None = None
    if mounts:
        parts = []
        for alias, mount in mounts.items():
            mode = getattr(mount, "mode", None) or (
                "readonly" if getattr(mount, "readonly", True) else "organize"
            )
            mode_zh = (
                "只读"
                if mode == "readonly"
                else ("可读写" if mode == "attach_rw" else "整理")
            )
            parts.append(f"`external/{alias}/`（{mode_zh}）")
        mounts_line = "区外：" + "；".join(parts)

    if run_enabled is not None:
        exec_on = run_enabled
    else:
        from agentcore.tools.builtin import execution_class_enabled_for

        exec_on = execution_class_enabled_for(backend, permission_axes)
    if browser_enabled is not None:
        browser_on = browser_enabled
    else:
        from agentcore.tools.builtin import browser_execution_enabled_for

        browser_on = exec_on and browser_execution_enabled_for(backend)
    from agentcore.runtime.closing_posture import note_browser_assembled

    note_browser_assembled(browser_on)
    local_open_on = is_local
    host_off = False
    if host_axis is not None:
        host_val = getattr(host_axis, "value", None) or str(host_axis)
        host_off = host_val == "off"
    host_on = desktop_online and not host_off
    mcp_on = mcp_enabled if mcp_label is None else mcp_label != "未装配"
    pkg_on = (
        package_install_enabled if package_install_enabled is not None else exec_on
    )
    if git_tool_enabled is not None:
        git_on = git_tool_enabled
    else:
        from agentcore.tools.builtin import git_execution_enabled_for

        git_on = git_execution_enabled_for(backend, desktop_online=desktop_online)

    leftover_lines: list[str] = []
    if not exec_on and not is_local:
        from agentcore.runtime.delegate.exec_env_remediation import (
            cloud_sandbox_failure_hint,
        )

        failure = cloud_sandbox_failure_hint()
        if failure:
            leftover_lines.append(f"沙箱：不可用（{failure}）")

    langs = exec_languages
    if langs is None:
        langs = getattr(backend, "_exec_languages", None)
    interpreters_line = ""
    if exec_on and langs is not None:
        from agentcore.tools.sandbox.exec_languages import format_interpreters_line

        interpreters_line = format_interpreters_line(tuple(langs))

    outlet_lines = [
        line
        for title, rel in (
            ("过程稿：", DRAFTS_DIR),
            ("调研：", RESEARCH_DIR),
            ("辩论：", DEBATE_DIR),
            ("审查：", REVIEWS_DIR),
        )
        if (line := format_outlet_line(title, rel, outlet_inventory))
    ]
    resolved_git = git_fact if git_fact is not None else detect_workspace_git_sync(backend)
    git_line = format_workspace_git_line(resolved_git, tool_enabled=git_on)
    desk_line = _desk_line(
        backend=backend,
        is_local=is_local,
        desk_folder_id=desk_folder_id,
        desk_folder_label=desk_folder_label,
        root_label=root_label,
        desk_visibly_empty=desk_visibly_empty,
    )
    body_lines = [
        location_line,
        desk_line,
        _system_line(
            is_local=is_local,
            is_remote_local=is_remote_local,
            langs=tuple(langs) if langs is not None else None,
        ),
        git_line,
        desktop_line,
        *outlet_lines,
        *([mounts_line] if mounts_line else []),
        _gap_line(
            (
                ("run", exec_on),
                ("package_install", pkg_on),
                ("git", git_on),
                ("browser", browser_on),
                ("local_open", local_open_on),
                ("host", host_on),
                ("mcp", mcp_on),
            )
        ),
        *leftover_lines,
        interpreters_line,
    ]
    body = "\n".join(line for line in body_lines if line)
    return f"<工作区>\n{body}\n</工作区>"
