"""Compose / assemble system prompts from prompt fragments."""

import re
from collections.abc import Sequence

from agentcore.config import settings
from agentcore.core.types import TOOL_FACE_LABELS, TOOL_FACE_ORDER
from agentcore.runtime.context import ContextAssembler, SectionOrder
from agentcore.runtime.context.consultable import ConsultDirectoryEntry
from agentcore.runtime.resolve.profile import (
    FRAGMENT_BASE,
    FRAGMENT_CEO_CORE,
    resolve,
)
from agentcore.runtime.resolve.prompt.base import (
    _DEFAULT_SYSTEM_PROMPT,
)
from agentcore.runtime.resolve.prompt.ceo_core import _CEO_CORE_HINT
from agentcore.runtime.resolve.prompt.memory_rules import _format_rules
from agentcore.runtime.skills.registry import SKILL_GROUP_ORDER


def assemble_system_prompt(
    *,
    rules_markdown: str | None = None,
    path_index: str | None = None,
    extra_context: str | None = None,
) -> str:
    """Build the shared system-prompt base for a conversation.

    ``rules_markdown`` is the always-on equal-authority join of user rules.
    When non-empty it becomes ONE ``<设定>``
    block. This base prompt is shared by the CEO
    chat agent and the delegated workers (runs/executor/), so both reach every agent.

    Per-turn ``<运行时>`` / ``<工作区>`` are NOT in this base. Workers and CEO
    both put them on a ``[系统提示]`` envelope (opening user), not ``role: system``.

    Sections are stitched by :class:`ContextAssembler` (上下文注入统一): base →
    memory <设定> → attachment context, joined with "\n". Empty
    optional sections (memory, attachments) are skipped. Catalog / tests that omit
    facts stay byte-identical to this render — load-bearing for DeepSeek prefix-cache
    identity of the shared prefix.

    The ``base`` fragment goes through ``resolve.profile.resolve`` (方向① 变体注入): with no
    active profile — the production state always — it returns ``_DEFAULT_SYSTEM_PROMPT``
    verbatim, so the prefix is unchanged; an eval may swap it via ``use_profile`` to A/B
    the shared base. A base override reaches both workers and the CEO (whose base_prompt
    is this function's output).
    """
    return (
        ContextAssembler()
        .add("base", resolve(FRAGMENT_BASE, _DEFAULT_SYSTEM_PROMPT), SectionOrder.BASE)
        .add(
            "memory_rules",
            _format_rules(rules_markdown),
            SectionOrder.MEMORY,
        )
        .add("path_rules", (path_index or "").strip() or None, SectionOrder.PATH_RULES)
        .add("attachment_context", extra_context, SectionOrder.ATTACHMENT)
        # D4 前缀缓存归因: 本层的段会被上层当作一整段收进去, 只有各层都登记, 击穿点才能归到叶段
        # (如 memory_rules) 而不是笼统的「CEO 提示变了」。只登记不改装配。
        .track_sections(scope="shared_base")
        .render()
    )


def _on_demand_preamble() -> list[str]:
    """Shared intro lines for ``<按需目录>``.

    The preamble states this is the on-demand catalog, how to pull full text, and
    that a catalog row is not a completed consult. Row shape is the rows. WHEN /
    deferred-tool promotion / family enable live in the consult tool description.
    Section headings render only when a section has something to contrast with.
    """
    return [
        "<按需目录>",
        "这是按需目录。用 `consult(name)` 拉全文。目录行 ≠ 已查阅。",
    ]


_SECTION_HEADINGS: tuple[tuple[str, str], ...] = (
    ("skill", "能力指引"),
    ("tool", "低频工具"),
    ("rule", "设定"),
)


def _consult_call(name: str) -> str:
    """Directory key as a consult invocation, not a bare token.

    A snake_case name at the start of a row is read as a workspace path when
    the block sits next to ``<工作区>``. The summary stays the trigger; the
    name only appears inside ``consult("…")``.
    """
    return f'consult("{name}")'


def _catalog_row(entry: ConsultDirectoryEntry, *, with_summaries: bool) -> str:
    call = _consult_call(entry.name)
    if with_summaries and entry.summary:
        return f"- {entry.summary}。{call}"
    return f"- {call}"


def _tool_section_lines(
    entries: Sequence[ConsultDirectoryEntry], *, with_summaries: bool
) -> list[str]:
    """低频工具栏：连接器单独；其余按能力面小标题。无 face 的测试行保持扁列。"""
    mcp: list[ConsultDirectoryEntry] = []
    leftover: list[ConsultDirectoryEntry] = []
    by_face: dict[str, list[ConsultDirectoryEntry]] = {}
    for entry in entries:
        if entry.name.startswith("mcp_"):
            mcp.append(entry)
        elif entry.face:
            by_face.setdefault(entry.face, []).append(entry)
        else:
            leftover.append(entry)
    lines: list[str] = []
    if leftover:
        lines.extend(_grouped_tool_rows(leftover, with_summaries=with_summaries))
    if mcp:
        lines.append("连接器：")
        lines.extend(_grouped_tool_rows(mcp, with_summaries=with_summaries))
    for face in TOOL_FACE_ORDER:
        group = by_face.get(face.value)
        if not group:
            continue
        lines.append(f"{TOOL_FACE_LABELS[face]}：")
        lines.extend(_grouped_tool_rows(group, with_summaries=with_summaries))
    return lines


def _skill_section_lines(
    entries: Sequence[ConsultDirectoryEntry], *, with_summaries: bool
) -> list[str]:
    """能力指引栏：按决策时刻排列。空组不出现。组名不印成子标题。"""
    leftover: list[ConsultDirectoryEntry] = []
    by_group: dict[str, list[ConsultDirectoryEntry]] = {}
    for entry in entries:
        if entry.group:
            by_group.setdefault(entry.group, []).append(entry)
        else:
            leftover.append(entry)
    lines: list[str] = []
    if leftover:
        lines.extend(_catalog_row(e, with_summaries=with_summaries) for e in leftover)
    for heading in SKILL_GROUP_ORDER:
        group = by_group.get(heading)
        if not group:
            continue
        lines.extend(_catalog_row(e, with_summaries=with_summaries) for e in group)
    return lines


def _grouped_tool_rows(
    entries: Sequence[ConsultDirectoryEntry], *, with_summaries: bool
) -> list[str]:
    buckets: list[list[ConsultDirectoryEntry]] = []
    index: dict[str, int] = {}
    for entry in entries:
        key = entry.family.strip() or f"#{id(entry)}:{entry.name}"
        slot = index.get(key)
        if slot is None:
            index[key] = len(buckets)
            buckets.append([entry])
        else:
            buckets[slot].append(entry)
    lines: list[str] = []
    for members in buckets:
        lead = members[0]
        if len(members) == 1 or not lead.family:
            lines.append(_catalog_row(lead, with_summaries=with_summaries))
            continue
        label = lead.family_label.strip() or lead.name
        calls = "、".join(_consult_call(m.name) for m in members)
        # 成套启用合同在 consult description。目录行写组名，成员只出现在 consult 调用里。
        lines.append(f"- {label}。{calls}")
    return lines


def render_on_demand_directory(
    entries: Sequence[ConsultDirectoryEntry],
    *,
    with_summaries: bool = True,
) -> str:
    """Render the unified ``<按需目录>`` block (name＋摘要；production always on).

    Returns "" when empty so the caller appends nothing. ``consult`` stays on the
    opening table even when this block is omitted (empty catalog is a soft miss).
    Entries must come from the same
    :class:`~agentcore.runtime.context.consult_sources.MergedConsultSource` the tool holds.
    ``with_summaries=False`` remains a test/compat switch — workers no longer use it.
    A section heading (能力指引 / 低频工具 / 设定) renders only when that section
    has a sibling section or unsectioned rows beside it. Skill decision-moment
    groups order rows and are not printed. Unsectioned lists stay a flat bullet
    list (tests / catalog bridges).
    """
    if not entries:
        return ""
    lines = _on_demand_preamble()
    if not any(e.section for e in entries):
        if with_summaries:
            lines.extend(_catalog_row(e, with_summaries=True) for e in entries)
        else:
            lines.extend(_catalog_row(e, with_summaries=False) for e in entries)
        lines.append("</按需目录>")
        return "\n".join(lines)

    by_section: dict[str, list[ConsultDirectoryEntry]] = {}
    leftover: list[ConsultDirectoryEntry] = []
    for entry in entries:
        if entry.section:
            by_section.setdefault(entry.section, []).append(entry)
        else:
            leftover.append(entry)
    if leftover:
        lines.extend(_catalog_row(e, with_summaries=with_summaries) for e in leftover)
    populated = [key for key, _heading in _SECTION_HEADINGS if by_section.get(key)]
    show_headings = len(populated) >= 2 or bool(leftover)
    for key, heading in _SECTION_HEADINGS:
        group = by_section.get(key)
        if not group:
            continue
        if show_headings:
            lines.append(f"{heading}：")
        if key == "tool":
            lines.extend(_tool_section_lines(group, with_summaries=with_summaries))
        elif key == "skill":
            lines.extend(_skill_section_lines(group, with_summaries=with_summaries))
        else:
            lines.extend(_catalog_row(e, with_summaries=with_summaries) for e in group)
    lines.append("</按需目录>")
    return "\n".join(lines)


_ON_DEMAND_BLOCK = re.compile(r"<按需目录>.*?</按需目录>", re.DOTALL)


def splice_on_demand_directory(prompt: str, block: str) -> str:
    """Replace or insert the ``<按需目录>`` block after a nested-lead consult refresh.

    Leaf/captain catalogs may diverge by one skill row; do not rebuild the whole
    worker base (workspace / attachments stay). Empty ``block`` is a no-op.
    """
    text = (block or "").strip()
    if not text:
        return prompt
    if _ON_DEMAND_BLOCK.search(prompt):
        return _ON_DEMAND_BLOCK.sub(text, prompt, count=1)
    marker = "<工作区>"
    idx = prompt.find(marker)
    if idx >= 0:
        return f"{prompt[:idx]}{text}\n\n{prompt[idx:]}"
    return f"{prompt.rstrip()}\n\n{text}\n"


def compose_worker_base_prompt(
    shared_base: str,
    *,
    on_demand_entries: Sequence[ConsultDirectoryEntry] = (),
    # Deprecated kwargs: date / workspace / attachments ride the worker envelope.
    attachment_context: str | None = None,
    workspace_context: str | None = None,
    # Deprecated: prefer ``on_demand_entries``. Tests still pass ``on_demand_rules``.
    on_demand_rules: Sequence[object] = (),
) -> str:
    """Build the delegated worker's frozen ``role: system``.

    Shared base + the same ``<按需目录>`` the CEO sees when
    ``on_demand_entries`` is non-empty. Date, workspace, and attachments ride
    :func:`~agentcore.runtime.resolve.prompt.envelope.render_worker_turn_envelope`
    on the opening user message. ``attachment_context`` / ``workspace_context``
    are ignored here so a missed call site cannot splice them back into system.
    """
    del attachment_context, workspace_context
    if on_demand_entries:
        entries = on_demand_entries
    elif on_demand_rules:
        entries = [
            ConsultDirectoryEntry(
                name=getattr(t, "name", str(t)),
                summary=getattr(t, "summary", "") or "",
                section="rule",
            )
            for t in on_demand_rules
        ]
    else:
        entries = ()
    on_demand_block = render_on_demand_directory(entries, with_summaries=True)
    return (
        ContextAssembler()
        .add("shared_base", shared_base, SectionOrder.BASE)
        .add("on_demand_directory", on_demand_block, SectionOrder.SKILL_DIRECTORY)
        .observe(scope="worker_base", soft_cap=settings.prompt_budget_char_soft_cap)
        .render()
    )


def compose_ceo_chat_prompt(
    base_prompt: str,
    *,
    ceo_tool_names: set[str],
    on_demand_entries: Sequence[ConsultDirectoryEntry] = (),
    # Deprecated: skill_registry / on_demand_rules — prefer on_demand_entries.
    skill_registry: object | None = None,
    on_demand_rules: Sequence[object] = (),
) -> str:
    """Compose the frozen CEO system prompt from the clean base.

    Layers the entry coordinator's residual identity (empty unless proven) + unified
    ``<按需目录>`` (omitted when the catalog is empty; ``consult`` stays on the
    opening table). Date, workspace (+ CEO file index), scene gates,
    attachments, and table ride the turn envelope — not this
    string. ``on_demand_entries`` must match the tool's merged source.
    Host / terminal / browser / grant HOW is consult-owned and must not
    hang on this frozen prompt.
    """
    ceo_core = resolve(FRAGMENT_CEO_CORE, _CEO_CORE_HINT)
    if on_demand_entries:
        entries = list(on_demand_entries)
    else:
        entries = [
            ConsultDirectoryEntry(
                name=getattr(t, "name", str(t)),
                summary=getattr(t, "summary", "") or "",
                section="rule",
            )
            for t in on_demand_rules
        ]
        # Test / catalog bridge: skills from registry when no merged entries passed.
        if skill_registry is not None and hasattr(skill_registry, "available"):
            for skill in skill_registry.available(ceo_tool_names, audience="ceo"):  # type: ignore[union-attr]
                entries.append(
                    ConsultDirectoryEntry(
                        name=skill.name,
                        summary=skill.summary,
                        section="skill",
                        group=getattr(skill, "group", "") or "",
                    )
                )
    on_demand_block = (
        render_on_demand_directory(entries, with_summaries=True)
        if "consult" in ceo_tool_names and entries
        else ""
    )
    return (
        ContextAssembler()
        .add("ceo_base", base_prompt, SectionOrder.BASE)
        .add("ceo_core", ceo_core, SectionOrder.CEO_CORE)
        .add("on_demand_directory", on_demand_block, SectionOrder.SKILL_DIRECTORY)
        # Frozen system: constitution + identity + catalog. Volatile facts observe
        # on ``ceo_envelope`` (render_ceo_turn_envelope). FOLDER_CATALOG slot stays
        # unused (名册改 folders).
        .track_sections(scope="ceo_chat")
        .render()
    )


def derive_ceo_addon(shared_base: str, ceo_full: str) -> str:
    """CEO-specific prompt layers only — everything after the shared base prefix.

    Used by the capability catalog to expose ``ceo_addon`` separately from
    ``shared_base``, so the 能力图鉴 can show the CEO delta without repeating the
    全员 block. Falls back to ``ceo_full`` if the prefix invariant breaks (should
    not happen in production; guarded by integration tests).
    """
    if ceo_full.startswith(shared_base):
        return ceo_full[len(shared_base) :].lstrip("\n")
    return ceo_full
