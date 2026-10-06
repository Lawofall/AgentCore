"""Tests for ``<工作区>`` environment-facts injection.

本文件只测事实坐标与「HOW 不进本块」：开场表看不出来的短坐标
（执行、桌、系统、Git、客户端、缺口、非空挂载），
以及工具名 / 字段名 / 围栏 / ``consult(`` 不进事实层。
不成对复述 skill / product_help / schema 教学句——那些各有测试所有者。
空状态不写。沙箱探测失败另起一行。禁止按能力复写成散文。
"""

from pathlib import Path

from agentcore.runtime.context.workspace_context import (
    build_workspace_context,
    desktop_client_can_bind,
    resolve_channel_profile,
)
from agentcore.runtime.resolve.prompt import (
    assemble_system_prompt,
    compose_ceo_chat_prompt,
    compose_worker_base_prompt,
    render_ceo_turn_envelope,
    render_worker_turn_envelope,
)
from agentcore.tools.builtin import build_ceo_tool_registry


def _gaps(ctx: str) -> set[str]:
    for line in ctx.splitlines():
        if line.startswith("缺口："):
            body = line.removeprefix("缺口：").rstrip("。")
            return {p.strip() for p in body.split("、") if p.strip()}
    return set()


def _assert_no_capability_restatements(ctx: str) -> None:
    for prefix in (
        "装包事实：",
        "执行事实：",
        "本机 Host 事实：",
        "本机 MCP 事实：",
        "浏览器事实：",
        "浏览器宿主：",
    ):
        assert prefix not in ctx, prefix


def _assert_how_identifiers_not_in_facts(ctx: str) -> None:
    for token in (
        "file_list",
        "target_folder_id",
        "file_batch",
        "bind_local_folder",
        "open_local_project",
        "register_local_project",
        "host(action=",
        "consult(",
        "永不代填密码",
        "create_folder",
        "mkdir",
    ):
        assert token not in ctx, token


class _FakeBackend:
    def __init__(self, location: str, root_label: str = "workspace", *, channel=None) -> None:
        self.location = location
        self.root_label = root_label
        if channel is not None:
            self._channel = channel


def test_desktop_client_can_bind_fail_closed():
    assert desktop_client_can_bind(None) is False
    assert desktop_client_can_bind("") is False
    assert desktop_client_can_bind("desktop") is True
    assert desktop_client_can_bind("web") is False
    assert desktop_client_can_bind("mobile") is False
    assert desktop_client_can_bind("mobile-web") is False
    assert desktop_client_can_bind("android") is False
    assert desktop_client_can_bind("admin") is False


def test_resolve_channel_profile_fail_closed_and_surfaces():
    unknown = resolve_channel_profile(None)
    assert unknown.surface == "unknown"
    assert unknown.desktop_online is False
    assert unknown.can_bind_folder is False

    blank = resolve_channel_profile("  ")
    assert blank.surface == "unknown"
    assert blank.desktop_online is False

    desktop = resolve_channel_profile("desktop")
    assert desktop.surface == "desktop"
    assert desktop.desktop_online is True
    assert desktop.can_bind_folder is True

    web = resolve_channel_profile("web")
    assert web.surface == "web"
    assert web.desktop_online is False
    assert web.can_bind_folder is False

    mobile_web = resolve_channel_profile("mobile-web")
    assert mobile_web.surface == "web"
    assert mobile_web.desktop_online is False

    mobile = resolve_channel_profile("mobile")
    assert mobile.surface == "mobile"
    assert mobile.desktop_online is False

    android = resolve_channel_profile("android")
    assert android.surface == "mobile"
    assert android.can_bind_folder is False

    # Unknown tokens fail closed (do not legacy-default to desktop).
    admin = resolve_channel_profile("admin")
    assert admin.surface == "unknown"
    assert admin.desktop_online is False


def test_channel_profile_for_turn_drops_desktop_for_members():
    desktop = resolve_channel_profile("desktop")
    owner = desktop.for_turn(member_turn=False)
    assert owner is desktop
    assert owner.desktop_online is True
    assert owner.can_bind_folder is True

    member = desktop.for_turn(member_turn=True)
    assert member.surface == "desktop"
    assert member.desktop_online is False
    assert member.can_bind_folder is False

    web = resolve_channel_profile("web").for_turn(member_turn=True)
    assert web.desktop_online is False
    assert web.can_bind_folder is False


def test_folder_boundary_omits_host_until_computer():
    """Web and desktop share the folder roster. Host appears only on 这台电脑."""
    from agentcore.core.types import WorkspaceBoundary

    web_names = {s.name for s in build_ceo_tool_registry(desktop_online=False).list_all()}
    assert "host" not in web_names
    computer = {
        s.name
        for s in build_ceo_tool_registry(
            desktop_online=False, permission_axes=WorkspaceBoundary.COMPUTER
        ).list_all()
    }
    assert "host" in computer


def test_birth_desk_facts_include_folder_id_without_tool_how():
    out = build_workspace_context(
        _FakeBackend("server", root_label="白板"),
        desktop_online=True,
        run_enabled=False,
        desk_folder_id="fid-board",
        desk_folder_label="白板",
        desk_is_birth=True,
    )
    assert "本会话出生桌=`白板`" not in out
    assert "folder_id=`fid-board`" not in out
    assert "桌：白板（文件夹）" in out
    assert "云端文件夹" not in out
    _assert_how_identifiers_not_in_facts(out)

    worker = build_workspace_context(
        _FakeBackend("server", root_label="图标"),
        desktop_online=True,
        run_enabled=False,
        desk_folder_id="fid-icon",
        desk_folder_label="设计/图标",
        desk_is_birth=False,
    )
    assert "桌：设计/图标（文件夹）" in worker
    assert "folder_id=`fid-icon`" not in worker
    assert "本会话出生桌=" not in worker
    _assert_how_identifiers_not_in_facts(worker)


def test_cloud_scratch_facts():
    out = build_workspace_context(
        _FakeBackend("server"),
        desktop_online=True,
        run_enabled=False,
    )
    assert out.startswith("<工作区>")
    assert "执行：云端" in out
    assert "执行：云端 · 出站：产品网络 · 原件：不能改" in out
    assert "执行：云端沙箱" not in out
    assert "桌：本会话草稿\n" in out
    assert "（云端）" not in out
    assert "host" not in _gaps(out)  # desktop_online
    assert "run" in _gaps(out)
    assert "browser" in _gaps(out)
    assert "local_open" not in out
    assert "host=已装配" not in out
    assert "run=未装配" not in out
    assert "folder_id=" not in out
    _assert_no_capability_restatements(out)
    _assert_how_identifiers_not_in_facts(out)


def _system_line(ctx: str) -> str:
    for line in ctx.splitlines():
        if line.startswith("系统："):
            return line
    return ""


def test_cloud_system_line_declares_guest_surface_when_run_on():
    from agentcore.tools.sandbox.guest_rootfs import CLOUD_GUEST_SURFACE

    out = build_workspace_context(
        _FakeBackend("server"),
        desktop_online=True,
        run_enabled=True,
    )
    system = _system_line(out)
    assert system.startswith("系统：")
    assert "Linux" in system
    assert "壳：bash" in system
    for name in CLOUD_GUEST_SURFACE:
        assert name in system
    assert "禁止" not in system
    assert "consult(" not in out
    _assert_how_identifiers_not_in_facts(out)


def test_cloud_system_line_omits_guest_surface_when_run_off():
    from agentcore.tools.sandbox.guest_rootfs import CLOUD_GUEST_SURFACE

    out = build_workspace_context(
        _FakeBackend("server"),
        desktop_online=True,
        run_enabled=False,
    )
    system = _system_line(out)
    assert "Linux" in system
    assert "壳：bash" in system
    for name in CLOUD_GUEST_SURFACE:
        assert name not in system


def test_local_system_line_does_not_paste_cloud_guest_surface():
    from agentcore.tools.sandbox.guest_rootfs import format_cloud_guest_surface

    out = build_workspace_context(
        _FakeBackend("local"),
        desktop_online=True,
        run_enabled=True,
    )
    system = _system_line(out)
    assert format_cloud_guest_surface() not in system
    assert "python3" not in system


def test_empty_desk_adds_operational_root_fact():
    """空桌操作事实当场进根行；满桌 / 未探测不加；事实层不写 mkdir HOW。"""
    empty = build_workspace_context(
        _FakeBackend("server"),
        desktop_online=True,
        run_enabled=False,
        desk_visibly_empty=True,
    )
    assert "顶层空" in empty
    assert "`package.json`" not in empty
    assert "mkdir" not in empty
    assert "create_folder" not in empty
    assert "禁止" not in empty
    _assert_how_identifiers_not_in_facts(empty)

    full = build_workspace_context(
        _FakeBackend("server"),
        desktop_online=True,
        run_enabled=False,
        desk_visibly_empty=False,
    )
    assert "顶层空" not in full
    assert "package.json" not in full

    unknown = build_workspace_context(
        _FakeBackend("server"),
        desktop_online=True,
        run_enabled=False,
    )
    assert "可见顶层空" not in unknown


def test_cloud_folder_desk_identity_is_not_scratch():
    """已建云桌：身份是云端文件夹，勿再写成 scratch「草稿/临时」。"""
    backend = _FakeBackend("server", root_label="我的白板")
    backend._root = Path("/data/workspaces/u/tree/我的白板")
    backend._internal_root = Path("/data/workspaces/u/internal/folder/fid")
    out = build_workspace_context(
        backend,
        desktop_online=True,
        run_enabled=False,
    )
    assert "桌：我的白板（文件夹）" in out
    assert "非本机目录" not in out
    assert "云端草稿/临时文件空间" not in out
    assert "桌：本会话草稿" not in out
    assert "create_folder" not in out
    _assert_how_identifiers_not_in_facts(out)


def test_cloud_conv_root_stays_scratch_identity():
    """盘上 conv/ 根仍是会话草稿，不因有 _root 就改口成云端文件夹。"""
    backend = _FakeBackend("server", root_label="workspace")
    backend._root = Path("/data/workspaces/u/conv/cid")
    backend._internal_root = Path("/data/workspaces/u/internal/conv/cid")
    out = build_workspace_context(
        backend,
        desktop_online=True,
        run_enabled=False,
    )
    assert "桌：本会话草稿\n" in out
    assert "桌：workspace（文件夹）" not in out


def test_folder_does_not_list_host_as_a_gap():
    from agentcore.core.types import WorkspaceBoundary

    out = build_workspace_context(
        _FakeBackend("server"),
        desktop_online=True,
        run_enabled=False,
        permission_axes=WorkspaceBoundary.FOLDER,
    )
    assert "host" not in _gaps(out)
    assert "边界：这个文件夹" in out
    assert "客户端：桌面已连接" in out
    assert "客户端：未连接" not in out
    computer = build_workspace_context(
        _FakeBackend("local"),
        desktop_online=True,
        run_enabled=True,
        permission_axes=WorkspaceBoundary.COMPUTER,
    )
    assert "边界：这台电脑" in computer
    assert "host" not in _gaps(computer)
    assert "客户端：" not in computer
    assert "桌面回填通道未连接" not in out
    _assert_no_capability_restatements(out)


def test_cloud_web_has_no_in_app_preview_essay():
    """Web / 非桌面：事实层不写完整预览说明书。"""
    out = build_workspace_context(
        _FakeBackend("server"),
        desktop_online=False,
        run_enabled=False,
    )
    assert "产物出口" not in out
    assert "完整预览" not in out
    _assert_how_identifiers_not_in_facts(out)


def test_no_desktop_host_unassembled():
    out = build_workspace_context(
        _FakeBackend("server"),
        desktop_online=False,
        run_enabled=False,
    )
    assert "host" not in _gaps(out)
    assert "客户端：未连接" in out
    assert "桌面回填通道未连接" not in out
    _assert_no_capability_restatements(out)


def test_local_remote_channel_facts():
    out = build_workspace_context(
        _FakeBackend("local", root_label="MyProject", channel=object()),
        desktop_online=True,
        run_enabled=True,
        browser_enabled=False,
    )
    assert "执行：用户本机" in out
    assert "执行：用户本机 · 出站：这台电脑 · 原件：能改" in out
    assert "同一出站" not in out
    assert "请人贴" not in out
    assert "桌：MyProject" in out
    assert "run" not in _gaps(out)
    assert "browser" in _gaps(out)
    assert "local_open" not in out
    assert "产物出口" not in out
    assert "客户端：桌面已连接" in out
    assert "open_local_project" not in out
    assert "跑**当前**" not in out
    assert "action=bind_local_folder" not in out
    _assert_how_identifiers_not_in_facts(out)


def test_local_desk_line_is_folder_name_not_os_path():
    """Sidecar Local ``backend.root`` is a disk path; 桌行 must not leak it."""
    backend = _FakeBackend("local", root_label="LegalMystery")
    backend.root = Path(r"C:\Project\LegalMystery")
    labeled = build_workspace_context(
        backend,
        desktop_online=True,
        run_enabled=True,
        desk_folder_label="法庭迷局",
    )
    assert "桌：法庭迷局" in labeled
    assert "C:" not in labeled
    assert r"\Project" not in labeled
    assert "LegalMystery" not in labeled

    fallback = build_workspace_context(
        backend,
        desktop_online=True,
        run_enabled=True,
    )
    assert "桌：LegalMystery" in fallback
    assert "C:" not in fallback
    assert r"\Project" not in fallback

    backend.root = Path("/Users/me/LegalMystery")
    posix = build_workspace_context(
        backend,
        desktop_online=True,
        run_enabled=True,
        desk_folder_label="LegalMystery",
    )
    assert "桌：LegalMystery" in posix
    assert "/Users/" not in posix


def test_browser_capability_override():
    out = build_workspace_context(
        _FakeBackend("server"),
        desktop_online=True,
        run_enabled=True,
        browser_enabled=True,
    )
    assert "browser" not in _gaps(out)
    assert "local_open" not in out
    assert "CEO 可直持" not in out
    assert "仅 worker" not in out
    assert "浏览器宿主：" not in out
    assert "完整预览" not in out
    assert "browser_open" not in out
    assert "同一出站" not in out
    assert "请人贴" not in out
    _assert_how_identifiers_not_in_facts(out)


def test_local_browser_guide_mentions_workspace_relative_path():
    """本机 + browser 已装配：事实层只报装配，不写相对路径说明书。"""
    out = build_workspace_context(
        _FakeBackend("local"),
        desktop_online=True,
        run_enabled=True,
        browser_enabled=True,
    )
    assert "browser" not in _gaps(out)
    assert "浏览器宿主：" not in out
    _assert_no_capability_restatements(out)
    assert "console" not in out
    _assert_how_identifiers_not_in_facts(out)


def test_bridge_session_sandbox_browser_guide_no_relative_html(monkeypatch):
    """过桥：local + 无 Bridge + gVisor → 已装配但沙箱指引（相对路径不可测）。"""
    from agentcore.config import settings
    from agentcore.runtime.browser.desktop_bridge import reset_desktop_bridge_health_for_tests
    from agentcore.tools.sandbox.cloud_health import set_cloud_sandbox_health_for_tests

    reset_desktop_bridge_health_for_tests()
    monkeypatch.setattr(settings, "gvisor_enabled", True)
    set_cloud_sandbox_health_for_tests(True)
    out = build_workspace_context(
        _FakeBackend("local"),
        desktop_online=True,
        run_enabled=True,
        # 不 override browser_enabled — 走真实闸与 host_kind
    )
    assert "browser" not in _gaps(out)
    assert "云端沙箱浏览器" not in out
    assert "浏览器宿主：" not in out
    assert "相对路径" not in out
    assert "Local Bridge 可打开" not in out
    assert "或启用云端沙箱浏览器" not in out
    _assert_no_capability_restatements(out)


def test_browser_unassembled_guide_mentions_bind_or_gvisor():
    out = build_workspace_context(
        _FakeBackend("server"),
        desktop_online=True,
        run_enabled=False,
        browser_enabled=False,
    )
    assert "browser" in _gaps(out)
    _assert_no_capability_restatements(out)
    assert "浏览器宿主：" not in out
    assert "本机传统" not in out
    assert "装配启用" not in out
    assert "open_local_project" not in out
    assert "open/bind" not in out
    assert "或启用云端沙箱浏览器" not in out
    assert "同轮可开工" not in out
    assert "【能力未装配·统一姿势】" not in out
    _assert_how_identifiers_not_in_facts(out)


def test_local_browser_unassembled_guide_splits_reason_no_sandbox_teaser():
    """真·本地未装配：拆因；禁「或启用云端沙箱浏览器」误导。"""
    out = build_workspace_context(
        _FakeBackend("local"),
        desktop_online=True,
        run_enabled=True,
        browser_enabled=False,
    )
    assert "browser" in _gaps(out)
    _assert_no_capability_restatements(out)
    assert "浏览器宿主：" not in out
    assert "或启用云端沙箱浏览器" not in out
    assert "装配启用" not in out
    assert "Local Chromium Bridge 健康" not in out
    assert "同轮可开工" not in out


def test_desktop_offline_folder_omits_host_gap_and_posture():
    """文件夹边界 + 桌面不在线：host 不进缺口；开工姿势不进本块。"""
    out = build_workspace_context(
        _FakeBackend("server"),
        desktop_online=False,
        run_enabled=False,
        browser_enabled=False,
    )
    assert "host" not in _gaps(out)
    assert "客户端：未连接" in out
    _assert_no_capability_restatements(out)
    assert "同轮可开工" not in out
    assert "【能力未装配·统一姿势】" not in out


def test_sidecar_local_without_channel():
    out = build_workspace_context(
        _FakeBackend("local"),
        desktop_online=True,
        run_enabled=True,
    )
    assert "执行：用户本机" in out
    assert "出站：这台电脑" in out
    assert "sidecar" not in out
    assert "当前目录已可写" not in out
    _assert_how_identifiers_not_in_facts(out)


def test_mobile_session_omits_bind_nudge():
    out = build_workspace_context(
        _FakeBackend("server"),
        desktop_online=False,
        run_enabled=False,
    )
    assert "客户端：未连接" in out
    # Must not accuse a device form when the channel is merely offline / fail-closed.
    assert "Web / 移动端" not in out
    assert "Web / 手机" not in out
    assert "当前为 Web" not in out
    assert "授权仅桌面端可用" not in out
    assert "官方桌面客户端" not in out
    assert "https://fashitianxia.xyz/download" not in out
    assert "action=bind_local_folder" not in out
    assert "action=open_local_project" not in out
    assert "立即发卡" not in out
    assert "与工作区绑定正交" not in out
    assert "本对话尚无会话级区外目录授权" not in out
    assert "本对话已授权区外目录：" not in out
    assert "通道复检铁律" not in out
    assert "口述覆盖" not in out
    assert "就好办了" not in out
    assert "打开【本对话】" not in out and "打开本对话" not in out
    assert "装配启用" not in out
    assert "状态栏" not in out
    assert "Folders" not in out
    assert "臆造" not in out
    _assert_how_identifiers_not_in_facts(out)


def test_channel_offline_self_claim_desktop_recheck_honesty():
    """案 A：通道未接时 workspace_context 只报通道事实；复检 HOW 不进本块。"""
    out = build_workspace_context(
        _FakeBackend("server"),
        desktop_online=False,
        run_enabled=False,
    )
    assert "host" not in _gaps(out)
    assert "local_open" not in out
    assert "通道复检铁律" not in out
    assert "正在用客户端" not in out
    assert "口述覆盖" not in out
    assert "就好办了" not in out
    assert "桌面就好办" not in out
    assert "①" not in out
    assert "https://fashitianxia.xyz/download" not in out
    assert "Folders" not in out
    assert "设置→Folders" not in out
    assert "复述固定步骤" not in out
    assert "只指真源入口名" not in out
    assert "立即发卡" not in out
    assert "action=open_local_project" not in out
    _assert_how_identifiers_not_in_facts(out)


def test_no_mounts_forbids_claiming_grant_confirmed():
    """未见 external 挂载行时，事实层不写空状态。"""
    out = build_workspace_context(
        _FakeBackend("server"),
        desktop_online=False,
        run_enabled=False,
    )
    assert "本对话尚无会话级区外目录授权" not in out
    assert "本对话已授权区外目录：" not in out
    assert "禁止声称授权已确认" not in out
    assert out.count("授权已确认") == 0
    assert "【对人说】" not in out
    assert "先答这句" not in out


class _FakeMount:
    def __init__(self, mode: str) -> None:
        self.mode = mode


def test_mount_mode_labels_are_capability_facts_not_how():
    """区外坐标只报授权档；对人开口 / 拷贝配方不进本块。"""
    backend = _FakeBackend("server")
    backend._mounts = {  # noqa: SLF001
        "desk": _FakeMount("readonly"),
        "org": _FakeMount("organize"),
        "home": _FakeMount("attach_rw"),
    }
    out = build_workspace_context(backend, desktop_online=True, run_enabled=False)
    assert "区外：" in out
    assert "`external/desk/`（只能看）" in out
    assert "`external/org/`（可拷入、不覆盖）" in out
    assert "`external/home/`（可改原件）" in out
    assert "（只读）" not in out
    assert "（可读写）" not in out
    assert "先写工作区" not in out
    assert "【对人说】" not in out
    _assert_how_identifiers_not_in_facts(out)


def test_cloud_desktop_online_allows_external_grant_without_bind():
    """W3 正交：云端 scratch + 桌面在线 → 可直接只读静默挂载，勿要求先 bind。"""
    out = build_workspace_context(
        _FakeBackend("server", root_label="conv:x"),
        desktop_online=True,
        run_enabled=False,
    )
    assert "执行：云端" in out
    assert "执行：云端沙箱" not in out
    assert "与工作区绑定正交" not in out
    assert "本机某目录" not in out
    assert "区外目录授权需先处在本地工作区" not in out
    assert "选择器兜底" not in out
    _assert_how_identifiers_not_in_facts(out)


def test_assemble_system_prompt_omits_workspace_facts():
    """Facts are not in the shared base — they ride the compose layer after the core."""
    bare = assemble_system_prompt()
    # Shared HOW may mention the tag name; the injected block is the closing tag.
    assert "</工作区>" not in bare
    assert "<工作区>\n" not in bare


def test_workspace_facts_follow_resident_core_for_ceo_and_worker():
    facts = build_workspace_context(
        _FakeBackend("server"),
        desktop_online=True,
        run_enabled=False,
    )
    base = assemble_system_prompt()
    ceo = compose_ceo_chat_prompt(
        base,
        ceo_tool_names={"delegate"},
    )
    env = render_ceo_turn_envelope(
        workspace_context=facts,
        workspace_file_index="文件：空",
        include_runtime=False,
    )
    worker = compose_worker_base_prompt(base, workspace_context=facts)
    worker_env = render_worker_turn_envelope(
        workspace_context=facts,
        include_runtime=False,
    )
    assert "<工作区>\n" not in ceo
    assert "<工作区>\n" in env
    assert "<工作区>\n" not in worker
    assert "<工作区>\n" in worker_env
    assert "执行：云端" in env and "执行：云端" in worker_env
    assert "出站：产品网络" in env and "出站：产品网络" in worker_env
    assert "同一出站" not in env and "同一出站" not in worker_env
    assert "执行：云端沙箱" not in env and "执行：云端沙箱" not in worker_env
    assert "<身份>" not in ceo
    assert "<运行时>" not in worker
    assert "文件：空" in env
    assert env.index("文件：空") < env.index("</工作区>")
    assert facts.count("</工作区>") == 1
    assert env.count("</工作区>") == 1
    assert worker_env.count("</工作区>") == 1
    assert env.count("<工作区>\n") == 1
    assert "<工作区文件>" not in env
    assert "文件：空" not in worker
    assert "文件：空" not in worker_env
    _assert_how_identifiers_not_in_facts(facts)


def test_git_fact_present_line_no_soft_init_tip(tmp_path):
    from agentcore.runtime.context.workspace_context import (
        detect_workspace_git_sync,
    )
    from agentcore.tools.sandbox.subprocess import SubprocessSandbox
    from agentcore.workspace.server import ServerWorkspace

    root = tmp_path / "repo"
    root.mkdir()
    (root / ".git").mkdir()
    (root / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    backend = ServerWorkspace(root=root, sandbox=SubprocessSandbox())
    fact = detect_workspace_git_sync(backend)
    assert fact.present is True
    assert fact.branch == "main"
    out = build_workspace_context(
        backend,
        desktop_online=True,
        run_enabled=False,
        git_fact=fact,
    )
    assert "Git：main" in out
    assert "不扫嵌套" not in out
    assert "不上溯" not in out


def test_git_absent_soft_tip_visible_with_explicit_fact():
    from agentcore.runtime.context.workspace_context import WorkspaceGitFact

    out = build_workspace_context(
        _FakeBackend("local"),
        desktop_online=True,
        run_enabled=False,
        git_fact=WorkspaceGitFact(present=False),
    )
    assert "Git：无" in out
    assert "不挡派工" not in out


def test_git_unassembled_states_channel_without_enable_steps():
    from agentcore.runtime.context.workspace_context import WorkspaceGitFact

    out = build_workspace_context(
        _FakeBackend("local", channel=object()),
        desktop_online=False,
        run_enabled=False,
        git_tool_enabled=False,
        git_fact=WorkspaceGitFact(present=True, branch="main"),
    )
    assert "git" not in _gaps(out)
    assert "Git：" not in out
    assert "装配启用" not in out
    assert "打开本对话" not in out
    assert "main" not in out


def test_local_run_omits_registry_egress_from_facts():
    """本机执行开着时，事实层不写包装源门禁。"""
    out = build_workspace_context(
        _FakeBackend("local", root_label="MyProject", channel=object()),
        desktop_online=True,
        run_enabled=True,
        browser_enabled=False,
    )
    assert "run" not in _gaps(out)
    _assert_no_capability_restatements(out)
    assert "registry_egress" not in out


def test_no_execution_omits_table_structure_facts():
    """无 run：表格 HOW 不进事实层；只报缺口。"""
    off = build_workspace_context(
        _FakeBackend("server"),
        desktop_online=True,
        run_enabled=False,
    )
    assert "表格解析" not in off
    assert "列名" not in off
    assert "自产表格可回读" not in off
    assert "run" in _gaps(off)
    assert "手抄" not in off

    on = build_workspace_context(
        _FakeBackend("server"),
        desktop_online=True,
        run_enabled=True,
    )
    assert "表格解析" not in on
    assert "结构面（列名" not in on


def test_workspace_omits_artifact_format_catalog():
    """产物格式不进 ``<工作区>``。"""
    out = build_workspace_context(
        _FakeBackend("server"),
        desktop_online=True,
        run_enabled=False,
    )
    assert "产物格式：" not in out
    assert ".xlsx=" not in out
    assert "md_export" not in out
    assert "run" in _gaps(out)


def test_artifact_formats_follow_real_assembly_not_constants(monkeypatch):
    """注册表辅助函数仍按闸算格式——不再注入 ``<工作区>``。"""
    from dataclasses import replace

    from agentcore.runtime.context.artifact_formats import build_artifact_format_line
    from agentcore.tools.builtin.md_export import MdExportTool

    exporters_only = build_artifact_format_line({"md_export"})
    with_exec = build_artifact_format_line({"md_export", "run"})
    assert ".xlsx=不可产" in exporters_only
    assert ".xlsx=可产" in with_exec
    assert exporters_only != with_exec

    monkeypatch.setattr(
        MdExportTool,
        "registration",
        replace(MdExportTool.registration, produces_formats=(".docx", ".odt")),
    )
    mutated = build_artifact_format_line({"md_export"})
    assert ".odt=可产" in mutated
    assert "md_export" in mutated
    assert ".odt=" not in exporters_only


def test_env_examples_gvisor_timeout_does_not_clamp_outer_verify():
    """样例 GVISOR_TIMEOUT_MAX_SECONDS 勿钉 60（会夹死外环灾难顶 1200s）。"""
    roots = [
        Path(__file__).resolve().parents[3] / "deploy" / "config" / "production.env.example",
        Path(__file__).resolve().parents[1] / ".env.example",
    ]
    for path in roots:
        text = path.read_text(encoding="utf-8")
        assert "GVISOR_TIMEOUT_MAX_SECONDS=60" not in text
        assert "GVISOR_TIMEOUT_MAX_SECONDS=1230" in text
        assert "夹死" in text or "外环" in text


def test_no_exec_opaque_source_stays_out_of_facts():
    """无执行 + 源数据：事实层不写源数据行。"""
    out = build_workspace_context(
        _FakeBackend("server"),
        desktop_online=True,
        run_enabled=False,
    )
    assert "本回合有无法可靠解析的源数据文件" not in out
    assert "表格解析" not in out
    assert "源数据文件下一步" not in out
    assert "稍后重试" not in out


def test_no_exec_engineering_keeps_local_remediation():
    """工程类无执行：事实行不写补救菜单。"""
    out = build_workspace_context(
        _FakeBackend("server"),
        desktop_online=True,
        run_enabled=False,
    )
    fact = out
    assert "源数据文件下一步" not in fact
    assert "本回合有无法可靠解析的源数据文件" not in fact
    assert "export_to_local" not in fact
    assert "本机传统" not in fact
    assert "bind_local" not in out


def test_opaque_source_does_not_read_backend_materials_into_facts():
    """附件材料不进 ``<工作区>``。"""
    backend = _FakeBackend("server")
    backend.ai_list_materials = frozenset({"attachments/synthetic_bill.csv"})
    out = build_workspace_context(
        backend,
        desktop_online=True,
        run_enabled=False,
    )
    assert "本回合有无法可靠解析的源数据文件" not in out
    assert "synthetic_bill" not in out


def test_cloud_exec_probe_failure_is_one_fact_line(monkeypatch):
    """云端 run=未装配 且探测有因：只留一行执行环境，不复写能力格。"""
    monkeypatch.setattr(
        "agentcore.runtime.delegate.exec_env_remediation.cloud_sandbox_failure_hint",
        lambda: "not_linux（platform=win32）",
    )
    out = build_workspace_context(
        _FakeBackend("server"),
        desktop_online=True,
        run_enabled=False,
    )
    assert "沙箱：不可用（not_linux（platform=win32））" in out
    _assert_no_capability_restatements(out)
    assert "run" in _gaps(out)


def test_cloud_exec_withheld_omits_env_line_without_probe(monkeypatch):
    """云端 run=未装配 但探测空：不声称沙箱不可用，能力格已够。"""
    monkeypatch.setattr(
        "agentcore.runtime.delegate.exec_env_remediation.cloud_sandbox_failure_hint",
        lambda: None,
    )
    out = build_workspace_context(
        _FakeBackend("server"),
        desktop_online=True,
        run_enabled=False,
    )
    assert "执行环境：" not in out
    assert "沙箱不可用" not in out
    assert "run" in _gaps(out)
    _assert_no_capability_restatements(out)


def test_local_exec_withheld_never_claims_sandbox_probe():
    """本机 withhold 没有云探测：不写执行环境行。"""
    out = build_workspace_context(
        _FakeBackend("local"),
        desktop_online=True,
        run_enabled=False,
    )
    assert "执行环境：" not in out
    assert "run" in _gaps(out)
    _assert_no_capability_restatements(out)


def test_workspace_facts_omit_stage_cabinets():
    out = build_workspace_context(
        _FakeBackend("server"),
        desktop_online=True,
        run_enabled=False,
    )
    assert "过程稿：" not in out
    assert "调研：" not in out
    assert "辩论：" not in out
    assert "审查：" not in out
    assert "约定文档出口" not in out

