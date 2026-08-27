"""批 D1 · 证人模式：幕1 透镜调研员以证人身份进入幕2 辩论。

机制要点：
- 开赛探测本会话 roster 中的幕1 透镜 ``RunSession``；无则整场无证人、零行为变化。
- 图锚：在辩论幕内声明证人席位节点；答问用席位 session（fork 自透镜 transcript），
  ``continues_run_id`` 指席位根（辩论幕内），避免把辩论拍挂到幕1 透镜节点。
- 点名：主持人质询 beat 内 LLM 判定（只问事实性问题）；失败/超时不阻塞主流程。
- 续写：窄 ``continue_run``；工具面默认全开，只读纪律靠提示自觉；不递增透镜
  ``recall_count``（豁免 CEO 续派额度）。
- 台账：答问登记进场级 ``EvidenceLedger``（``side_key=witness:{key}``）。
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from agentcore.core.logging import get_logger
from agentcore.runtime.costing import ROLE_ARENA
from agentcore.runtime.debate.cross_exam_parse import (
    build_cross_exam_exchanges,
    parse_cross_exam_response,
)
from agentcore.runtime.debate.types import (
    CrossExamQa,
    DebateConfig,
    SideTurn,
    WitnessExamExchange,
    WitnessSeatInfo,
)

if TYPE_CHECKING:
    from agentcore.runtime.debate.moderator_common import CompleteJson
    from agentcore.runtime.runs.session import RunSession
    from agentcore.runtime.sessions import SessionStore
    from agentcore.tools.builtin.debate.tool import DebateTool

logger = get_logger(__name__)

# 透镜 run_id：playbook ``lens_crosscheck`` 铸 ``lens_0``…``lens_N``。
_LENS_RUN_ID_RE = re.compile(r"^lens_\d+$")
# 角色名启发式（兼容非标准 id 的透镜）：``法律视角`` / ``品牌商业视角``。
_LENS_ROLE_RE = re.compile(r".+视角$")

_WITNESS_EXAM_SYSTEM = (
    "你是一场结构化辩论的主持人，现在决定是否需要【传唤证人】澄清事实。"
    "证人来自幕1 多视角调研（亲手查过材料），不占辩席、不写辩词，只回答【事实性问题】——"
    "例如时间线、主体关系、文件/条款原文、已公开数据、来源出处。禁止问立场、策略、价值判断、"
    "或「你站哪边」。不需要证人时输出空对象。严格只输出要求的 JSON。"
)

WITNESS_GROUP = "debate:witness"
WITNESS_SIDE_KEY_PREFIX = "witness:"


@dataclass
class WitnessSeat:
    """本场可用证人：辩论幕内席位 + 自透镜 fork 的可续写 session。"""

    key: str
    lens_run_id: str
    lens_label: str
    seat_run_id: str
    session: RunSession
    origin_act_label: str = "幕1"

    @property
    def display_name(self) -> str:
        return f"证人·{self.lens_label}"

    @property
    def origin_caption(self) -> str:
        return f"来自{self.origin_act_label}·{self.lens_label}"

    def to_wire(self) -> dict[str, str]:
        return {
            "key": self.key,
            "name": self.display_name,
            "lens_run_id": self.lens_run_id,
            "seat_run_id": self.seat_run_id,
            "lens_label": self.lens_label,
            "origin_caption": self.origin_caption,
        }


def is_lens_session(session: RunSession) -> bool:
    """启发式：幕1 多透镜调研员 session（非汇总员）。"""
    rid = (session.run_id or "").strip()
    if _LENS_RUN_ID_RE.match(rid):
        return True
    role = (session.spec.role or "").strip()
    return bool(_LENS_ROLE_RE.match(role) and "汇总" not in role)


def lens_label_from_session(session: RunSession) -> str:
    """展示用透镜名：优先角色去「视角」后缀，否则用 run_id。"""
    role = (session.spec.role or "").strip()
    if role.endswith("视角"):
        return role[: -len("视角")] or role
    if role:
        return role
    return session.run_id


def probe_witness_sessions(
    store: SessionStore | None,
    *,
    loader_hits: Sequence[RunSession] = (),
) -> list[RunSession]:
    """开赛机制探测：本会话有无可续写的幕1 透镜 session。

    无 store / 无透镜 → 空列表（整场无证人）。``loader_hits`` 供测试注入落盘命中。
    """
    found: dict[str, RunSession] = {}
    if store is not None:
        for session in store.list_sessions():
            if is_lens_session(session) and session.transcript:
                found[session.run_id] = session
    for session in loader_hits:
        if is_lens_session(session) and session.transcript:
            found.setdefault(session.run_id, session)
    # 稳定序：按 lens_N 数字，其余按 run_id。
    def _sort_key(s: RunSession) -> tuple[int, str]:
        m = _LENS_RUN_ID_RE.match(s.run_id or "")
        if m:
            return (int(s.run_id.split("_", 1)[1]), s.run_id)
        return (10_000, s.run_id)

    return sorted(found.values(), key=_sort_key)


def fork_witness_session(
    lens: RunSession,
    *,
    seat_run_id: str,
    moderator_run_id: str,
    depth: int,
) -> RunSession:
    """自透镜 fork 席位 session：共享现场记忆，独立 recall；工具面不收窄。"""
    from agentcore.runtime.runs.session import RunSession

    spec = replace(
        lens.spec,
        run_id=seat_run_id,
        agent_id=seat_run_id,
        role=f"证人·{lens_label_from_session(lens)}",
        task=(
            f"以证人身份回答主持人的事实性问题"
            f"（{lens_label_from_session(lens)}透镜·幕1 调研现场记忆）。"
        ),
        parent_run_id=moderator_run_id,
        # 真纯丙·H4：不再注入 WITNESS_TOOLS 只读箱；默认全开（仍受写盘授权）。
        tools=None,
        stance="",
        group=WITNESS_GROUP,
        round=0,
        depth=depth,
        # 证人答问走单阶段成稿（事实短答），不走辩手两阶段检索成稿。
        research_then_draft=False,
    )
    return RunSession(
        run_id=seat_run_id,
        spec=spec,
        transcript=list(lens.transcript),
        content=lens.content or "",
        recall_count=0,
        partial=False,
    )


def build_witness_seats(
    lens_sessions: Sequence[RunSession],
    *,
    moderator_run_id: str,
    depth: int,
) -> dict[str, WitnessSeat]:
    """透镜 session → 本场证人席位表（key=lens run_id 稳定键）。"""
    seats: dict[str, WitnessSeat] = {}
    for lens in lens_sessions:
        key = lens.run_id
        seat_run_id = f"{moderator_run_id}_wit_{key}"
        label = lens_label_from_session(lens)
        session = fork_witness_session(
            lens,
            seat_run_id=seat_run_id,
            moderator_run_id=moderator_run_id,
            depth=depth,
        )
        seats[key] = WitnessSeat(
            key=key,
            lens_run_id=lens.run_id,
            lens_label=label,
            seat_run_id=seat_run_id,
            session=session,
        )
    return seats


def witness_plan_event(
    tool: DebateTool,
    execution_id: str,
    moderator_run_id: str,
    seats: Mapping[str, WitnessSeat],
):
    """声明辩论幕内证人席位节点（parent=主持人）。"""
    from agentcore.runtime.debate.events import debate_act_payload
    from agentcore.runtime.events import run_plan

    agents: list[dict[str, Any]] = []
    runs: list[dict[str, Any]] = []
    for seat in seats.values():
        agents.append(
            {
                "id": seat.seat_run_id,
                "role": seat.display_name,
                "thinking": True,
            }
        )
        runs.append(
            {
                "id": seat.seat_run_id,
                "agent_id": seat.seat_run_id,
                "task": (
                    f"{seat.origin_caption}；不占辩席，仅在主持人点名时回答事实性问题。"
                ),
                "depends_on": [],
                "parent_run_id": moderator_run_id,
                "group": WITNESS_GROUP,
            }
        )
    prev_execution_id = getattr(tool, "_debate_prev_execution_id", None)
    return run_plan(
        execution_id=execution_id,
        plan_type="debate",
        task_summary="",
        agents=agents,
        runs=runs,
        prev_execution_id=prev_execution_id,
        act=debate_act_payload(tool),
    )


def seats_to_wire(seats: Mapping[str, WitnessSeat]) -> list[dict[str, str]]:
    return [s.to_wire() for s in seats.values()]


def seats_to_info(seats: Mapping[str, WitnessSeat]) -> list[WitnessSeatInfo]:
    return [
        WitnessSeatInfo(
            key=s.key,
            name=s.display_name,
            lens_run_id=s.lens_run_id,
            seat_run_id=s.seat_run_id,
            lens_label=s.lens_label,
            origin_caption=s.origin_caption,
        )
        for s in seats.values()
    ]


async def witness_exam_questions(
    complete_json: CompleteJson,
    config: DebateConfig,
    focus: str,
    turns: Sequence[SideTurn],
    roster: Mapping[str, str],
) -> dict[str, list[str]]:
    """主持人判定：是否点名证人、问谁、问什么（仅事实性问题）。

    ``roster`` = ``{witness_key: 展示说明}``（如 ``证人·法律（来自幕1·法律）``）。
    返回 ``{witness_key: [问题, ...]}``；无需要 / 坏 JSON / 无席位 → {}。
    """
    if not roster:
        return {}
    present = [t for t in turns if t.ok]
    if not present:
        return {}
    from agentcore.runtime.debate.moderator_common import _as_str_list, _turns_block

    roster_lines = [f"- key=`{k}` · {label}" for k, label in sorted(roster.items())]
    valid = ", ".join(sorted(roster.keys()))
    user = (
        f"辩论命题：{config.motion}\n本轮焦点：{focus}\n\n"
        f"本轮出席辩手发言：\n{_turns_block(present)}\n\n"
        f"本场可用证人（幕1 透镜调研员）：\n" + "\n".join(roster_lines) + "\n\n"
        "请判断：本轮交锋是否出现【需要证人澄清的事实缺口】"
        "（双方对某事实各执一词、或某方主张依赖未核验的具体事实 / 时间线 / 条款 / 数据）。"
        "若需要，为相关证人拟 1–2 个【纯事实性问题】（可被出处 / 材料正面回答）；"
        "不需要则 ``questions`` 为空对象。\n"
        "禁问立场、策略、价值判断、「你支持哪方」。\n"
        "只输出 JSON：\n"
        f'{{"questions": {{"<witness_key∈[{valid}]>": ["事实问题1"]}}}}'
    )
    data = await complete_json(_WITNESS_EXAM_SYSTEM, user, "witness_exam")
    raw = data.get("questions")
    if not isinstance(raw, dict):
        return {}
    out: dict[str, list[str]] = {}
    for key, qs in raw.items():
        k = str(key)
        if k not in roster:
            continue
        questions = _as_str_list(qs)[:2]
        if questions:
            out[k] = questions
    return out


def witness_answer_feedback(
    seat: WitnessSeat,
    *,
    round_no: int,
    focus: str,
    questions: Sequence[str],
) -> str:
    n = len(questions)
    numbered = "\n".join(f"{i}. {q}" for i, q in enumerate(questions, start=1))
    return (
        f"## 第 {round_no} 轮 · 证人答问（本轮焦点：{focus}）\n"
        f"你是【{seat.display_name}】（{seat.origin_caption}）。"
        "你不占辩席、不写辩词，只根据幕1 调研现场记忆回答主持人的【事实性问题】。\n\n"
        "纪律：\n"
        "- 只答事实：时间线 / 主体 / 条款原文 / 已查到来源；不知就说不知；\n"
        "- 可检索 / 读文件核对；职责是答事实，勿写盘 / handoff / 委派；\n"
        "- 不要站队、不要评价辩手策略、不要写立论。\n\n"
        f"问题列表（共 {n} 条）：\n{numbered}"
    )


def witness_draft_brief(
    seat: WitnessSeat,
    *,
    round_no: int,
    focus: str,
    questions: Sequence[str],
) -> str:
    n = len(questions)
    skeleton = "\n\n".join(
        f"### 质询{['一', '二', '三', '四', '五'][i] if i < 5 else str(i + 1)}\n"
        f"对该条的事实性回答……"
        for i in range(n)
    )
    return (
        f"## 第 {round_no} 轮 · 证人答问（本轮焦点：{focus}）\n"
        f"你是【{seat.display_name}】。请按下方标题**逐条**用事实回答"
        f"（共 {n} 条；直接以第一个 ``### 质询…`` 标题开头）：\n\n"
        f"{skeleton}\n\n"
        "每条先给可核验事实，再附出处线索（若有）；不知则明确写「未知 / 材料未覆盖」。"
        "约 80–160 字/条，完整句子收束。"
    )


def witness_context_blocks(
    round_no: int,
    questions: Sequence[str],
    feedback: str,
) -> list:
    from agentcore.runtime.runs.types import ContextBlock

    return [
        ContextBlock(channel="task", heading="证人答问", body=feedback),
        ContextBlock(
            channel="witness_exam",
            heading=f"第 {round_no} 轮 · 证人答问（事实性问题）",
            body="\n".join(f"- {q}" for q in questions),
        ),
    ]


def register_witness_answers_in_ledger(
    ledger: Any,
    *,
    seat: WitnessSeat,
    exchanges: Sequence[CrossExamQa],
) -> list[str]:
    """答问登记进场级证据台账；返回新登记的 ``#eN`` 列表。"""
    if ledger is None:
        return []
    ids: list[str] = []
    side_key = f"{WITNESS_SIDE_KEY_PREFIX}{seat.key}"
    for ex in exchanges:
        answer = (ex.answer or "").strip()
        question = (ex.question or "").strip()
        if not answer:
            continue
        title = f"证人·{seat.lens_label}：{question[:80]}"
        snippet = answer[:240]
        eid = ledger.register(
            url="",
            title=title,
            snippet=snippet,
            side_key=side_key,
            tier="unknown",
        )
        ids.append(eid)
    return ids


def make_witness_runner(
    tool: DebateTool,
    execution_id: str,
    moderator_run_id: str,
    seats: Mapping[str, WitnessSeat],
):
    """证人答问 runner：窄 continue_run；失败降级为空答，不阻塞辩论。"""

    async def run_witness_exam(
        *,
        round_no: int,
        focus: str,
        questions: dict[str, list[str]],
    ) -> list[WitnessExamExchange]:
        from agentcore.runtime.runs import RunPhase, continue_run, resolve_max_parallel

        def _emit_unexamined(*, reason: str) -> None:
            """可观测：本轮质询未点名任何证人（席位保持待命/收场后未传唤）。"""
            for seat in seats.values():
                logger.info(
                    "debate.witness.unexamined",
                    witness_key=seat.key,
                    seat_run_id=seat.seat_run_id,
                    round_no=round_no,
                    reason=reason,
                )

        if not questions or not seats:
            if seats and not questions:
                _emit_unexamined(reason="no_questions")
            return []

        targets = [
            (k, list(qs))
            for k, qs in questions.items()
            if k in seats and qs
        ]
        if not targets:
            _emit_unexamined(reason="no_targets")
            return []

        # 上游不预判：弹不弹卡交给 tool_exec 收口点（对齐 rounds / drive_setup）。
        worker_gate = tool._approval_gate
        max_parallel = tool._max_parallel or resolve_max_parallel()
        semaphore = asyncio.Semaphore(max_parallel)

        async def _answer(key: str, qs: list[str]):
            seat = seats[key]
            session = seat.session
            wit_run_id = f"{moderator_run_id}_r{round_no}_wit_{key}"
            research_fb = witness_answer_feedback(
                seat, round_no=round_no, focus=focus, questions=qs
            )
            speech_brief = witness_draft_brief(
                seat, round_no=round_no, focus=focus, questions=qs
            )
            context_blocks = witness_context_blocks(round_no, qs, speech_brief)
            try:
                async with semaphore:
                    t0 = time.monotonic()
                    state = await continue_run(
                        session=session,
                        feedback=research_fb,
                        continuation_run_id=wit_run_id,
                        llm=tool._llm,
                        tools=tool._tools,
                        sink=tool._sink,
                        base_tool_context=tool._base_tool_context,
                        execution_id=execution_id,
                        profile_set=tool._profile_set,
                        cost_role=ROLE_ARENA,
                        approval_gate=worker_gate,
                        round_no=round_no,
                        side_key=f"{WITNESS_SIDE_KEY_PREFIX}{key}",
                        context_blocks=context_blocks,
                        parent_run_id=seat.seat_run_id,
                        draft_brief=speech_brief,
                        allow_research=True,
                        evidence_ledger=tool._evidence_ledger,
                        check_evidence_ledger=False,
                    )
                    return state, int((time.monotonic() - t0) * 1000)
            except Exception as exc:  # noqa: BLE001 — 证人失败不阻塞辩论
                logger.warning(
                    "debate.witness.answer_failed",
                    witness_key=key,
                    round_no=round_no,
                    error=str(exc),
                )
                return None, 0

        triples = await asyncio.gather(*(_answer(k, qs) for k, qs in targets))
        out: list[WitnessExamExchange] = []
        for (key, qs), (state, _elapsed) in zip(targets, triples, strict=False):
            seat = seats[key]
            wit_run_id = f"{moderator_run_id}_r{round_no}_wit_{key}"
            session = seat.session
            if state is None:
                out.append(
                    WitnessExamExchange(
                        witness_key=key,
                        lens_run_id=seat.lens_run_id,
                        name=seat.display_name,
                        origin_caption=seat.origin_caption,
                        exchanges=build_cross_exam_exchanges(qs, ""),
                        answer_run_id=wit_run_id,
                        seat_run_id=seat.seat_run_id,
                    )
                )
                continue
            rev_spec = replace(session.spec, run_id=wit_run_id, agent_id=wit_run_id)
            tool._acc.add_run(
                rev_spec, state, parent_run_id=seat.seat_run_id, role=ROLE_ARENA
            )
            if state.phase is RunPhase.COMPLETED and (state.content or "").strip():
                # 席位 session 延展；不碰透镜 recall_count（豁免）。
                session.transcript = state.transcript
                session.content = state.content
                session.updated_at = time.time()
                # 独立计数（不走 CEO DEFAULT_RECALL_LIMIT）。
                session.recall_count += 1
                qa_pairs = parse_cross_exam_response(
                    qs, state.content, side_key=key
                )
                register_witness_answers_in_ledger(
                    tool._evidence_ledger, seat=seat, exchanges=qa_pairs
                )
                out.append(
                    WitnessExamExchange(
                        witness_key=key,
                        lens_run_id=seat.lens_run_id,
                        name=seat.display_name,
                        origin_caption=seat.origin_caption,
                        exchanges=qa_pairs,
                        answer_run_id=wit_run_id,
                        seat_run_id=seat.seat_run_id,
                    )
                )
                logger.info(
                    "debate.witness.answered",
                    witness_key=key,
                    round_no=round_no,
                    questions=len(qs),
                )
            else:
                out.append(
                    WitnessExamExchange(
                        witness_key=key,
                        lens_run_id=seat.lens_run_id,
                        name=seat.display_name,
                        origin_caption=seat.origin_caption,
                        exchanges=build_cross_exam_exchanges(qs, ""),
                        answer_run_id=wit_run_id,
                        seat_run_id=seat.seat_run_id,
                    )
                )
                logger.info(
                    "debate.witness.answer_empty",
                    witness_key=key,
                    round_no=round_no,
                )
        return out

    return run_witness_exam
