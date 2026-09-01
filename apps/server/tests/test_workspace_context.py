"""Tests for ``<工作区>`` environment-facts injection.

去重定案（一条纪律只留一个权威位置）：本块**只陈述本回合选动作要用的短事实**——
位置、身份、根、能力格、出站网络、通道、git、工作台、非空挂载、非空约定文档出口、
产物格式。空状态不写。能力格写不出的真事实（执行环境探测 / 无执行表格与源数据）
另起一行；禁止按能力复写成「装包事实 / 执行事实 / Host / MCP / 浏览器事实」散文。
「该怎么做 / 禁止什么 / 怎么装上」的 HOW：
按需面（host / run / browser / 区外授权）归 consult（``capability_how_suffix``；
run HOW → ``consult(run)``）；本机进桌 / 通道复检 / 打开本对话 / 授权姿势 / 空桌 /
Office / 路径 / 约定文档布局 HOW 归 ``team_delivery_env``；产物出口 UI 归 ``product_help``；
跨文件夹百科归 ``team_cross_folder``；出卡 HOW 归 ``asking_the_user``；git 无仓政策归 git 工具描述；其余归共享基座。
因此这里的用例成对写：事实留在 ``out``，HOW 断言指向 consult / skill / 基座/核。
"""

from pathlib import Path

from agentcore.runtime.context.workspace_context import (
    build_workspace_context,
    desktop_client_can_bind,
    resolve_channel_profile,
)
from agentcore.runtime.resolve.prompt import (
    _ATTACHMENT_MATERIAL_HINT,
    _CEO_CORE_HINT,
    _DEFAULT_SYSTEM_PROMPT,
    assemble_system_prompt,
    capability_how_suffix,
    compose_ceo_chat_prompt,
    compose_worker_base_prompt,
)
from agentcore.runtime.skills import (
    _TEAM_CROSS_FOLDER,
    _TEAM_DELIVERY_ENV,
    build_system_skill_registry,
)
from agentcore.tools.builtin import build_ceo_tool_registry


def _assert_no_capability_restatements(ctx: str) -> None:
    for prefix in (
        "装包事实：",
        "执行事实：",
        "本机 Host 事实：",
        "本机 MCP 事实：",
        "浏览器事实：",
    ):
        assert prefix not in ctx, prefix


def _desk_how() -> str:
    skill = build_system_skill_registry().get("team_delivery_env")
    assert skill is not None
    return skill.body


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


def test_web_and_missing_header_ceo_registry_omits_host():
    """Acceptance: web / missing header → no ``host`` on CEO registry (web-safe)."""
    web_names = {s.name for s in build_ceo_tool_registry(desktop_online=False).list_all()}
    assert "host" not in web_names

    # Profile wiring: web / None → desktop_online False → same roster.
    assert resolve_channel_profile("web").desktop_online is False
    assert resolve_channel_profile(None).desktop_online is False
    missing = {
        s.name
        for s in build_ceo_tool_registry(
            desktop_online=resolve_channel_profile(None).desktop_online
        ).list_all()
    }
    assert "host" not in missing

    desktop_names = {
        s.name
        for s in build_ceo_tool_registry(
            desktop_online=resolve_channel_profile("desktop").desktop_online
        ).list_all()
    }
    assert "host" in desktop_names


def test_birth_desk_facts_include_folder_id_without_tool_how():
    out = build_workspace_context(
        _FakeBackend("server", root_label="白板"),
        desktop_online=True,
        run_enabled=False,
        desk_folder_id="fid-board",
        desk_folder_label="白板",
        desk_is_birth=True,
    )
    assert "本会话出生桌=`白板`" in out
    assert "folder_id=`fid-board`" in out
    assert "file_list" not in out
    assert "list_folders" not in out
    assert "resolve_folder" not in out
    assert "target_folder_id" not in out

    worker = build_workspace_context(
        _FakeBackend("server", root_label="图标"),
        desktop_online=True,
        run_enabled=False,
        desk_folder_id="fid-icon",
        desk_folder_label="设计/图标",
        desk_is_birth=False,
    )
    assert "默认工作区=`设计/图标`" in worker
    assert "folder_id=`fid-icon`" in worker
    assert "本会话出生桌=" not in worker


def test_cloud_scratch_facts():
    out = build_workspace_context(
        _FakeBackend("server"),
        desktop_online=True,
        run_enabled=False,
    )
    assert out.startswith("<工作区>")
    assert "执行位置：云端沙箱" in out
    assert "云端草稿/临时文件空间" in out
    assert "本文件夹根即工作区根" in out
    assert "工程壳" not in out
    assert "非本机目录" in out
    assert "不是用户本机已打开的仓库" not in out
    assert "空树" not in out
    assert "本机空工程" not in out
    assert "触达不了用户的电脑" not in out
    assert "host=已装配" in out
    # Host 面细则归 consult(host)；事实层不列短命令百科。
    assert "短命令" not in out
    assert "音响/系统信息" not in out
    # 本机进桌 / 本机传统 HOW 在 team_delivery_env，事实层不写意图分流
    assert "bind_local_folder" not in out
    assert "open_local_project" not in out
    assert "register_local_project" not in out
    mid = _desk_how()
    assert "bind_local_folder" in mid
    assert "open_local_project" in mid
    assert "register_local_project" in mid
    assert "导入到云" in mid
    assert "从 Git 克隆" in mid
    assert "连接 Git" not in mid
    assert "合法非默认" in mid or "非默认" in mid
    assert "本机传统" in mid
    assert "改导" not in out  # skill/context 不得写 Ask 改导导入
    # 跨文件夹：事实层只留「默认坐哪张桌」；整条 HOW 归 team_cross_folder（核不常驻钩）
    assert "出生桌" in out
    assert "跨文件夹指挥" not in out
    assert "target_folder_id" not in out
    assert "list_folders" not in out and "resolve_folder" not in out
    assert "file_list" not in out
    assert "可能降级" not in out
    hint = _CEO_CORE_HINT
    assert "【跨文件夹】" not in hint
    assert "team_cross_folder" not in hint
    assert "list_folder_dir" not in hint
    assert "禁猜最近" not in hint
    delivery = _TEAM_DELIVERY_ENV
    assert "【空桌落盘】" not in hint
    assert "工程壳" in delivery
    assert "同名" in delivery and ("顶层" in delivery or "再套" in delivery)
    assert "要不要再套一层" not in delivery
    cross = _TEAM_CROSS_FOLDER
    assert "list_folders" in cross and "resolve_folder" in cross
    assert "按路径" in cross and "rel_path" in cross
    assert "自动建云文件夹" in cross
    assert "禁猜最近" in cross
    assert "scratch" in cross
    assert "协作图不改" in cross or "并行支线" in cross
    assert "先建后派" in cross
    assert "拒后禁塌缩" in cross
    assert "list_folder_dir" in cross and "read_folder_file" in cross
    assert "认桌" in cross or "抽样" in cross
    assert "云端读不到本地" in cross
    assert "file_list" in cross
    assert "开发双仓" in cross
    # 区外授权：事实层只报可授权；工具名与姿势归 consult / team_delivery_env
    assert "external_mount_readonly" not in out
    assert "grant_organize_folder" not in out
    assert "grant_attach_folder" not in out
    assert "与工作区绑定正交" in out
    assert "先写工作区" not in out
    assert "file_copy" not in out
    assert "区外目录授权需先处在本地工作区" not in out
    assert "云端无法直接授权本机区外路径" not in out
    assert "选择器兜底" not in out
    assert "口头同意闭环" not in out
    assert "失败分型" not in out
    # 口头同意 / 只读≠整理 / 授权后先写工作区：归 consult 手册；事实块自己不抄。
    granted = capability_how_suffix({"external_mount_readonly"})
    assert granted.count("口头同意") == 1
    assert "只读已挂" in granted
    assert "grant_readonly_folder" not in granted
    assert "well_known" in granted
    assert "先写工作区" in granted and "file_copy" in granted
    assert "well_known" not in out
    assert "在哪工作" not in out
    assert "仅新建会话" not in out
    assert "在哪工作" in mid
    assert "仅新建会话" in mid
    assert "grant_organize_folder" in mid
    assert "与工作区绑定正交" in out
    assert "勿引导用户去设置改模式" not in out
    assert "勿引导用户去设置改模式" in mid
    # 定案 A：优化项目 ≠ 默认催开项目；附件收窄范围时先干活（后半句场面门，不进事实层）。
    assert "≠默认开文件夹卡" not in out
    assert "≠默认开文件夹卡" in mid
    assert "开工前置" not in out
    gated = _ATTACHMENT_MATERIAL_HINT
    assert "【本轮材料收窄】" in gated
    assert "开工前置" in mid
    assert "【本轮材料收窄】" not in hint
    assert "不可改绑" not in out
    assert "严禁引导" not in out
    assert "本机草稿" not in out
    assert "勿推销本机草稿" in mid
    assert "本会话发绑定卡" not in out  # 旧口径：已改为意图分流
    assert "run=未装配" in out
    assert "package_install=未装配" in out
    assert "browser=未装配" in out
    assert "local_open=未装配" in out
    _assert_no_capability_restatements(out)
    assert "导入到云" not in out
    assert "连接 Git" not in out
    assert "不会让沙箱" not in out
    assert "装配启用" not in out
    assert "host=已装配" in out
    # 产物出口 UI / 完整预览：唯一所有者 product_help，事实层不抄。
    assert "产物出口" not in out
    assert "完整预览" not in out
    assert "右坞" not in out
    # 「禁给本机路径 / 禁说双击打开」是收口 HOW，归 product_help
    assert "双击打开" not in out
    help_map = build_system_skill_registry().get("product_help").body
    assert "双击打开" in help_map
    assert "禁止给本机磁盘路径" in help_map
    assert "产物出口" in help_map
    assert "【交付指引】" not in hint
    assert "浏览器宿主：" not in out
    assert "browser=未装配" in out
    assert "host=已装配" in out
    assert "host(action=status/os_log/shell)" not in out
    assert "install_package" not in out
    assert "host_info" not in out
    assert "host_ping" not in out
    assert "host_package_install" not in out
    # 三分日志在 consult(host)，事实层不再逐 host 分支复述
    assert "三分日志" not in out
    assert "【三分日志】" not in hint
    host_how = capability_how_suffix({"host"})
    assert host_how.count("三分日志") == 1
    assert "host(action=os_log)" in host_how
    assert "Get-WinEvent" in host_how
    assert "host_os_log_summary" not in hint
    # 案 20260803-image-gen-byok-egress-boundary A：云沙箱无任意 HTTPS 出口事实行
    assert "出站网络" in out
    assert "包装源" in out or "allowlist" in out
    assert "无任意 HTTPS" in out
    assert "无原生生图工具" in out
    # 出图对照事实行；Key 不落明文归共享基座
    assert "代调" not in out
    assert "API Key" not in out and "明文" not in out
    assert "出站网络" in hint
    # 旧「云端临时空间」短标签已换成诚实草稿口径
    assert "工作区身份：云端临时空间" not in out
    # 约定文档布局 HOW 在 team_delivery_env；空抽屉不写进事实层
    assert "约定文档出口" not in out
    assert "约定文档边界" not in out
    assert "讨论/调研/审查类交付写此树" not in out
    assert "用户工程源码仍写业务路径" not in out
    assert "用户工程源码仍写业务路径" in _TEAM_DELIVERY_ENV
    assert "约定文档出口" in _TEAM_DELIVERY_ENV
    # 「报路径须完整前缀、禁缩短成裸 reviews/」是 HOW，编排 skill【产物路径】持有
    assert "完整前缀" not in out
    assert "**完整**路径" in _TEAM_DELIVERY_ENV
    assert "裸 `reviews/…`" in _TEAM_DELIVERY_ENV
    # FakeBackend has no root → probe unknown；建仓 / 无仓政策在 git schema，不进事实行。
    assert "init_baseline" not in out
    assert "不挡派工" not in out
    assert "no_repo" not in out
    assert "通常无 Git" not in out
    from agentcore.tools.builtin.git_ops.tool import GitTool

    assert "no_repo" in GitTool().schema.description
    assert "init_baseline" in GitTool().schema.description


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
    assert "工作区身份：云端文件夹" in out
    assert "本文件夹根即工作区根" in out
    assert "非本机目录" in out
    assert "本文件夹尚无用户文件" not in out
    assert "云端草稿/临时文件空间" not in out
    assert "本会话云端草稿尚无文件" not in out
    assert "工程壳" not in out
    hint = _CEO_CORE_HINT
    delivery = _TEAM_DELIVERY_ENV
    assert "【空桌落盘】" not in hint
    assert "工程壳" in delivery


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
    assert "云端草稿/临时文件空间" in out
    assert "工作区身份：云端文件夹" not in out
    assert "本文件夹根即工作区根" in out


def test_cloud_host_off_capability():
    from agentcore.core.types import HostAxis

    out = build_workspace_context(
        _FakeBackend("server"),
        desktop_online=True,
        run_enabled=False,
        host_axis=HostAxis.OFF,
    )
    assert "host=未装配" in out
    assert "桌面端在线" in out
    assert "桌面回填通道未连接" not in out
    _assert_no_capability_restatements(out)


def test_cloud_web_has_no_in_app_preview_essay():
    """Web / 非桌面：产物出口 HOW 在 product_help，事实层不写完整预览说明书。"""
    out = build_workspace_context(
        _FakeBackend("server"),
        desktop_online=False,
        run_enabled=False,
    )
    assert "产物出口" not in out
    assert "完整预览" not in out
    help_map = build_system_skill_registry().get("product_help").body
    assert "完整预览" in help_map
    assert "文件面板下载" in help_map or "下载" in help_map


def test_no_desktop_host_unassembled():
    out = build_workspace_context(
        _FakeBackend("server"),
        desktop_online=False,
        run_enabled=False,
    )
    assert "host=未装配" in out
    assert "桌面回填通道未连接" in out
    _assert_no_capability_restatements(out)


def test_local_remote_channel_facts():
    out = build_workspace_context(
        _FakeBackend("local", root_label="MyProject", channel=object()),
        desktop_online=True,
        run_enabled=True,
        browser_enabled=False,
    )
    assert "执行位置：用户本机（经桌面通道遥控）" in out
    assert "本地目录（根标签 `MyProject`）" in out
    assert "本文件夹根即工作区根" in out
    assert "工程壳" not in out
    assert "run=已装配" in out
    assert "package_install=已装配" in out
    assert "browser=未装配" in out
    assert "local_open=已装配" in out
    assert "产物出口" not in out
    # 本机传统工程：通道在线是事实；跑当前 / 勿再弹 open 归 team_delivery_env
    assert "客户端通道：桌面端在线" in out
    assert "open_local_project" not in out
    assert "跑**当前**" not in out
    mid = _desk_how()
    assert "open_local_project" in mid
    assert "当前" in mid and "跑" in mid
    # 桌面分流可教三件套，但不得写成 action= 履约广告句
    assert "action=bind_local_folder" not in out


def test_browser_capability_override():
    out = build_workspace_context(
        _FakeBackend("server"),
        desktop_online=True,
        run_enabled=True,
        browser_enabled=True,
    )
    assert "browser=已装配" in out
    assert "local_open=未装配" in out
    # 事实层：装没装配。相对路径 / 完整预览 HOW 在 browser url description。
    assert "CEO 可直持" not in out
    assert "仅 worker" not in out
    assert "浏览器宿主：" not in out
    assert "完整预览" not in out
    from agentcore.tools.builtin.browser import BrowserTool

    url_desc = BrowserTool().schema.parameters["properties"]["url"]["description"]
    assert "相对" in url_desc or "site/index.html" in url_desc
    assert "完整预览" in url_desc or "http(s)" in url_desc
    # HOW 归 consult（登录接管 / 禁编造工具名 / 禁 read_url 冒充 / 意图梯度），事实层不复述
    assert "ask_user(browser_login=true)" not in out
    assert "browser_open" not in out
    hint = _CEO_CORE_HINT
    assert "consult(browser)" not in hint
    assert "验收" not in hint
    how = capability_how_suffix({"browser"})
    assert "ask_user(browser_login=true)" in how
    assert "escalate(browser_login=true)" not in how
    assert "自己" in how
    assert "read_url" in how and "已开页" in how
    assert "跑起来" in how


def test_local_browser_guide_mentions_workspace_relative_path():
    """甲：本机 + browser 已装配 → 指引相对路径与完整预览同源。"""
    out = build_workspace_context(
        _FakeBackend("local"),
        desktop_online=True,
        run_enabled=True,
        browser_enabled=True,
    )
    assert "browser=已装配" in out
    assert "浏览器宿主：" not in out
    from agentcore.tools.builtin.browser import BrowserTool

    url_desc = BrowserTool().schema.parameters["properties"]["url"]["description"]
    assert "site/index.html" in url_desc or "相对" in url_desc
    assert "完整预览" in url_desc
    assert "file://" in url_desc
    _assert_no_capability_restatements(out)
    # 「页异常先 console」已出 consult 场面剧本；事实层不列 action
    assert "console" not in out
    assert "ask_user(browser_login=true)" in capability_how_suffix({"browser"})


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
    assert "browser=已装配" in out
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
    assert "browser=未装配" in out
    _assert_no_capability_restatements(out)
    assert "浏览器宿主：" not in out
    # 本机传统可教非默认；云协作仍推荐——HOW 在 team_delivery_env，事实层不写装配步骤
    assert "本机传统" not in out
    assert "装配启用" not in out
    assert "bind_local_folder" not in out
    assert "open_local_project" not in out
    assert "open/bind" not in out
    mid = _desk_how()
    assert "bind_local_folder" in mid or "open_local_project" in mid
    # 禁误导：未装配时勿暗示「本机未装就可随手启用云端沙箱」旧句
    assert "或启用云端沙箱浏览器" not in out
    # 缺能力怎么办只写一遍：共享基座 <诚实> 管所有能力，事实层不逐条复述
    assert "同轮可开工" not in out
    hint = _CEO_CORE_HINT
    base = _DEFAULT_SYSTEM_PROMPT
    assert base.count("<诚实>") == 1
    assert "【能力未装配·统一姿势】" not in hint
    assert "假开页" not in hint
    assert "consult(browser)" not in hint
    assert "ask_user(browser_login=true)" not in hint  # 登录接管随 browser 装配注入
    how = capability_how_suffix({"browser"})
    assert "ask_user(browser_login=true)" in how
    assert "escalate(browser_login=true)" not in how
    assert "永不代填密码" in how
    from agentcore.tools.builtin.browser import BrowserTool

    assert "非右坞" in BrowserTool().schema.description
    assert "非右坞" not in base
    assert "同轮可开工" not in base
    assert "手脑" not in base
    assert "多轮复读" not in base
    assert "补救，但不是" not in hint
    assert "不是接管流程" not in hint


def test_local_browser_unassembled_guide_splits_reason_no_sandbox_teaser():
    """真·本地未装配：拆因；禁「或启用云端沙箱浏览器」误导。"""
    out = build_workspace_context(
        _FakeBackend("local"),
        desktop_online=True,
        run_enabled=True,
        browser_enabled=False,
    )
    assert "browser=未装配" in out
    _assert_no_capability_restatements(out)
    assert "浏览器宿主：" not in out
    assert "或启用云端沙箱浏览器" not in out
    assert "装配启用" not in out
    # 原因留在事实行；「怎么装上」不在这里
    assert "Local Chromium Bridge 健康" not in out
    assert "同轮可开工" not in out


def test_host_mcp_unassembled_states_facts_and_defers_posture_to_core():
    """host/mcp 未装配：事实层只写装没装配与为什么；同轮可开工姿势归共享基座。"""
    out = build_workspace_context(
        _FakeBackend("server"),
        desktop_online=False,
        run_enabled=False,
        browser_enabled=False,
    )
    assert "host=未装配" in out
    assert "mcp=未装配" in out
    assert "桌面回填通道未连接" in out
    _assert_no_capability_restatements(out)
    assert "同轮可开工" not in out
    hint = _CEO_CORE_HINT
    base = _DEFAULT_SYSTEM_PROMPT
    assert "<诚实>" in base
    assert "不得声称" in base
    assert "已装配" in base and "通道在" in base
    assert "未装配能力" not in hint


def test_mcp_assembled_states_channel_not_who_holds():
    """mcp 已装配：只报通道事实与工具名形；谁可持不在事实层。"""
    out = build_workspace_context(
        _FakeBackend("server"),
        desktop_online=True,
        run_enabled=False,
        mcp_enabled=True,
    )
    assert "mcp=已装配" in out
    _assert_no_capability_restatements(out)
    assert "mcp_<server>_<tool>" not in out
    assert "CEO 不直持" not in out
    assert "仅 worker 持 MCP" not in out


def test_sidecar_local_without_channel():
    out = build_workspace_context(
        _FakeBackend("local"),
        desktop_online=True,
        run_enabled=True,
    )
    assert "本机引擎 / sidecar" in out
    assert "当前目录已可写" in out
    assert "grant_attach_folder" not in out
    mid = _desk_how()
    assert "grant_attach_folder" in mid


def test_mobile_session_omits_bind_nudge():
    out = build_workspace_context(
        _FakeBackend("server"),
        desktop_online=False,
        run_enabled=False,
    )
    assert "桌面回填通道未连接" in out
    # Must not accuse a device form when the channel is merely offline / fail-closed.
    assert "Web / 移动端" not in out
    assert "Web / 手机" not in out
    assert "当前为 Web" not in out
    assert "授权仅桌面端可用" in out
    assert "官方桌面客户端" in out
    assert "https://fashitianxia.xyz/download" not in out
    assert "勿发 grant_* / bind_local_folder / open_local_project" not in out
    # 禁止语里可点名 action；不得写成可履约的 action= 分流广告
    assert "action=bind_local_folder" not in out
    assert "action=grant_readonly_folder" not in out
    assert "action=open_local_project" not in out
    assert "立即发卡" not in out
    assert "与工作区绑定正交" not in out
    assert "本对话尚无会话级区外目录授权" not in out
    assert "本对话已授权区外目录：" not in out  # 无挂载不得声称已授权状态行
    # 案 20260803-cloud-local-root-auth-where A：自称桌面须复检通道；禁「就好办了」/臆造路径
    # ——HOW 在 team_delivery_env，事实层只报通道未接
    assert "通道复检铁律" not in out
    assert "口述不得覆盖" not in out
    assert "就好办了" not in out
    mid = _desk_how()
    assert "通道复检" in mid
    assert "口述不得覆盖" in mid
    assert "就好办了" in mid
    assert "打开【本对话】" not in out and "打开本对话" not in out
    assert "打开【本对话】" in mid
    assert "装配启用" not in out
    assert "状态栏" not in out
    assert "Folders" not in out
    assert "臆造" not in out
    assert "Folders" in mid
    assert "臆造" in mid


def test_channel_offline_self_claim_desktop_recheck_honesty():
    """案 A：通道未接时 workspace_context 只报通道事实；复检 / 禁臆造入口在 team_delivery_env。"""
    out = build_workspace_context(
        _FakeBackend("server"),
        desktop_online=False,
        run_enabled=False,
    )
    assert "host=未装配" in out
    assert "local_open=未装配" in out
    assert "通道复检铁律" not in out
    assert "正在用客户端" not in out
    assert "口述不得覆盖" not in out
    assert "就好办了" not in out
    assert "桌面就好办" not in out
    assert "①" not in out
    assert "https://fashitianxia.xyz/download" not in out
    assert "Folders" not in out
    assert "设置→Folders" not in out
    assert "复述固定步骤" not in out
    assert "只指真源入口名" not in out
    mid = _desk_how()
    assert "通道复检" in mid
    assert "正在用客户端" in mid or "已装桌面" in mid
    assert "就好办了" in mid
    assert "Folders" in mid
    # 不得在离线分支广告可履约发卡
    assert "立即发卡" not in out
    assert "action=open_local_project" not in out


def test_no_mounts_forbids_claiming_grant_confirmed():
    """未见 external 挂载行时，事实层不写空状态；「禁止声称已确认」在 team_delivery_env。"""
    out = build_workspace_context(
        _FakeBackend("server"),
        desktop_online=False,
        run_enabled=False,
    )
    assert "本对话尚无会话级区外目录授权" not in out
    assert "本对话已授权区外目录：" not in out
    assert "禁止声称授权已确认" not in out
    mid = _desk_how()
    assert "禁止" in mid and "授权已确认" in mid
    assert out.count("授权已确认") == 0


def test_cloud_desktop_online_allows_external_grant_without_bind():
    """W3 正交：云端 scratch + 桌面在线 → 可直接只读静默挂载，勿要求先 bind。"""
    out = build_workspace_context(
        _FakeBackend("server", root_label="conv:x"),
        desktop_online=True,
        run_enabled=False,
    )
    assert "执行位置：云端沙箱" in out
    assert "external_mount_readonly" not in out
    assert "与工作区绑定正交" in out
    assert "本机某目录" not in out
    assert "区外目录授权需先处在本地工作区" not in out
    assert "选择器兜底" not in out
    # 怎么定位目录（禁手填绝对路径 / 禁探家目录 / 只读禁再发卡）归 consult——桌面在线这一回合
    # `external_mount_readonly` 已装配，HOW 在 consult 正文；事实块自己不抄。
    granted = capability_how_suffix({"external_mount_readonly"})
    assert "探家目录" in granted
    assert "host(action=shell)" in granted
    assert "grant_readonly_folder" not in granted
    assert "grant_readonly_folder" not in out


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
        workspace_context=facts,
        workspace_file_index="文件：空",
    )
    worker = compose_worker_base_prompt(base, workspace_context=facts)
    assert "<工作区>\n" in ceo
    assert "<工作区>\n" in worker
    assert "云端沙箱" in ceo and "云端沙箱" in worker
    # Actual XML block (newline after the tag), not the core/base tag mention.
    assert ceo.index("<身份>") < ceo.index("<工作区>\n")
    assert worker.index("</运行时>") < worker.index("<工作区>\n")
    assert "文件：空" in ceo
    assert ceo.index("文件：空") < ceo.index("</工作区>")
    # Closing tag is unique to the XML block (base ``<诚实>`` may mention the name).
    assert facts.count("</工作区>") == 1
    assert ceo.count("</工作区>") == 1
    assert worker.count("</工作区>") == 1
    assert ceo.count("<工作区>\n") == 1
    assert "<工作区文件>" not in ceo
    assert "文件：空" not in worker


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
    assert "版本控制：Git" in out
    assert "分支 `main`" in out
    assert "init_baseline" not in out
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
    assert "工作区根无 Git" in out
    assert "init_baseline" not in out
    assert "no_repo" not in out
    assert "不挡派工" not in out
    from agentcore.tools.builtin.git_ops.tool import GitTool

    assert "init_baseline" in GitTool().schema.description
    assert "no_repo" in GitTool().schema.description


def test_git_unassembled_states_channel_without_enable_steps():
    from agentcore.runtime.context.workspace_context import WorkspaceGitFact

    out = build_workspace_context(
        _FakeBackend("local", channel=object()),
        desktop_online=False,
        run_enabled=False,
        git_tool_enabled=False,
        git_fact=WorkspaceGitFact(present=True, branch="main"),
    )
    assert "未装配 git" in out
    assert "装配启用" not in out
    assert "打开本对话" not in out
    assert "init_baseline" not in out
    assert "no_repo" not in out
    assert "分支 `main`" not in out


def test_cloud_package_install_tracks_code_execute():
    """云端：package_install 与 run 同一谓词；override 仅测试探针。"""
    out_off = build_workspace_context(
        _FakeBackend("server"),
        desktop_online=True,
        run_enabled=True,
        package_install_enabled=False,
    )
    assert "run=已装配" in out_off
    assert "package_install=未装配" in out_off
    _assert_no_capability_restatements(out_off)

    out_on = build_workspace_context(
        _FakeBackend("server"),
        desktop_online=True,
        run_enabled=True,
        package_install_enabled=True,
    )
    assert "run=已装配" in out_on
    assert "package_install=已装配" in out_on
    assert "allowlist" in out_on or "chokepoint" in out_on or "云桌" in out_on

    out_same = build_workspace_context(
        _FakeBackend("server"),
        desktop_online=True,
        run_enabled=True,
    )
    assert "package_install=已装配" in out_same

    out_both_off = build_workspace_context(
        _FakeBackend("server"),
        desktop_online=True,
        run_enabled=False,
    )
    assert "package_install=未装配" in out_both_off


def test_local_package_install_follows_execution_class():
    """本机：装依赖跟执行类，不吃主机 registry_egress。"""
    out = build_workspace_context(
        _FakeBackend("local", root_label="MyProject", channel=object()),
        desktop_online=True,
        run_enabled=True,
        browser_enabled=False,
    )
    assert "run=已装配" in out
    assert "package_install=已装配" in out
    _assert_no_capability_restatements(out)
    assert "registry_egress" not in out

    out_off = build_workspace_context(
        _FakeBackend("local"),
        desktop_online=True,
        run_enabled=False,
    )
    assert "package_install=未装配" in out_off


def _xlsx_clause(block: str) -> str:
    for part in block.split("产物格式：", 1)[-1].split("；"):
        if part.startswith(".xlsx="):
            return part
    raise AssertionError(f"no .xlsx clause in {block!r}")


def test_no_execution_states_table_structure_facts():
    """无 run：能力行陈述结构面已在附件块；有执行环境时不注入该句。"""
    off = build_workspace_context(
        _FakeBackend("server"),
        desktop_online=True,
        run_enabled=False,
    )
    assert "表格解析" in off
    assert "结构面" in off
    assert "列名" in off
    assert "自产表格可回读" in off
    assert "不可靠" not in off
    assert "手抄" not in off  # HOW 归 data_file_landing，事实层不写禁令

    on = build_workspace_context(
        _FakeBackend("server"),
        desktop_online=True,
        run_enabled=True,
    )
    assert "表格解析" not in on
    assert "结构面（列名" not in on


def test_artifact_formats_without_execution_mark_office_honesty():
    """无执行环境：.xlsx/.pptx 不可产；.docx/.pdf 经 md_to_* 可产。"""
    out = build_workspace_context(
        _FakeBackend("server"),
        desktop_online=True,
        run_enabled=False,
    )
    assert "产物格式：" in out
    assert ".xlsx=不可产" in out
    assert ".pptx=不可产" in out
    assert "run" in _xlsx_clause(out)
    assert ".docx=可产" in out and "md_to_docx" in out
    assert ".pdf=可产" in out and "md_to_pdf" in out
    assert "不吃沙箱" in out


def test_artifact_formats_follow_real_assembly_not_constants(monkeypatch):
    """装配态变化时表随之变化——读的是注册表+闸，不是写死的格式清单。"""
    from dataclasses import replace

    from agentcore.runtime.context.artifact_formats import build_artifact_format_line
    from agentcore.tools.builtin.md_to_docx import MdToDocxTool

    off = build_workspace_context(
        _FakeBackend("server"),
        desktop_online=True,
        run_enabled=False,
    )
    on = build_workspace_context(
        _FakeBackend("server"),
        desktop_online=True,
        run_enabled=True,
    )
    assert ".xlsx=不可产" in off
    assert ".xlsx=可产" in on
    assert "run" in _xlsx_clause(on)
    assert ".docx=可产" in on and "md_to_docx" in on

    exporters_only = build_artifact_format_line({"md_to_docx", "md_to_pdf"})
    with_exec = build_artifact_format_line({"md_to_docx", "md_to_pdf", "run"})
    assert ".xlsx=不可产" in exporters_only
    assert ".xlsx=可产" in with_exec
    assert exporters_only != with_exec

    monkeypatch.setattr(
        MdToDocxTool,
        "registration",
        replace(MdToDocxTool.registration, produces_formats=(".docx", ".odt")),
    )
    mutated = build_artifact_format_line({"md_to_docx", "md_to_pdf"})
    assert ".odt=可产" in mutated
    assert "md_to_docx" in mutated
    assert ".odt=" not in exporters_only


def test_env_examples_gvisor_timeout_does_not_clamp_outer_verify():
    """样例 GVISOR_TIMEOUT_MAX_SECONDS 勿钉 60（会夹死外环灾难顶 1200s）。"""
    from pathlib import Path

    roots = [
        Path(__file__).resolve().parents[3] / "deploy" / "config" / "production.env.example",
        Path(__file__).resolve().parents[1] / ".env.example",
    ]
    for path in roots:
        text = path.read_text(encoding="utf-8")
        assert "GVISOR_TIMEOUT_MAX_SECONDS=60" not in text
        assert "GVISOR_TIMEOUT_MAX_SECONDS=1230" in text
        assert "夹死" in text or "外环" in text


def test_no_exec_opaque_source_omits_local_run_from_exec_fact():
    """无执行 + 源数据文件：事实行只报有无法解析的源数据；下一步 HOW 在编排 skill。"""
    out = build_workspace_context(
        _FakeBackend("server"),
        desktop_online=True,
        run_enabled=False,
        opaque_source_data_paths=["attachments/synthetic_bill.csv"],
    )
    fact = out
    assert "本回合有无法可靠解析的源数据文件" in fact
    assert "表格解析" in fact
    assert "源数据文件下一步" not in fact
    assert "稍后重试" not in fact
    assert "本机跑 / 本机传统" not in fact
    assert "open/bind 合法非默认" not in fact
    assert "可选稍后重试 / export_to_local" not in fact
    assert "源数据文件下一步" in _TEAM_DELIVERY_ENV
    assert "稍后重试" in _TEAM_DELIVERY_ENV
    assert "export_to_local" in _TEAM_DELIVERY_ENV


def test_no_exec_engineering_keeps_local_remediation():
    """工程类无执行（无源数据文件）：事实行不写补救菜单；export_to_local 在核 / 编排 skill。"""
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
    assert "export_to_local" not in _CEO_CORE_HINT
    assert "export_to_local" in _TEAM_DELIVERY_ENV


def test_opaque_source_reads_backend_this_turn_materials():
    """生产路径：prepare 写入的 ai_list_materials 即 no_exec_table 同源判据。"""
    backend = _FakeBackend("server")
    backend.ai_list_materials = frozenset({"attachments/synthetic_bill.csv"})
    out = build_workspace_context(
        backend,
        desktop_online=True,
        run_enabled=False,
    )
    assert "本回合有无法可靠解析的源数据文件" in out

    md_only = _FakeBackend("server")
    md_only.ai_list_materials = frozenset({"attachments/note.md"})
    md_out = build_workspace_context(
        md_only,
        desktop_online=True,
        run_enabled=False,
    )
    assert "本回合有无法可靠解析的源数据文件" not in md_out


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
    assert "执行环境：沙箱不可用（探测=not_linux（platform=win32））。" in out
    _assert_no_capability_restatements(out)
    assert "run=未装配" in out


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
    assert "run=未装配" in out
    _assert_no_capability_restatements(out)


def test_local_exec_withheld_never_claims_sandbox_probe():
    """本机 withhold 没有云探测：不写执行环境行。"""
    out = build_workspace_context(
        _FakeBackend("local"),
        desktop_online=True,
        run_enabled=False,
    )
    assert "执行环境：" not in out
    assert "run=未装配" in out
    _assert_no_capability_restatements(out)


def test_outlet_inventory_empty_and_named_suffixes():
    from agentcore.runtime.context.outlet_inventory import OUTLET_DIRS, OutletDirListing
    from agentcore.workspace.stage_dirs import REVIEWS_DIR

    empty = {d: OutletDirListing() for d in OUTLET_DIRS}
    empty_out = build_workspace_context(
        _FakeBackend("server"),
        desktop_online=True,
        run_enabled=False,
        outlet_inventory=empty,
    )
    assert "约定文档出口" not in empty_out
    assert "当前为空" not in empty_out

    named = {
        **empty,
        REVIEWS_DIR: OutletDirListing(names=("协作图审计-架构.md", "协作图审计-渲染链路.md")),
    }
    named_out = build_workspace_context(
        _FakeBackend("server"),
        desktop_online=True,
        run_enabled=False,
        outlet_inventory=named,
    )
    assert "（现有：协作图审计-架构.md；协作图审计-渲染链路.md）" in named_out
    assert "约定文档出口·审查：" in named_out
    assert "约定文档出口·调研/讨论：" not in named_out
    assert "记忆注入审计.md" not in named_out
