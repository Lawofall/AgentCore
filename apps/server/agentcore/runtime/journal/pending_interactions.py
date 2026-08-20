"""Interactions journal fold（提问确认交互统一 P1/P3 · D5）.

独立投影：从 turn_journal / SSE 事件折出交互全量清单（pending|resolved|orphaned）。
供 ``GET …/recovery``（热路 pending 子集）+ conformance oracle（ProjectedTurn.interactions）共用——
**单一实现，不双写规则**。

7 user-facing kind：approval / escalation /
ask_user / plan_review / team_preview / stage_card。

``awaiting=ceo`` 的 escalation 不进用户可答清单（由活着的 CEO 仲裁）。
冷路（ask_user / plan_review / team_preview）的 frame 恢复仍走 ``paused_turns``；
本 fold 只负责交互卡生命周期投影。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from agentcore.runtime.interaction import (
    INTERACTION_KIND_SPECS,
    RECOVERY_PENDING_KINDS,
    is_user_answerable,
)

InteractionStatus = Literal["pending", "resolved", "orphaned"]

# kind → required event / resolved event / payload 自有 id 字段
# Single source: ``INTERACTION_KIND_SPECS`` (also dumped by ``pnpm gen:types``).
_KIND_SPEC: dict[str, tuple[str, str | None, str]] = {
    kind.value: (spec.required_event, spec.resolved_event, spec.id_field)
    for kind, spec in INTERACTION_KIND_SPECS.items()
}

_REQUIRED_TO_KIND = {required: kind for kind, (required, _, _) in _KIND_SPEC.items()}
_RESOLVED_TO_KIND = {
    resolved: kind
    for kind, (_, resolved, _) in _KIND_SPEC.items()
    if resolved is not None
}
_ID_FIELD_BY_REQUIRED = {
    required: id_field for _, (required, _, id_field) in _KIND_SPEC.items()
}
_ID_FIELD_BY_RESOLVED = {
    resolved: id_field
    for _, (_, resolved, id_field) in _KIND_SPEC.items()
    if resolved is not None
}


@dataclass(frozen=True, slots=True)
class InteractionRecord:
    """One interaction across its lifecycle (oracle / ProjectedTurn leaf)."""

    kind: str
    id: str
    status: InteractionStatus
    payload: dict[str, Any]
    resolution: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class PendingInteraction:
    """One hot-path interaction still awaiting user settlement (recovery API)."""

    kind: str
    id: str
    message_id: str
    payload: dict[str, Any]


@dataclass
class _Open:
    kind: str
    iid: str
    payload: dict[str, Any]
    status: InteractionStatus = "pending"
    resolution: dict[str, Any] | None = None
    order: int = 0


def fold_interactions(entries: list[dict[str, Any]]) -> list[InteractionRecord]:
    """Fold journal/SSE entries → full interaction list (insertion order of required).

    ``entries`` are ``{kind|type, payload}`` dicts.     Terminal status is pending until a
    matching resolved/orphaned settles it. ``awaiting=ceo`` escalations are
    omitted entirely.
    """
    by_key: dict[tuple[str, str], _Open] = {}
    order_counter = 0

    for entry in entries:
        event_kind = str(entry.get("kind") or entry.get("type") or "")
        payload = dict(entry.get("payload") or {})

        if event_kind == "interaction_orphaned":
            orphan_kind = str(payload.get("kind") or "")
            orphan_id = str(payload.get("interaction_id") or "")
            if orphan_kind and orphan_id:
                key = (orphan_kind, orphan_id)
                existing = by_key.get(key)
                if existing is not None and existing.status == "pending":
                    existing.status = "orphaned"
            continue

        if event_kind in _REQUIRED_TO_KIND:
            kind = _REQUIRED_TO_KIND[event_kind]
            id_field = _ID_FIELD_BY_REQUIRED[event_kind]
            iid = str(payload.get(id_field) or "")
            if not iid:
                continue
            if not is_user_answerable(kind, payload):
                continue
            key = (kind, iid)
            existing = by_key.get(key)
            if existing is not None and existing.status in ("resolved", "orphaned"):
                # Already settled — ignore duplicate required (replay safety).
                continue
            if existing is None:
                by_key[key] = _Open(
                    kind=kind,
                    iid=iid,
                    payload=payload,
                    status="pending",
                    order=order_counter,
                )
                order_counter += 1
            else:
                existing.payload = payload
                existing.status = "pending"
                existing.resolution = None
            continue

        if event_kind in _RESOLVED_TO_KIND:
            kind = _RESOLVED_TO_KIND[event_kind]
            id_field = _ID_FIELD_BY_RESOLVED[event_kind]
            iid = str(payload.get(id_field) or "")
            if not iid:
                continue
            key = (kind, iid)
            existing = by_key.get(key)
            if existing is None:
                # Resolved without a tracked required — ignore. Typical case: awaiting=ceo
                # was skipped on required (not user-answerable); CEO resolve must not invent
                # an empty user-facing card in interactions[].
                continue
            elif existing.status == "pending":
                existing.status = "resolved"
                existing.resolution = payload

    return [
        InteractionRecord(
            kind=o.kind,
            id=o.iid,
            status=o.status,
            payload=o.payload,
            resolution=o.resolution,
        )
        for o in sorted(by_key.values(), key=lambda x: x.order)
    ]


def fold_pending_interactions(
    entries: list[dict[str, Any]],
    *,
    message_id: str = "",
) -> list[PendingInteraction]:
    """Pending subset for ``GET …/recovery`` (hot-path + durable stage_card)."""
    return [
        PendingInteraction(
            kind=rec.kind,
            id=rec.id,
            message_id=message_id,
            payload=rec.payload,
        )
        for rec in fold_interactions(entries)
        if rec.status == "pending" and rec.kind in RECOVERY_PENDING_KINDS
    ]


def project_interaction_leaf(rec: InteractionRecord) -> dict[str, Any]:
    """InteractionRecord → camelCase ProjectedTurn.interactions[] leaf (oracle + golden)."""
    p = rec.payload
    base: dict[str, Any] = {"kind": rec.kind, "id": rec.id, "status": rec.status}

    if rec.kind == "approval":
        return {
            **base,
            "toolCallId": p.get("tool_call_id", ""),
            "toolName": p.get("tool_name", ""),
            "arguments": p.get("arguments") or {},
        }
    if rec.kind == "ask_user":
        return {
            **base,
            "question": p.get("question", ""),
            "context": p.get("context", ""),
        }
    if rec.kind == "plan_review":
        run_ids = [s.get("run_id", "") for s in (p.get("steps") or [])]
        return {**base, "runIds": run_ids}
    if rec.kind == "team_preview":
        worker_ids = [w.get("run_id", "") for w in (p.get("workers") or [])]
        leaf: dict[str, Any] = {**base, "workerIds": worker_ids}
        # Resolved 修正摘要（开工组队有限否决）— 仅非空时投影，旧向量保持无字段。
        resolution = rec.resolution or {}
        excluded = resolution.get("excluded_run_ids")
        if isinstance(excluded, list) and excluded:
            leaf["excludedRunIds"] = [str(x) for x in excluded if str(x).strip()]
        overrides = resolution.get("write_capability_overrides")
        if isinstance(overrides, list) and overrides:
            projected_overrides: list[dict[str, str]] = []
            for row in overrides:
                if not isinstance(row, dict):
                    continue
                rid = str(row.get("run_id") or "").strip()
                if not rid:
                    continue
                projected_overrides.append({"runId": rid, "capability": "text_only"})
            if projected_overrides:
                leaf["writeCapabilityOverrides"] = projected_overrides
        models = resolution.get("model_overrides")
        if isinstance(models, dict) and models:
            projected_models: dict[str, dict[str, str]] = {}
            for rid, row in models.items():
                key = str(rid or "").strip()
                if not key or not isinstance(row, dict):
                    continue
                model = str(row.get("model") or "").strip()
                if not model:
                    continue
                entry: dict[str, str] = {"model": model}
                origin = str(row.get("origin") or "").strip()
                if origin:
                    entry["origin"] = origin
                provider_id = str(row.get("provider_id") or "").strip()
                if provider_id:
                    entry["provider_id"] = provider_id
                projected_models[key] = entry
            if projected_models:
                leaf["modelOverrides"] = projected_models
        return leaf
    if rec.kind == "escalation":
        esc: dict[str, Any] = {
            **base,
            "runId": p.get("run_id", ""),
            "agentId": p.get("agent_id", ""),
            "question": p.get("question", ""),
            "assumption": p.get("assumption", ""),
        }
        if p.get("awaiting") in ("user", "ceo"):
            esc["awaiting"] = p["awaiting"]
        return esc
    if rec.kind == "stage_card":
        return {
            **base,
            "motion": p.get("motion", ""),
            "sides": p.get("sides") or [],
            "form": p.get("form", "debate"),
            "rationale": p.get("rationale", ""),
            "factPointers": p.get("fact_pointers") or [],
            "thorough": bool(p.get("thorough", True)),
            "maxRounds": int(p.get("max_rounds") or 5),
            "note": p.get("note") if isinstance(p.get("note"), str) else None,
        }
    return base


def settlement_id_field(event_kind: str) -> str | None:
    """Map a journal/SSE event kind to its own-id payload field (D8 dedupe)."""
    if event_kind in _ID_FIELD_BY_REQUIRED:
        return _ID_FIELD_BY_REQUIRED[event_kind]
    if event_kind in _ID_FIELD_BY_RESOLVED:
        return _ID_FIELD_BY_RESOLVED[event_kind]
    if event_kind == "interaction_orphaned":
        return "interaction_id"
    return None


def settlement_dedupe_key(
    turn_id: str, event_kind: str, payload: dict[str, Any]
) -> tuple[str, str, str] | None:
    """``(turn_id, kind, id)`` for settlement dedupe, or None if not a settlement fact.

    ``kind`` 必须是**完整事件 kind**（required / resolved / orphaned 是三个不同事实），
    只对同一事实的双写去重。历史教训：曾折叠成交互族键，导致宿主回合里的
    ``stage_card_required`` 行把同卡 ``stage_card_resolved`` / ``interaction_orphaned``
    的落库静默吞掉（卡在恢复视图永远 pending）。
    """
    if event_kind.endswith("_resolved") or event_kind.endswith("_required"):
        key_kind = event_kind
    elif event_kind == "interaction_orphaned":
        # orphan 事实按被孤儿化的交互族分桶（同卡重复 orphan 幂等），
        # 前缀命名空间确保不与 required/resolved 撞键。
        inner = str(payload.get("kind") or "")
        if not inner:
            return None
        key_kind = f"interaction_orphaned:{inner}"
    else:
        return None

    id_field = settlement_id_field(event_kind)
    if not id_field:
        return None
    iid = str(payload.get(id_field) or "")
    if not iid:
        return None
    return (turn_id, key_kind, iid)
