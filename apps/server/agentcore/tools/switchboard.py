"""Account and conversation tool switches.

A switch turns off one whole, user-facing tool. Off means the name is absent
from the opening table and from execution. The set is read when a turn entry
builds the registry; registration skips those names. A loop that is already
streaming keeps the table it started with.

Coordination controls (``replan`` / ``cancel_worker``) are not switches. They
leave the table together with ``delegate``.
"""

from __future__ import annotations

from collections.abc import Iterable
from contextvars import ContextVar, Token
from dataclasses import dataclass

from agentcore.tools.registry import ToolRegistry

_DISABLED: ContextVar[frozenset[str]] = ContextVar(
    "disabled_tool_groups",
    default=frozenset(),
)


@dataclass(frozen=True, slots=True)
class ToolSwitch:
    """One row on the assembly page."""

    id: str
    label: str
    summary: str
    doc_tool: str
    note: str | None = None


SWITCHES: tuple[ToolSwitch, ...] = (
    ToolSwitch("files", "改文件", "在这个文件夹里写、改、删文件。", "write"),
    ToolSwitch("run", "跑命令", "在这个文件夹里跑命令。", "run"),
    ToolSwitch("browser", "浏览器", "打开网页、点击、输入。", "browser"),
    ToolSwitch("web", "上网", "搜索，以及打开网页正文。", "web_search"),
    ToolSwitch(
        "ask_user",
        "问你",
        "停下来等你拍板。",
        "ask_user",
        "关掉之后不再停下来等你拍板。",
    ),
    ToolSwitch(
        "delegate",
        "派人",
        "叫人一起做。",
        "delegate",
        "关掉之后不再叫人，这场只剩主管自己干。",
    ),
    ToolSwitch("host", "本机", "操作这台电脑。", "host"),
    ToolSwitch("look", "看文件", "读这个文件夹里的文件、列出和搜索。", "read"),
    ToolSwitch("debate", "辩论", "开一场正反辩论。", "debate"),
    ToolSwitch("folders", "文件夹", "列出或解析云文件夹。", "folders"),
    ToolSwitch(
        "chats",
        "对话",
        "检索并阅读这个账号里的旧对话。",
        "search_conversations",
        "关掉之后这场不能自己去翻旧对话。",
    ),
)

_SWITCH_IDS: frozenset[str] = frozenset(switch.id for switch in SWITCHES)
_ORDER: dict[str, int] = {switch.id: index for index, switch in enumerate(SWITCHES)}

# Not their own rows. They only exist to manage people ``delegate`` called.
_DELEGATE_COMPANIONS: frozenset[str] = frozenset({"replan", "cancel_worker"})


def normalize_disabled_tools(raw: object) -> tuple[str, ...]:
    """Known switch ids, canonical order. Unknown values are dropped."""
    if isinstance(raw, str) or not isinstance(raw, Iterable):
        return ()
    found: set[str] = set()
    for item in raw:
        if isinstance(item, str) and item in _SWITCH_IDS:
            found.add(item)
    return tuple(sorted(found, key=_ORDER.__getitem__))


def group_tool_names(switch_id: str) -> frozenset[str]:
    """Model-facing tool names one switch removes. Empty for an unknown id."""
    if switch_id == "files":
        from agentcore.tools.builtin import file_mutation_tool_names

        return file_mutation_tool_names()
    if switch_id == "run":
        return frozenset({"run"})
    if switch_id == "browser":
        return frozenset({"browser"})
    if switch_id == "web":
        return frozenset({"web_search", "web_fetch"})
    if switch_id == "ask_user":
        return frozenset({"ask_user"})
    if switch_id == "delegate":
        return frozenset({"delegate"})
    if switch_id == "host":
        from agentcore.tools.registration import host_class_tool_names

        return host_class_tool_names()
    if switch_id == "look":
        return frozenset({"read", "file_list", "glob", "grep"})
    if switch_id == "debate":
        return frozenset({"debate"})
    if switch_id == "folders":
        return frozenset({"folders"})
    if switch_id == "chats":
        return frozenset({"search_conversations", "read_conversation"})
    return frozenset()


def switch_member_names(switch_id: str) -> tuple[str, ...]:
    """Catalog names this switch covers, sorted.

    Delegate companions leave the table with that switch. The assembly page
    does not list them as their own cards.
    """
    names = set(group_tool_names(switch_id))
    if switch_id == "delegate":
        names |= _DELEGATE_COMPANIONS
    return tuple(sorted(names))


def all_switch_ids() -> tuple[str, ...]:
    """Every assembly-page switch, in display order."""
    return tuple(switch.id for switch in SWITCHES)


def withheld_names(disabled: object) -> frozenset[str]:
    """Names that must not be on the table for this deny list."""
    ids = normalize_disabled_tools(disabled)
    names: set[str] = set()
    for switch_id in ids:
        names |= group_tool_names(switch_id)
    if "delegate" in ids:
        names |= _DELEGATE_COMPANIONS
    return frozenset(names)


def bind_disabled_tool_groups(raw: object) -> Token[frozenset[str]]:
    """Publish this turn entry's deny list. Reset with :func:`reset_disabled_tool_groups`."""
    return _DISABLED.set(frozenset(normalize_disabled_tools(raw)))


def reset_disabled_tool_groups(token: Token[frozenset[str]]) -> None:
    _DISABLED.reset(token)


def current_disabled_tools() -> frozenset[str]:
    return _DISABLED.get()


def switch_blocks(name: str) -> bool:
    """True when this turn's deny list says the name must not be registered."""
    return name in withheld_names(_DISABLED.get())


def omit_disabled_tools(registry: ToolRegistry) -> None:
    """Drop names the current turn entry turned off.

    Assembly skips these names before register. This remains for a registry
    that was built first and must be trimmed to the same list.
    """
    for name in withheld_names(_DISABLED.get()):
        registry.unregister(name)


async def disabled_tools_for_user(session: object, user_id: str) -> list[str]:
    """Deny list on the starred assembly. Missing user or star → none off."""
    from agentcore.db.models import LlmModelProfile
    from agentcore.db.repositories import UserRepository

    user = await UserRepository(session).get_by_id(user_id)  # type: ignore[arg-type]
    aid = getattr(user, "default_assembly_id", None) if user else None
    if not aid:
        return []
    row = await session.get(LlmModelProfile, aid)  # type: ignore[attr-defined]
    if row is None or row.user_id != user_id:
        return []
    from agentcore.assembly.recipes import surface_from_row

    return list(surface_from_row(row).disabled_tools)
