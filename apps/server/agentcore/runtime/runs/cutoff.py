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
REASON_DEGRADED_HANDOFF = "degraded_handoff"
# Turn 级累计顶（策略 A）：与 ``runtime/turn/token_budget.REASON_TURN_TOKEN_BUDGET`` 同值。
# 本步拒派 / 跳过未跑节点时可挂 gaps.reason，勿硬补对账层。
REASON_TURN_TOKEN_BUDGET = "turn_token_budget"

CUTOFF_REASONS = frozenset(
    {REASON_TOKEN_BUDGET, REASON_WORKER_TIMEOUT, REASON_DEGRADED_HANDOFF}
)

# RunState.warnings / gap description 稳定文案（collect_worker_gaps 依文案反查 reason）
TOKEN_BUDGET_WARNING = "队员因 token 预算触顶被迫收口，产出可能不完整"
WORKER_TIMEOUT_WARNING = "队员运行超时（硬收尾/强制取消），交付可能缩水"
# 用户面口语：避免 worker / handoff 等内部词；引擎反查仍认本常量全文。
DEGRADED_HANDOFF_WARNING = "交接说明不够完整，系统已代为补写摘要"

WARNING_TO_REASON: dict[str, str] = {
    TOKEN_BUDGET_WARNING: REASON_TOKEN_BUDGET,
    WORKER_TIMEOUT_WARNING: REASON_WORKER_TIMEOUT,
    DEGRADED_HANDOFF_WARNING: REASON_DEGRADED_HANDOFF,
}

REASON_TO_WARNING: dict[str, str] = {
    REASON_TOKEN_BUDGET: TOKEN_BUDGET_WARNING,
    REASON_WORKER_TIMEOUT: WORKER_TIMEOUT_WARNING,
    REASON_DEGRADED_HANDOFF: DEGRADED_HANDOFF_WARNING,
}

# 预算收尾窗口：累计 token ≥ ceiling − reserve 时进入落盘/handoff-only（默认 30k）
DEFAULT_TOKEN_WIND_DOWN_RESERVE = 30_000

# 收尾窗口允许的工具（落盘 + 内环诊断 + handoff；调查/执行类一律剔除）
# file_read 不在此基础集：仅交付类（form=files / 非空 artifacts，工具面仍含
# file_write）经 :func:`wind_down_allowed_tools` 叠加——回读自己产物属于写作，
# 不是继续调查；web_search / read_url / grep 等检索类不放回。
# code_diagnostics：修码自检（内环），token/timeout 收尾收窄后仍可用。
# 交文件 delivery_idle 收窄已退役；本白名单仍可被显式构造的 idle narrow 复用。
# md_to_docx / md_to_pdf：把已成篇 .md 确定性导出为同目录同名交付件——用户要
# PDF / Word / 可分享文件时的成文主路径末步（见 research_quality.MD_EXPORT_DISCIPLINE），
# 属收口落盘而非新战线：不检索、不出网、回执只有一行 manifest。
# 便签三件套亦不在基础集：仅 collaboration/wall（工具面已授 post_note）经
# :func:`wind_down_allowed_tools` 叠加——收尾可贴/读/改便签，仍禁检索/外网。
WIND_DOWN_ALLOWED_TOOLS = frozenset(
    {
        "handoff",
        "file_write",
        "file_append",
        "str_replace",
        "write_section",
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

# collaboration=wall 时收窄后仍保留的便签三件套（非协作工具面无此三件 → 不会出现）。
WIND_DOWN_NOTE_TOOLS = frozenset({"post_note", "read_notes", "amend_note"})


def _notes_surface_clause(*, keep_notes: bool) -> str:
    """Brief allowlist mention when collaboration notes stay on the wind-down surface."""
    if not keep_notes:
        return ""
    return "、便签（可贴/读/改 post_note/read_notes/amend_note）"


def wind_down_instruction_token(*, keep_notes: bool = False) -> str:
    notes = _notes_surface_clause(keep_notes=keep_notes)
    return (
        "[系统提示] 累计 token 已接近预算硬顶。本轮起进入收尾窗口：仅允许落盘"
        f"（file_write / str_replace / file_append 等）、内环 code_diagnostics{notes} 与 handoff；"
        "交付类可 file_read 回读已写文件核对契约。"
        "长文/成篇：若正在按章写作，请停在完整章边界落盘并 handoff——"
        "标明已完成章节与待续章节；禁止章中部硬截、禁止删稿重写。"
        "请立即把已有产出落盘并调用 handoff 提交交接简报；禁止继续调查或开新战线。"
    )


def wind_down_instruction_timeout(*, keep_notes: bool = False) -> str:
    notes = _notes_surface_clause(keep_notes=keep_notes)
    return (
        "[系统提示] 墙钟已触及超时阈值。本轮为宽限交卷轮：仅允许落盘、内环诊断"
        f"{notes}与 handoff"
        "（交付类可 file_read 回读已写文件）；请立即提交合格 handoff。"
        "宽限结束后将强制取消本队员，禁止继续调查或开新战线。"
    )


def wind_down_breach_nudge(*, keep_landing: bool = False, keep_notes: bool = False) -> str:
    """Post-breach steer. Notes only mentioned when keep_landing keeps the write surface."""
    if not keep_landing:
        return (
            "[系统提示] 收尾窗口违约：你调用了非落盘/诊断/handoff 工具。"
            "工具面已收缩为仅 handoff。请立刻用已有产出调用 handoff 提交交接简报；"
            "禁止再调查、读文件或开新战线。再次违约将本地合成交付并强制收口。"
        )
    notes = _notes_surface_clause(keep_notes=keep_notes)
    return (
        "[系统提示] 收尾窗口违约：你调用了检索/外网类工具。"
        f"工具面已禁止继续调查，但仍保留落盘、内环诊断{notes}与 handoff（本 run 仍负有落盘义务）。"
        "请立刻把已有产出落盘并调用 handoff；禁止再检索或开新战线。"
        "再次违约将本地合成交付并强制收口。"
    )


# Backward-compat aliases (non-collaboration / no-notes wording).
WIND_DOWN_INSTRUCTION_TOKEN = wind_down_instruction_token()
WIND_DOWN_INSTRUCTION_TIMEOUT = wind_down_instruction_timeout()
WIND_DOWN_BREACH_NUDGE = wind_down_breach_nudge()
WIND_DOWN_BREACH_NUDGE_KEEP_LANDING = wind_down_breach_nudge(keep_landing=True)


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


def worker_keeps_notes_in_wind_down(
    *,
    available: set[str],
    allowed: list[str] | None,
) -> bool:
    """True when collaboration note tools are on the live surface (wall / env.collaboration).

    ``post_note`` is the grant sentinel — executor hands the trio together when
    ``env.collaboration`` is true; non-collab surfaces never register it.
    """
    if "post_note" not in available:
        return False
    if allowed is None:
        return True
    return "post_note" in allowed


def wind_down_allowed_tools(
    *, keep_file_read: bool = False, keep_notes: bool = False
) -> frozenset[str]:
    """Effective wind-down whitelist (optional file_read / collaboration notes)."""
    base: frozenset[str] = WIND_DOWN_ALLOWED_TOOLS
    if keep_file_read:
        base = base | {WIND_DOWN_FILE_READ}
    if keep_notes:
        base = base | WIND_DOWN_NOTE_TOOLS
    return base


def narrow_tools_for_wind_down(
    available: set[str],
    *,
    allowed: list[str] | None,
    keep_file_read: bool | None = None,
    keep_notes: bool | None = None,
) -> list[str]:
    """Intersect caller's allow-list with the wind-down persist/handoff whitelist.

    When ``keep_file_read`` / ``keep_notes`` is None, infer from the live tool surface
    (files deliverable / collaboration). Retrieval tools (web_search / read_url / …)
    stay excluded.
    """
    if keep_file_read is None:
        keep_file_read = worker_keeps_file_read_in_wind_down(
            available=available, allowed=allowed
        )
    if keep_notes is None:
        keep_notes = worker_keeps_notes_in_wind_down(available=available, allowed=allowed)
    whitelist = wind_down_allowed_tools(
        keep_file_read=keep_file_read, keep_notes=keep_notes
    )
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
    keep_notes: bool = False,
    allowed: list[str] | None = None,
) -> list[str]:
    """Post-breach surface after a wind-down whitelist violation.

    Default = handoff-only (strip retrieval thrash). When the run still owes
    workspace landing (``keep_landing``), keep the wind_down write whitelist so
    ``file_write`` / append / replace are not allowlist-denied mid-obligation.
    Collaboration notes stay only with ``keep_landing`` (same overlay as enter).
    """
    if keep_landing:
        return narrow_tools_for_wind_down(
            available,
            allowed=allowed,
            keep_file_read=keep_file_read,
            keep_notes=keep_notes,
        )
    return narrow_tools_for_handoff_only(available)


def wind_down_breach_tool_names(
    tool_names: list[str] | tuple[str, ...] | set[str],
    *,
    keep_file_read: bool = False,
    keep_notes: bool = False,
    allowed: frozenset[str] | set[str] | None = None,
) -> list[str]:
    """Return tool names that violate the wind-down whitelist (stable order, deduped)."""
    whitelist = (
        frozenset(allowed)
        if allowed is not None
        else wind_down_allowed_tools(keep_file_read=keep_file_read, keep_notes=keep_notes)
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
    """True when a wind-down breach must local-synth instead of another LLM nudge round.

    Retrieval-budget wind-down: first breach already forces local close (avoid grep /
    search thrash after slots are gone). Other reasons: second+ breach always forces;
    first breach still forces when already at/over the hard token ceiling.
    """
    if wind_down_reason == "retrieval_budget":
        return True
    if prior_breaches >= 1:
        return True
    return token_budget > 0 and tokens >= token_budget
