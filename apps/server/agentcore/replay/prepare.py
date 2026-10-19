"""按消费端准备 ReplaySource（remint 集中于此）。"""

from __future__ import annotations

from typing import Any

from agentcore.demo_tape.identity import remint_interaction_ids
from agentcore.replay.consumer import ConsumerKind, ReplaySource
from agentcore.replay.document import EventDocument, open_event_document


def prepare_replay_source(
    document: EventDocument | dict[str, Any],
    *,
    consumer: ConsumerKind,
    message_id: str | None = None,
) -> ReplaySource:
    """把超集文档准备成**单一**消费端可用的 :class:`ReplaySource`。

    策略：

    * **FOLD (A)** — 永不重铸交互 id（巡检 golden / 深链依赖稳定 id）
    * **SINK (B)** — 默认 ``remint_interaction_ids``

    pacing（``t_ms``）原样保留在事件上——FOLD 消费端忽略等步；SINK 侧由
    demo_tape pacing 修饰层消费。不在此包实现注入 / 挂起语义。
    CEO 自持工具无 ``run_id`` 是 runtime 契约（``execute_tools`` display/trace
    拆分）；磁带按录制面回放，不再做 captain ``run_id`` 剥离。
    """
    doc = (
        document
        if isinstance(document, EventDocument)
        else open_event_document(document)
    )
    events: list[dict[str, Any]] = list(doc.events)

    if consumer is ConsumerKind.SINK:
        if not message_id:
            raise ValueError(
                "SINK consumer requires message_id for per-replay identity remint"
            )
        events = remint_interaction_ids(events, message_id=message_id)

    return ReplaySource(
        consumer=consumer,
        events=tuple(events),
        name=doc.name,
        document_kind=doc.kind.value,
        has_pacing=doc.has_pacing,
    )
