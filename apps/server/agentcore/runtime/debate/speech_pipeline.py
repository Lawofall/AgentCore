"""辩手两阶段发言管线：ReAct 检索 → 证据笔记；干净上下文成稿 → 发言全文。

根因：旧契约把「ReAct 最后一条无工具 stop」当正式发言，模型在收工语境混入过程句与
案情复述。本管线把检索交付物改为笔记，成稿在无工具、无检索往返的干净上下文中完成。

→ 见设计: docs/03-AI核心/辩论编排设计.md §4-2.5
"""

from __future__ import annotations

from collections.abc import Callable, Collection
from typing import TYPE_CHECKING

from agentcore.core.logging import get_logger
from agentcore.llm.model_selection import SelectedCall, build_selected_request
from agentcore.llm.profiles import ProfileParams
from agentcore.llm.provider.protocol import LLMMessage, LLMProvider, TokenUsage
from agentcore.runtime.approvals import ApprovalGate
from agentcore.runtime.debate.evidence_guard import (
    demote_verified_tags,
    format_evidence_ledger_steer,
    invalid_verified_tags,
)
from agentcore.runtime.debate.evidence_ledger import (
    extract_ledger_ids,
    format_evidence_ledger_hint,
)
from agentcore.runtime.engine import ReactLoopOut, react_loop
from agentcore.runtime.engine.stream import stream_llm_round
from agentcore.runtime.events import (
    EventSink,
    FinishReason,
    run_output_delta,
    run_output_reset,
    run_reasoning_delta,
    run_tool_progress,
)
from agentcore.tools.protocol import ToolContext
from agentcore.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from agentcore.runtime.debate.evidence_ledger import EvidenceLedger

logger = get_logger(__name__)

_EMPTY_NOTES_PLACEHOLDER = "（本阶段无补充检索笔记；请仅依据发言任务与已知材料成稿。）"


def research_continuation_message(feedback: str) -> LLMMessage:
    """续轮检索阶段追加的 user 消息（交付物 = 证据笔记，非发言）。"""
    return LLMMessage(
        role="user",
        content=(
            f"## 检索与笔记指令\n{feedback}\n\n"
            "本阶段只交付【证据笔记】（不是正式发言）。"
            "需要时用工具取证；完成后直接输出笔记正文。"
        ),
    )


def build_draft_user(
    draft_brief: str,
    evidence_notes: str,
    *,
    guard_steer: str = "",
    ledger_hint: str = "",
) -> str:
    """成稿调用的 user 正文：任务 + 证据笔记 + 可选台账 id 表（可选回炉提示）。"""
    notes = (evidence_notes or "").strip() or _EMPTY_NOTES_PLACEHOLDER
    brief = (draft_brief or "").strip()
    text = (
        f"## 发言任务\n{brief}\n\n"
        f"## 证据笔记\n{notes}\n\n"
    )
    hint = (ledger_hint or "").strip()
    if hint:
        text = f"{text}## 证据台账\n{hint}\n\n"
    text = (
        f"{text}"
        "请根据发言任务与证据笔记，直接输出【正式发言】全文（自由 markdown）。"
        "证据笔记只作素材，勿整段粘贴笔记标题或复述案件简介；禁止过程叙述与收工汇报。"
    )
    steer = (guard_steer or "").strip()
    if steer:
        text = f"{text}\n\n{steer}"
    return text


async def research_then_draft(
    messages: list[LLMMessage],
    *,
    llm: LLMProvider,
    tools: ToolRegistry,
    sink: EventSink,
    tool_ctx: ToolContext,
    profile: ProfileParams,
    turn_model: str,
    allowed_tools: list[str] | None,
    run_id: str,
    agent_id: str,
    citation_sink: list[dict],
    approval_gate: ApprovalGate | None,
    draft_system: str,
    draft_brief: str,
    allow_research: bool = True,
    usage_sink: list[TokenUsage] | None = None,
    on_round_begin: Callable[[], list[LLMMessage]] | None = None,
    streamed_content: list[str] | None = None,
    gate_escalation_sink: list[dict] | None = None,
    token_budget: int = 0,
    finish_override_sink: list[FinishReason] | None = None,
    cutoff_reason_sink: list[str] | None = None,
    evidence_ledger: EvidenceLedger | None = None,
    side_key: str = "",
    check_evidence_ledger: bool = False,
    allowed_ledger_ids: Collection[str] | None = None,
) -> tuple[str, str, TokenUsage, int]:
    """两阶段：可选 ReAct 检索产笔记（不进 run 卡片正文）→ 干净成稿（流式进卡片）。

    ``messages`` 就地延展：检索工具往返保留；成稿发言以 assistant 消息追加（供续轮记忆）。
    笔记正文不追加为产品消息——只经 journal 的 llm_call fact 可见。

    检索期经 ``evidence_ledger.research_proxy()`` 把工具命中写入共享核并注解 ``#rN``，
    笔记行尾绑定 id；随后 ``commit_research`` 只提交 deep_read + 笔记引用子集上 wire。
    成稿闸：``【已核实·#rN】`` 的 id 须 ∈ 本方笔记引用集 ∪ ``allowed_ledger_ids``
    （结辩传入历轮并集）；违规回炉一次，再违规降级【待核实·推断】。
    """
    total_usage = TokenUsage()
    total_reasoning_parts: list[str] = []
    total_rounds = 0
    notes = ""

    # 无可用取证工具 → 退化为单次成稿（结辩 allow_research=False / 空 allow-list /
    # 注册表未挂 web_search 等）。``allowed_tools is None`` = 不限制，以注册表是否非空为准。
    if not allow_research or allowed_tools is not None and len(allowed_tools) == 0:
        tools_available = False
    elif allowed_tools is None:
        tools_available = any(True for _ in tools.list_all())
    else:
        tools_available = any(tools.get_optional(n) is not None for n in allowed_tools)

    if tools_available:
        # 检索阶段：工具进度 / 思考可直播；正文不进 run_output_delta（避免笔记冒充发言）。
        # 场级核经 proxy 注解 #rN（不发射回合 evidence_ledger SSE）。
        research_ledger = (
            evidence_ledger.research_proxy() if evidence_ledger is not None else None
        )
        notes, research_reasoning, research_usage, research_rounds = await react_loop(
            messages=messages,
            llm=llm,
            tools=tools,
            sink=sink,
            tool_context=tool_ctx,
            profile=profile,
            turn_model=turn_model,
            allowed_tool_names=allowed_tools,
            on_content=lambda _delta: None,
            on_reasoning=lambda d: sink.emit(run_reasoning_delta(run_id, agent_id, d)),
            on_tool_progress=lambda tool, chars: sink.emit(
                run_tool_progress(run_id, agent_id, tool, chars)
            ),
            on_reset=None,
            raise_on_error=True,
            annotate_citations=False,
            turn_evidence_ledger=research_ledger,  # type: ignore[arg-type]
            ledger_registrant=side_key or "debater",
            approval_gate=approval_gate,
            out=ReactLoopOut(
                citations=citation_sink,
                usage=usage_sink,
                finish_override=finish_override_sink,
                gate_escalations=gate_escalation_sink,
                cutoff_reasons=cutoff_reason_sink,
            ),
            on_round_begin=on_round_begin,
            run_id=run_id,
            role="worker",
            deliverable_only=True,
            token_budget=token_budget,
        )
        total_usage = total_usage + research_usage
        total_rounds += research_rounds
        if research_reasoning:
            total_reasoning_parts.append(research_reasoning)
        logger.info(
            "debate.speech.research_done",
            run_id=run_id,
            notes_chars=len((notes or "").strip()),
            rounds=research_rounds,
        )

    note_cited = extract_ledger_ids(notes)
    if evidence_ledger is not None:
        newly = evidence_ledger.commit_research(note_cited_ids=note_cited)
        if newly:
            logger.info(
                "debate.speech.ledger_commit",
                run_id=run_id,
                committed=sorted(newly),
                note_cited=sorted(note_cited),
            )

    # 闸 / hint 基准：本方笔记引用 ∪ 显式允许集（结辩 = 历轮并集）。
    known = frozenset(note_cited) | frozenset(allowed_ledger_ids or ())
    ledger_hint = (
        format_evidence_ledger_hint(evidence_ledger, ids=known)
        if evidence_ledger is not None and known
        else ""
    )

    def _on_draft_content(delta: str) -> None:
        sink.emit(run_output_delta(run_id, agent_id, delta))
        if streamed_content is not None:
            streamed_content.append(delta)

    speech, draft_reasoning, draft_usage = await _stream_draft(
        llm=llm,
        profile=profile,
        turn_model=turn_model,
        draft_system=draft_system,
        draft_brief=draft_brief,
        evidence_notes=notes,
        ledger_hint=ledger_hint,
        on_content=_on_draft_content,
        on_reasoning=lambda d: sink.emit(run_reasoning_delta(run_id, agent_id, d)),
    )
    total_usage = total_usage + draft_usage
    total_rounds += 1
    if draft_reasoning:
        total_reasoning_parts.append(draft_reasoning)

    # ── 成稿证据台账 id 闸（全 beat：立论 / 续辩 / 质询 / 结辩） ──
    if check_evidence_ledger and evidence_ledger is not None:
        def _guard_issues(text: str) -> tuple[list[str], str]:
            bad = invalid_verified_tags(text, known)
            if bad:
                return bad, format_evidence_ledger_steer(bad)
            return [], ""

        bad_tags, steer = _guard_issues(speech)
        if bad_tags:
            logger.info(
                "debate.speech.evidence_guard_rework",
                run_id=run_id,
                guard="evidence_ledger",
                tags=bad_tags,
            )
            sink.emit(run_output_reset(run_id, agent_id, "finish_guard"))
            if streamed_content is not None:
                streamed_content.clear()
            speech, retry_reasoning, retry_usage = await _stream_draft(
                llm=llm,
                profile=profile,
                turn_model=turn_model,
                draft_system=draft_system,
                draft_brief=draft_brief,
                evidence_notes=notes,
                ledger_hint=ledger_hint,
                guard_steer=steer,
                on_content=_on_draft_content,
                on_reasoning=lambda d: sink.emit(
                    run_reasoning_delta(run_id, agent_id, d)
                ),
            )
            total_usage = total_usage + retry_usage
            total_rounds += 1
            if retry_reasoning:
                total_reasoning_parts.append(retry_reasoning)
            still_tags, _ = _guard_issues(speech)
            if still_tags:
                demoted = demote_verified_tags(speech, still_tags)
                logger.warning(
                    "debate.speech.evidence_guard_demote",
                    run_id=run_id,
                    guard="evidence_ledger",
                    tags=still_tags,
                )
                if demoted != speech:
                    sink.emit(run_output_reset(run_id, agent_id, "finish_guard"))
                    if streamed_content is not None:
                        streamed_content.clear()
                        streamed_content.append(demoted)
                    sink.emit(run_output_delta(run_id, agent_id, demoted))
                    speech = demoted

    if usage_sink is not None:
        usage_sink.clear()
        usage_sink.append(total_usage)

    messages.append(LLMMessage(role="assistant", content=speech))
    return speech, "".join(total_reasoning_parts), total_usage, total_rounds


async def _stream_draft(
    *,
    llm: LLMProvider,
    profile: ProfileParams,
    turn_model: str,
    draft_system: str,
    draft_brief: str,
    evidence_notes: str,
    on_content: Callable[[str], None],
    on_reasoning: Callable[[str], None],
    guard_steer: str = "",
    ledger_hint: str = "",
) -> tuple[str, str, TokenUsage]:
    """无工具、干净上下文的成稿流式调用。"""
    draft_messages = [
        LLMMessage(role="system", content=draft_system),
        LLMMessage(
            role="user",
            content=build_draft_user(
                draft_brief,
                evidence_notes,
                guard_steer=guard_steer,
                ledger_hint=ledger_hint,
            ),
        ),
    ]
    request = build_selected_request(
        SelectedCall(model=turn_model, profile=profile),
        draft_messages,
        tools=None,
        tool_choice="none",
    )
    # stream_llm_round 类型标注 OpenAICompatibleProvider；运行时协议兼容。
    streamed = await stream_llm_round(
        llm,  # type: ignore[arg-type]
        request,
        on_content,
        on_reasoning,
        on_tool_progress=None,
        on_reset=None,
    )
    usage = streamed.usage or TokenUsage()
    return (streamed.content or "").strip(), streamed.reasoning or "", usage
