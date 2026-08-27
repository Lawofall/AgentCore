"""PromptContributor — the uniform "plugin" shape every always-on prompt source takes.

上下文注入统一 Step 2（常驻源插件化）. Step 1 centralized the *assembly* (ContextAssembler);
this gives every always-on source — base prompt, runtime context, memory ``<rules>``, CEO
core, skill directory, citation hint, workspace overview, per-turn attachment — ONE shape:
a named fragment + its render ``order`` + an optional ``budget``. So:

- ordering is DECLARATIVE in one place (:class:`SectionOrder`), not implicit in the
  ``.add()`` call sequence at each site, and
- ``budget`` is unused metadata on the contributor; the assembler never trims
  against it. Write-side always-on quota lives in ``memory/always_quota.py``.

Eager by design: the owner computes ``text`` (some sources are async, e.g. the workspace
overview) and hands the finished string here — this is a descriptor, not a lazy renderer.
A falsy ``text`` (None / "") means "this source contributes nothing this turn" and is
dropped, exactly as the prior ``if part: parts.append(part)`` guards did.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum


class SectionOrder(IntEnum):
    """Canonical render order of system-prompt sections, foundation → volatile tail.

    One ordering universe so every assembler renders sections in the same relative order
    regardless of the sequence that contributed them. Spaced by 100 to leave room for
    future sections to slot between without renumbering. The tail (workspace overview,
    attachment) is deliberately LAST so the foundation/hint prefix can stay byte-identical
    across turns — a **cost optimization** for exact-prefix cache billing (e.g. DeepSeek),
    not a product invariant. Discipline: new sections only append after the current tail
    or insert at a new stable key between existing slots; never reorder existing keys.

    Exception (2026-08-19): ``WORKSPACE_FACTS`` moved 250 → 750. That discipline exists
    so a later edit cannot silently reshuffle the billed prefix; this move *is* the
    prefix-cache fix, measured on production ``cost.prefix_cache`` (CEO first call of
    each turn, hit_ratio): overall 35.75%; ``workspace_facts`` changed in 45.7% of
    adjacent openings vs ``folder_catalog`` 13.2%; facts-only turns hit 6.49% vs
    catalog-only 28.69%. The gap is whether the ~19k resident core sits downstream of
    the volatile section. Facts used to ride the shared-base blob (order 250) in front
    of the core, so a location / capability restamp invalidated the whole core.
    Catalog already sits after the core (570). Moving facts after the core, adjacent
    to the volatile tail (overview = 800), makes shared-base + resident core one
    uninterrupted byte-stable prefix. CEO and workers share that base and add facts
    at this same key — do not fork a second facts section. Do not revert this to
    restore "never reorder" without a new measurement showing the gap closed.
    """

    BASE = 100
    RUNTIME_CONTEXT = 200
    MEMORY = 300
    CEO_CORE = 400
    SKILL_DIRECTORY = 500
    # The 记忆主题目录 (consult's catalog) sits beside the skill directory:
    # both are "here is a catalog, pull the full text by name" blocks (记忆文件夹化 §六).
    # CEO and worker both get name+summary (记忆系统 · 读侧无差别).
    MEMORY_TOPICS = 550
    # On-demand user rules (consult) — constraint appendices, NOT memory topics.
    # Same live-tool gate: render only when ``consult`` is wired this turn.
    RULE_DIRECTORY = 560
    # Derived cross-folder roster (Folder path + 画像.md first line). CEO-only;
    # rendered outside ``<rules>`` so it stays separate from the always-on entry block.
    FOLDER_CATALOG = 570
    CITATION = 600
    CEO_VISUALIZATION = 700
    # Per-turn environment facts (location / desktop / capabilities). Volatile with
    # binding changes. Was 250 (in front of the ~19k CEO core); moved 2026-08-19 —
    # see class docstring Exception. Both CEO and worker composers add this key;
    # it is not baked into ``assemble_system_prompt``.
    WORKSPACE_FACTS = 750
    WORKSPACE_OVERVIEW = 800
    # CEO-only conversation state: the newest appendable team graph (跨回合同图追加's
    # cross-turn id echo — history replays no tool I/O, so it rides the volatile tail).
    RECENT_TEAM_GRAPH = 850
    # Cross-turn soft ledger when the prior turn journal has partial/blocked delivery
    # with blocking gaps (one-shot; mutual-excl. with PRIOR_DELEGATE_RETRY — gaps win).
    PRIOR_DELIVERY_GAPS = 855
    # Cross-turn soft nudge when the prior turn journal fingerprints empty-delegate /
    # unproductive (history drops tool I/O — same volatile-tail reason as recent graph).
    PRIOR_DELEGATE_RETRY = 860
    # Cross-turn one-shot when the prior other turn has tool_call.cross_turn_retry=futile
    # (history drops tool I/O; prompt information only — not a gate).
    PRIOR_FUTILE_RETRIES = 862
    ATTACHMENT = 900
    # 已登记来源台账 (#rN): hydrated from the whole conversation's assistant rows, so it
    # grows monotonically with the chat — the most volatile section there is, and the one
    # a future budget lever would trim first. Last on the CEO turn assembler so the
    # sections above keep their bytes.
    REGISTERED_SOURCES = 950
    # Worker opening tail (executor ``worker_turn`` observe). Not used on the CEO
    # turn assembler — numeric values sit after the CEO tail so a mistaken add
    # cannot reorder CEO sections.
    WORKER_IDENTITY = 960
    WORKER_ROLE = 970
    WORKER_SUPPLEMENT = 980


@dataclass(frozen=True)
class PromptContributor:
    """One always-on source's contribution to the system prompt.

    ``key`` is a stable identifier (debuggable / addressable; not rendered). ``text`` is
    the verbatim fragment — the owner keeps owning exact wording + whitespace. ``order``
    places it (see :class:`SectionOrder`). ``budget`` is unused metadata
    (``None`` = unbounded); the assembler never trims against it. Write-side
    always-on quota lives in ``memory/always_quota.py``.
    """

    key: str
    text: str
    order: int
    budget: int | None = None
