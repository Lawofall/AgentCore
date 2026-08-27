"""ContextAssembler — the single seam that stitches system-prompt fragments.

上下文注入统一 Step 1（装配主干）+ Step 2（常驻源插件化）. Before Step 1 the system prompt
was built by ad-hoc string concatenation scattered across ``runtime.resolve.prompt``. Step 1
collected those fragments into one assembler; Step 2 made each fragment a uniform
:class:`PromptContributor` plugin carrying its own ``order`` (and a reserved ``budget``),
so:

- every injected fragment has a stable ``key`` (debuggable / addressable),
- ordering is DECLARATIVE (each contributor's :class:`SectionOrder`), not implicit in the
  ``.add()`` call sequence — the render order is the same no matter what sequence a site
  contributes in, and
- contributor ``budget`` is unused metadata (carried, not enforced); the assembler
  never trims. Write-side always-on quota lives in ``memory/always_quota.py``.

Behavior-preserving today: with the ``SectionOrder`` values the call sites pass, the
sorted render reproduces the prior inline order exactly, joined with ``"\\n"`` —
byte-identical to the old assembly. Keeping that foundation prefix stable across turns is a
**cost optimization** for providers that discount exact-prefix cache hits (e.g. DeepSeek) —
not an irrevocable product invariant. Discipline lives here (:class:`SectionOrder`): new
sections append at the volatile tail or insert at a stable key between existing slots;
never reorder existing keys (exception: ``WORKSPACE_FACTS`` 250→750, 2026-08-19 —
see :class:`SectionOrder`). ``observe`` emits ``assembly_hash`` so drift is searchable
without enforcing a hard PrefixStabilityPolicy — and, since the discount is a claim about
BILLING rather than about the assembled text, ``track_sections`` feeds
``observability.prefix_cache`` so ``cost.prefix_cache`` can check that claim against what the
provider actually charged (审计议题 D4: 先补可观测, 不动装配).
"""

from __future__ import annotations

import hashlib

from agentcore.core.logging import get_logger
from agentcore.observability.prefix_cache import digest_text, record_prompt_sections
from agentcore.runtime.context.contributor import PromptContributor

logger = get_logger(__name__)


def assembly_hash(text: str) -> str:
    """SHA-256 hex of an assembled prompt (utf-8) — observe / test helper, not a gate."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class ContextAssembler:
    """Collects :class:`PromptContributor` plugins and renders the system prompt.

    Usage mirrors the concatenation it replaces, now with an explicit order::

        text = (
            ContextAssembler()
            .add("base", base_prompt, SectionOrder.BASE)
            .add("memory_rules", rules_or_none, SectionOrder.MEMORY)  # None / "" skipped
            .render()
        )

    ``add`` / ``contribute`` return ``self`` for fluent chaining and **skip falsy text**
    (``None`` or ``""``), exactly reproducing the prior ``if part: parts.append(part)``
    guards so optional sections (memory, attachments, an empty skill directory) drop out
    without leaving a blank line. ``render`` sorts the kept contributors by ``order``
    (stable — ties keep contribution order) and joins them with ``"\\n"``.
    """

    def __init__(self) -> None:
        self._contributors: list[PromptContributor] = []

    def contribute(self, contributor: PromptContributor) -> ContextAssembler:
        """Add a plugin's contribution; no-op when its ``text`` is falsy (None / "")."""
        if contributor.text:
            self._contributors.append(contributor)
        return self

    def add(
        self, key: str, text: str | None, order: int, *, budget: int | None = None
    ) -> ContextAssembler:
        """Ergonomic sugar: build a :class:`PromptContributor` and contribute it.

        ``text`` is typed ``str | None`` so a site can pass an optional section straight
        through — a falsy one is skipped (no blank line), same guard as ``contribute``.
        """
        if text:
            self._contributors.append(
                PromptContributor(key=key, text=text, order=order, budget=budget)
            )
        return self

    def contributors(self) -> list[PromptContributor]:
        """The kept contributors in RENDER order (sorted; for debugging / budgeting)."""
        return sorted(self._contributors, key=lambda c: c.order)

    def track_sections(self, *, scope: str) -> ContextAssembler:
        """Register this layer's section fingerprints with the prefix-cache probe (no log).

        审计议题 D4. Nested layers (shared base → CEO chat → per-turn tail) each hand their
        render up as ONE section of the layer above, so without this the outer ``observe``
        can only say "the whole CEO prompt changed". Registering every layer lets
        ``observability.prefix_cache`` splice them back into leaves and name the section that
        actually broke the byte prefix (文件索引 / 项目清单 / 变尾段). Silent by design —
        ``observe`` already owns the one log line per assembly.

        Zero behavior change, same contract as ``observe``: reads the kept contributors,
        returns ``self``.
        """
        kept = sorted(self._contributors, key=lambda c: c.order)
        record_prompt_sections(scope=scope, sections=[(c.key, c.text) for c in kept])
        return self

    def observe(self, *, scope: str, soft_cap: int | None = None) -> ContextAssembler:
        """Log assembled size, per-section chars, and ``assembly_hash`` — observe-only
        (COST-004 / M6).

        零行为副作用: 只埋点不改装配 (返回 ``self`` 供链式调用)。``cost.prompt_assembled`` 给出
        每段 chars 明细 (归因哪段膨胀) + 总 chars + 是否越软闸 + 装配产物 ``assembly_hash``
        (同输入同 hash；段序/正文变则 hash 变)——为「开发期无真实数据」攒据, 并让前缀漂移可检索,
        待数据出再据此开「仅裁易变尾 (order≥800)」软闸 (项目审计-成本性能专项 §九)。trace /
        conversation 上下文由 contextvars 自动并入每行日志, 故此处无需显式传。``soft_cap`` 为
        None ⇒ 不判越限。

        ``section_digests`` (D4) 让「哪段变了」离线可算: 比对相邻两回合同 scope 的日志行即可,
        无需进程内状态 (在线归因见 ``track_sections``)。
        """
        kept = sorted(self._contributors, key=lambda c: c.order)
        sections = {c.key: len(c.text) for c in kept}
        total = sum(sections.values())
        rendered = "\n".join(c.text for c in kept)
        logger.info(
            "cost.prompt_assembled",
            scope=scope,
            total_chars=total,
            sections=sections,
            section_digests={c.key: digest_text(c.text) for c in kept},
            assembly_hash=assembly_hash(rendered),
            over_soft_cap=soft_cap is not None and soft_cap > 0 and total > soft_cap,
            soft_cap=soft_cap,
        )
        return self.track_sections(scope=scope)

    def render(self) -> str:
        """Sort kept contributors by ``order`` (stable) and join with ``"\\n"``."""
        return "\n".join(c.text for c in sorted(self._contributors, key=lambda c: c.order))
