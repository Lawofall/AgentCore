"""Static on-demand tool roster — listed in ``<按需目录>``, omitted from the
opening OpenAI tool table until ``consult(name)`` (or a family sibling) promotes them.

Not an intent classifier: the split is a fixed name set, identical for every task.
Tools stay registered (catalog / execute / skill gates / capability lines); only
``ToolRegistry.get_openai_definitions`` withholds them until offered.
"""

from __future__ import annotations

from collections.abc import Sequence

# ---------------------------------------------------------------------------
# Roster (single source). Adding a name here is what moves a tool off the
# always-offered table. Keep the tool class, schema, and execute path intact.
#
# Defer = optional capability face (consult first is an extra round, not a
# missing channel). Do NOT defer a mode primitive the runtime already
# always-grants when assembled: 便签墙三件套 (collab batch), escalate/handoff
# (already resident). If registered ⇔ the mode is on, opening offer must include it.
# ---------------------------------------------------------------------------

ON_DEMAND_TOOL_NAMES: frozenset[str] = frozenset(
    {
        # Host face (L1–L3) — OS inspection / panels / controlled actions.
        "host_ping",
        "host_info",
        "host_audio_devices",
        "host_storage",
        "host_power",
        "host_network_summary",
        "host_apps",
        "host_os_log_summary",
        "host_shell",
        "host_open_settings",
        "host_audio_set_default",
        "host_service_restart",
        "host_package_install",
        # Browser face — page drive; screenshot stays worker-only when assembled.
        "browser_navigate",
        "browser_click",
        "browser_type",
        "browser_scroll",
        "browser_snapshot",
        "browser_console",
        "browser_screenshot",
        # Long-running local processes.
        "terminal",
        # Desktop-only silent mount.
        "external_mount_readonly",
        # Export / fetch / unpack (not the daily write loop).
        "md_to_docx",
        "md_to_pdf",
        "archive_extract",
        "download_url",
        # Site HTML SECTION inject (not the daily write loop; MD uses str_replace).
        "write_section",
        # Cross-conversation logs (privacy-gated; still on-demand when wired).
        "search_conversations",
        "read_conversation",
        # Rare CEO folder admin (list/resolve/peek stay resident).
        "create_folder",
        "delete_folder",
        # Desktop toast.
        "desktop_notify",
    }
)

ON_DEMAND_SUMMARIES: dict[str, str] = {
    "host_ping": "探测本机 Host 通道是否可达（先 consult 再调 host_*）",
    "host_info": "读取本机 OS / 架构 / 主机名摘要",
    "host_audio_devices": "列出本机音频设备",
    "host_storage": "本机磁盘用量摘要",
    "host_power": "本机电量 / 电源状态",
    "host_network_summary": "本机网络接口摘要",
    "host_apps": "本机已装应用摘要",
    "host_os_log_summary": "有界本机 OS 事件摘要（勿 host_shell 倾倒日志）",
    "host_shell": "本机短命令（CEO 可直调；长驻进程改 terminal）",
    "host_open_settings": "打开系统设置面板（worker · GRANTABLE）",
    "host_audio_set_default": "切换默认音频设备（须先观测设备）",
    "host_service_restart": "受控重启白名单系统服务",
    "host_package_install": "本机装包（winget/brew/apt · 恒确认）",
    "browser_navigate": "打开网页或工作区 HTML（短操作 CEO 自调，勿为此派工）",
    "browser_click": "点击页面元素（须先 snapshot）",
    "browser_type": "向页面输入（密码框硬拒）",
    "browser_scroll": "滚动页面",
    "browser_snapshot": "无障碍快照 + 元素 ref 表",
    "browser_console": "页内 console / 未捕获异常",
    "browser_screenshot": "截图验收（仅队员）",
    "terminal": "本机长驻进程启/停/读（dev server；禁改走 code_execute）",
    "external_mount_readonly": "静默只读挂载本机目录为 external/<别名>/",
    "md_to_docx": "工作区 .md 导出为同名 .docx",
    "md_to_pdf": "工作区 .md 导出为同名 .pdf",
    "archive_extract": "工作区 zip 解压到指定目录",
    "download_url": "HTTP(S) URL 落盘到工作区相对路径",
    "write_section": "建站 HTML 分区注入（SECTION 标记对；非 Markdown）",
    "search_conversations": "检索用户历史对话目录",
    "read_conversation": "深读一条历史对话全文",
    "create_folder": "新建云文件夹（先建后派；勿为裸聊写盘过闸）",
    "delete_folder": "按 folder_id 软删一个文件夹（恒确认）",
    "desktop_notify": "向本机桌面发一条通知",
}

# Consulting any member offers every assembled sibling in the same family.
_FAMILIES: tuple[frozenset[str], ...] = (
    frozenset(
        {
            "browser_navigate",
            "browser_click",
            "browser_type",
            "browser_scroll",
            "browser_snapshot",
            "browser_console",
            "browser_screenshot",
        }
    ),
    frozenset(
        {
            "host_ping",
            "host_info",
            "host_audio_devices",
            "host_storage",
            "host_power",
            "host_network_summary",
            "host_apps",
            "host_os_log_summary",
            "host_shell",
            "host_open_settings",
            "host_audio_set_default",
            "host_service_restart",
            "host_package_install",
        }
    ),
    frozenset({"search_conversations", "read_conversation"}),
    frozenset({"md_to_docx", "md_to_pdf"}),
    frozenset({"create_folder", "delete_folder"}),
)


def is_on_demand_tool(name: str) -> bool:
    return name in ON_DEMAND_TOOL_NAMES


def family_of(name: str) -> frozenset[str]:
    """Name plus any family siblings (always includes ``name`` itself)."""
    for family in _FAMILIES:
        if name in family:
            return family
    return frozenset({name})


def on_demand_summary(name: str) -> str:
    return ON_DEMAND_SUMMARIES.get(name) or name


def render_tool_consult_body(
    name: str,
    *,
    description: str,
    audience: str | None,
    enabled: Sequence[str],
) -> str:
    """Short enable-ack + schema trigger + (CEO) gated HOW. Full JSON stays on the next FC table."""
    siblings = [n for n in enabled if n != name]
    lines = [
        f"已启用工具 `{name}`。下一轮工具表将包含完整参数，可直接调用。",
    ]
    if siblings:
        lines.append("同族已一并启用：" + "、".join(siblings) + "。")
    desc = (description or "").strip()
    if desc:
        lines.append("")
        lines.append(desc)
    if audience == "ceo":
        how = _ceo_how_for(name)
        if how:
            lines.append("")
            lines.append(how)
    return "\n".join(lines)


def _ceo_how_for(name: str) -> str:
    """CEO routing manuals that used to ride ``capability_how_suffix`` for these tools."""
    from agentcore.runtime.resolve.prompt.ceo_core import (
        _BROWSER_HOW,
        _EXTERNAL_GRANT_HOW,
        _HOST_HOW,
        _TERMINAL_RUNTIME_HOW,
    )

    if name == "terminal":
        return _TERMINAL_RUNTIME_HOW.strip()
    if name.startswith("host_"):
        return _HOST_HOW.strip()
    if name.startswith("browser_"):
        return _BROWSER_HOW.strip()
    if name == "external_mount_readonly":
        return _EXTERNAL_GRANT_HOW.strip()
    return ""
