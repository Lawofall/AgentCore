"""Compose / assemble system prompts from prompt fragments."""

import time
from collections.abc import Sequence

from agentcore.config import settings
from agentcore.runtime.context import ContextAssembler, SectionOrder
from agentcore.runtime.context.consultable import ConsultDirectoryEntry
from agentcore.runtime.context.folder_catalog import (
    FolderCatalogEntry,
    render_folder_catalog,
)
from agentcore.runtime.resolve.profile import (
    FRAGMENT_BASE,
    FRAGMENT_CEO_CORE,
    resolve,
)
from agentcore.runtime.resolve.prompt.base import (
    _DEFAULT_SYSTEM_PROMPT,
    _RUNTIME_CONTEXT_TEMPLATE,
)
from agentcore.runtime.resolve.prompt.ceo_core import (
    _CEO_CORE_HINT,
    _attachment_material_block,
)
from agentcore.runtime.resolve.prompt.cold_start import (
    _FOLDER_NAV_STALE_HINT,
    _FOLDER_PROFILE_EMPTY_SOFT_HINT,
    _explore_act_block,
)
from agentcore.runtime.resolve.prompt.memory_rules import _format_rules


def assemble_system_prompt(
    *,
    rules_markdown: str | None = None,
    extra_context: str | None = None,
) -> str:
    """Build the shared system-prompt base for a conversation.

    ``rules_markdown`` is the always-on equal-authority join of user rules + AI memory
    core (Agent记忆与知识系统 · 取消权威档). When non-empty it becomes ONE ``<设定>``
    block — no user-hard / AI-soft subsections. This base prompt is shared by the CEO
    chat agent and the delegated workers (runs/executor/), so both reach every agent.

    Per-turn ``<工作区>`` environment facts are NOT in this base — they
    ride :data:`SectionOrder.WORKSPACE_FACTS` on both :func:`compose_ceo_chat_prompt`
    and :func:`compose_worker_base_prompt` so a location / capability restamp cannot
    sit in front of the resident core (see ``SectionOrder`` Exception 2026-08-19).

    Sections are stitched by :class:`ContextAssembler` (上下文注入统一): base →
    runtime context → memory <设定> → attachment context, joined with "\n". Empty
    optional sections (memory, attachments) are skipped. Catalog / tests that omit
    facts stay byte-identical to this render — load-bearing for DeepSeek prefix-cache
    identity of the shared prefix.

    The ``base`` fragment goes through ``resolve.profile.resolve`` (方向① 变体注入): with no
    active profile — the production state always — it returns ``_DEFAULT_SYSTEM_PROMPT``
    verbatim, so the prefix is unchanged; an eval may swap it via ``use_profile`` to A/B
    the shared base. A base override reaches both workers and the CEO (whose base_prompt
    is this function's output).
    """
    runtime_context = _RUNTIME_CONTEXT_TEMPLATE.format(
        date=time.strftime("%Y-%m-%d %Z", time.localtime())
    )
    return (
        ContextAssembler()
        .add("base", resolve(FRAGMENT_BASE, _DEFAULT_SYSTEM_PROMPT), SectionOrder.BASE)
        .add("runtime_context", runtime_context, SectionOrder.RUNTIME_CONTEXT)
        .add(
            "memory_rules",
            _format_rules(rules_markdown),
            SectionOrder.MEMORY,
        )
        .add("attachment_context", extra_context, SectionOrder.ATTACHMENT)
        # D4 前缀缓存归因: 本层的段会被上层当作一整段收进去, 只有各层都登记, 击穿点才能归到叶段
        # (如 memory_rules) 而不是笼统的「CEO 提示变了」。只登记不改装配。
        .track_sections(scope="shared_base")
        .render()
    )


def _on_demand_preamble(*, with_summaries: bool) -> list[str]:
    """Shared intro lines for ``<按需目录>`` (CEO and worker both get name＋摘要).

    The preamble states ONLY that this is the on-demand catalog and how to pull
    full text. WHEN / four kinds / deferred-tool promotion live in the consult
    tool description.
    """
    detail = "name＋一行摘要" if with_summaries else "name"
    return [
        "<按需目录>",
        f"这是按需目录（仅列{detail}、全文未常驻）。用 `consult(name)` 拉全文。",
    ]


_SECTION_HEADINGS: tuple[tuple[str, str], ...] = (
    ("skill", "能力指引"),
    ("tool", "低频工具"),
    ("rule", "设定"),
    ("memory", "主题"),
)


def _catalog_row(entry: ConsultDirectoryEntry, *, with_summaries: bool) -> str:
    if with_summaries and entry.summary:
        return f"- {entry.name}：{entry.summary}"
    return f"- {entry.name}"


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
        names = "、".join(m.name for m in members)
        lines.append(f"- {label}（查阅任一即整组启用）：{names}")
    return lines


def render_on_demand_directory(
    entries: Sequence[ConsultDirectoryEntry],
    *,
    with_summaries: bool = True,
) -> str:
    """Render the unified ``<按需目录>`` block (name＋摘要；production always on).

    Returns "" when empty so the caller appends nothing (directory↔tool: only when
    ``consult`` is wired this turn). Entries must come from the same
    :class:`~agentcore.runtime.context.consult_sources.MergedConsultSource` the tool holds.
    ``with_summaries=False`` remains a test/compat switch — workers no longer use it.
    Grouped headings appear only when entries carry ``section``; unsectioned
    lists stay a flat bullet list (tests / catalog bridges).
    """
    if not entries:
        return ""
    lines = _on_demand_preamble(with_summaries=with_summaries)
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
    for key, heading in _SECTION_HEADINGS:
        group = by_section.get(key)
        if not group:
            continue
        lines.append(f"{heading}：")
        if key == "tool":
            lines.extend(_grouped_tool_rows(group, with_summaries=with_summaries))
        else:
            lines.extend(_catalog_row(e, with_summaries=with_summaries) for e in group)
    lines.append("</按需目录>")
    return "\n".join(lines)


def compose_worker_base_prompt(
    shared_base: str,
    *,
    on_demand_entries: Sequence[ConsultDirectoryEntry] = (),
    attachment_context: str | None = None,
    workspace_context: str | None = None,
    # Deprecated kwargs kept so older call sites / tests fail loudly if still passed
    # with old semantics — prefer ``on_demand_entries``.
    memory_topics: Sequence[object] = (),
    on_demand_rules: Sequence[object] = (),
) -> str:
    """Build the delegated worker's system prompt from the shared base.

    Layers the same ``<按需目录>`` (name＋摘要) the CEO sees when ``on_demand_entries``
    is non-empty, then per-turn workspace facts (same :data:`SectionOrder.WORKSPACE_FACTS`
    the CEO uses), then the attachment block last (缓存友好). Summaries are the existing
    ``description`` / skill ``summary`` strings — this does not rewrite them.
    """
    if on_demand_entries:
        entries = on_demand_entries
    elif memory_topics or on_demand_rules:
        # Legacy bridge: convert old topic/rule lists (tests mid-migration).
        entries = [
            ConsultDirectoryEntry(
                name=getattr(t, "name", str(t)),
                summary=getattr(t, "summary", "") or "",
                section="memory" if t in memory_topics else "rule",
            )
            for t in (*memory_topics, *on_demand_rules)
        ]
    else:
        entries = ()
    on_demand_block = render_on_demand_directory(entries, with_summaries=True)
    return (
        ContextAssembler()
        .add("shared_base", shared_base, SectionOrder.BASE)
        .add("on_demand_directory", on_demand_block, SectionOrder.SKILL_DIRECTORY)
        .add("workspace_facts", workspace_context, SectionOrder.WORKSPACE_FACTS)
        .add("attachment_context", attachment_context, SectionOrder.ATTACHMENT)
        .observe(scope="worker_base", soft_cap=settings.prompt_budget_char_soft_cap)
        .render()
    )


def compose_ceo_chat_prompt(
    base_prompt: str,
    *,
    ceo_tool_names: set[str],
    on_demand_entries: Sequence[ConsultDirectoryEntry] = (),
    folder_catalog: Sequence[FolderCatalogEntry] = (),
    current_folder_id: str | None = None,
    workspace_context: str | None = None,
    cold_start_explore: bool | str | None = False,
    folder_nav_stale: bool = False,
    folder_profile_empty_soft: bool = False,
    attachment_material: bool = False,
    ceo_offered_names: set[str] | None = None,
    # Deprecated: skill_registry / memory_topics / on_demand_rules — prefer on_demand_entries.
    skill_registry: object | None = None,
    memory_topics: Sequence[object] = (),
    on_demand_rules: Sequence[object] = (),
) -> str:
    """Compose the CEO chat agent's system prompt from the clean base.

    Layers the entry coordinator's hint stack onto the shared base: the SLIM CEO core
    + unified ``<按需目录>`` (only when ``consult`` is wired) + derived ``<文件夹清单>`` +
    per-turn workspace facts (same
    :data:`SectionOrder.WORKSPACE_FACTS` workers use, after the core). ``on_demand_entries``
    must match the tool's merged source.
    ``ceo_offered_names`` is the OpenAI table this turn (on-demand tools omitted until
    consult). Host / terminal / browser / grant HOW is consult-owned and must not
    hang on this frozen prompt — even when catalog/eval omit ``offered`` or a
    later round already has the tool on the table.
    """
    del ceo_offered_names
    ceo_core = resolve(FRAGMENT_CEO_CORE, _CEO_CORE_HINT)
    reason: str | None
    if cold_start_explore is True:
        reason = "empty"
    elif cold_start_explore in ("empty", "rebind", "refresh"):
        reason = str(cold_start_explore)
    else:
        reason = None
    explore_block = _explore_act_block(reason)
    material_block = _attachment_material_block(attachment_material)
    empty_soft_block = (
        _FOLDER_PROFILE_EMPTY_SOFT_HINT.strip()
        if folder_profile_empty_soft and not explore_block
        else ""
    )
    stale_block = (
        _FOLDER_NAV_STALE_HINT.strip()
        if folder_nav_stale and not explore_block
        else ""
    )
    if on_demand_entries:
        entries = list(on_demand_entries)
    else:
        entries = [
            ConsultDirectoryEntry(
                name=getattr(t, "name", str(t)),
                summary=getattr(t, "summary", "") or "",
                section="memory" if t in memory_topics else "rule",
            )
            for t in (*memory_topics, *on_demand_rules)
        ]
        # Test / catalog bridge: skills from registry when no merged entries passed.
        if skill_registry is not None and hasattr(skill_registry, "available"):
            for skill in skill_registry.available(ceo_tool_names):  # type: ignore[union-attr]
                entries.append(
                    ConsultDirectoryEntry(
                        name=skill.name, summary=skill.summary, section="skill"
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
        .add("cold_start_explore", explore_block, SectionOrder.CEO_CORE)
        .add("attachment_material", material_block, SectionOrder.CEO_CORE)
        .add("folder_profile_empty_soft", empty_soft_block, SectionOrder.CEO_CORE)
        .add("folder_nav_stale", stale_block, SectionOrder.CEO_CORE)
        .add("on_demand_directory", on_demand_block, SectionOrder.SKILL_DIRECTORY)
        .add(
            "folder_catalog",
            render_folder_catalog(
                folder_catalog, current_folder_id=current_folder_id
            ),
            SectionOrder.FOLDER_CATALOG,
        )
        .add("workspace_facts", workspace_context, SectionOrder.WORKSPACE_FACTS)
        # D4: 见 assemble_system_prompt —— 本层带来 folder_catalog（项目清单，按最近活跃排序、
        # 却落在稳定前缀中段），正是要能被单独指认的击穿嫌疑段。workspace_facts 也在本层，
        # 但在核之后、紧邻易变尾（见 SectionOrder Exception 2026-08-19）。
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
