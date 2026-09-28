"""协调中用户插话：received → injected（终态）。

插进当前回合的话留在这一轮。主 Agent 不把它改排到下一轮，团队收口也不自动升成排队。
``injected`` = 内容真正写入 CEO 上下文（与经典 ReAct 步顶 drain 对齐）。
durable ``user_interjection`` 由调用方保证同 id 语义更新；发送方确认流勿重复落 journal。

``addressed`` 与协调侧把插话改排的 ``queued`` 新回合不发。旧日记里的这两态仍可回放。
经典回合赶不上下一步的 leftover 升队不走本模块。
"""

from __future__ import annotations

from typing import Any

from agentcore.core.mentions import resolve_interjection_mentions
from agentcore.runtime.events import user_interjection
from agentcore.workspace.attachments import interjection_attachment_meta

InterjectionStatus = str  # received | injected | failed（本模块只发 injected）


def _att_meta(stashed: dict[str, Any] | None) -> list[dict[str, Any]] | None:
    if not stashed:
        return None
    meta = interjection_attachment_meta(list(stashed.get("attachments") or []))
    return meta or None


def _mention_meta(
    payload: dict[str, Any] | None = None,
    stashed: dict[str, Any] | None = None,
) -> list[dict[str, Any]] | None:
    return resolve_interjection_mentions(payload, stashed)


def emit_interjection_status(
    sink: Any | None,
    *,
    session: Any,
    interjection_id: str,
    content: str,
    status: InterjectionStatus,
    note: str | None = None,
    attachments: list[dict[str, Any]] | None = None,
    agent_mentions: list[dict[str, Any]] | None = None,
) -> None:
    """Emit durable status update on the live turn sink (when available)."""
    if sink is None:
        sink = getattr(session, "event_sink", None)
    if sink is None:
        return
    sink.emit(
        user_interjection(
            interjection_id=interjection_id,
            execution_id=session.execution_id,
            content=content,
            status=status,
            note=note,
            attachments=attachments,
            agent_mentions=agent_mentions,
        )
    )


async def note_interjections_injected(session: Any, events: list[Any]) -> None:
    """Emit ``injected`` once the interjection text is in the CEO context.

    Does not settle replies on inject — prompt assembly in ``inject.py`` must
    not settle (replayed every round). ``injected`` is the terminal status for
    a coordination interjection.
    """
    from agentcore.runtime.coordination.session import CoordinationEventKind

    sink = getattr(session, "event_sink", None)
    for ev in events:
        if getattr(ev, "kind", None) is not CoordinationEventKind.USER_INTERJECTION:
            continue
        payload = getattr(ev, "payload", None) or {}
        iid = str(payload.get("interjection_id") or "").strip()
        if not iid:
            continue
        stashed = session.get_interjection(iid)
        raw_content = str(
            (stashed or {}).get("content") or payload.get("content") or ""
        ).strip()
        content = raw_content or "（无正文）"
        att = _att_meta(stashed)
        if att is None:
            raw = payload.get("attachments")
            att = raw if isinstance(raw, list) and raw else None
        emit_interjection_status(
            sink,
            session=session,
            interjection_id=iid,
            content=content,
            status="injected",
            attachments=att,
            agent_mentions=_mention_meta(payload, stashed),
        )
