"""Baseline exemptions for the field-level consumer ratchet.

The gate flags **new** unread leaf names. Stock unread names live here, grouped
by why nobody reads them today — not a flat 50-line dump. Adding a name requires
a factual reason. Deleting a contract field to go green is a contract change and
is out of scope for this gate.

Keys are **leaf names** (last path segment), matching the gate's leaf-zero-hit
criterion. A name that later gains a consumer can stay; the gate does not reverse
ratchet.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FieldConsumerBaselineGroup:
    id: str
    reason: str
    leaves: frozenset[str]


FIELD_CONSUMER_BASELINE: tuple[FieldConsumerBaselineGroup, ...] = (
    FieldConsumerBaselineGroup(
        id="graph_append.extra_ids",
        reason=(
            "旧跨回合同图追加：UI 读 host_message_id / added_count；"
            "append_message_id 与 added_run_ids 未接线。"
        ),
        leaves=frozenset({"append_message_id", "added_run_ids"}),
    ),
    FieldConsumerBaselineGroup(
        id="tool_use_end.audience",
        reason="tool_use_end.audience 标记 CEO-only 工具输出；客户端未按此分支。",
        leaves=frozenset({"audience"}),
    ),
    FieldConsumerBaselineGroup(
        id="debate.evidence_pack",
        reason=(
            "庭前证据包：UI 读 completeness / skip_reason / external_evidence_mode；"
            "不读 evidence_pack 子树与 external_evidence blob。"
        ),
        leaves=frozenset(
            {
                "evidence_pack",
                "dispute_candidates",
                "excerpt",
                "ledger_ids",
                "related_source_ids",
                "source_id",
                "why_contested",
                "external_evidence",
            }
        ),
    ),
    FieldConsumerBaselineGroup(
        id="debate.witnesses_absent",
        reason=(
            "辩论结果证人席与缺席标记：witnesses 整段、lens_label、sides.absent "
            "未进辩论卡渲染。"
        ),
        leaves=frozenset({"witnesses", "lens_label", "absent"}),
    ),
    FieldConsumerBaselineGroup(
        id="evidence_ledger.doc_kind",
        reason="回合台账 JSON 透传 doc_kind；徽章读 dossier_label 等，不读此叶。",
        leaves=frozenset({"doc_kind"}),
    ),
    FieldConsumerBaselineGroup(
        id="stage_card.host_anchors",
        reason=(
            "舞台卡按 stage_card_id 交互；host_execution_id / synthesizer_run_id "
            "是机制直传，契约注明旧客户端忽略。"
        ),
        leaves=frozenset({"host_execution_id", "synthesizer_run_id"}),
    ),
    FieldConsumerBaselineGroup(
        id="team_synthesis_preview.in_progress",
        reason=(
            "team_synthesis_preview 整包入库，UI 用 completed/headline/text，"
            "不读 in_progress。"
        ),
        leaves=frozenset({"in_progress"}),
    ),
    FieldConsumerBaselineGroup(
        id="sim.interaction.relation_deltas",
        reason=(
            "Town InteractionStateChanges 映射了 mood/money/inventory/governance，"
            "没有 relation_deltas 的 JsonProperty。"
        ),
        leaves=frozenset({"relation_deltas"}),
    ),
    FieldConsumerBaselineGroup(
        id="turn_queue_started.remaining_depth",
        reason="排队出队帧用来清 queue_id 轻态；remaining_depth 未读。",
        leaves=frozenset({"remaining_depth"}),
    ),
    FieldConsumerBaselineGroup(
        id="run_escalation_gate.signals",
        reason=(
            "Phase 1 有意不给 Gate 独立 UI：两端 fold 是穷尽用的空 case，不读 payload。"
            "且 evaluate_after_tools 恒返回 continue，该事件当前 emit 不可达；"
            "耐久升级走 run_escalation / escalate 通道。"
        ),
        leaves=frozenset({"signals"}),
    ),
    FieldConsumerBaselineGroup(
        id="message_end.team_batch.worker_count",
        reason=(
            "message_end.team_batch 整包入库且用户面不渲染编制人数；"
            "worker_count 无读点。"
        ),
        leaves=frozenset({"worker_count"}),
    ),
)


def baseline_leaf_names() -> frozenset[str]:
    names: set[str] = set()
    for group in FIELD_CONSUMER_BASELINE:
        names |= set(group.leaves)
    return frozenset(names)


def duplicate_baseline_leaves() -> dict[str, tuple[str, ...]]:
    """Leaf → group ids, only for names claimed by more than one group."""
    owners: dict[str, list[str]] = {}
    for group in FIELD_CONSUMER_BASELINE:
        for leaf in group.leaves:
            owners.setdefault(leaf, []).append(group.id)
    return {leaf: tuple(ids) for leaf, ids in owners.items() if len(ids) > 1}
