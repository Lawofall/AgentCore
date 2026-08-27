"""Per-turn ``<workspace_context>`` — structured environment facts for CEO and workers.

根治「模型环境盲」：每回合把执行位置、工作区身份、桌面通道、本回合可执行能力写成显式
事实块注入 system prompt，避免 CEO 在云端 scratch 上规划「打开本机软件」并空跑委派。

**只陈述本回合事实**（位置 / 能力行 / 产物格式 / 挂载 / 产物出口路径 /
某能力装没装配、宿主是哪种）。
「该怎么做 / 禁止什么」的 HOW 不在这里（空桌 / 不可解析源数据 → ``team_delivery_env``）。
往本文件加禁令前，先确认它不在 ``resolve/prompt/base.py`` / 工具 schema / 对应 skill 里。
分层与理由 → docs/03-AI核心/上下文工程.md「提示词设计原则」。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
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
    """Root-``.git`` fact for ``<workspace_context>`` (same rule as ``git`` tool).

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


_GIT_UNASSEMBLED_LINE = (
    "版本控制：本回合未装配 `git` 工具——"
    "本机工作区的 Git 只能经桌面回填通道在用户机器上跑，而本会话通道未连接。"
    "装配启用：在桌面客户端打开【本对话】（通道连上即装配），"
    "或改用云端工作区 / 桌面 sidecar 会话。"
    "文件读写与其它已装配工具不受影响。"
)

# Capability fact when execution class is withheld (HOW → worker identity / skill).
_NO_EXEC_TABLE_FACT = (
    "表格解析：xlsx/csv/tsv 本回合无代码解析路径；"
    "结构面（列名/行数/类型/样例）已在附件块；"
    "file_read 不对用户表格抽文本；本 run 已落盘的自产表格可回读。"
)

# Same structural premise as ``no_exec_table`` (this-turn attachments / workspace
# type signal — not a body scan). Next-step HOW lives in team_delivery_env.
_OPAQUE_SOURCE_FACT = "本回合有无法可靠解析的源数据文件。"


def _opaque_source_data_present(
    backend: WorkspaceBackend,
    *,
    opaque_source_data_paths: Sequence[str] | None,
) -> bool:
    """True when this turn has source files workers cannot parse without execution."""
    from agentcore.runtime.runs.contract import collect_opaque_source_data_paths

    materials: Iterable[str] | None
    if opaque_source_data_paths is not None:
        materials = opaque_source_data_paths
    else:
        materials = getattr(backend, "ai_list_materials", None)
    return bool(collect_opaque_source_data_paths(material_paths=materials))


def format_workspace_git_line(
    fact: WorkspaceGitFact, *, tool_enabled: bool = True
) -> str:
    """Single git fact line for ``<workspace_context>`` (never a kickoff gate).

    ``tool_enabled`` is the same verdict the registries use
    (``tools.builtin.git_execution_enabled_for``): when the tool is not assembled the
    repo-presence tip is replaced outright, so the block never names ``init_baseline``
    on a turn where ``git`` is absent from the model's tool table.
    """
    if not tool_enabled:
        return _GIT_UNASSEMBLED_LINE
    scope = "仅识别工作区根 `.git`，不扫嵌套、不上溯"
    readonly = "只读 status/diff/log 无仓 → no_repo；其它写入无仓仍硬错"
    if fact.present is True:
        branch_bit = f"，分支 `{fact.branch}`" if fact.branch else ""
        return (
            f"版本控制：Git（{scope}{branch_bit}）。"
            f"{readonly}。"
        )
    if fact.present is False:
        return f"版本控制：工作区根无 Git（{scope}）。{readonly}。"
    return f"版本控制：未能确认根 `.git`（{scope}）。{readonly}。"

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


def _format_desk_line(
    *,
    desk_folder_id: str | None,
    desk_folder_label: str | None,
    root_label: str,
    desk_is_birth: bool,
) -> str:
    """One fact line: which folder ``file_*`` bind to this turn."""
    fid = (desk_folder_id or "").strip()
    label = (desk_folder_label or "").strip() or (root_label if fid else "")
    if fid:
        shown = label or fid
        if desk_is_birth:
            return (
                f"工作台：本会话出生桌=`{shown}`（folder_id=`{fid}`）。"
                "通用 `file_*` 只绑这张桌。"
            )
        return (
            f"工作台：默认工作区=`{shown}`（folder_id=`{fid}`）。"
            "通用 `file_*` 只绑这张桌。"
        )
    return "工作台：默认工作区=本会话出生桌（通用 `file_*` 只绑出生桌）。"


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
    code_execute_enabled: bool | None = None,
    terminal_enabled: bool | None = None,
    browser_enabled: bool | None = None,
    package_install_enabled: bool | None = None,
    git_tool_enabled: bool | None = None,
    exec_languages: list[str] | tuple[str, ...] | None = None,
    host_axis: HostAxis | str | None = None,
    permission_axes: PermissionAxes | None = None,
    mcp_enabled: bool = False,
    mcp_label: str | None = None,
    git_fact: WorkspaceGitFact | None = None,
    opaque_source_data_paths: Sequence[str] | None = None,
    outlet_inventory: Mapping[str, OutletDirListing] | None = None,
    desk_folder_id: str | None = None,
    desk_folder_label: str | None = None,
    desk_is_birth: bool = True,
) -> str:
    """Render the ``<workspace_context>`` block for this turn's backend + client.

    Always returns a non-empty block when ``backend`` is set (environment is a fact,
    even for an empty cloud scratch). ``backend is None`` → ``""`` (caller omits).

    Capability line uses the same predicates as worker registry assembly
    (``execution_class_enabled_for`` / ``browser_execution_enabled_for``, including
    ``command=ask`` withhold); optional ``*_enabled`` overrides are for tests /
    probes only — not a second truth source.

    ``permission_axes`` folds ask-withhold into ``code_execute`` / ``terminal`` /
    ``browser`` so the line never contradicts the worker toolset or identity
    (案 20260803-docx-office-exec-capability-lie A). When ``host_axis`` is omitted,
    it is taken from ``permission_axes.host``.

    ``package_install`` on cloud uses the same predicate as ``code_execute``
    (desk/net guest can start). Local follows execution-class only (pinned
    registry env, no host egress gate). Override ``package_install_enabled``
    is tests/probes only.

    ``exec_languages`` is the probed (local/sidecar) or fixed (cloud) language
    surface advertised on ``code_execute``; when set and execution is on, a one-line
    interpreter fact is appended so the model never plans against a missing launcher.

    ``git_fact`` is the root-``.git`` probe (same rule as the ``git`` tool). Callers
    that already awaited :func:`detect_workspace_git` should pass it; otherwise a
    sync root probe runs. Soft tip only — never a kickoff / durable-pause gate.
    Whether the ``git`` TOOL is assembled at all is a separate fact
    (``git_execution_enabled_for`` — the same predicate the registries use), stamped on
    the capability line; override ``git_tool_enabled`` is tests / probes only.

    ``opaque_source_data_paths`` is the same this-turn source list
    ``collect_opaque_source_data_paths`` uses for ``no_exec_table`` (tests).
    Production omits it and reads ``backend.ai_list_materials``.

    ``outlet_inventory`` is the live basename listing for the four 约定文档出口
    dirs (from :func:`collect_outlet_inventory`). ``None`` omits the suffix
    (tests); production callers pass the probe so empty dirs say 当前为空.

    ``desk_folder_id`` / ``desk_folder_label`` name the folder this agent is
    sitting on (conversation birth desk, or a worker's ``target_folder_id``).
    Facts only — no tool HOW. ``desk_is_birth`` distinguishes the conversation's
    birth desk from a per-call target desk.
    """
    if backend is None:
        return ""

    if host_axis is None and permission_axes is not None:
        host_axis = permission_axes.host

    location: Literal["server", "local"] = backend.location
    root_label = (getattr(backend, "root_label", None) or "workspace").strip() or "workspace"
    # Sidecar reuses ServerWorkspace(location=local) with direct Path I/O; LocalWorkspace
    # is the remote desktop-channel path. Both are "用户本机" for the model.
    is_local = location == "local"
    channel = getattr(backend, "_channel", None)
    is_remote_local = is_local and channel is not None

    if is_local:
        location_line = (
            "执行位置：用户本机"
            + ("（经桌面通道遥控）" if is_remote_local else "（本机引擎 / sidecar）")
        )
        identity_line = f"工作区身份：本地目录（根标签 `{root_label}`）"
        reach_line = "本机应用、本机文件与本机终端均可按已装配工具触达。"
        artifact_line = (
            "产物出口：你写入工作区的文件位于用户本机目录，"
            "用户可在「文件」面板查看；HTML 同样走「完整预览」进右坞「浏览器」标签"
            "（或本机直接打开，按用户习惯）。"
        )
        # 事实面：本机有出口 + 无原生生图。Key 明文禁令归共享基座 <credential_hygiene>。
        egress_line = (
            "出站网络：本机 code_execute / terminal 可走用户机器网络；无原生生图工具。"
        )
    else:
        location_line = "执行位置：云端沙箱（服务端）"
        # 已建云桌（tree / internal/folder）勿写成 scratch「草稿/临时」。
        # 裸聊默认云 scratch：空树 ≠ 本机/已打开仓库。对模型显式纠偏，避免把宿主路径当项目。
        if _is_cloud_folder_desk(backend):
            identity_line = (
                f"工作区身份：云端文件夹（根标签 `{root_label}`）——"
                "不是用户本机目录，也不是用户本机已打开的仓库或工程工作区。"
            )
            empty_tree_clause = (
                "空树只表示本文件夹尚无用户文件，不是本机空工程"
                "或宿主机器上的 Git 仓库。"
            )
        else:
            identity_line = (
                f"工作区身份：本会话云端草稿/临时文件空间（根标签 `{root_label}`）——"
                "不是用户本机目录，也不是用户本机已打开的仓库或工程工作区。"
            )
            empty_tree_clause = (
                "空树只表示本会话云端草稿尚无文件，不是本机空工程"
                "或宿主机器上的 Git 仓库。"
            )
        # Host 定案 §3.4: 云 reach 与 host= 正交——工作区在云；本机 Host 以能力行为准。
        reach_line = (
            "云端工作区文件在云端沙箱，不是用户本机磁盘；"
            "本机 Host（短命令 / 系统状态 / 设置等）另计，以能力行 host= 为准——"
            "host=已装配时可经桌面回填通道调用 host；"
            + empty_tree_clause
        )
        artifact_line = (
            "产物出口：你写入工作区的文件保存在云端工作区（不在用户本机），"
            "用户可在桌面端「文件」面板查看与下载；"
            "HTML 完整效果走终稿路径或文件横幅的「完整预览」"
            "（打开右坞「浏览器」标签，应用内渲染，非系统浏览器）。"
        )
        # 案 20260803-image-gen-byok-egress-boundary A：云桌 guest 出站经包装源
        # allowlist chokepoint，不是任意 HTTPS。事实面只陈述出口。
        egress_line = (
            "出站网络：云端 code_execute 无任意 HTTPS 出口（包装源 allowlist "
            "chokepoint 仅装包，≠通用出网）；无原生生图工具；"
            "browser 另计（隔离浏览器，≠ code_execute 出网）。"
        )

    if desktop_online:
        # 事实面：授权通道通不通 + 两种授权形态的工具名与访问路径。
        # 何时用哪种、口头同意、失败分型、先写再 copy → consult / ask_user_midtask。
        grant_line = (
            "区外目录：桌面在线，本机区外目录可授权——"
            "只读工具 `external_mount_readonly`，"
            "整理工具 `grant_organize_folder`；"
            "授权后以 `external/<别名>/…` 访问（经桌面通道、仅本次对话、可撤销），"
            "与工作区绑定正交。"
        )
        if is_local:
            desktop_line = "客户端通道：桌面端在线（本机执行通道可用）。"
        else:
            desktop_line = "客户端通道：桌面端在线。"
    else:
        # desktop_online=False covers missing header, unknown surface, and true
        # non-desktop clients — never accuse a device form (Web/手机) by default.
        # 通道复检 / 勿发卡冒充 / 禁臆造入口 → ask_user_midtask；此处只报通道事实。
        desktop_line = (
            "客户端通道：桌面回填通道未连接——"
            "打开本机文件夹、本机文件夹绑定、区外目录授权均须官方桌面客户端且通道已连接，"
            "当前会话无法履约。"
            "装配启用：在桌面客户端打开本对话。"
        )
        grant_line = "区外目录：授权仅桌面端可用；当前客户端无法履行。"

    mounts = getattr(backend, "_mounts", None) or {}
    if mounts:
        parts = []
        for a, m in mounts.items():
            mode = getattr(m, "mode", None) or (
                "readonly" if getattr(m, "readonly", True) else "organize"
            )
            mode_zh = "只读" if mode == "readonly" else "整理"
            parts.append(
                f"`external/{a}/`（{getattr(m, 'label', a)}，{mode_zh}）"
            )
        mounts_line = "本对话已授权区外目录：" + "；".join(parts) + "。"
    else:
        mounts_line = "本对话尚无会话级区外目录授权。"

    if code_execute_enabled is not None:
        exec_on = code_execute_enabled
    else:
        from agentcore.tools.builtin import execution_class_enabled_for

        exec_on = execution_class_enabled_for(backend, permission_axes)
    # terminal follows the same execution-class predicate as code_execute / test_run.
    term_on = (
        terminal_enabled if terminal_enabled is not None else exec_on
    )
    if browser_enabled is not None:
        browser_on = browser_enabled
    else:
        from agentcore.tools.builtin import browser_execution_enabled_for

        # Registry: include_browser = include_execution ∧ browser_execution_enabled_for.
        browser_on = exec_on and browser_execution_enabled_for(backend)
    # B1：装配事实闩锁 → 收口禁在未装配时声称已开浏览器（结构化对账，非扫气泡）。
    from agentcore.runtime.closing_posture import note_browser_assembled

    note_browser_assembled(browser_on)
    # local_open = 本机工作区可让用户直接打开产物（非 L3 浏览器工具；与 location 同事实）。
    local_open_on = is_local
    # Host 已装配 ⇔ host≠off ∧ 桌面回填通道可达（desktop_online）。
    host_off = False
    if host_axis is not None:
        host_val = getattr(host_axis, "value", None) or str(host_axis)
        host_off = host_val == "off"
    host_on = desktop_online and not host_off
    mcp_on = bool(mcp_enabled) if mcp_label is None else mcp_label == "已装配"
    mcp_cap = mcp_label if mcp_label is not None else ("已装配" if mcp_enabled else "未装配")
    # 装包与跑代码同一装配谓词（云桌 guest）；本机不吃主机 egress 门。
    pkg_on = (
        package_install_enabled if package_install_enabled is not None else exec_on
    )
    caps: list[str] = []
    caps.append(f"code_execute={'已装配' if exec_on else '未装配'}")
    caps.append(f"package_install={'已装配' if pkg_on else '未装配'}")
    caps.append(f"terminal={'已装配' if term_on else '未装配'}")
    # git 与执行类正交：云/sidecar 直接 spawn，本机远程工作区须桌面通道在线。
    if git_tool_enabled is not None:
        git_on = git_tool_enabled
    else:
        from agentcore.tools.builtin import git_execution_enabled_for

        git_on = git_execution_enabled_for(backend, desktop_online=desktop_online)
    caps.append(f"git={'已装配' if git_on else '未装配'}")
    caps.append(f"browser={'已装配' if browser_on else '未装配'}")
    caps.append(f"local_open={'已装配' if local_open_on else '未装配'}")
    caps.append(f"host={'已装配' if host_on else '未装配'}")
    caps.append(f"mcp={mcp_cap}")
    capability_line = "本回合执行能力：" + "；".join(caps) + "。"
    from agentcore.runtime.context.artifact_formats import format_artifact_capability_line

    artifact_format_line = format_artifact_capability_line(
        include_execution=exec_on,
        include_browser=browser_on,
        include_host=host_on,
        include_git=git_on,
        desktop_online=desktop_online,
        location=location,
    )
    if is_local:
        if pkg_on:
            package_guide_line = (
                "装包事实：package_install=已装配（本机执行环境；钉源 env，"
                "不吃主机 registry_egress）。"
                "装本机软件另须 host=已装配。"
            )
        else:
            package_guide_line = (
                "装包事实：package_install=未装配（本回合无执行环境；对照 code_execute=）。"
            )
    elif pkg_on:
        package_guide_line = (
            "装包事实：package_install=已装配（云桌 guest 健康 + 包装源 allowlist "
            "chokepoint）——可装依赖；≠通用 HTTPS 出网"
            "（对照「出站网络」行）。"
        )
    else:
        package_guide_line = (
            "装包事实：package_install=未装配（云桌 guest 未起，与 code_execute= "
            "同一谓词；装包腿随执行类一并不可用）。"
        )
    if exec_on:
        exec_guide_line = None
    else:
        has_opaque_source = _opaque_source_data_present(
            backend, opaque_source_data_paths=opaque_source_data_paths
        )
        if is_local:
            if has_opaque_source:
                exec_guide_line = (
                    "执行事实：code_execute=未装配（本机执行类未开）。"
                    + _OPAQUE_SOURCE_FACT
                    + _NO_EXEC_TABLE_FACT
                )
            else:
                exec_guide_line = (
                    "执行事实：code_execute=未装配（本机执行类未开）。"
                    + _NO_EXEC_TABLE_FACT
                )
        else:
            from agentcore.runtime.delegate.exec_env_remediation import (
                cloud_sandbox_failure_hint,
            )

            failure = cloud_sandbox_failure_hint()
            failure_clause = f"（探测={failure}）" if failure else ""
            if has_opaque_source:
                exec_guide_line = (
                    "执行事实：code_execute=未装配——已是云端会话、沙箱不可用"
                    f"{failure_clause}。"
                    "本机目录导入或远程仓克隆进这张桌，都不会让沙箱变为可用。"
                    + _OPAQUE_SOURCE_FACT
                    + _NO_EXEC_TABLE_FACT
                )
            else:
                exec_guide_line = (
                    "执行事实：code_execute=未装配——已是云端会话、沙箱不可用"
                    f"{failure_clause}。"
                    "本机目录导入或远程仓克隆进这张桌，都不会让沙箱变为可用。"
                    + _NO_EXEC_TABLE_FACT
                )

    if not desktop_online:
        mcp_guide_line = (
            "本机 MCP 事实：mcp=未装配（无桌面回填通道；通道缺失≠用户在用 Web/手机）。"
        )
    elif mcp_on:
        mcp_guide_line = (
            "本机 MCP 事实：mcp=已装配（经桌面 stdio 回填，非云进程直连本机）；"
            "仅 worker 持 MCP 工具（一律需审批），CEO 不直持；"
            "工具名形如 mcp_<server>_<tool>。"
        )
    else:
        mcp_guide_line = (
            f"本机 MCP 事实：mcp={mcp_cap}——"
            "本回合无可用 MCP 工具（未配置 / 握手失败已降级）。"
        )
    if host_off:
        host_guide_line = (
            "本机 Host 事实：host=未装配（用户已关本机协助 / host=off）——"
            "无 OS Host 事件日志通道；"
            "工作区 terminal / code_execute 仍可能已装配（host=off ≠ 整机只读）。"
        )
    elif host_on:
        host_guide_line = (
            "本机 Host 事实：host=已装配（经桌面回填通道，非云进程直探本机）——"
            "CEO 可 host(action=status/os_log/shell)；"
            "L2/L3（open_settings/set_audio/restart_service/install_package）仅 worker。"
        )
    else:
        host_guide_line = (
            "本机 Host 事实：host=未装配（无桌面回填通道）——无本机 OS Host 事件日志通道。"
        )
    if browser_on:
        # Path capability follows real host_kind (Bridge→local；过桥无桥→sandbox).
        # Test override ``browser_enabled=True`` without probing: local→Bridge guide.
        use_local_bridge_guide = is_local
        if browser_enabled is None:
            from agentcore.tools.builtin import browser_host_kind_for

            use_local_bridge_guide = browser_host_kind_for(backend) == "local"
        if use_local_bridge_guide:
            path_capability = (
                "宿主为桌面 Local Bridge：可打开本会话工作区相对 HTML 路径"
                "（如 `site/index.html`，与用户「完整预览」同源 workspace://）；"
                "公网仍用完整 http(s)；不支持 file://。"
            )
        else:
            path_capability = (
                "宿主为云端沙箱浏览器：仅支持公网 http(s)，"
                "本会话 HTML 相对路径**打不开**。"
            )
        browser_guide_line = (
            "浏览器事实：本回合已装配 browser"
            "（action=navigate/click/type/scroll/snapshot/console 由 CEO 可直持；"
            "screenshot 仅 worker；右坞会直播）。" + path_capability
        )
    else:
        if is_local:
            browser_base = (
                "浏览器事实：本回合 browser=未装配"
                "（无本机 Bridge 且本进程无可用云端隔离浏览器）——"
            )
            how_enable = (
                "装配启用需桌面 Local Chromium Bridge 健康；无 Bridge 且无 gVisor 时不可装配。"
            )
        elif desktop_online:
            browser_base = "浏览器事实：本回合 browser=未装配（无云端隔离浏览器）——"
            how_enable = (
                "装配启用：云端路径需云桌 guest 健康；"
                "本机已绑/本机传统会话可走本机 Bridge"
                "（过桥且云侧沙箱健康时装配 sandbox）。"
            )
        else:
            browser_base = "浏览器事实：本回合 browser=未装配（无云端隔离浏览器）——"
            how_enable = (
                "装配启用：当前非桌面会话无法绑定本机 Local Bridge；"
                "云端路径需云桌 guest 健康，或换桌面端。"
            )
        browser_guide_line = browser_base + how_enable

    # Prefer explicit languages; else a probe cached on the backend.
    langs = exec_languages
    if langs is None:
        langs = getattr(backend, "_exec_languages", None)
    interpreters_line: str | None = None
    if exec_on and langs is not None:
        from agentcore.tools.sandbox.exec_languages import format_interpreters_line

        interpreters_line = format_interpreters_line(tuple(langs))

    # 约定文档布局（始终可见）：四行出口 + 一句边界。只陈述路径事实，不注入文档正文进 <rules>。
    # 有 inventory 时附「现有 / 当前为空」——出口是写入落点，不是可直读的文件书目。
    dossier_drafts_line = format_outlet_line(
        "约定文档出口·默认落点（无专属出口的产物）：", DRAFTS_DIR, outlet_inventory
    )
    dossier_research_line = format_outlet_line(
        "约定文档出口·调研/讨论：", RESEARCH_DIR, outlet_inventory
    )
    dossier_debate_line = format_outlet_line(
        "约定文档出口·辩论副产物：", DEBATE_DIR, outlet_inventory
    )
    dossier_reviews_line = format_outlet_line(
        "约定文档出口·审查：", REVIEWS_DIR, outlet_inventory
    )
    dossier_boundary_line = (
        "约定文档边界：讨论/调研/审查类交付写此树，其余产物走默认落点；"
        "用户工程源码仍写业务路径。"
    )
    # Git fact: prefer caller probe (async Local); else sync root; never gates kickoff.
    # Repo presence and tool assembly are separate facts — an unassembled turn must
    # not name ``init_baseline`` (the model does not hold the tool).
    resolved_git = git_fact if git_fact is not None else detect_workspace_git_sync(backend)
    git_line = format_workspace_git_line(resolved_git, tool_enabled=git_on)

    # 跨文件夹指挥 HOW 归 team_cross_folder（工具面机制另在
    # delegate / list_folders / resolve_folder / create_folder / folder_fs 的 schema 里，
    # 队员持哪把工具就看哪条 schema）；此处只留一行事实：这张桌的路径和 id。
    desk_line = _format_desk_line(
        desk_folder_id=desk_folder_id,
        desk_folder_label=desk_folder_label,
        root_label=root_label,
        desk_is_birth=desk_is_birth,
    )
    # 事实句，不是禁令。空桌勿套工程壳的 HOW 归 team_delivery_env。
    root_scope_line = "工作区根：本文件夹根即工作区根。"

    body_lines = [
        location_line,
        identity_line,
        root_scope_line,
        reach_line,
        artifact_line,
        egress_line,
        dossier_drafts_line,
        dossier_research_line,
        dossier_debate_line,
        dossier_reviews_line,
        dossier_boundary_line,
        git_line,
        desk_line,
        desktop_line,
        grant_line,
        mounts_line,
        capability_line,
        *([artifact_format_line] if artifact_format_line else []),
        package_guide_line,
        *([exec_guide_line] if exec_guide_line else []),
        host_guide_line,
        mcp_guide_line,
        browser_guide_line,
    ]
    if interpreters_line is not None:
        body_lines.append(interpreters_line)
    body = "\n".join(body_lines)
    return f"<workspace_context>\n{body}\n</workspace_context>"
