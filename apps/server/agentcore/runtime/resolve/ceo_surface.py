"""CEO orchestration tool surface: idle vs coordination gating.

Injection aligns with the coordination tools' execution gate
(``active_coordination``): idle chat omits replan / coordination suite;
``delegate`` + ``ask_user`` + ``debate`` stay always-on. Mid-turn promotion
(coordination starts or supervised wave yield) registers the gated tools in
place — one-time prefix-cache miss is acceptable.

Also owns COST-004 tools-surface observation (exact JSON chars + a token band)
for ``ceo_turn`` and ``worker_run``. The coordination-period hint is owned by
the ``wait`` tool description (not copied into each inject).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from agentcore.core.logging import get_logger
from agentcore.tools.protocol import tool_schema_to_openai_format

if TYPE_CHECKING:
    from agentcore.tools.builtin.delegate import DelegateTool
    from agentcore.tools.registry import ToolRegistry

logger = get_logger(__name__)

COORDINATION_GATED_TOOLS: frozenset[str] = frozenset(
    {
        "replan",
        "wait",
        "update_synthesis",
        "cancel_worker",
        "resolve_escalation",
        "queue_user_message",
    }
)

COORDINATION_PERIOD_HINT = (
    "【协调期】图在转、无新结论可静默；对用户开口只谈请示/阻塞/阶段结论/回应中途插话。"
)

# A tool schema is Chinese prose (every ``description``) wrapped in ASCII JSON, and the two
# halves tokenize an order of magnitude apart: a Han character costs about one token on
# cl100k-family vocabularies and ~1.5 chars/token on the CJK-dense ones (DeepSeek / Qwen),
# while the scaffolding (keys, enum literals, punctuation) runs 3–4.5 chars/token. One blended
# divisor cannot be honest about both: the assembled CEO surface is 22.5k chars / 30% CJK,
# which the old flat ``chars // 4`` called 5.6k tokens against a 7.8k–11.9k band. So report the
# BAND, per char class, next to the exact char split — recomputable for a known tokenizer, and
# never a single number that reads as measured.
_CJK_TOKENS_PER_CHAR = (0.65, 1.0)
_OTHER_TOKENS_PER_CHAR = (1 / 4.5, 1 / 3.0)


def _is_cjk(char: str) -> bool:
    """True for the char classes that tokenize roughly one-per-character."""
    code = ord(char)
    return (
        0x3000 <= code <= 0x9FFF  # CJK punctuation · kana · ext-A · unified ideographs
        or 0xF900 <= code <= 0xFAFF  # compatibility ideographs
        or 0xFF00 <= code <= 0xFFEF  # fullwidth / halfwidth forms
    )


def _token_band(*, cjk_chars: int, other_chars: int) -> tuple[int, int]:
    """Lower / upper token estimate for a char split — never a single measured-looking value."""
    low = cjk_chars * _CJK_TOKENS_PER_CHAR[0] + other_chars * _OTHER_TOKENS_PER_CHAR[0]
    high = cjk_chars * _CJK_TOKENS_PER_CHAR[1] + other_chars * _OTHER_TOKENS_PER_CHAR[1]
    return round(low), round(high)


def coordination_surface_active(*, execution_id: str | None = None) -> bool:
    """True iff a live coordination session exists (same gate as coord tool execute)."""
    from agentcore.runtime.coordination.session import active_coordination

    session = active_coordination(execution_id)
    return session is not None and bool(session.active)


def _owns_coordination(delegate: Any) -> bool:
    """True only for the root delegate handle — the one that can own a coordination session.

    ``should_enter_coordination`` only ever arms a session at ``depth == 0``, but a
    ``depth >= 1`` worker carries its own nested ``delegate`` handle AND shares the
    parent's ``execution_id`` — so an identity-blind promote hands ``wait`` /
    ``cancel_worker`` to plain members, which are then offered for real because
    workers run unrestricted (``allowed_tools=None``). Nested leads keep
    ``delegate`` from round 1; ``replan`` is promoted here once ``_supervised``
    is set (same idle→coordination idea as the CEO). The LeadSubteam bundle
    still mints both (dispose / 波边界 binding).
    """
    depth = getattr(delegate, "_depth", 0)
    return depth == 0 if isinstance(depth, int) else False


def resync_coordination_binding(chat_tools: ToolRegistry) -> bool:
    """Re-point the turn ContextVar at the execution ``delegate`` actually coordinated.

    ``current_execution_id`` is bound at turn entry (``pipeline/prepare``). Cross-turn
    adopt leaves it on the previous live graph for wait / cancel / interjection, while
    ``base_tool_context.execution_id`` stays this turn's mint for dispatch. Same-turn
    merge re-binds the shared tool context onto the host from inside the delegate
    tool's ``asyncio.gather`` child, where the ContextVar write stays in the child
    copy. Without re-reading a *live* host, the captain's ``active_coordination()``
    lookups miss the session: it neither blocks on team events nor gets the
    coordination tool surface.

    Only follow the tool-context eid when that id already has an active session —
    otherwise a this-turn mint with no graph yet would clobber the adopted live
    binding. After this turn starts its own graph, ``set_active_coordination``
    registers the mint and this follows it.

    Returns True when the binding moved.
    """
    delegate = chat_tools.get_optional("delegate")
    if delegate is None:
        return False
    ctx = getattr(delegate, "_base_tool_context", None)
    raw = getattr(ctx, "execution_id", None) if ctx is not None else None
    bound = raw.strip() if isinstance(raw, str) else ""
    if not bound:
        return False

    from agentcore.runtime.coordination.session import (
        active_coordination,
        current_execution_id,
    )

    previous = (current_execution_id.get() or "").strip()
    if previous == bound:
        return False
    host = active_coordination(bound)
    if host is None or not host.active:
        return False
    current_execution_id.set(bound)
    logger.info(
        "coordination.binding_resynced",
        execution_id=bound,
        previous_execution_id=previous or None,
    )
    return True


def register_coordination_surface(
    chat_tools: ToolRegistry,
    *,
    delegate_tool: DelegateTool,
    sink: Any,
    include: bool,
) -> None:
    """Register replan + coord suite when ``include`` is True."""
    if not include:
        return
    from agentcore.runtime.coordination.tools import (
        CancelWorkerTool,
        QueueUserMessageTool,
        ResolveEscalationTool,
        UpdateSynthesisTool,
        WaitTool,
    )
    from agentcore.tools.builtin.replan import ReplanTool

    if chat_tools.get_optional("replan") is None:
        chat_tools.register(ReplanTool(delegate=delegate_tool))
    if chat_tools.get_optional("wait") is None:
        chat_tools.register(WaitTool())
    if chat_tools.get_optional("update_synthesis") is None:
        chat_tools.register(UpdateSynthesisTool(sink=sink))
    if chat_tools.get_optional("cancel_worker") is None:
        chat_tools.register(CancelWorkerTool())
    if chat_tools.get_optional("resolve_escalation") is None:
        chat_tools.register(ResolveEscalationTool())
    if chat_tools.get_optional("queue_user_message") is None:
        chat_tools.register(QueueUserMessageTool(sink=sink))


def ensure_coordination_surface_before_llm(chat_tools: ToolRegistry) -> bool:
    """Before an LLM round: install gated tools when coordination is already live.

    Closes the one-beat gap where a coordination brief tells the CEO to call
    ``wait`` but the registry still lacks it (hint ahead of tool-surface).
    Same registration path as :func:`promote_coordination_surface_if_needed`.
    """
    return promote_coordination_surface_if_needed(chat_tools)


def promote_coordination_surface_if_needed(chat_tools: ToolRegistry) -> bool:
    """Mid-turn: add gated tools when coordination is live or replan is executable.

    Returns True when OpenAI tool defs must be refreshed.
    """
    delegate = chat_tools.get_optional("delegate")
    if delegate is None:
        return False

    supervised = getattr(delegate, "_supervised", None) is not None
    coord = _owns_coordination(delegate) and coordination_surface_active()
    if not coord and not supervised:
        return False

    from agentcore.runtime.coordination.tools import (
        CancelWorkerTool,
        QueueUserMessageTool,
        ResolveEscalationTool,
        UpdateSynthesisTool,
        WaitTool,
    )
    from agentcore.tools.builtin.replan import ReplanTool

    added: list[str] = []
    sink = getattr(delegate, "_sink", None)

    if chat_tools.get_optional("replan") is None:
        chat_tools.register(ReplanTool(delegate=delegate))  # type: ignore[arg-type]
        added.append("replan")

    if coord and sink is not None:
        if chat_tools.get_optional("wait") is None:
            chat_tools.register(WaitTool())
            added.append("wait")
        if chat_tools.get_optional("update_synthesis") is None:
            chat_tools.register(UpdateSynthesisTool(sink=sink))
            added.append("update_synthesis")
        if chat_tools.get_optional("cancel_worker") is None:
            chat_tools.register(CancelWorkerTool())
            added.append("cancel_worker")
        if chat_tools.get_optional("resolve_escalation") is None:
            chat_tools.register(ResolveEscalationTool())
            added.append("resolve_escalation")
        if chat_tools.get_optional("queue_user_message") is None:
            chat_tools.register(QueueUserMessageTool(sink=sink))
            added.append("queue_user_message")

    if added:
        logger.info(
            "ceo.tool_surface.promoted",
            added=added,
            coordination=coord,
            supervised=supervised,
        )
    return bool(added)


def observe_tools_offered(
    tools: ToolRegistry,
    *,
    scope: str,
    tool_defs: list[dict] | None = None,
) -> None:
    """COST-004: log tools-surface JSON size (observe-only; no SSE / API fields).

    ``scope``: ``ceo_turn`` (pipeline assemble) or ``worker_run`` (engine opening
    offer). ``total_chars`` / ``cjk_chars`` / ``per_tool`` are exact; the token
    cost is a band (:func:`_token_band`) because no tokenizer for the serving
    model is available here.
    """
    defs = tool_defs
    if defs is None:
        defs = tools.get_openai_definitions() if tools.count > 0 else []
    if not defs:
        logger.info(
            "cost.tools_offered",
            scope=scope,
            tool_count=0,
            total_chars=0,
            cjk_chars=0,
            approx_tokens_low=0,
            approx_tokens_high=0,
            per_tool={},
        )
        return
    per_tool: dict[str, int] = {}
    total = 0
    cjk = 0
    for d in defs:
        name = (d.get("function") or {}).get("name") or d.get("name") or "?"
        raw = json.dumps(d, ensure_ascii=False)
        per_tool[str(name)] = len(raw)
        total += len(raw)
        cjk += sum(1 for char in raw if _is_cjk(char))
    low, high = _token_band(cjk_chars=cjk, other_chars=total - cjk)
    logger.info(
        "cost.tools_offered",
        scope=scope,
        tool_count=len(defs),
        total_chars=total,
        cjk_chars=cjk,
        approx_tokens_low=low,
        approx_tokens_high=high,
        per_tool=per_tool,
    )


def measure_openai_tool_chars(schema: Any) -> int:
    """OpenAI-format JSON char length of one schema (tests / probes)."""
    return len(json.dumps(tool_schema_to_openai_format(schema), ensure_ascii=False))
