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
    FRAGMENT_CEO_VISUALIZATION,
    FRAGMENT_CITATION,
    resolve,
)
from agentcore.runtime.resolve.prompt.base import (
    _DEFAULT_SYSTEM_PROMPT,
    _RUNTIME_CONTEXT_TEMPLATE,
)
from agentcore.runtime.resolve.prompt.ceo_core import (
    _CEO_CORE_HINT,
    _attachment_material_block,
    capability_how_suffix,
)
from agentcore.runtime.resolve.prompt.citation import CHAT_CITATION_HINT
from agentcore.runtime.resolve.prompt.cold_start import (
    _FOLDER_NAV_STALE_HINT,
    _FOLDER_PROFILE_EMPTY_SOFT_HINT,
    _FOLDER_PROFILE_TOOL_HINT,
    _explore_act_block,
)
from agentcore.runtime.resolve.prompt.memory_rules import _format_rules
from agentcore.runtime.resolve.prompt.visualization import _CEO_VISUALIZATION_HINT


def assemble_system_prompt(
    *,
    rules_markdown: str | None = None,
    extra_context: str | None = None,
) -> str:
    """Build the shared system-prompt base for a conversation.

    ``rules_markdown`` is the always-on equal-authority join of user rules + AI memory
    core (Agent记忆与知识系统 · 取消权威档). When non-empty it becomes ONE ``<rules>``
    block — no user-hard / AI-soft subsections. This base prompt is shared by the CEO
    chat agent and the delegated workers (runs/executor/), so both reach every agent.

    Per-turn ``<workspace_context>`` environment facts are NOT in this base — they
    ride :data:`SectionOrder.WORKSPACE_FACTS` on both :func:`compose_ceo_chat_prompt`
    and :func:`compose_worker_base_prompt` so a location / capability restamp cannot
    sit in front of the resident core (see ``SectionOrder`` Exception 2026-08-19).

    Sections are stitched by :class:`ContextAssembler` (上下文注入统一): base →
    runtime context → memory <rules> → attachment context, joined with "\n". Empty
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

    The preamble states ONLY what the directory is and how to pull from it. Routing
    ("which scene must consult what", 交付档 / intensity / playbook / 绿场准入) belongs to
    the resident core — restating it here made the same rule land three times in one
    assembled prompt (核 + 前言 + 条目摘要). 每条纪律只留一个权威位置.
    """
    detail = "name＋一行摘要" if with_summaries else "name"
    return [
        "<按需目录>",
        f"下列按需条目（仅列{detail}、全文未常驻）可用 `consult(name)` 拉取："
        "系统能力指引、按需用户规则、记忆主题笔记、以及本回合未进工具表的低频工具。"
        "低频工具：能力行已装配仍可能未进开场表；consult 之后本回合下一模型轮即可调（不必等用户再发一条）；"
        "常驻内容已在 ``<rules>``，常驻工具已在工具表，无需查阅。"
        "何时该拉哪条，按条目自身说明判断；"
        "「必查 / 不必先查」的路由口径以常驻正文为准，本目录不另立一套：",
    ]


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
    """
    if not entries:
        return ""
    lines = _on_demand_preamble(with_summaries=with_summaries)
    if with_summaries:
        lines.extend(
            f"- {e.name}：{e.summary}" if e.summary else f"- {e.name}" for e in entries
        )
    else:
        lines.extend(f"- {e.name}" for e in entries)
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
    memory_enabled: bool = True,
    on_demand_rules: Sequence[object] = (),
) -> str:
    """Build the delegated worker's system prompt from the shared base.

    Layers the same ``<按需目录>`` (name＋摘要) the CEO sees when ``on_demand_entries``
    is non-empty, then per-turn workspace facts (same :data:`SectionOrder.WORKSPACE_FACTS`
    the CEO uses), then the attachment block last (缓存友好). Summaries are the existing
    ``description`` / skill ``summary`` strings — this does not rewrite them.
    """
    del memory_enabled  # gate is has_entries at wire time; entries already filtered
    if on_demand_entries:
        entries = on_demand_entries
    elif memory_topics or on_demand_rules:
        # Legacy bridge: convert old topic/rule lists (tests mid-migration).
        entries = [
            ConsultDirectoryEntry(
                name=getattr(t, "name", str(t)),
                summary=getattr(t, "summary", "") or "",
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
    citation + visualization + per-turn workspace facts (same
    :data:`SectionOrder.WORKSPACE_FACTS` workers use, after the core). ``on_demand_entries``
    must match the tool's merged source.
    ``ceo_offered_names`` is the OpenAI table this turn (on-demand tools omitted until
    consult); HOW manuals follow that set, not the full registry.
    """
    offered = ceo_offered_names if ceo_offered_names is not None else ceo_tool_names
    ceo_core = resolve(FRAGMENT_CEO_CORE, _CEO_CORE_HINT)
    how_suffix = capability_how_suffix(offered)
    if how_suffix:
        ceo_core = f"{ceo_core.rstrip()}\n{how_suffix}\n"
    if "update_folder_profile" in ceo_tool_names:
        ceo_core = f"{ceo_core.rstrip()}\n{_FOLDER_PROFILE_TOOL_HINT.strip()}\n"
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
            )
            for t in (*memory_topics, *on_demand_rules)
        ]
        # Test / catalog bridge: skills from registry when no merged entries passed.
        if skill_registry is not None and hasattr(skill_registry, "available"):
            for skill in skill_registry.available(ceo_tool_names):  # type: ignore[union-attr]
                entries.append(
                    ConsultDirectoryEntry(name=skill.name, summary=skill.summary)
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
            render_folder_catalog(folder_catalog),
            SectionOrder.FOLDER_CATALOG,
        )
        .add("citation", resolve(FRAGMENT_CITATION, CHAT_CITATION_HINT), SectionOrder.CITATION)
        .add(
            "ceo_visualization",
            resolve(FRAGMENT_CEO_VISUALIZATION, _CEO_VISUALIZATION_HINT),
            SectionOrder.CEO_VISUALIZATION,
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
