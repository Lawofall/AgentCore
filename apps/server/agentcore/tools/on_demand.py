"""HOW roster for tools whose handbook lives in ``consult``.

``resident`` is a human catalog chip (开场即用), **not** a FC-table gate.
Factory builtins and assembled MCP actions are opening-resident; unplugged
connectors simply do not list actions. No builtin currently has a consult
handbook. Consult does not promote schemas. Vendor ``defer_loading`` (hide
definitions from the model prefix) is a different axis → ``tools.defer_loading``.
"""

from __future__ import annotations

from collections.abc import Sequence
from functools import lru_cache

# Retired model-facing names → current on-demand tool. Did-you-mean for unknown
# *calls* lives in ``registry._KNOWN_TOOL_ALIASES`` (never auto-execute). Keep
# these two maps in lockstep when retiring a name.
RETIRED_ON_DEMAND_NAMES: dict[str, str] = {
    "md_to_docx": "md_export",
    "md_to_pdf": "md_export",
}

# Consulting any member lists every assembled sibling in the same family.
_FAMILIES: tuple[frozenset[str], ...] = ()

_FAMILY_LABELS: dict[frozenset[str], str] = {}


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


@lru_cache(maxsize=1)
def _declared_tool_names() -> frozenset[str]:
    from agentcore.tools.registration import declared_tool_name, declared_tools

    return frozenset(declared_tool_name(cls) for cls in declared_tools())


def is_tool_fc_name(name: str) -> bool:
    """Declared builtin or MCP FC name — not a skill/rule consult key."""
    key = (name or "").strip()
    return bool(key) and (key in _declared_tool_names() or is_mcp_tool_name(key))


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
    Server → the whole assembled family is already on the opening table).
    """
    key = (name or "").strip()
    if not key or registry is None:
        return None
    key = RETIRED_ON_DEMAND_NAMES.get(key, key)
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


def on_demand_summary(name: str, *, description: str = "") -> str:
    """One-line catalog text. Builtins use registration; MCP uses live schema."""
    static = _declared_on_demand()[1].get(name)
    if static:
        return static
    if is_mcp_tool_name(name):
        desc = " ".join((description or "").split())
        return desc or name
    return name


def has_consult_how(name: str) -> bool:
    """True when this tool's handbook lives in ``consult`` (none today)."""
    return bool(_ceo_how_for(name))


def render_tool_consult_body(
    name: str,
    *,
    description: str,
    audience: str | None,
    enabled: Sequence[str],
) -> str:
    """HOW body for catalog tools already on the opening FC table.

    No builtin handbook remains; body is the on-table ack (no schema reprint).
    """
    del description
    siblings = [n for n in enabled if n != name]
    lines = [
        f"工具 `{name}` 已在开场表，可直接调用。",
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
