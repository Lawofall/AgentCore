"""On-demand tool roster — listed in ``<按需目录>``, omitted from the
opening OpenAI tool table until ``consult(name)`` (or a family sibling) promotes them.

Not an intent classifier: the builtin split is a fixed name set, identical for
every task. Discovered MCP tools (``mcp_*``) join the same gate by prefix so
their schemas stay off the opening table; the catalog still lists them.
Tools stay registered (catalog / execute / skill gates / capability lines); only
``ToolRegistry.get_openai_definitions`` withholds them until offered.
"""

from __future__ import annotations

from collections.abc import Sequence

# ---------------------------------------------------------------------------
# Roster (single source for builtins). Adding a name here is what moves a
# builtin off the always-offered table. Keep the tool class, schema, and
# execute path intact. Dynamic MCP names are not listed here — ``is_on_demand_tool``
# recognizes the ``mcp_`` prefix produced by ``sanitize_mcp_tool_name``.
#
# Defer = optional capability face (consult first is an extra round, not a
# missing channel). Do NOT defer a mode primitive the runtime already
# always-grants when assembled: 便签墙三件套 (collab batch), escalate/handoff
# (already resident). If registered ⇔ the mode is on, opening offer must include it.
# ---------------------------------------------------------------------------

ON_DEMAND_TOOL_NAMES: frozenset[str] = frozenset(
    {
        # Host face — single ``host`` (action policy table).
        "host",
        # Browser face — single ``browser`` (action policy table).
        "browser",
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
        # Cross-conversation logs (product-always-on; still on-demand when wired).
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
    "host": "本机 Host（status/os_log/shell；面板/音频/服务/装包仅队员）",
    "browser": "右坞浏览器（navigate/click/type/scroll/snapshot/console；screenshot 仅队员）",
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
    frozenset({"search_conversations", "read_conversation"}),
    frozenset({"md_to_docx", "md_to_pdf"}),
    frozenset({"create_folder", "delete_folder"}),
)


def is_mcp_tool_name(name: str) -> bool:
    """FC names minted by ``sanitize_mcp_tool_name`` (``mcp_{server}_{tool}``)."""
    return name.startswith("mcp_")


def is_on_demand_tool(name: str) -> bool:
    return name in ON_DEMAND_TOOL_NAMES or is_mcp_tool_name(name)


def family_of(name: str, *, registry: object | None = None) -> frozenset[str]:
    """Name plus any family siblings (always includes ``name`` itself).

    Builtin families are the static table. MCP tools share a family per
    assembled Server (``McpDynamicTool.mcp_server_id``); without a registry
    the dynamic siblings are unknown, so the name stands alone.
    """
    for family in _FAMILIES:
        if name in family:
            return family
    if registry is not None and is_mcp_tool_name(name):
        get = getattr(registry, "get_optional", None)
        names = getattr(registry, "names", None)
        if callable(get) and names is not None:
            tool = get(name)
            server_id = getattr(tool, "mcp_server_id", None) if tool is not None else None
            if server_id:
                siblings = [
                    n
                    for n in names
                    if is_mcp_tool_name(n)
                    and getattr(get(n), "mcp_server_id", None) == server_id
                ]
                if siblings:
                    return frozenset(siblings)
    return frozenset({name})


def on_demand_summary(name: str, *, description: str = "") -> str:
    """One-line catalog text. Builtins use the static table; MCP uses live schema."""
    static = ON_DEMAND_SUMMARIES.get(name)
    if static:
        return static
    if is_mcp_tool_name(name):
        desc = " ".join((description or "").split())
        return desc or name
    return name


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
        (
            f"已启用工具 `{name}`。本回合下一模型轮工具表将含完整参数，"
            "可直接调用（不必等用户再发一条）。"
        ),
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
    if name == "host":
        return _HOST_HOW.strip()
    if name == "browser":
        return _BROWSER_HOW.strip()
    if name == "external_mount_readonly":
        return _EXTERNAL_GRANT_HOW.strip()
    return ""
