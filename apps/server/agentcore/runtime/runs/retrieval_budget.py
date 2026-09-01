"""Plan-time retrieval budget (检索与交付约束前置提案 A1).

Structured defaults on ``RunSpec.retrieval_budget`` + strip search tools when the
resolved limit is 0. Runtime counter lives on ``ToolContext.retrieval_budget``
(:class:`~agentcore.tools.protocol.RetrievalBudgetState`); enforce in
``tool_exec`` (orthogonal to LoopController.investigation_calls). Cache hits and A3
query-contract rejects do not consume budget. CEO / delegate schema 不可配置该
字段；额度只来自统一常量（辩手有约定文档窄例外由辩论内部 writer 补写）。

预算感知：花过额度的 worker 每轮由 :func:`sync_retrieval_budget_awareness` 注入一条
当前余额（含分工具已用），临界告知并进同一条——不改共享池语义，只让模型别盲搜。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from agentcore.llm.provider.protocol import LLMMessage
from agentcore.tools.protocol import RetrievalBudgetState

if TYPE_CHECKING:
    from agentcore.runtime.runs.plan import RunPlan
    from agentcore.runtime.runs.types import RunSpec
    from agentcore.tools.protocol import ToolResult

__all__ = [
    "BUDGET_EXHAUSTED_FEEDBACK",
    "DEFAULT_RETRIEVAL_BUDGET",
    "DEFAULT_RETRIEVAL_BUDGET_DEBATER_WITH_DOSSIER",
    "RETRIEVAL_BUDGET_AWARENESS_PREFIX",
    "RETRIEVAL_BUDGET_CRITICAL_REMAINING",
    "RETRIEVAL_TOOL_NAMES",
    "RetrievalBudgetAwareness",
    "RetrievalBudgetState",
    "apply_retrieval_budgets",
    "apply_retrieval_budgets_to_specs",
    "budget_exhausted_output",
    "charges_retrieval_budget",
    "default_retrieval_budget",
    "drop_retrieval_budget_awareness",
    "exclude_retrieval_tools",
    "format_retrieval_budget_awareness_prompt",
    "format_retrieval_budget_critical_prompt",
    "format_retrieval_budget_line",
    "is_retrieval_budget_critical",
    "parse_retrieval_budget",
    "rework_refill_slots",
    "sync_retrieval_budget_awareness",
]

# Tools that share one per-run retrieval budget (web_search + read_url combined).
RETRIEVAL_TOOL_NAMES: frozenset[str] = frozenset({"web_search", "read_url"})

# 全员统一默认：普通 worker → 14（含 form=prose）。开发期无真实产线数据，14 为假设
# 统一阀（原 RESEARCH 档复用；已删 prose→0 / ROOT/DOWNSTREAM / 透镜 base/gap /
# CEO 显式覆盖）。不做批级共享池 / 按 worker 数缩放——接受 N×线性税。
DEFAULT_RETRIEVAL_BUDGET = 14
# 辩手有幕1 约定文档时：约定文档已覆盖底料，只留残搜槽位补漏。原 4 → 2026-07-22 复测：
# 约定文档充分时残搜 3 次几乎全是噪声域名，正文引用几乎全来自约定文档 → 校准为 2。
# 窄硬例外（内部 writer 写入 RunSpec，非 CEO 可配置），不是结构猜档。无约定文档路径不动。
DEFAULT_RETRIEVAL_BUDGET_DEBATER_WITH_DOSSIER = 2

# 同轮超订缓解：剩余槽位 ≤ 此值时经 reflection 注入提前告知，避免当轮 fan-out 超订被挡回。
RETRIEVAL_BUDGET_CRITICAL_REMAINING = 2

# 预算感知（BATS 实测：知不知道余额比额度大小更决定效果）每轮只留一条，靠此前缀识别并替换旧的。
# 必须与 wind_down 的「检索预算已用尽」收尾指令区分开——那条不归本路径管，不能被顺手删掉。
RETRIEVAL_BUDGET_AWARENESS_PREFIX = "[系统提示] 检索余额"

BUDGET_EXHAUSTED_FEEDBACK = (
    "检索预算已尽：请基于证据台账中现有材料交付，并在交接（handoff）中如实标注检索缺口"
    "（缺什么、为何没补上）。不要再调用 web_search / read_url。"
)


def parse_retrieval_budget(raw: Any) -> int | None:
    """Normalise an internal ``retrieval_budget`` int; ``None`` = omit / invalid.

    Not a CEO/delegate config path — schema 已不暴露该字段；仅供辩论等内部 writer
    在 plan 建成后补写窄例外时规范化。
    """
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, int) and raw >= 0:
        return raw
    if isinstance(raw, float) and raw >= 0 and raw == int(raw):
        return int(raw)
    return None


def default_retrieval_budget(spec: RunSpec, *, complexity_hint: str = "standard") -> int:
    """Structured default — unified single value for all ordinary workers.

    Always :data:`DEFAULT_RETRIEVAL_BUDGET`（14）. ``form`` / role 不参与分档。
    辩手有约定文档残搜 2 由辩论内部 writer 在 plan 建成后写入，不经本函数。
    ``complexity_hint`` 保留签名兼容，**不再**参与分档。
    """
    del complexity_hint  # API compat only; no tiering
    del spec  # form / deps 不再影响默认
    return DEFAULT_RETRIEVAL_BUDGET


def exclude_retrieval_tools(
    tools: list[str] | None,
    valid_tools: set[str] | None,
) -> list[str] | None:
    """Remove web_search/read_url from an allow-list (预算 0 → 不装配检索工具).

    Unrestricted (``None``) becomes an explicit list of ``valid_tools`` minus
    retrieval tools when ``valid_tools`` is known. Returns ``[]`` (not ``None``)
    when the stripped set is empty — unlike builder._tools, empty here means
    "no tools from the declared set" so the engine does not re-open all tools;
    escalate / notes are re-granted later by the executor.
    """
    if tools is not None:
        return [t for t in tools if t not in RETRIEVAL_TOOL_NAMES]
    if valid_tools is not None:
        return sorted(valid_tools - RETRIEVAL_TOOL_NAMES)
    return None


def apply_retrieval_budgets(
    plan: RunPlan,
    *,
    valid_tools: set[str] | None = None,
    complexity_hint: str = "standard",
) -> None:
    """Fill structured defaults on every node; strip retrieval tools when limit is 0."""
    for spec in plan.nodes:
        _apply_one(spec, valid_tools=valid_tools, complexity_hint=complexity_hint)


def apply_retrieval_budgets_to_specs(
    specs: list[RunSpec],
    *,
    valid_tools: set[str] | None = None,
    complexity_hint: str = "standard",
) -> None:
    """Same as :func:`apply_retrieval_budgets` for a replan ``add`` batch."""
    for spec in specs:
        _apply_one(spec, valid_tools=valid_tools, complexity_hint=complexity_hint)


def _apply_one(
    spec: RunSpec, *, valid_tools: set[str] | None, complexity_hint: str = "standard"
) -> None:
    # 额度只来自结构化默认；CEO/task 字段不再写入。内部 writer（辩手有约定文档）在
    # apply 之后补写 RunSpec.retrieval_budget，故此处仅填 None。
    if spec.retrieval_budget is None:
        spec.retrieval_budget = default_retrieval_budget(spec, complexity_hint=complexity_hint)
    if spec.retrieval_budget == 0:
        # 复用 tasks[].tools 白名单：预算 0 → 不装配检索工具（引擎/测试手工构造）。
        stripped = exclude_retrieval_tools(spec.tools, valid_tools)
        if stripped is not None:
            spec.tools = stripped


def format_retrieval_budget_line(budget: int | None) -> str:
    """One-liner for a retrieval cap. Opening context no longer glues this onto 交付物规格."""
    if budget is None:
        return ""
    if budget <= 0:
        return (
            "- 检索预算：0（本任务不装配 web_search / read_url；"
            "基于上游与台账现有证据交付，缺口在交接中标注）"
        )
    return (
        f"- 检索预算：本 run 合计最多 {budget} 次 web_search/read_url"
        "（缓存命中不计）；用尽后基于台账现有证据交付并在交接中标注检索缺口。"
    )


def is_retrieval_budget_critical(remaining: int, *, limit: int) -> bool:
    """True when budget is still open but remaining slots are critically low.

    Used by the engine to inject a one-shot reflection before the next think round,
    so the model does not fan out more ``web_search``/``read_url`` calls than slots left.
    Exhausted (``remaining <= 0``) is handled by wind_down, not this path.
    """
    if limit <= 0:
        return False
    return 0 < remaining <= RETRIEVAL_BUDGET_CRITICAL_REMAINING


def _spend_clause(searches: int | None, reads: int | None) -> str:
    """``；已用 N 次：web_search a · read_url b`` — empty when no split is known."""
    if searches is None or reads is None:
        return ""
    return f"；已用 {searches + reads} 次：web_search {searches} · read_url {reads}"


def format_retrieval_budget_critical_prompt(
    *, remaining: int, limit: int, searches: int | None = None, reads: int | None = None
) -> str:
    """``[系统提示]`` steer when retrieval slots are critically low (同轮超订缓解).

    Also the 临界轮的余额播报：分项用量并进同一条，不另发一段预算文字。
    """
    return (
        f"{RETRIEVAL_BUDGET_AWARENESS_PREFIX}：仅剩 {remaining} 次"
        f"（本 run 上限 {limit} 次 web_search/read_url，共用一池、缓存命中不计"
        f"{_spend_clause(searches, reads)}）。"
        "下一轮请只发起不超过剩余次数的检索调用，"
        "优先深读最关键来源；勿并行扇出超过剩余槽位的查询——超订会被挡回并浪费本轮。"
        "若现有证据已够，请直接基于台账交付并在交接中标注检索缺口。"
    )


def format_retrieval_budget_awareness_prompt(
    *, remaining: int, limit: int, searches: int, reads: int
) -> str:
    """Per-round balance readout for a worker that already spent slots."""
    return (
        f"{RETRIEVAL_BUDGET_AWARENESS_PREFIX}：已用 {searches + reads} 次"
        f"（web_search {searches} · read_url {reads}），剩余 {remaining} 次"
        f"（本 run 上限 {limit} 次 web_search/read_url，共用一池、缓存命中不计）。"
        "请按剩余额度规划：先明确这一轮要验证什么再检索，避免重复查询与低价值扇出；"
        "额度用尽后只能基于台账现有证据交付，并在交接中标注检索缺口。"
    )


@dataclass(frozen=True)
class RetrievalBudgetAwareness:
    """What :func:`sync_retrieval_budget_awareness` injected this round (供埋点读)."""

    text: str
    critical: bool
    limit: int
    used: int
    remaining: int
    searches: int
    reads: int


def _is_awareness_message(msg: LLMMessage) -> bool:
    return (
        msg.role == "user"
        and isinstance(msg.content, str)
        and msg.content.startswith(RETRIEVAL_BUDGET_AWARENESS_PREFIX)
    )


def drop_retrieval_budget_awareness(messages: list[LLMMessage]) -> bool:
    """Remove the balance message; True ⇒ transcript changed.

    Called on its own once the run stops searching (wind_down / exhausted), where a
    lingering "还剩 N 次" would contradict the 收尾 instruction.
    """
    if not any(_is_awareness_message(m) for m in messages):
        return False
    messages[:] = [m for m in messages if not _is_awareness_message(m)]
    return True


def sync_retrieval_budget_awareness(
    messages: list[LLMMessage], state: RetrievalBudgetState
) -> RetrievalBudgetAwareness | None:
    """Refresh the single balance message at the tail; ``None`` ⇒ nothing injected.

    预算感知（BATS）：花过额度的 worker 每轮都要看到「已用多少 / 还剩多少」，否则只能盲搜。
    Skipped for a worker that never spent a slot（生产上多数 worker 一次都不检索，注入是纯
    噪音）、关闭额度（``limit <= 0``）、以及已耗尽（收尾话术归 wind_down，行为不变）。
    临界（剩余 ≤ :data:`RETRIEVAL_BUDGET_CRITICAL_REMAINING`）与提前告知合并成同一条。
    Refreshing = drop the stale copy then append, so the transcript never carries two
    contradicting balances and the current one stays adjacent to the next think round.
    """
    drop_retrieval_budget_awareness(messages)
    limit = state.limit
    used = state.used
    if limit <= 0 or used <= 0:
        return None
    remaining = state.remaining
    if remaining <= 0:
        return None
    searches = state.searches_used
    reads = state.reads_used
    critical = is_retrieval_budget_critical(remaining, limit=limit)
    text = (
        format_retrieval_budget_critical_prompt(
            remaining=remaining, limit=limit, searches=searches, reads=reads
        )
        if critical
        else format_retrieval_budget_awareness_prompt(
            remaining=remaining, limit=limit, searches=searches, reads=reads
        )
    )
    messages.append(LLMMessage(role="user", content=text))
    return RetrievalBudgetAwareness(
        text=text,
        critical=critical,
        limit=limit,
        used=used,
        remaining=remaining,
        searches=searches,
        reads=reads,
    )


def rework_refill_slots(
    *,
    original_limit: int,
    wind_down_entered: bool,
    write_disk_form: bool = False,
) -> int:
    """How many retrieval slots a contract rework may add (预算语义不绕过).

    - Write-disk form (``form=files`` / artifacts landing) rework: **0** — worker
      needs a directed write/repair pass, not more ``web_search``/``read_url``.
    - After token / timeout wind_down: **0** — rework must not restore investigation.
    - Otherwise: half the original resolved budget (min 1), same slice size as before.
    Caller must apply via :meth:`RetrievalBudgetState.refill_within_cap` with
    ``cap=original_limit`` so the absolute ceiling never grows past the plan-time
    budget (unlike unbounded :meth:`~RetrievalBudgetState.refill`).
    """
    if write_disk_form or wind_down_entered or original_limit <= 0:
        return 0
    return max(1, int(original_limit) // 2)


def charges_retrieval_budget(result: ToolResult) -> bool:
    """True when a completed retrieval call should consume one budget slot.

    Cache hits (``metadata.cached``) do not charge. Failures (including A3 query
    contract rejects) do not charge — they never produced a live backend hit worth
    counting, and A3 must remain free to rewrite (提案 A3).
    """
    if not result.success:
        return False
    meta = result.metadata or {}
    return not meta.get("cached")


def budget_exhausted_output() -> str:
    return BUDGET_EXHAUSTED_FEEDBACK
