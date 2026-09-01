"""持久化处置单一源的门禁（持久化优化 A+）。

守护 :mod:`agentcore.runtime.events.disposition`：

1. **穷尽门禁**：每个 ``EventType`` 必须在处置表里声明处置（新增事件不声明即红）——
   根治「新增事件静默不落库、重载后凭空消失」。
2. **派生一致**：``_JOURNAL_EVENT_TYPES`` 必须等于所有 DURABLE（不再手维护第二份清单）。
3. **DURABLE 覆盖门禁**：每个 DURABLE 事件必须被某条 conformance 向量覆盖，或在
   ``DURABLE_VECTOR_WAIVERS`` 里显式豁免（带理由）——挡住「落库但从没测过重放」。
"""

from __future__ import annotations

from agentcore.conformance.vectors import VECTORS
from agentcore.runtime.events.disposition import (
    DURABLE_EVENT_TYPES,
    EVENT_DISPOSITION,
    Disposition,
)
from agentcore.runtime.events.journal_config import _JOURNAL_EVENT_TYPES
from agentcore.runtime.events.types import EventType

# DURABLE 但当前尚无专门 conformance 向量覆盖的显式豁免：EventType → 理由。
# A+ 要求 DURABLE「须有向量或豁免」；进这里等于知情记账（而非静默盲区），
# 后续应尽量补真向量再从本表移除。空 = 全部 DURABLE 已被向量覆盖。
DURABLE_VECTOR_WAIVERS: dict[EventType, str] = {
    EventType.BATCH_METRICS: (
        "调度埋点量化——DURABLE（落 journal，重载折入桌面 Execution.batches；"
        "采集仍在、产品不展示；手机 conformance fold 显式 no-op），但**不进**规范化 ProjectedTurn "
        "表面，故没有 golden 能断言其往返。属知情记账，非静默盲区。"
    ),
    EventType.GRAPH_APPEND: (
        "已停发：旧跨回合同图追加锚点。新路径用 run_plan.prev_execution_id；"
        "payload 类型保留仅兼容旧 journal 回放，新向量不再 emit。"
    ),
}


def test_every_event_type_has_a_disposition() -> None:
    """穷尽性：EventType 与处置表严格一一对应（多一个/少一个都红）。"""
    missing = set(EventType) - set(EVENT_DISPOSITION)
    assert not missing, (
        f"EventType 缺持久化处置声明: {sorted(e.value for e in missing)} —— "
        "请在 events/disposition.py 的 EVENT_DISPOSITION 里补一条（DURABLE/DERIVED/EPHEMERAL + 理由）"
    )
    unknown = set(EVENT_DISPOSITION) - set(EventType)
    assert not unknown, f"处置表含未知/已删除的 EventType: {sorted(str(e) for e in unknown)}"


def test_journal_set_is_derived_from_dispositions() -> None:
    """_JOURNAL_EVENT_TYPES 必须 == 所有 DURABLE（journal allow-list 单一源）。"""
    assert _JOURNAL_EVENT_TYPES == DURABLE_EVENT_TYPES


def test_dispositions_are_valid_enum_members() -> None:
    """每条处置的值域合法、理由非空（防手滑写错标签或漏理由）。"""
    for event, entry in EVENT_DISPOSITION.items():
        disposition, reason = entry
        assert isinstance(disposition, Disposition), f"{event.value} 处置值非法: {disposition!r}"
        assert reason and reason.strip(), f"{event.value} 缺处置理由"


def _event_types_in_conformance_vectors() -> set[EventType]:
    """跑遍所有 conformance 向量，收集其中实际出现过的事件类型。"""
    seen: set[EventType] = set()
    for _description, build in VECTORS.values():
        for event in build():
            seen.add(event.type)
    return seen


def test_durable_events_are_covered_by_conformance_vectors() -> None:
    """A+ 覆盖门禁：每个 DURABLE 都被某条向量覆盖，或显式豁免（带理由）。"""
    covered = _event_types_in_conformance_vectors()
    uncovered = DURABLE_EVENT_TYPES - covered - set(DURABLE_VECTOR_WAIVERS)
    assert not uncovered, (
        "DURABLE 事件无 conformance 向量覆盖: "
        f"{sorted(e.value for e in uncovered)} —— 请加一条覆盖它的向量，"
        "或（临时）加入 DURABLE_VECTOR_WAIVERS 并写明理由"
    )


def test_durable_vector_waivers_are_actually_durable_and_needed() -> None:
    """豁免表自洁：豁免项必须真的是 DURABLE，且确实没被向量覆盖（否则应删除豁免）。"""
    covered = _event_types_in_conformance_vectors()
    for event, reason in DURABLE_VECTOR_WAIVERS.items():
        assert event in DURABLE_EVENT_TYPES, f"豁免了非 DURABLE 事件: {event.value}"
        assert reason and reason.strip(), f"豁免 {event.value} 缺理由"
        assert event not in covered, (
            f"{event.value} 其实已被向量覆盖，请从 DURABLE_VECTOR_WAIVERS 移除"
        )
