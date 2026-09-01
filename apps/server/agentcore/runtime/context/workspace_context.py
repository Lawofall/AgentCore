"""Per-turn ``<工作区>`` — structured environment facts for CEO and workers.

根治「模型环境盲」：每回合把执行位置、工作区身份、桌面通道、本回合可执行能力写成显式
事实块注入 system prompt，避免 CEO 在云端 scratch 上规划「打开本机软件」并空跑委派。

**只陈述本回合选动作要用的短事实**（位置 / 身份 / 根 / 能力格 / 出站网络 /
通道 / git / 工作台 / 非空挂载 / 非空约定文档出口 / 产物格式）。
空状态不写。产物出口 UI、约定文档边界、区外工具名、浏览器宿主 HOW、git 探测范围
不在这里（``product_help`` / ``team_delivery_env`` / 工具 description / consult）。
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


_GIT_UNASSEMBLED_LINE = "版本控制：本回合未装配 git。"

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
    """Single git fact line for ``<工作区>`` (never a kickoff gate).

    ``tool_enabled`` is the same verdict the registries use
    (``tools.builtin.git_execution_enabled_for``): when the tool is not assembled
    the line states that fact only. Repo-policy (``no_repo`` / ``init_baseline``)
    lives on the git tool description — not here.
    """
    if not tool_enabled:
        return _GIT_UNASSEMBLED_LINE
    if fact.present is True:
        branch_bit = f"，分支 `{fact.branch}`" if fact.branch else ""
        return f"版本控制：Git{branch_bit}。"
    if fact.present is False:
        return "版本控制：工作区根无 Git。"
    return "版本控制：未能确认根 `.git`。"

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
    """One fact line: which folder this agent sits on this turn."""
    fid = (desk_folder_id or "").strip()
    label = (desk_folder_label or "").strip() or (root_label if fid else "")
    if fid:
        shown = label or fid
        if desk_is_birth:
            return f"工作台：本会话出生桌=`{shown}`（folder_id=`{fid}`）。"
        return f"工作台：默认工作区=`{shown}`（folder_id=`{fid}`）。"
    return "工作台：默认工作区=本会话出生桌。"


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
    opaque_source_data_paths: Sequence[str] | None = None,
    outlet_inventory: Mapping[str, OutletDirListing] | None = None,
    desk_folder_id: str | None = None,
    desk_folder_label: str | None = None,
    desk_is_birth: bool = True,
) -> str:
    """Render the ``<工作区>`` block for this turn's backend + client.

    Always returns a non-empty block when ``backend`` is set (environment is a fact,
    even for an empty cloud scratch). ``backend is None`` → ``""`` (caller omits).

    Capability line uses the same predicates as worker registry assembly
    (``execution_class_enabled_for`` / ``browser_execution_enabled_for``, including
    ``command=ask`` withhold); optional ``*_enabled`` overrides are for tests /
    probes only — not a second truth source.

    ``permission_axes`` folds ask-withhold into ``run`` /
    ``browser`` so the line never contradicts the worker toolset or identity
    (案 20260803-docx-office-exec-capability-lie A). When ``host_axis`` is omitted,
    it is taken from ``permission_axes.host``.

    ``package_install`` on cloud uses the same predicate as ``run``
    (desk/net guest can start). Local follows execution-class only (pinned
    registry env, no host egress gate). Override ``package_install_enabled``
    is tests/probes only.

    ``exec_languages`` is the probed (local/sidecar) or fixed (cloud) language
    surface advertised on ``run``; when set and execution is on, a one-line
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
    dirs (from :func:`collect_outlet_inventory`). Empty dirs and ``None`` omit
    the line (layout HOW → ``team_delivery_env``).

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
        identity_line = (
            f"工作区身份：本地目录（根标签 `{root_label}`）；当前目录已可写。"
        )
        # 事实面：本机有出口 + 无原生生图。Key 明文禁令归共享基座 <工作权威>。
        egress_line = (
            "出站网络：本机 run 可走用户机器网络；无原生生图工具。"
        )
    else:
        location_line = "执行位置：云端沙箱（服务端）"
        # 已建云桌（tree / internal/folder）勿写成 scratch「草稿/临时」。
        # 「非本机目录」是云 vs 本机的对比边界（A≠A′），只写一次。
        if _is_cloud_folder_desk(backend):
            identity_line = (
                f"工作区身份：云端文件夹（根标签 `{root_label}`；非本机目录）。"
            )
        else:
            identity_line = (
                f"工作区身份：本会话云端草稿/临时文件空间"
                f"（根标签 `{root_label}`；非本机目录）。"
            )
        # 案 20260803-image-gen-byok-egress-boundary A：云桌 guest 出站经包装源
        # allowlist chokepoint，不是任意 HTTPS。事实面只陈述出口。
        egress_line = (
            "出站网络：云端 run 无任意 HTTPS 出口（包装源 allowlist "
            "chokepoint 仅装包，≠通用出网）；无原生生图工具；"
            "browser 另计（隔离浏览器，≠ run 出网）。"
        )

    if desktop_online:
        # 工具名 / 口头同意 / 先写再 copy → consult(external_mount_readonly)。
        grant_line = "区外目录：可授权（与工作区绑定正交）。"
        if is_local:
            desktop_line = "客户端通道：桌面端在线（本机执行通道可用）。"
        else:
            desktop_line = "客户端通道：桌面端在线。"
    else:
        # desktop_online=False covers missing header, unknown surface, and true
        # non-desktop clients — never accuse a device form (Web/手机) by default.
        # 通道复检 / 打开本对话 / 勿发卡冒充 / 禁臆造入口 → team_delivery_env；此处只报通道事实。
        desktop_line = (
            "客户端通道：桌面回填通道未连接——"
            "打开本机文件夹、本机文件夹绑定、区外目录授权均须官方桌面客户端且通道已连接，"
            "当前会话无法履约。"
        )
        grant_line = "区外目录：授权仅桌面端可用；当前客户端无法履行。"

    mounts = getattr(backend, "_mounts", None) or {}
    if mounts:
        parts = []
        for a, m in mounts.items():
            mode = getattr(m, "mode", None) or (
                "readonly" if getattr(m, "readonly", True) else "organize"
            )
            mode_zh = (
                "只读"
                if mode == "readonly"
                else ("可读写" if mode == "attach_rw" else "整理")
            )
            parts.append(
                f"`external/{a}/`（{getattr(m, 'label', a)}，{mode_zh}）"
            )
        mounts_line = "本对话已授权区外目录：" + "；".join(parts) + "。"
    else:
        mounts_line = None

    if run_enabled is not None:
        exec_on = run_enabled
    else:
        from agentcore.tools.builtin import execution_class_enabled_for

        exec_on = execution_class_enabled_for(backend, permission_axes)
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
    mcp_cap = mcp_label if mcp_label is not None else ("已装配" if mcp_enabled else "未装配")
    # 装包与跑代码同一装配谓词（云桌 guest）；本机不吃主机 egress 门。
    pkg_on = (
        package_install_enabled if package_install_enabled is not None else exec_on
    )
    caps: list[str] = []
    caps.append(f"run={'已装配' if exec_on else '未装配'}")
    caps.append(f"package_install={'已装配' if pkg_on else '未装配'}")
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
    leftover_lines: list[str] = []
    if not exec_on:
        has_opaque_source = _opaque_source_data_present(
            backend, opaque_source_data_paths=opaque_source_data_paths
        )
        if not is_local:
            from agentcore.runtime.delegate.exec_env_remediation import (
                cloud_sandbox_failure_hint,
            )

            failure = cloud_sandbox_failure_hint()
            if failure:
                leftover_lines.append(f"执行环境：沙箱不可用（探测={failure}）。")
        if has_opaque_source:
            leftover_lines.append(_OPAQUE_SOURCE_FACT)
        leftover_lines.append(_NO_EXEC_TABLE_FACT)

    # Prefer explicit languages; else a probe cached on the backend.
    langs = exec_languages
    if langs is None:
        langs = getattr(backend, "_exec_languages", None)
    interpreters_line: str | None = None
    if exec_on and langs is not None:
        from agentcore.tools.sandbox.exec_languages import format_interpreters_line

        interpreters_line = format_interpreters_line(tuple(langs))

    # 约定文档出口：非空才写。布局 HOW → team_delivery_env。
    outlet_lines = [
        line
        for title, rel in (
            ("约定文档出口·默认落点（无专属出口的产物）：", DRAFTS_DIR),
            ("约定文档出口·调研/讨论：", RESEARCH_DIR),
            ("约定文档出口·辩论副产物：", DEBATE_DIR),
            ("约定文档出口·审查：", REVIEWS_DIR),
        )
        if (line := format_outlet_line(title, rel, outlet_inventory))
    ]
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
        egress_line,
        *outlet_lines,
        git_line,
        desk_line,
        desktop_line,
        grant_line,
        *([mounts_line] if mounts_line else []),
        capability_line,
        *([artifact_format_line] if artifact_format_line else []),
        *leftover_lines,
    ]
    if interpreters_line is not None:
        body_lines.append(interpreters_line)
    body = "\n".join(line for line in body_lines if line)
    return f"<工作区>\n{body}\n</工作区>"
