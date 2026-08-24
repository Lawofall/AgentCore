"""handoff — a worker's structured 交接简报 + finish signal (完工交接简报单一源).

Worker-only, terminal. Semantics: 简报 = 【接力契约 + 增量交代】. A delegated worker calls
``handoff`` ONCE, in the SAME turn as its finished deliverable, to submit a STRUCTURED brief.
Topology splits the brief's job (identity copy; this schema stays shared):
- Nodes with dependents, or leaves that may land files: brief stays conclusion-bearing
  (CEO / 下游 may only see the brief).
- ``form=prose`` leaves: brief is relay status only；结论在正文 (CEO reads the body).
可选 ``motion_card``（发现核心争议命题时建议开辩的唯一契约载体）。

Topology (prompt + this description say the same thing; engine gate unchanged):
- Nodes with downstream dependents **must** handoff — downstream relays on the brief
  (executor injects one correction shot; still missing → degraded synth).
- Leaf nodes (no dependents): call only when there is incremental briefing beyond the
  body (assumptions / risks / next steps / files list); a short self-evident deliverable
  may finish with a plain no-tool answer — no debrief, deliverable stands alone.

Why a tool, not a「## 交接简报」markdown section (its former form): the brief is structured DATA
for READERS (下游依赖注入 / CEO 综述 / run-detail 卡), so it travels in a structured channel and is
read straight off the call's arguments
(:func:`~agentcore.runtime.runs.serialize.debrief_from_transcript`),
never parsed back out of prose. The deliverable stays the worker's streamed ``content``; this tool
carries ONLY the brief and signals the run is done — so the run-detail「输出」(the deliverable) and
「交接简报」(this brief) can never overlap the way a retained-in-prose section did.

Terminal by design (``ToolEffect.HANDOFF``): the worker writes its deliverable as content and calls
``handoff`` in the same round to finish. A terminal effect KEEPS that round's content (only prose
before a NON-terminal tool is rolled back as narration, Fork-B) — so ``content`` == the deliverable
and the brief rides the tool args. ``final_text`` is normally empty (deliverable already streamed);
when the round has 0 body chars but a non-empty brief that meets the upstream floor, ``final_text``
carries the promoted brief so downstream still gets a readable product.

Wired into the delegated worker toolset (``build_worker_registry``) and NOT into
``build_builtin_registry`` — so it never reaches the CEO's own toolset (``build_ceo_tool_registry``
derives the CEO subset from the builtins) or the read-only ``GET /tools`` capability catalog,
mirroring how ``escalate`` / ``post_note`` are wired in only where they belong.
"""

from __future__ import annotations

import contextlib
import re
from typing import Any

from agentcore.core.logging import get_logger
from agentcore.core.types import ToolApproval, ToolCategory, ToolEffect
from agentcore.runtime.runs.constants import HANDOFF_TOOL_NAME
from agentcore.tools.builtin.debate.schema import STANCE_MAX_CHARS
from agentcore.tools.builtin.motion_card import parse_motion_card
from agentcore.tools.protocol import ToolContext, ToolResult, ToolSchema
from agentcore.tools.registration import (
    AUDIENCE_WORKER_ONLY,
    ToolRegistration,
    ToolSurface,
)

logger = get_logger(__name__)

# 建议开辩意图：收窄在「建议/推荐开辩」短语，避免误拦仅提及辩论事实的正文
# （如法律报告「一审辩论过程」）。只扫建议语境字段（summary / key_points / next_steps）。
_DEBATE_SUGGEST_RE = re.compile(
    r"(?:"
    r"建议(?:发起)?(?:一场)?(?:正反)?(?:对抗)?辩论"
    r"|建议开辩"
    r"|推荐(?:发起)?(?:一场)?(?:正反)?(?:对抗)?辩论"
    r"|推荐开辩"
    r"|宜(?:发起)?(?:一场)?(?:正反)?辩论"
    r"|宜开辩"
    r"|should\s+(?:open\s+a\s+)?debate"
    r"|recommend(?:s|ed)?\s+(?:a\s+)?debate"
    r")",
    re.IGNORECASE,
)

_MOTION_CARD_MISSING_TIP = (
    "交接内容已建议开辩/发起辩论，但未携带合规 `motion_card`。"
    "请把开辩建议写成 handoff 的结构化 `motion_card` 字段后重试"
    "（正文 markdown 表、key_points 散文【不能】代替）。"
    "最小示例："
    '{"motion":"争议命题","sides":[{"key":"pro","name":"正方","stance":"支持该主张"},'
    '{"key":"con","name":"反方","stance":"反对该主张"}],'
    '"fact_pointers":[],"rationale":"继续调研无法消解对立，须对抗交锋"}。'
)


def _body_chars(context: ToolContext) -> int:
    """Deliverable prose length for this round (0 when unset / unknown)."""
    n = context.round_content_chars
    return int(n) if isinstance(n, int) and n >= 0 else 0


def _suggestion_context(arguments: dict[str, Any]) -> str:
    """拼接建议语境字段（不含交付正文 / assumptions 事实叙述）。"""
    parts: list[str] = [str(arguments.get("summary") or "")]
    key_points = arguments.get("key_points")
    if isinstance(key_points, list):
        parts.extend(str(p) for p in key_points)
    elif key_points is not None:
        parts.append(str(key_points))
    parts.append(str(arguments.get("next_steps") or ""))
    return "\n".join(parts)


def claims_debate_suggestion(arguments: dict[str, Any]) -> bool:
    """建议语境字段是否声称「建议/推荐开辩」。

    仅扫 summary / key_points / next_steps；提及辩论事实（如「一审辩论过程」）不触发。
    """
    return bool(_DEBATE_SUGGEST_RE.search(_suggestion_context(arguments)))


class HandoffTool:
    """The worker's structured 交接简报 + finish primitive (terminal).

    Stateless: the call returns a terminal ``ToolResult`` that ends the run; the brief itself is
    read off THIS call's arguments by the executor's transcript harvest, so the tool owns only the
    done-signal + a short ack (the executor owns event shape / RunState, 引擎纯化).
    """

    registration = ToolRegistration(
        surface=ToolSurface.WORKER_ONLY,
        audience=AUDIENCE_WORKER_ONLY,
    )

    @property
    def schema(self) -> ToolSchema:
        # Schema layer (工具面瘦身): topology one-liners + field cues. Field
        # checklist / escalate-vs-next_steps live in identity; maxLength on schema.
        return ToolSchema(
            name=HANDOFF_TOOL_NAME,
            description=(
                "提交交接简报并收尾。简报=【接力契约 + 增量交代】（给主管/下游，不是正文复述）。"
                "有下游：完成后必须调用。无下游：有工具活动或较长交付须交短摘要；短答自明可省。"
                "先写完交付再同一轮调用。form=files：summary 须含路径。"
                "建议开辩时必须填 motion_card 对象（正文表不能代替）。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "summary": {
                        "type": "string",
                        "maxLength": 300,
                        "description": (
                            "结论：一句话说清你这次做出了什么 / 核心结论（短句，勿贴长文）。"
                        ),
                    },
                    "key_points": {
                        "type": "array",
                        "maxItems": 4,
                        "items": {"type": "string", "maxLength": 120},
                        "description": (
                            "关键要点：下游或主管最该知道的 2-4 条短句（具体数字 / 文件路径 / "
                            "关键决定，别空泛；勿塞长文）。"
                        ),
                    },
                    "assumptions": {
                        "type": "string",
                        "maxLength": 300,
                        "description": (
                            "关键假设：信息不足时你采用的关键假设（没有就省略此条；短述即可）。"
                        ),
                    },
                    "next_steps": {
                        "type": "string",
                        "maxLength": 300,
                        "description": (
                            "建议下一步：基于你这一环的发现，团队 / 用户接下来值得考虑做什么"
                            "（没有就省略此条；短述即可）。"
                        ),
                    },
                    "motion_card": {
                        "type": "object",
                        "description": "建议开辩时必填的命题卡对象。省略=不开辩。",
                        "properties": {
                            "motion": {
                                "type": "string",
                                "description": "争议命题（可直接作 debate.motion）。",
                            },
                            "sides": {
                                "type": "array",
                                "description": (
                                    f"参与方（≥2）；stance 一句话（≤{STANCE_MAX_CHARS} 字）。"
                                ),
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "key": {"type": "string", "description": "唯一英文短词。"},
                                        "name": {"type": "string", "description": "展示立场名。"},
                                        "stance": {
                                            "type": "string",
                                            "maxLength": STANCE_MAX_CHARS,
                                            "description": "一句话结论倾向；禁换行/分号/论证展开。",
                                        },
                                    },
                                    "required": ["key", "name", "stance"],
                                },
                            },
                            "fact_pointers": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "事实指针（#rN / 路径 / URL）；不装论点。",
                            },
                            "rationale": {
                                "type": "string",
                                "description": "为何必须对抗交锋而非继续调研。",
                            },
                            "form": {
                                "type": "string",
                                "enum": ["debate", "red_team", "roundtable"],
                                "description": "辩论形态；默认 debate。",
                            },
                        },
                        "required": ["motion", "sides", "fact_pointers", "rationale"],
                    },
                },
                "required": ["summary"],
            },
            category=ToolCategory.ORCHESTRATION,
            approval=ToolApproval.NEVER,
        )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        from agentcore.runtime.engine.tool_protocol_sanitize import (
            sanitize_protocol_text,
            sanitize_tool_args,
        )

        # Defense in depth: strip vendor protocol tags from brief fields (tool_exec
        # already cleans before invoke; harvest path may still see raw transcript).
        cleaned = sanitize_tool_args(arguments) if isinstance(arguments, dict) else arguments
        if isinstance(cleaned, dict):
            arguments = cleaned
            # key_points: tolerate markdown bullet string / JSON-array-as-string via the
            # shared coerce path (ask_user.schema); truly bad JSON still fails at parse.
            if "key_points" in arguments:
                from agentcore.tools.builtin.ask_user.schema import (
                    ListArgError,
                    coerce_list_arg,
                )

                with contextlib.suppress(ListArgError):
                    arguments["key_points"] = [
                        str(p).strip()
                        for p in coerce_list_arg(
                            arguments.get("key_points"),
                            field="key_points",
                            allow_markdown_bullets=True,
                        )
                        if str(p).strip()
                    ]
        summary = sanitize_protocol_text(str(arguments.get("summary") or "")).strip()
        card, card_err = parse_motion_card(arguments.get("motion_card"))
        if card_err:
            logger.info(
                "worker.handoff",
                run_id=context.run_id,
                has_summary=bool(summary),
                chars=len(summary),
                body_chars=_body_chars(context),
                has_motion_card=False,
                rejected="motion_card",
            )
            return ToolResult(
                tool_call_id="",
                success=False,
                output="",
                error=card_err,
            )
        # 建议开辩却无合规卡 → 拒收（success=False 不触发交接），与卡字段校验错误可区分。
        if card is None and claims_debate_suggestion(arguments):
            logger.info(
                "worker.handoff",
                run_id=context.run_id,
                has_summary=bool(summary),
                chars=len(summary),
                body_chars=_body_chars(context),
                has_motion_card=False,
                rejected="debate_suggest_without_motion_card",
            )
            return ToolResult(
                tool_call_id="",
                success=False,
                output="",
                error=_MOTION_CARD_MISSING_TIP,
            )
        # 空交不再硬拒（实测误伤多，行业也不拦「没写出东西」）。
        # 正文空时仍可把 summary 升格成下游可读正文，有真实正文则不覆盖。
        promoted_body = ""
        body_chars = _body_chars(context)
        if body_chars == 0 and summary:
            from agentcore.runtime.runs.research_quality import (
                brief_may_satisfy_body_floor,
                promote_brief_to_deliverable,
            )

            form = context.handoff_deliverable_form
            if brief_may_satisfy_body_floor(deliverable_form=form):
                promoted_body = promote_brief_to_deliverable(
                    summary, arguments.get("key_points")
                )
        logger.info(
            "worker.handoff",
            run_id=context.run_id,
            has_summary=bool(summary),
            chars=len(summary),
            body_chars=body_chars,
            has_motion_card=card is not None,
            promoted_body=bool(promoted_body),
        )
        # Terminal (HANDOFF): 有真实正文时 final_text 为空（交付已在 streamed content）。
        # 同轮 0 字且简报升格成功时，final_text=候选正文，供引擎并入下游可读产出。
        # The structured brief is still read off THIS call's arguments by
        # serialize.debrief_from_transcript.
        return ToolResult(
            tool_call_id="",
            success=True,
            output="已收尾并提交交接简报。",
            effect=ToolEffect.HANDOFF,
            final_text=promoted_body,
        )
