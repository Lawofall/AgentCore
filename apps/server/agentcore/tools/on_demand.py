"""On-demand tool roster — listed in ``<按需目录>``, omitted from the
opening OpenAI tool table until ``consult(name)`` (or a family sibling) promotes them.

Not an intent classifier: the builtin split is ``ToolRegistration.resident``
(False = defer), identical for every task. Discovered MCP tools (``mcp_*``)
join the same gate by prefix so their schemas stay off the opening table;
the catalog still lists them.
Tools stay registered (catalog / execute / skill gates / capability lines); only
``ToolRegistry.get_openai_definitions`` withholds them until offered.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from functools import lru_cache

# Consulting any member offers every assembled sibling in the same family.
_FAMILIES: tuple[frozenset[str], ...] = (
    frozenset({"md_to_docx", "md_to_pdf"}),
    frozenset({"archive_extract", "archive_create"}),
    frozenset({"create_folder", "delete_folder"}),
    frozenset({"file_move", "file_copy", "file_batch"}),
    frozenset({"board_ops", "board_read"}),
)

_FAMILY_LABELS: dict[frozenset[str], str] = {
    frozenset({"md_to_docx", "md_to_pdf"}): "导出 Word/PDF",
    frozenset({"archive_extract", "archive_create"}): "压缩包",
    frozenset({"create_folder", "delete_folder"}): "文件夹增删",
    frozenset({"file_move", "file_copy", "file_batch"}): "搬移/批量",
    frozenset({"board_ops", "board_read"}): "白板",
}

_CONSULT_TOOL_NAMES = frozenset(
    {"consult", "consult_memory", "consult_skill", "consult_rule"}
)


@lru_cache(maxsize=1)
def _declared_on_demand() -> tuple[frozenset[str], dict[str, str], dict[str, str]]:
    """Builtin names / summaries / faces where ``resident`` is False."""
    from agentcore.tools.registration import (
        declared_tool_name,
        declared_tool_schema,
        declared_tools,
        tool_registration,
    )

    names: set[str] = set()
    summaries: dict[str, str] = {}
    faces: dict[str, str] = {}
    for cls in declared_tools():
        reg = tool_registration(cls)
        if reg.resident:
            continue
        name = declared_tool_name(cls)
        names.add(name)
        summaries[name] = reg.catalog_summary
        faces[name] = declared_tool_schema(cls).face.value
    return frozenset(names), summaries, faces


def on_demand_builtin_names() -> frozenset[str]:
    return _declared_on_demand()[0]


def on_demand_face(name: str) -> str:
    """``ToolFace`` value for a builtin on-demand name; empty if unknown / MCP."""
    return _declared_on_demand()[2].get(name, "")


def is_mcp_tool_name(name: str) -> bool:
    """FC names minted by ``sanitize_mcp_tool_name`` (``mcp_{server}_{tool}``)."""
    return name.startswith("mcp_")


def is_on_demand_tool(name: str) -> bool:
    return name in on_demand_builtin_names() or is_mcp_tool_name(name)


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


def family_catalog_meta(name: str) -> tuple[str, str]:
    """Catalog group key + label for builtin families. Empty when the tool is solo."""
    for family in _FAMILIES:
        if name in family:
            return "+".join(sorted(family)), _FAMILY_LABELS.get(family, "")
    return "", ""


def resolve_on_demand_name(registry: object | None, name: str) -> str | None:
    """Map a catalog / consult name onto a registered on-demand tool.

    Accepts an exact tool name, or an MCP Server id / display name (consult the
    Server → offer the whole assembled family).
    """
    key = (name or "").strip()
    if not key or registry is None:
        return None
    get = getattr(registry, "get_optional", None)
    names = getattr(registry, "names", None)
    if get is not None and callable(get) and get(key) is not None:
        return key
    if not callable(get) or names is None:
        return None
    needle = key.lower()
    for candidate in names:
        if not is_mcp_tool_name(candidate):
            continue
        tool = get(candidate)
        if tool is None:
            continue
        sid = str(getattr(tool, "mcp_server_id", "") or "").strip().lower()
        sname = str(getattr(tool, "mcp_server_name", "") or "").strip().lower()
        if needle in (sid, sname):
            return candidate
    return None


def offer_tools_from_window(registry: object, messages: Sequence[object]) -> int:
    """Re-offer on-demand tools already used or consulted in this conversation window.

    Same-turn resume and the next user message both rebuild the deferred set;
    without this, a tool already enabled in the bubble is missing from the table.
    Returns how many ``offer`` calls changed the deferred set.
    """
    offer = getattr(registry, "offer", None)
    if not callable(offer):
        return 0
    recalled: list[str] = []
    seen: set[str] = set()
    for message in messages or ():
        for fname, arguments in _tool_calls_from_message(message):
            raw = fname.strip()
            if raw in _CONSULT_TOOL_NAMES:
                raw = _consult_name_arg(arguments)
            if not raw or raw in seen:
                continue
            seen.add(raw)
            recalled.append(raw)
    changed = 0
    for name in recalled:
        target = resolve_on_demand_name(registry, name)
        if target and offer(target):
            changed += 1
    return changed


def _tool_calls_from_message(message: object) -> list[tuple[str, str]]:
    if message is None:
        return []
    role = getattr(message, "role", None)
    if role is None and isinstance(message, dict):
        role = message.get("role")
        tcs = message.get("tool_calls") or []
    else:
        if role != "assistant":
            return []
        tcs = getattr(message, "tool_calls", None) or []
    if role != "assistant":
        return []
    out: list[tuple[str, str]] = []
    for tc in tcs:
        fn = getattr(tc, "function", None)
        if fn is not None:
            out.append(
                (str(getattr(fn, "name", "") or ""), str(getattr(fn, "arguments", "") or ""))
            )
            continue
        if isinstance(tc, dict):
            nested = tc.get("function")
            if isinstance(nested, dict):
                out.append(
                    (str(nested.get("name") or ""), str(nested.get("arguments") or ""))
                )
            else:
                out.append((str(tc.get("name") or ""), str(tc.get("arguments") or "")))
    return out


def _consult_name_arg(arguments: str) -> str:
    try:
        data = json.loads(arguments or "")
    except (json.JSONDecodeError, TypeError, ValueError):
        return ""
    if isinstance(data, dict):
        return str(data.get("name") or "").strip()
    return ""


def on_demand_summary(name: str, *, description: str = "") -> str:
    """One-line catalog text. Builtins use registration; MCP uses live schema."""
    static = _declared_on_demand()[1].get(name)
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
    """Enable-ack + one HOW owner. Full JSON stays on the next FC table.

    CEO + host/terminal/browser/grant: consult HOW only (no schema reprint).
    No HOW: enable-ack only — do not paste the schema description a second time.
    """
    del description
    siblings = [n for n in enabled if n != name]
    lines = [
        (
            f"已启用工具 `{name}`。本回合下一模型轮工具表将含完整参数，"
            "可直接调用（不必等用户再发一条）。"
        ),
    ]
    if siblings:
        lines.append("同族已一并启用：" + "、".join(siblings) + "。")
    how = _ceo_how_for(name) if audience == "ceo" else ""
    if how:
        lines.append("")
        lines.append(how)
    return "\n".join(lines)


def _ceo_how_for(name: str) -> str:
    """CEO routing manuals: unique owner is this consult body, not the frozen core."""
    from agentcore.runtime.resolve.prompt.ceo_core import capability_how_suffix

    return capability_how_suffix({name})


def __getattr__(name: str) -> object:
    """Derived roster aliases for tests (``ON_DEMAND_TOOL_NAMES`` / summaries)."""
    if name == "ON_DEMAND_TOOL_NAMES":
        return on_demand_builtin_names()
    if name == "ON_DEMAND_SUMMARIES":
        return dict(_declared_on_demand()[1])
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
