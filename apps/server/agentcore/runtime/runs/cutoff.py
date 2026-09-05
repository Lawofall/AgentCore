"""Worker 掐断透明化 + 收尾窗口：原因码、文案与工具白名单。

正轨 token 撞顶 / 墙钟超时 / 降级交接 必须产生结构化原因码，贯通
``RunState.warnings`` → ``delivery_status.gaps.reason``（用户可见缺口唯一可信源）；
CEO 综述仅轻纪律禁完成度断言，缺口披露由呈现层对账卡承担。
收尾窗口在硬顶前把工具面收窄到落盘 + 内环诊断 + handoff，降低 ``degraded_synth``。
"""

from __future__ import annotations

# delivery_status.gaps.reason 口径（契约单源：Wire DeliveryGap.reason）
REASON_TOKEN_BUDGET = "token_budget"
REASON_WORKER_TIMEOUT = "worker_timeout"
REASON_MAX_ROUNDS = "max_rounds"
REASON_DEGRADED_HANDOFF = "degraded_handoff"
# Turn 级累计顶（策略 A）：与 ``runtime/turn/token_budget.REASON_TURN_TOKEN_BUDGET`` 同值。
# 本步拒派 / 跳过未跑节点时可挂 gaps.reason，勿硬补对账层。
REASON_TURN_TOKEN_BUDGET = "turn_token_budget"

CUTOFF_REASONS = frozenset(
    {
        REASON_TOKEN_BUDGET,
        REASON_WORKER_TIMEOUT,
        REASON_MAX_ROUNDS,
        REASON_DEGRADED_HANDOFF,
    }
)

# RunState.warnings / gap description 稳定文案（collect_worker_gaps 依文案反查 reason）
TOKEN_BUDGET_WARNING = "队员因 token 预算触顶被迫收口，产出可能不完整"
WORKER_TIMEOUT_WARNING = "队员运行超时（硬收尾/强制取消），交付可能缩水"
MAX_ROUNDS_WARNING = "队员因轮次上限强制收口，产出可能不完整"
# 用户面口语：避免 worker / handoff 等内部词；引擎反查仍认本常量全文。
DEGRADED_HANDOFF_WARNING = "交接说明不够完整，系统已代为补写摘要"

WARNING_TO_REASON: dict[str, str] = {
    TOKEN_BUDGET_WARNING: REASON_TOKEN_BUDGET,
    WORKER_TIMEOUT_WARNING: REASON_WORKER_TIMEOUT,
    MAX_ROUNDS_WARNING: REASON_MAX_ROUNDS,
    DEGRADED_HANDOFF_WARNING: REASON_DEGRADED_HANDOFF,
}

REASON_TO_WARNING: dict[str, str] = {
    REASON_TOKEN_BUDGET: TOKEN_BUDGET_WARNING,
    REASON_WORKER_TIMEOUT: WORKER_TIMEOUT_WARNING,
    REASON_MAX_ROUNDS: MAX_ROUNDS_WARNING,
    REASON_DEGRADED_HANDOFF: DEGRADED_HANDOFF_WARNING,
}

# 预算收尾窗口：累计 fuse token ≥ ceiling − reserve 时进入落盘/handoff-only（默认 20 万）
DEFAULT_TOKEN_WIND_DOWN_RESERVE = 200_000

# 收尾窗口允许的工具（落盘 + 内环诊断 + handoff；调查/执行类一律剔除）
# file_read 不在此基础集：仅交付类（form=files / 非空 artifacts，工具面仍含
# file_write）经 :func:`wind_down_allowed_tools` 叠加——回读自己产物属于写作，
# 不是继续调查；web_search / web_fetch / grep 等检索类不放回。
# code_diagnostics：修码自检（内环），token/timeout 收尾收窄后仍可用。
# 交文件 delivery_idle 收窄已退役；本白名单仍可被显式构造的 idle narrow 复用。
# md_to_docx / md_to_pdf：把已成篇 .md 确定性导出为同目录同名交付件——用户要
# PDF / Word / 可分享文件时的成文主路径末步（见 research_quality.MD_EXPORT_DISCIPLINE），
# 属收口落盘而非新战线：不检索、不出网、回执只有一行 manifest。
WIND_DOWN_ALLOWED_TOOLS = frozenset(
    {
        "handoff",
        "file_write",
        "file_append",
        "str_replace",
        "file_move",
        "file_copy",
        "mkdir",
        "file_batch",
        "file_list",
        "md_to_docx",
        "md_to_pdf",
        "code_diagnostics",
    }
)

WIND_DOWN_FILE_READ = "file_read"


def wind_down_instruction_token() -> str:
    return (
        "[系统提示] 累计 token 已接近预算硬顶。本轮起进入收尾窗口："
        "调查与外网工具已停用。"
    )


def wind_down_instruction_timeout() -> str:
    return (
        "[系统提示] 墙钟已触及超时阈值。本轮为宽限交卷轮："
        "调查与外网工具已停用。"
    )


def wind_down_instruction_retrieval() -> str:
    return (
        "[系统提示] 检索预算已用尽。本轮起进入收尾窗口："
        "web_search / web_fetch 已停用。"
    )


def wind_down_deny_output(name: str) -> str:
    """Tool-result fact when a call is outside the wind-down whitelist."""
    return f"工具 '{name}' 不在收尾窗口白名单，未执行。"


# Backward-compat aliases.
WIND_DOWN_INSTRUCTION_TOKEN = wind_down_instruction_token()
WIND_DOWN_INSTRUCTION_TIMEOUT = wind_down_instruction_timeout()
WIND_DOWN_INSTRUCTION_RETRIEVAL = wind_down_instruction_retrieval()


def reason_for_warning(text: str) -> str | None:
    """Map a canonical cutoff warning string to its reason code, or None."""
    return WARNING_TO_REASON.get(str(text).strip())


def warning_for_reason(reason: str) -> str | None:
    """Canonical warning text for a reason code, or None if unknown."""
    return REASON_TO_WARNING.get(reason)


def should_enter_token_wind_down(tokens: int, budget: int, reserve: int) -> bool:
    """True when cumulative tokens have reached the soft wind-down threshold.

    Soft window opens at ``budget - reserve`` (absolute headroom for a final
    persist/handoff round). ``reserve <= 0`` disables the soft window; so does
    ``reserve >= budget`` (pathological — no room left before the hard ceiling).
    """
    if budget <= 0 or reserve <= 0 or reserve >= budget:
        return False
    return tokens >= budget - reserve


def worker_keeps_file_read_in_wind_down(
    *,
    available: set[str],
    allowed: list[str] | None,
) -> bool:
    """True for files-form / artifacts workers (live surface still offers file_write).

    Prose workers withhold ``file_write``; deliverable workers keep it — same heuristic
    as finalize persist. ``file_read`` must also be registered / allowed to keep.
    """
    if WIND_DOWN_FILE_READ not in available:
        return False
    if "file_write" not in available:
        return False
    if allowed is None:
        return True
    return "file_write" in allowed and WIND_DOWN_FILE_READ in allowed


def wind_down_allowed_tools(*, keep_file_read: bool = False) -> frozenset[str]:
    """Effective wind-down whitelist (optional file_read)."""
    base: frozenset[str] = WIND_DOWN_ALLOWED_TOOLS
    if keep_file_read:
        base = base | {WIND_DOWN_FILE_READ}
    return base


def narrow_tools_for_wind_down(
    available: set[str],
    *,
    allowed: list[str] | None,
    keep_file_read: bool | None = None,
) -> list[str]:
    """Intersect caller's allow-list with the wind-down persist/handoff whitelist.

    When ``keep_file_read`` is None, infer from the live tool surface
    (files deliverable). Retrieval tools (web_search / web_fetch / …)
    stay excluded.
    """
    if keep_file_read is None:
        keep_file_read = worker_keeps_file_read_in_wind_down(
            available=available, allowed=allowed
        )
    whitelist = wind_down_allowed_tools(keep_file_read=keep_file_read)
    base = set(allowed) if allowed is not None else set(available)
    narrowed = sorted(base & whitelist)
    if "handoff" in available and "handoff" not in narrowed:
        narrowed.append("handoff")
    return narrowed


def narrow_tools_for_handoff_only(available: set[str]) -> list[str]:
    """Post-breach surface: handoff only (when registered)."""
    return ["handoff"] if "handoff" in available else []


def narrow_tools_for_wind_down_breach(
    available: set[str],
    *,
    keep_landing: bool = False,
    keep_file_read: bool = False,
    allowed: list[str] | None = None,
) -> list[str]:
    """Post-breach surface after a wind-down whitelist violation.

    Default = handoff-only (strip retrieval thrash). When the run still owes
    workspace landing (``keep_landing``), keep the wind_down write whitelist so
    ``file_write`` / append / replace are not allowlist-denied mid-obligation.
    """
    if keep_landing:
        return narrow_tools_for_wind_down(
            available,
            allowed=allowed,
            keep_file_read=keep_file_read,
        )
    return narrow_tools_for_handoff_only(available)


def wind_down_breach_tool_names(
    tool_names: list[str] | tuple[str, ...] | set[str],
    *,
    keep_file_read: bool = False,
    allowed: frozenset[str] | set[str] | None = None,
) -> list[str]:
    """Return tool names that violate the wind-down whitelist (stable order, deduped)."""
    whitelist = (
        frozenset(allowed)
        if allowed is not None
        else wind_down_allowed_tools(keep_file_read=keep_file_read)
    )
    seen: set[str] = set()
    breached: list[str] = []
    for raw in tool_names:
        name = str(raw or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        if name not in whitelist:
            breached.append(name)
    return breached


def should_force_local_after_wind_down_breach(
    *,
    prior_breaches: int,
    tokens: int,
    token_budget: int,
    wind_down_reason: str = "",
) -> bool:
    """True when a wind-down breach must local-synth instead of another LLM round.

    Retrieval-budget wind-down: first breach already forces local close (avoid grep /
    search thrash after slots are gone). Other reasons: second+ breach always forces;
    first breach still forces when already at/over the hard token ceiling.
    """
    if wind_down_reason == "retrieval_budget":
        return True
    if prior_breaches >= 1:
        return True
    return token_budget > 0 and tokens >= token_budget
