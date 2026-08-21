"""Contract-retry / decision-ladder helpers for AGENT-node execution.

Split from ``.node`` — pure move. Public ``should_skip_contract_retry_for_budget``
and existing test imports stay re-exported from ``.node``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agentcore.config import settings
from agentcore.llm.provider.protocol import LLMMessage
from agentcore.runtime.runs.constants import HANDOFF_TOOL_NAME, MAX_RUN_TOTAL_ROUNDS
from agentcore.runtime.runs.contract import (
    ContractVerdict,
    is_format_repairable,
    is_zero_files_gap,
)
from agentcore.runtime.runs.executor.shared import _registry_without
from agentcore.runtime.runs.retrieval_budget import RETRIEVAL_TOOL_NAMES
from agentcore.runtime.runs.worker_budget import DIRECTED_SEARCH_TOOL_NAMES

# Light-repair allow-list: format backfill / handoff enrichment only — no re-investigation.
_LIGHT_REPAIR_TOOL_NAMES: frozenset[str] = frozenset(
    {
        HANDOFF_TOOL_NAME,
        "file_write",
        "file_append",
        "str_replace",
        "write_section",
        "file_read",
    }
)
_LIGHT_REPAIR_MAX_ROUNDS = 4

# Pass-boundary cap announcement (light_repair / contract retry). Not a live
# countdown: rounds are an engine ceiling, not a worker-planning budget (BATS
# applies to retrieval slots only). Numbers only; no tool narrowing, no
# completion / quality steer.
ROUND_BUDGET_AWARENESS_PREFIX = "[系统提示] 轮次余额"


def _pass_max_rounds(*, light_pass: bool, profile_max: int, spent: int = 0) -> int:
    """ReAct cap for this produce pass, clipped by the cross-attempt total.

    ``light_repair`` / ``write_pass`` / cite-upgrade short passes still get a
    dedicated :data:`_LIGHT_REPAIR_MAX_ROUNDS` — not leftover from the main
    pool — so a format fix after a full main pass still sees 4 when the
    total cap has room. ``spent`` is cumulative produce rounds already
    finished on this run (main + prior retries / short passes). The total
    :data:`MAX_RUN_TOTAL_ROUNDS` stops contract.retry from opening another
    full investigation. Round-ceiling hits skip that full retry entirely.
    ``0`` means no rounds remain under the total; callers must skip the
    pass rather than start a ``max_rounds=0`` loop.
    """
    remaining = max(0, MAX_RUN_TOTAL_ROUNDS - max(0, int(spent)))
    if remaining <= 0:
        return 0
    if light_pass:
        return min(_LIGHT_REPAIR_MAX_ROUNDS, remaining)
    return min(max(0, int(profile_max)), remaining)


def format_round_budget_awareness(*, limit: int) -> str:
    """One-line pass-cap fact for a new produce segment. No advice, no intercept."""
    limit_n = max(0, int(limit))
    return f"{ROUND_BUDGET_AWARENESS_PREFIX}：本段上限 {limit_n} 轮。"


def _is_round_budget_awareness(msg: LLMMessage) -> bool:
    return (
        msg.role == "user"
        and isinstance(msg.content, str)
        and msg.content.startswith(ROUND_BUDGET_AWARENESS_PREFIX)
    )


def drop_round_budget_awareness(messages: list[LLMMessage]) -> bool:
    """Remove the pass-cap announcement; True ⇒ transcript changed."""
    if not any(_is_round_budget_awareness(m) for m in messages):
        return False
    messages[:] = [m for m in messages if not _is_round_budget_awareness(m)]
    return True


def sync_round_budget_awareness(
    messages: list[LLMMessage],
    *,
    limit: int,
    before_last_user: bool = False,
) -> str | None:
    """Announce this produce segment's round cap once.

    Call only at a new-pass boundary (light_repair / contract retry), not every
    ReAct round — a live used/remaining ticker hijacks the next-think user slot.
    ``limit <= 0`` ⇒ skip (no cap to report). Refresh = drop stale copy then
    insert, so the transcript never carries two contradicting caps.

    ``before_last_user`` parks the fact under the latest user instruction
    (light_repair / retry shortfall stays last).
    """
    if limit <= 0:
        return None
    drop_round_budget_awareness(messages)
    text = format_round_budget_awareness(limit=limit)
    msg = LLMMessage(role="user", content=text)
    if (
        before_last_user
        and messages
        and messages[-1].role == "user"
        and not _is_round_budget_awareness(messages[-1])
    ):
        messages.insert(-1, msg)
    else:
        messages.append(msg)
    return text


def stamp_coord_round_budget(
    run_id: str,
    *,
    used: int,
    limit: int,
    tokens_spent: int | None = None,
    kind: str = "llm",
) -> None:
    """Piggyback pass-local used/limit (and run tokens) on the busy channel.

    Live used/limit for the CEO idle brief. No-op without a live coordination
    session. Once per round / pass start — not per token. Does not inject into
    the worker window.
    """
    rid = (run_id or "").strip()
    if not rid or limit <= 0:
        return
    from agentcore.runtime.coordination.session import note_coord_worker_busy

    note_coord_worker_busy(
        rid,
        kind,
        rounds_used=max(0, int(used)),
        rounds_limit=int(limit),
        tokens_spent=None if tokens_spent is None else max(0, int(tokens_spent)),
    )


def bind_round_budget_on_begin(
    pull_notes: Callable[[], list[LLMMessage]],
    used_box: list[int],
    limit_box: list[int],
    *,
    run_id: str = "",
    tokens_spent_of: Callable[[], int] | None = None,
) -> Callable[[], list[LLMMessage]]:
    """``on_round_begin`` wrapper: stamp CEO live spend, then teammate notes.

    Does **not** inject a rounds ticker into the worker window. The engine
    calls the hook at the start of every round after the first; each
    invocation means ``used`` completed rounds (including the round about to
    start). Returns only the notes list for the engine to extend.

    When ``run_id`` is set, used/limit (and optional run-level tokens) are
    stamped onto the coordination busy channel for the CEO idle brief.
    """

    def _on_round_begin() -> list[LLMMessage]:
        used_box[0] += 1
        spent: int | None = None
        if tokens_spent_of is not None:
            spent = int(tokens_spent_of())
        stamp_coord_round_budget(
            run_id,
            used=used_box[0],
            limit=limit_box[0],
            tokens_spent=spent,
        )
        return pull_notes()

    return _on_round_begin


def _files_expected(deliverable: Any) -> bool:
    """True when this run's contract expects workspace landing.

    Only ``form=files`` and/or non-empty ``artifacts`` — legacy flags alone do not.
    """
    if deliverable is None:
        return False
    if getattr(deliverable, "form", None) == "files":
        return True
    return bool(getattr(deliverable, "artifacts", None))


def _retry_token_budget(*, ceiling: int, spent: int) -> int:
    """Remaining token budget for a correction pass (总预算约束).

    ``ceiling <= 0`` means the hard ceiling is disabled (pass through 0).
    A new react_loop's usage counter starts at 0, so returning ``1`` when
    already at/over the ceiling still allows one full produce round (the
    engine checks the cap at round start). Callers must skip opening a new
    pass when ``spent >= ceiling`` (loop already does) — do not rely on
    this sentinel to stop the first retry round.
    """
    if ceiling <= 0:
        return 0
    remaining = ceiling - spent
    if remaining <= 0:
        return 1
    return remaining


def _wind_down_entered(
    *,
    cutoff_reasons: list[str],
    token_ceiling: int,
    tokens_spent: int,
) -> bool:
    """True when this run already entered token/timeout wind_down (or past soft reserve)."""
    if "token_budget" in cutoff_reasons or "worker_timeout" in cutoff_reasons:
        return True
    if token_ceiling <= 0:
        return False
    from agentcore.runtime.runs.cutoff import (
        DEFAULT_TOKEN_WIND_DOWN_RESERVE,
        should_enter_token_wind_down,
    )

    reserve = int(
        settings.engine_worker_token_wind_down_reserve or DEFAULT_TOKEN_WIND_DOWN_RESERVE
    )
    return should_enter_token_wind_down(tokens_spent, token_ceiling, reserve)


def should_skip_contract_retry_for_budget(
    *,
    handoff_ok: bool,
    wind_down_entered: bool,
) -> bool:
    """定案 B：handoff 已成功且预算收尾/将尽 → 跳过自动契约返工（防空转）。

    真缺口交给审校/CEO，不靠耗尽后再硬返工。硬顶短路见调用方的 ceiling 分支。
    """
    return bool(handoff_ok and wind_down_entered)


def should_skip_full_contract_retry_for_round_ceiling(
    *,
    cutoff_reasons: list[str],
    prior_round_ceiling: bool = False,
) -> bool:
    """Round fuse blown: keep light_repair / write_pass, do not reopen investigation.

    Token wind-down still requires handoff_ok (定案 B). Round ceiling skips the
    full retry even when handoff is thin — salvage already ran; leftover
    shortfalls stay ``partial`` for CEO / ``continue_from``.

    ``cutoff_reasons`` is cleared each produce pass; ``prior_round_ceiling``
    is the sticky latch so a light_repair after the fuse still cannot open
    a full investigation.
    """
    return bool(prior_round_ceiling) or "max_rounds" in cutoff_reasons


def _narrow_for_light_repair(
    worker_tools: Any,
    allowed_tools: list[str] | None,
) -> tuple[Any, list[str]]:
    """Strip investigation tools for a format-only light repair pass.

    Unrestricted (``None``) → explicit light-repair allow-list ∩ registry.
    Explicit lists are intersected with :data:`_LIGHT_REPAIR_TOOL_NAMES` (already
    includes persist writes). 真纯丙后不再有「名单缺写盘 → 补写」半成品。
    """
    withhold = tuple(
        sorted(
            RETRIEVAL_TOOL_NAMES
            | DIRECTED_SEARCH_TOOL_NAMES
            | frozenset({"file_list", "code_execute", "test_run", "terminal"})
        )
    )
    narrowed_registry = _registry_without(worker_tools, *withhold)
    if allowed_tools is None:
        # Unrestricted → explicit light-repair allow-list (intersect registry).
        present = {
            s.name
            for s in narrowed_registry.list_all()
            if s.name in _LIGHT_REPAIR_TOOL_NAMES
        }
        return narrowed_registry, sorted(present)
    narrowed_allowed = [t for t in allowed_tools if t in _LIGHT_REPAIR_TOOL_NAMES]
    if HANDOFF_TOOL_NAME not in narrowed_allowed:
        narrowed_allowed = [*narrowed_allowed, HANDOFF_TOOL_NAME]
    return narrowed_registry, narrowed_allowed


def _can_light_repair(
    *,
    verdict: ContractVerdict,
    handoff_ok: bool,
    light_repair_used: bool,
) -> bool:
    """Format / handoff-thin failures get one in-place light repair before full retry."""
    if light_repair_used:
        return False
    if verdict.ok and handoff_ok:
        return False
    # Zero-disk gaps use write pass (not format light repair / full investigation retry).
    if is_zero_files_gap(verdict):
        return False
    return not (not verdict.ok and not is_format_repairable(verdict))


def _can_write_pass(
    *,
    verdict: ContractVerdict,
    files_expected: bool,
    files_written: int,
    write_pass_used: bool,
) -> bool:
    """Files-expected + zero disk → one short write pass (not full contract.retry)."""
    if write_pass_used or not files_expected:
        return False
    if int(files_written or 0) > 0:
        return False
    return is_zero_files_gap(verdict)
