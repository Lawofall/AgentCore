"""CEO per-turn user envelope — volatile facts outside ``role: system``.

Frozen constitution / identity / on-demand catalog stay in ``messages[0]``.
If this turn's composed system differs from that node 0, DeepSeek in-history
appends the changed ``<设定>`` / ``<按需目录>`` (or the full new string when the
rest drifted) as ``role: system`` after history and before the envelope
(``usage.in_history_system`` replay). Date, workspace (+ CEO file
index), scene gates, attachments, and table ride a synthetic
user message fenced with ``[系统提示]``. A new envelope that matches the last
one already in the window is omitted. The envelope is snapshotted on
``turn_started`` and stamped onto the prompting user row
(``usage.turn_envelope``) so later turns replay it in order. The LLM window
is append-only: prior envelopes stay; this turn appends a new envelope only
when it differs, plus the new user utterance. UI bubbles still show the original user text.
Workers use the same fence for date / workspace / attachments (opening user
tail, not ``role: system``) so sibling runs share a frozen system prefix.
Do not copy the CEO file index onto workers. Resume restamp of worker facts
appends ``[系统提示]`` after history and never rewrites the emitted
``role: system``; debate stays out. Tool-failure facts stay in receipts /
synthesis, not ``role: system``.
See docs/03-AI核心/执行引擎架构设计.md §七.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from agentcore.config import settings
from agentcore.llm.provider.protocol import (
    LLMMessage,
    ToolCall,
    ToolCallFunction,
    llm_content_text,
)
from agentcore.runtime.context import ContextAssembler, SectionOrder
from agentcore.runtime.context.workspace_overview import attach_workspace_file_index
from agentcore.runtime.resolve.prompt.base import render_runtime_date_block
from agentcore.runtime.resolve.prompt.ceo_core import _attachment_material_block

TURN_ENVELOPE_FENCE = "[系统提示]"
# Stamped on the prompting user row (``messages.usage``). REST UsageBreakdown
# strips unknown keys — the blob must not become bubble text.
TURN_ENVELOPE_USAGE_KEY = "turn_envelope"
IN_HISTORY_SYSTEM_USAGE_KEY = "in_history_system"
# Folded history tag so ``drop_trailing_user_turn`` can pop this turn's extra
# system without eating a prior failure note.
IN_HISTORY_SYSTEM_ORIGIN = "in_history_system"


def is_turn_envelope_content(content: str | None) -> bool:
    """True when ``content`` is an engine ``[系统提示]`` envelope, not user prose."""
    return (content or "").lstrip().startswith(TURN_ENVELOPE_FENCE)


def strip_turn_envelope_fence(text: str) -> str:
    """Drop the leading fence line so XML tags remain for the context catalog."""
    raw = (text or "").strip()
    if not raw:
        return ""
    if raw.startswith(TURN_ENVELOPE_FENCE):
        rest = raw[len(TURN_ENVELOPE_FENCE) :].lstrip("\n")
        return rest.strip()
    return raw


def visualization_system_body(system: str, envelope: str) -> str:
    """Concatenate envelope XML into the system ContextBlock (no new SSE channel).

    The LLM still sees the envelope as a separate user message; the desktop
    「收到的上下文」reader indexes ``<工作区>`` from ``channel=system``.
    """
    xml = strip_turn_envelope_fence(envelope)
    frozen = (system or "").rstrip()
    if not xml:
        return frozen
    if not frozen:
        return xml
    return f"{frozen}\n\n{xml}"


def history_row_to_llm_message(msg: dict) -> LLMMessage:
    """One chat-context row → ``LLMMessage``, including tool rounds."""
    tool_calls: list[ToolCall] | None = None
    raw_calls = msg.get("tool_calls")
    if isinstance(raw_calls, list) and raw_calls:
        parsed: list[ToolCall] = []
        for tc in raw_calls:
            if not isinstance(tc, dict):
                continue
            function = tc.get("function")
            fn: dict[str, Any] = function if isinstance(function, dict) else {}
            parsed.append(
                ToolCall(
                    id=str(tc.get("id") or ""),
                    type="function",
                    function=ToolCallFunction(
                        name=str(fn.get("name") or ""),
                        arguments=str(fn.get("arguments") or ""),
                    ),
                )
            )
        tool_calls = parsed or None
    reasoning = msg.get("reasoning_content")
    tcid = msg.get("tool_call_id")
    return LLMMessage(
        role=msg["role"],
        content=msg.get("content"),
        tool_calls=tool_calls,
        tool_call_id=str(tcid) if isinstance(tcid, str) and tcid else None,
        reasoning_content=reasoning if isinstance(reasoning, str) and reasoning else None,
    )


def opening_ceo_messages(
    *,
    system_prompt: str,
    history: Sequence[LLMMessage] | Sequence[dict] | None,
    turn_envelope: str,
    user_content: str | list,
    in_history_system: str = "",
) -> list[LLMMessage]:
    """Captain opening window: node0 system → history → in-history system → envelope → user.

    Fold and the live executor must share this helper so pause conformance
    stays byte-identical. ``in_history_system`` is DeepSeek's extra system when
    the live catalog/rules differ from frozen ``messages[0]`` (delta blocks, or
    the full compose when the rest drifted). An envelope that is byte-identical
    to the last ``[系统提示]`` already in the window is omitted — the prior
    snapshot stays the complete environment truth.
    """
    messages = [LLMMessage(role="system", content=system_prompt)]
    for msg in history or []:
        if isinstance(msg, LLMMessage):
            messages.append(msg)
        else:
            messages.append(history_row_to_llm_message(msg))
    extra = (in_history_system or "").strip()
    if extra and extra != (system_prompt or "").strip():
        last_extra = ""
        for msg in reversed(messages[1:]):
            if msg.role != "system":
                continue
            text = llm_content_text(msg.content).strip()
            if text.startswith("（系统注记"):
                continue
            last_extra = text
            break
        if last_extra != extra:
            messages.append(LLMMessage(role="system", content=extra))
    env = (turn_envelope or "").strip()
    if env and _last_envelope_text(messages) == env:
        env = ""
    if env:
        messages.append(LLMMessage(role="user", content=env))
    messages.append(LLMMessage(role="user", content=user_content))
    return messages


def _last_envelope_text(messages: Sequence[LLMMessage]) -> str:
    """Most recent ``[系统提示]`` envelope already in the window, or empty."""
    for msg in reversed(messages):
        raw = msg.content
        text = raw.strip() if isinstance(raw, str) else ""
        if is_turn_envelope_content(text):
            return text
    return ""


def render_ceo_turn_envelope(
    *,
    workspace_context: str | None = None,
    workspace_file_index: str | None = None,
    table_context: str | None = None,
    attachment_material: bool = False,
    attachment_context: str = "",
    soft_cap: int | None = None,
    include_runtime: bool = True,
) -> str:
    """Render the CEO's ephemeral ``[系统提示]`` user message (empty → ``""``)."""
    material_block = _attachment_material_block(attachment_material)
    cap = settings.prompt_budget_char_soft_cap if soft_cap is None else soft_cap
    body = (
        ContextAssembler()
        .add(
            "runtime_context",
            render_runtime_date_block() if include_runtime else None,
            SectionOrder.RUNTIME_CONTEXT,
        )
        .add(
            "workspace_facts",
            attach_workspace_file_index(
                workspace_context or "",
                workspace_file_index or "",
            )
            or None,
            SectionOrder.WORKSPACE_FACTS,
        )
        .add("attachment_material", material_block, SectionOrder.WORKING_SET)
        .add("attachment_context", attachment_context, SectionOrder.ATTACHMENT)
        .add("table_context", table_context, SectionOrder.TABLE_FACTS)
        .observe(scope="ceo_envelope", soft_cap=cap)
        .render()
    )
    text = (body or "").strip()
    if not text:
        return ""
    return f"{TURN_ENVELOPE_FENCE}\n{text}"


def render_worker_turn_envelope(
    *,
    workspace_context: str | None = None,
    attachment_context: str | None = None,
    include_runtime: bool = True,
) -> str:
    """Worker opening ``[系统提示]`` (no CEO file index / table)."""
    cap = settings.prompt_budget_char_soft_cap
    body = (
        ContextAssembler()
        .add(
            "runtime_context",
            render_runtime_date_block() if include_runtime else None,
            SectionOrder.RUNTIME_CONTEXT,
        )
        .add(
            "workspace_facts",
            (workspace_context or "").strip() or None,
            SectionOrder.WORKSPACE_FACTS,
        )
        .add(
            "attachment_context",
            (attachment_context or "").strip() or None,
            SectionOrder.ATTACHMENT,
        )
        .observe(scope="worker_envelope", soft_cap=cap)
        .render()
    )
    text = (body or "").strip()
    if not text:
        return ""
    return f"{TURN_ENVELOPE_FENCE}\n{text}"
