"""Format coordination events into CEO ReAct window messages."""

from __future__ import annotations

from agentcore.conversation.mentions import (
    format_agent_mention_prompt,
    resolve_interjection_mentions,
)
from agentcore.llm.provider.protocol import LLMMessage
from agentcore.runtime.coordination.cancel_close import (
    cancel_close_line,
    cancel_discipline_sentence,
    cancel_event_headline,
    classify_cancel_close,
)
from agentcore.runtime.coordination.pipeline_view import (
    format_idle_yield_brief,
    format_pipeline_progress,
)
from agentcore.runtime.coordination.session import (
    CoordinationEvent,
    CoordinationEventKind,
    CoordinationSession,
)
from agentcore.runtime.delegate.team_synthesis import worker_output_blurb

_COORD_HINT_CLOSED_TAIL = "全部完成后做最终合成（正文），然后退出协调。"

# Independent-review / audit-package roles (playbook stamps). Not 调研/方向专员.
_AUDIT_REVIEW_ROLE_MARKERS = ("审校", "审计")
# code_audit = 审计套餐; cite_write_review = 成文专线（用户点名审校的结构戳）.
_AUDIT_PLAYBOOKS = frozenset({"code_audit", "cite_write_review"})
_AUDIT_NUDGE = (
    "质量面敏感成品（成篇/构建/审查类）若未经独立审计，先派审计再收尾。"
)

_TEAM_CLOSE_LINE = {
    "success": (
        "本波结果按终稿纪律向用户交代（走 content_delta）；"
        "活没干完就接着干，不需要后续动作则按终稿交付即可。"
    ),
    "failure": (
        "按终稿纪律向用户交代已有产出与失败缺口（走 content_delta）；"
        "勿假装全员成功，不要把失败当成功继续铺开。"
    ),
    "cancelled": (
        "按终稿纪律基于已完成部分向用户交代（走 content_delta）；"
        "说明已取消，调度已停，不要接着派活。"
    ),  # fallback; live cancel close_line uses classify_cancel_close
    "soft_stop": (
        "按终稿纪律向用户交代当前进展与待决问题（走 content_delta）；"
        "等用户拍板后再继续，不要自行接着干。"
    ),
}


def _user_facts_dict(session: CoordinationSession, payload: dict) -> dict:
    raw = payload.get("user_facts")
    if isinstance(raw, dict):
        return raw
    stashed = session.harvest_user_facts
    return stashed if isinstance(stashed, dict) else {}


def _accepted_landing_paths(session: CoordinationSession, payload: dict) -> list[str]:
    """Accepted relative paths from execution user_facts (CEO inject only)."""
    facts = _user_facts_dict(session, payload)
    files = facts.get("files") or []
    if not isinstance(files, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for raw in files:
        path = str(raw).strip()
        if path and path not in seen:
            seen.add(path)
            out.append(path)
    return out


def _role_is_independent_review(role: str) -> bool:
    text = (role or "").strip()
    return bool(text) and any(m in text for m in _AUDIT_REVIEW_ROLE_MARKERS)


def _deliverable_is_review_form(deliverable: object) -> bool:
    if deliverable is None:
        return False
    if isinstance(deliverable, dict):
        if deliverable.get("code_audit_gate"):
            return True
    elif getattr(deliverable, "code_audit_gate", False):
        return True
    from agentcore.runtime.runs.research_quality import deliverable_declares_reviews_files

    return deliverable_declares_reviews_files(deliverable)


def _playbook_is_audit_package(payload: dict, facts: dict) -> bool:
    for raw in (payload.get("playbook"), facts.get("playbook")):
        name = str(raw).strip() if raw is not None else ""
        if name in _AUDIT_PLAYBOOKS:
            return True
    return False


def _wave_wants_audit_nudge(session: CoordinationSession, payload: dict) -> bool:
    """True when this wave is audit-shaped (playbook / form / review role).

    Does not scan user prose or wrap-up wording. map_fanout and ordinary
    writing (no review node / audit playbook / reviews/ form) stay off.
    """
    facts = _user_facts_dict(session, payload)
    if _playbook_is_audit_package(payload, facts):
        return True
    live = session.live_plan
    if live is not None:
        for node in getattr(live, "nodes", ()) or ():
            role = str(
                getattr(node, "role", None) or getattr(node, "agent_name", None) or ""
            )
            if _role_is_independent_review(role):
                return True
            if _deliverable_is_review_form(getattr(node, "deliverable", None)):
                return True
    for raw in facts.get("nodes") or []:
        if not isinstance(raw, dict):
            continue
        if _role_is_independent_review(str(raw.get("role") or "")):
            return True
        if _deliverable_is_review_form(raw.get("deliverable")):
            return True
    return False


def _team_close_kind(
    session: CoordinationSession,
    events: list[CoordinationEvent],
) -> str:
    """Wording key for inject close_line after ALL_COMPLETED / cancel."""
    if session.soft_stop:
        return "soft_stop"
    if session.user_stopped:
        return "cancelled"
    if any(ev.kind is CoordinationEventKind.DRIVE_CANCELLED for ev in events):
        return "cancelled"
    for ev in events:
        if ev.kind is not CoordinationEventKind.ALL_COMPLETED:
            continue
        payload = ev.payload or {}
        if payload.get("cancelled") or payload.get("error"):
            return "cancelled"
        if payload.get("criteria_met") is False:
            return "failure"
    if session.failed_run_ids:
        return "failure"
    cancelled = (session.cancel_ids & session.completed_run_ids) - session.failed_run_ids
    if cancelled:
        return "cancelled"
    return "success"


def _format_ownership_escalation_hint(payload: dict) -> str:
    """Optional ownership conflict briefing for CEO escalate inject."""
    paths = payload.get("ownership_paths") or []
    if not isinstance(paths, list) or not paths:
        return ""
    path_bit = "、".join(f"`{p}`" for p in paths if isinstance(p, str) and p.strip())
    if not path_bit:
        return ""
    lock_owner = (payload.get("lock_owner_run_id") or "").strip()
    kind = payload.get("ownership_kind") or ""
    status = payload.get("owner_status") or ""
    nested = payload.get("escalator_is_lock_owner_nested_child")
    kind_label = (
        "仅派发占位未落盘"
        if kind == "declared"
        else ("已写入" if kind == "written" else "归属冲突")
    )
    status_label = {
        "running": "进行中",
        "completed": "已完成（账本仍记名；同座续派/declare 可接手）",
        "ended": "已结束（账本仍记名；declare/claim 可接手）",
        "unknown": "状态未知",
    }.get(str(status), "")
    bits = [f"文件归属：{path_bit}（{kind_label}"]
    if status_label:
        bits[0] += f"，锁主{status_label}"
    bits[0] += "）"
    if lock_owner:
        bits.append(f"锁主=`{lock_owner}`")
    if str(status) in ("completed", "ended", "unknown"):
        bits.append(
            "锁主已完成/已结束或状态未知——用同座位 replan/append（auto-replaces）接手，"
            "勿让用户点「移交写权」"
        )
    elif nested is True:
        bits.append(
            "升级方疑似锁主的【嵌套子队】——"
            "优先 transfer_ownership=true 路径级移交，勿误判为遗留 worker"
        )
    elif nested is False and lock_owner:
        bits.append("升级方并非锁主嵌套子")
    return "；" + "；".join(bits) + "。"


def _capability_dead_inject_lines(session: CoordinationSession) -> list[str]:
    """Sticky capability-missing facts for CEO inject (soft steer, not a new hard gate).

    Mirrors channel_dead / exec_env_dead user-visible copy. Does not scan task
    prose. user_stop close wording is independent and stays on cancel_close.
    """
    from agentcore.workspace.limits import capability_dead_inject_lines

    return capability_dead_inject_lines(
        workspace_channel_dead=bool(getattr(session, "workspace_channel_dead", False)),
        exec_env_dead=bool(getattr(session, "exec_env_dead", False)),
    )


def format_coordination_events(
    session: CoordinationSession,
    events: list[CoordinationEvent],
) -> str:
    """Build a single system-facing brief for a coalesced event batch."""
    lines: list[str] = ["【团队协调事件】"]
    # Pipeline progress rides every inject so CEO sees wave / blocked / running state.
    if session.live_plan is not None or session.total_workers > 0:
        lines.append(format_pipeline_progress(session))
        lines.append("")
    lines.extend(_capability_dead_inject_lines(session))
    for ev in events:
        lines.append(_format_one(session, ev, events))
    # 疑似缺依赖提示（builder.suspect_missing_dep 搭车）：随本批事件一并呈现，不新增唤醒。
    if session.dep_advisories:
        lines.append("")
        lines.append("【建图提示·疑似缺依赖】（供参考，无需单独回应）：")
        lines.extend(f"- {adv}" for adv in session.dep_advisories)
    if session.draft.strip():
        lines.append("")
        lines.append(f"当前合成草稿：\n{session.draft.strip()}")
    lines.append("")
    from agentcore.runtime.resolve.ceo_surface import COORDINATION_PERIOD_HINT

    closing_now = any(
        ev.kind
        in (
            CoordinationEventKind.ALL_COMPLETED,
            CoordinationEventKind.DRIVE_CANCELLED,
        )
        for ev in events
    )
    # 终态：现在写终稿，剥掉未来时尾巴，避免与 close_line 自相矛盾。
    hint = COORDINATION_PERIOD_HINT
    if closing_now and hint.endswith(_COORD_HINT_CLOSED_TAIL):
        hint = hint[: -len(_COORD_HINT_CLOSED_TAIL)].rstrip()
    lines.append(hint)
    from agentcore.runtime.interaction_orphan import (
        format_hot_pending_hold_line,
        holds_for_hot_user,
    )

    cancel_kind = classify_cancel_close(session, events)
    if holds_for_hot_user(session):
        close_line = format_hot_pending_hold_line(session.conversation_id)
    elif closing_now:
        terminal_kind = _team_close_kind(session, events)
        if terminal_kind == "cancelled" and cancel_kind is not None:
            close_line = cancel_close_line(cancel_kind)
        else:
            close_line = _TEAM_CLOSE_LINE[terminal_kind]
    else:
        close_line = "全部完成后做最终合成（走 content_delta），然后退出协调。"
    discipline = (
        "【终稿纪律】最终合成是给用户的交付、不是协调日志：交付物在前，过程简述从简；"
        "协调态进度旁白不得焊进终稿 content；以上协调事件、队员终态名册、escalation 原文"
        "与合成草稿是你的工作输入，禁止整段粘进终稿——草稿要用也须重写成交付口吻；"
        "未交付的承诺产物须显式列出，不得含糊带过。"
    )
    extra = ""
    if (
        not holds_for_hot_user(session)
        and cancel_kind is not None
        and (
            closing_now
            or any(ev.kind is CoordinationEventKind.DRIVE_CANCELLED for ev in events)
        )
    ):
        extra = cancel_discipline_sentence(cancel_kind, session)
    if extra:
        discipline += extra
    lines.append(
        "先判断本批事件要不要你出手：带指令的事件"
        "（阻塞仲裁 / 边界让出 / 插话 / 全部完成 / 队员失败）"
        "按其指令办；例行成功完成不会单独叫醒你，会挂在上述决策点一并送达。"
        "纯进展事件（worker_completed / note）多数【无需处置】——完成计数与"
        "各队员完成摘要已由系统自动展示给用户，勿为播报进度调 update_synthesis，"
        "也勿用用户可见正文复述进度（【可静默】）。"
        "无需处置时调 wait（或空响应、不写正文），等下一批事件即可——"
        "禁止用 delegate / update_synthesis 占位等待。"
        "对用户开口仅三选一：请示用户 / 报告阻塞与选项 / 宣布阶段结论（非纯进度）。"
        "可用工具：wait 确认等待；cancel_worker(run_id, reason) 终止队员；"
        "delegate 再派【全新角色/任务】队员（同回合追加进同一张协作图，不必等全队完成；"
        "禁止对在跑任务同构重派；流水线未完成时亦禁止与在图节点职责/文件目标重叠的追加）；"
        "replan(add=…) 仅在『计划已让出』波边界追加；"
        "resolve_escalation(run_id, answer) 兑现阻塞升级裁决；"
        "queue_user_message(interjection_id, reason) 把无关插话转入对话级排队（下一回合）；"
        "ask_user 向用户请示（偏好/授权/费用类须先问用户再 resolve）；"
        "update_synthesis(draft) 只在【里程碑】写合成草稿——新结论、冲突/方向修正、"
        "一波/一阶段收束、终稿收束；禁止纯进度播报；例行的单个 worker 完成【不写】"
        "（进度已由系统自动呈现），微调措辞更不算里程碑。"
        f"{close_line}"
        f"{discipline}"
    )
    return "\n".join(lines)


def _interjection_mentions(
    session: CoordinationSession, payload: dict
) -> list[dict] | None:
    """Mentions ride the event payload when present; otherwise the process-local stash."""
    iid = str(payload.get("interjection_id") or "").strip()
    stashed = session.get_interjection(iid) if iid else None
    return resolve_interjection_mentions(payload, stashed)


def events_to_messages(
    session: CoordinationSession,
    events: list[CoordinationEvent],
) -> list[LLMMessage]:
    if not events:
        return []
    return [LLMMessage(role="user", content=format_coordination_events(session, events))]


def idle_yield_messages(session: CoordinationSession) -> list[LLMMessage]:
    """Inject pipeline progress on idle-yield (workers busy, no team events)."""
    return [LLMMessage(role="user", content=format_idle_yield_brief(session))]


def _format_one(
    session: CoordinationSession,
    ev: CoordinationEvent,
    events: list[CoordinationEvent] | None = None,
) -> str:
    p = ev.payload
    if ev.kind is CoordinationEventKind.WORKER_COMPLETED:
        role = p.get("role") or p.get("run_id") or "?"
        status = p.get("status") or "completed"
        summary = p.get("summary") or ""
        done = len(session.completed_run_ids)
        total = session.total_workers
        return f"- worker_completed（{done}/{total}）【{role}】{status}：{summary}"
    if ev.kind is CoordinationEventKind.NOTE_POSTED:
        return (
            f"- note_posted【{p.get('role') or p.get('run_id') or '?'}】"
            f"{p.get('kind') or 'note'}：{p.get('text') or ''}"
        )
    if ev.kind is CoordinationEventKind.ESCALATION:
        role = p.get("role") or p.get("run_id") or "?"
        run_id = p.get("run_id") or "?"
        esc_kind = p.get("kind") or "normal"
        src = p.get("source") or "escalate"
        question = p.get("question") or p.get("summary") or ""
        assumption = p.get("assumption") or ""
        ownership_bit = _format_ownership_escalation_hint(p)
        if p.get("blocking"):
            assume_bit = f"；队员假设：{assumption}" if assumption else ""
            return (
                f"- escalation【阻塞仲裁】【{role}】run_id={run_id} "
                f"{esc_kind}（via {src}）：{question}{assume_bit}"
                f"{ownership_bit}"
                " ——你须仲裁：resolve_escalation(run_id, answer) 直裁；"
                "偏好/授权/费用类须先 ask_user 征询用户，再 "
                "resolve_escalation(run_id, answer, via_user=true)。"
                "若需把冲突路径移交给升级方，设 transfer_ownership=true"
                "（可带 paths；缺省用事件里的 ownership_paths）。"
                "超时无响应时队员会按假设继续，勿永久卡住。"
            )
        return (
            f"- escalation【{role}】{esc_kind}（via {src}）：{question}"
            f"{ownership_bit}"
            " ——可 update_synthesis 记分歧、cancel_worker、"
            "ask_user 请用户裁决、或 post_note 给指导；"
            "文件归属冲突可 resolve_escalation(..., transfer_ownership=true) 路径级移交。"
        )
    if ev.kind is CoordinationEventKind.TIMEOUT:
        rid = p.get("run_id") or "?"
        role = p.get("role") or rid
        elapsed = p.get("elapsed_s")
        status = p.get("status") or "running"
        reason = p.get("reason") or "运行过久"
        hard = p.get("hard")
        hard_bit = "【硬收尾】" if hard else ""
        # Surface the full run_id so the CEO can copy it straight into
        # cancel_worker (role/short names alone silently never cancel).
        if elapsed is not None:
            return (
                f"- timeout{hard_bit}【{role}】run_id={rid} status={status}，"
                f"已运行 {elapsed}s（阈值 {p.get('threshold_s', '?')}s）：{reason}"
            )
        return f"- timeout{hard_bit}【{role}】run_id={rid}：{reason}"
    if ev.kind is CoordinationEventKind.ALL_COMPLETED:
        done = p.get("completed", 0)
        total = p.get("total", session.total_workers)
        failed = p.get("failed")
        # Live drive may omit payload.cancelled; session stamps are the source.
        cancel_kind = classify_cancel_close(session, events)
        if cancel_kind or p.get("cancelled") or p.get("error"):
            kind = cancel_kind or "cancelled"
            lines = [
                cancel_event_headline(
                    kind, prefix="team_cancelled", done=done, total=total
                )
            ]
        elif p.get("criteria_met") is False:
            failed_n = failed if isinstance(failed, int) else 0
            lines = [
                f"- all_completed：团队调度结束（完成 {done}/{total}，失败 {failed_n}），"
                "有队员失败则不得视为成功交付；请按缺口说明处理，"
                "勿向用户宣称全部完成。"
                "调度已结束：勿再启同服，优先复用已有进程或只补浏览器。"
            ]
        elif session.soft_stop:
            lines = [
                f"- all_completed：团队因请示用户而暂停（{done}/{total}）。"
                "请交代当前进展与待决问题。"
            ]
        else:
            lines = [
                f"- all_completed：团队已全部结束（{done}/{total}）。"
                "请按终稿纪律报告本波结果。"
            ]
        output = p.get("output")
        if isinstance(output, str) and output.strip():
            lines.append(f"团队成品：\n{output.strip()}")
        landing = _accepted_landing_paths(session, p)
        if landing:
            listed = "、".join(f"`{path}`" for path in landing)
            lines.append(
                f"已接受落盘：{listed}。"
                "概览须点名这些工作区相对路径；禁止整段粘贴本清单当产物卡。"
            )
        if (
            not cancel_kind
            and not p.get("cancelled")
            and not p.get("error")
            and _wave_wants_audit_nudge(session, p)
        ):
            lines.append(_AUDIT_NUDGE)
        lines.append(
            "最终合成按【终稿纪律】写：交付物在前、过程简述从简，"
            "不粘贴协调事件 / 队员终态名册 / escalation 原文 / 中间合成草稿；"
            "未交付的承诺产物显式列出。"
        )
        return "\n".join(lines)
    if ev.kind is CoordinationEventKind.DRIVE_CANCELLED:
        from agentcore.runtime.interaction_orphan import (
            format_hot_pending_hold_line,
            holds_for_hot_user,
        )

        if holds_for_hot_user(session):
            return f"- {format_hot_pending_hold_line(session.conversation_id)}"
        done = p.get("completed", 0)
        total = p.get("total", session.total_workers)
        kind = classify_cancel_close(session, events) or "drive_cancelled"
        return (
            cancel_event_headline(
                kind, prefix="drive_cancelled", done=done, total=total
            )
            + "请基于已完成队员产出做最终合成并收口；未完成部分勿当作已交付。"
        )
    if ev.kind is CoordinationEventKind.BOUNDARY_YIELD:
        reason = p.get("reason") or "?"
        brief = p.get("brief") or ""
        if reason == "checkpoint":
            detail = f" 已完成摘要：{brief}" if brief.strip() else ""
            return (
                f"- boundary_yield（checkpoint）：这些节点要求用户把关，"
                "必须立即用 ask_user 把关键内容交用户拍板，"
                f"不得自行替用户决定。{detail}"
            )
        return (
            f"- boundary_yield（{reason}）：计划在波边界让出——"
            f"{brief or '请用 replan 续跑或收口'}。"
        )
    if ev.kind is CoordinationEventKind.USER_INTERJECTION:
        iid = p.get("interjection_id") or "?"
        text = (p.get("content") or "").strip()
        lines = [f"- user_interjection（id={iid}）：老板中途插话——「{text}」"]
        atts = p.get("attachments")
        if isinstance(atts, list):
            for a in atts:
                if not isinstance(a, dict):
                    continue
                name = a.get("name") or "?"
                wp = a.get("workspace_path") or ""
                binary = bool(a.get("binary"))
                path_bit = f" → {wp}" if isinstance(wp, str) and wp.strip() else ""
                mark = "（二进制）" if binary else ""
                lines.append(f"  附件：{name}{path_bit}{mark}")
        mention = format_agent_mention_prompt(_interjection_mentions(session, p))
        if mention:
            lines.extend(mention.splitlines())
        lines.append(
            "  【先回用户】须先用可见正文响应该句（哪怕极短「收到，仍按原计划」），"
            "再谈团队；禁止把旧进度旁白当成对插话的答复。"
        )
        lines.append(
            "  相关：图内处置（update_synthesis / delegate 追加队员 / cancel_worker）。"
        )
        lines.append(
            "  无关（独立新活）：必须 queue_user_message(interjection_id=…) 转入对话级排队，"
            "当前回合结束后自动起新回合；勿假装已办、勿丢弃。"
        )
        return "\n".join(lines)
    return f"- {ev.kind.value}：{p}"


def blurb_from_state(state: object) -> str:
    """Best-effort one-line blurb; accepts RunState or falls back."""
    try:
        from agentcore.runtime.runs.types import RunState

        if isinstance(state, RunState):
            return worker_output_blurb(state)
    except Exception:  # noqa: BLE001
        pass
    content = getattr(state, "content", None)
    if isinstance(content, str) and content.strip():
        return content.strip().splitlines()[0][:80]
    return "（无摘要）"
