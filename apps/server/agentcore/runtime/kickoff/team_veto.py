"""开工组队有限否决 — validate + prune + form=prose tighten + model overrides.

Card continue may exclude run_ids, tighten write capability to ``text_only``
(``deliverable.form=prose``), and override per-worker model identity (人盖 CEO).
Debate continue applies ``model_overrides`` only (sides + moderator slots);
``excluded_run_ids`` / write overrides stay ignore for debate.
Does **not** hard-strip ``file_write`` tools.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from agentcore.core.errors import ValidationError
from agentcore.runtime.checkpoints import CheckpointDecision
from agentcore.runtime.debate.models import (
    ModelIdentity,
    coerce_identity,
    identity_shape_error,
    needs_mention_resolve,
)
from agentcore.runtime.runs.plan import RunPlan
from agentcore.runtime.runs.types import Deliverable
from agentcore.runtime.suspension import TeamPreviewSuspension, TurnSuspension

WriteCapability = Literal["text_only"]


@dataclass(frozen=True)
class WriteCapabilityOverride:
    run_id: str
    capability: WriteCapability = "text_only"


@dataclass(frozen=True)
class ModelOverride:
    """Human override for one worker / debate slot — catalog identity family as ModelIdentity."""

    run_id: str
    model: str
    origin: str = ""
    provider_id: str = ""

    def identity(self) -> ModelIdentity:
        ident = ModelIdentity(
            model=self.model,
            origin=self.origin,
            provider_id=self.provider_id,
        )
        coerced, _err = coerce_identity(ident)
        return coerced.normalized()


def normalize_write_capability_overrides(
    raw: Sequence[WriteCapabilityOverride | dict[str, Any]] | None,
) -> list[WriteCapabilityOverride]:
    """Coerce API / CheckpointResponse shapes into typed overrides."""
    out: list[WriteCapabilityOverride] = []
    for item in raw or []:
        if isinstance(item, WriteCapabilityOverride):
            out.append(item)
            continue
        if not isinstance(item, dict):
            raise ValidationError("write_capability_overrides 项必须是对象")
        run_id = str(item.get("run_id") or "").strip()
        capability = str(item.get("capability") or "").strip()
        if not run_id:
            raise ValidationError("write_capability_overrides.run_id 不能为空")
        if capability != "text_only":
            # 升权或未知 capability — 卡上仅允许收紧为 text_only。
            raise ValidationError(
                "write_capability_overrides.capability 仅允许 text_only（不可升权）"
            )
        out.append(WriteCapabilityOverride(run_id=run_id, capability="text_only"))
    return out


def normalize_model_overrides(
    raw: Mapping[str, Any] | Sequence[Any] | None,
) -> list[ModelOverride]:
    """Coerce map ``{run_id: {model, origin?, provider_id?}}`` (list of dicts also ok).

    ``model`` may be a catalog ``@`` ref or a leftover triple. Empty / missing
    model for a key → skip (不改该节点). Non-empty must pass
    :func:`identity_shape_error` after coerce (禁 silent).
    """
    items: list[tuple[str, dict[str, Any]]] = []
    if raw is None:
        return []
    if isinstance(raw, Mapping):
        for rid, row in raw.items():
            key = str(rid or "").strip()
            if not key:
                continue
            if row is None:
                continue
            if not isinstance(row, dict):
                raise ValidationError("model_overrides 值必须是对象")
            items.append((key, row))
    elif isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
        for row in raw:
            if not isinstance(row, dict):
                raise ValidationError("model_overrides 项必须是对象")
            key = str(row.get("run_id") or "").strip()
            if not key:
                raise ValidationError("model_overrides.run_id 不能为空")
            items.append((key, row))
    else:
        raise ValidationError("model_overrides 须为 run_id→目录身份 的对象")

    out: list[ModelOverride] = []
    for run_id, row in items:
        model = str(row.get("model") or "").strip()
        if not model:
            # 空/缺 model = 不改该节点（与 map 缺键同效）。
            continue
        origin = str(row.get("origin") or "").strip().lower()
        provider_id = str(row.get("provider_id") or "").strip()
        ov = ModelOverride(
            run_id=run_id,
            model=model,
            origin=origin,
            provider_id=provider_id,
        )
        ident = ov.identity()
        if needs_mention_resolve(ident):
            raise ValidationError(
                f"model_overrides[{run_id}] 请抄写目录身份 "
                "@platform/{model} 或 @byok/{provider_id}/{model}"
            )
        err = identity_shape_error(ident, where=f"model_overrides[{run_id}]")
        if err:
            raise ValidationError(err)
        out.append(ov)
    return out


def should_apply_team_veto(
    suspension: TurnSuspension | Any,
    decision: CheckpointDecision | str,
) -> bool:
    """True only for delegate ``team_preview`` + ``continue`` (corrections apply)."""
    if not isinstance(suspension, TeamPreviewSuspension):
        return False
    if getattr(suspension, "primitive", "delegate") != "delegate":
        return False
    value = decision.value if isinstance(decision, CheckpointDecision) else str(decision)
    return value == CheckpointDecision.CONTINUE.value


def should_apply_debate_model_overrides(
    suspension: TurnSuspension | Any,
    decision: CheckpointDecision | str,
) -> bool:
    """True for debate ``team_preview`` + ``continue`` (人盖辩手/主持人模型)."""
    if not isinstance(suspension, TeamPreviewSuspension):
        return False
    if getattr(suspension, "primitive", "delegate") != "debate":
        return False
    value = decision.value if isinstance(decision, CheckpointDecision) else str(decision)
    return value == CheckpointDecision.CONTINUE.value


def debate_model_override_slot_ids(
    sides: Sequence[dict[str, Any]] | None,
    *,
    moderator_run_id: str = "",
    debate_arguments: Mapping[str, Any] | None = None,
) -> set[str]:
    """Known run_id slots for debate kickoff model_overrides validation."""
    known: set[str] = set()
    mod = (moderator_run_id or "").strip()
    if not mod and isinstance(debate_arguments, Mapping):
        mod = str(debate_arguments.get("moderator_run_id") or "").strip()
    if mod:
        known.add(mod)
    rows: list[Any] = list(sides or [])
    if isinstance(debate_arguments, Mapping):
        arg_sides = debate_arguments.get("sides")
        if isinstance(arg_sides, list) and arg_sides:
            rows = list(arg_sides)
    for row in rows:
        if not isinstance(row, dict):
            continue
        rid = str(row.get("run_id") or "").strip()
        if rid:
            known.add(rid)
    return known


def validate_debate_model_overrides(
    sides: Sequence[dict[str, Any]] | None,
    *,
    moderator_run_id: str = "",
    debate_arguments: Mapping[str, Any] | None = None,
    model_overrides: Mapping[str, Any] | Sequence[Any] | None = None,
) -> None:
    """Raise ``ValidationError`` when debate model_overrides hit unknown run_id / bad shape."""
    model_ovs = normalize_model_overrides(model_overrides)
    if not model_ovs:
        return
    known = debate_model_override_slot_ids(
        sides,
        moderator_run_id=moderator_run_id,
        debate_arguments=debate_arguments,
    )
    unknown = sorted({o.run_id for o in model_ovs if o.run_id not in known})
    if unknown:
        raise ValidationError(
            f"model_overrides 含未知 run_id: {', '.join(unknown)}"
        )


def apply_debate_model_overrides(
    debate_arguments: dict[str, Any],
    model_overrides: Mapping[str, Any] | Sequence[Any] | None = None,
    *,
    sides: list[dict[str, Any]] | None = None,
) -> dict[str, dict[str, str]]:
    """Merge model_overrides into debate_arguments (and optional wire sides).

    Writes side / moderator identity fields; returns wire-shaped map for resolved.
    Caller must have validated (or call :func:`validate_debate_model_overrides` first).
    """
    model_ovs = normalize_model_overrides(model_overrides)
    if not model_ovs:
        return {}

    mod_rid = str(debate_arguments.get("moderator_run_id") or "").strip()
    raw_sides = debate_arguments.get("sides")
    if not isinstance(raw_sides, list):
        raw_sides = []
        debate_arguments["sides"] = raw_sides

    side_by_rid: dict[str, dict[str, Any]] = {}
    for row in raw_sides:
        if isinstance(row, dict):
            rid = str(row.get("run_id") or "").strip()
            if rid:
                side_by_rid[rid] = row

    wire_sides_by_rid: dict[str, dict[str, Any]] = {}
    if sides is not None:
        for row in sides:
            if isinstance(row, dict):
                rid = str(row.get("run_id") or "").strip()
                if rid:
                    wire_sides_by_rid[rid] = row

    applied: dict[str, dict[str, str]] = {}
    for ov in model_ovs:
        ident = ov.identity()
        entry: dict[str, str] = {"model": ident.model}
        if ident.origin:
            entry["origin"] = ident.origin
        if ident.provider_id:
            entry["provider_id"] = ident.provider_id

        if mod_rid and ov.run_id == mod_rid:
            debate_arguments["moderator_model"] = ident.model
            debate_arguments["moderator_origin"] = ident.origin
            if ident.origin == "byok" and ident.provider_id:
                debate_arguments["moderator_provider_id"] = ident.provider_id
            else:
                debate_arguments.pop("moderator_provider_id", None)
            applied[ov.run_id] = entry
            continue

        target = side_by_rid.get(ov.run_id)
        if target is None:
            continue
        target["model"] = ident.model
        if ident.origin:
            target["origin"] = ident.origin
        else:
            target.pop("origin", None)
        if ident.origin == "byok" and ident.provider_id:
            target["provider_id"] = ident.provider_id
        else:
            target.pop("provider_id", None)
        wire = wire_sides_by_rid.get(ov.run_id)
        if wire is not None:
            wire["model"] = ident.model
            if ident.origin:
                wire["origin"] = ident.origin
            else:
                wire.pop("origin", None)
            if ident.origin == "byok" and ident.provider_id:
                wire["provider_id"] = ident.provider_id
            else:
                wire.pop("provider_id", None)
        applied[ov.run_id] = entry

    return applied


def validate_team_preview_veto(
    plan: RunPlan,
    *,
    excluded_run_ids: Sequence[str] | None = None,
    write_capability_overrides: Sequence[WriteCapabilityOverride | dict[str, Any]]
    | None = None,
    model_overrides: Mapping[str, Any] | Sequence[Any] | None = None,
) -> None:
    """Raise ``ValidationError`` (HTTP 422) when corrections are illegal."""
    nodes = [
        {
            "run_id": n.run_id,
            "depends_on": list(n.depends_on or []),
        }
        for n in plan.nodes
    ]
    validate_team_preview_veto_workers(
        nodes,
        excluded_run_ids=excluded_run_ids,
        write_capability_overrides=write_capability_overrides,
        model_overrides=model_overrides,
    )


def validate_team_preview_veto_workers(
    workers: Sequence[dict[str, Any]],
    *,
    excluded_run_ids: Sequence[str] | None = None,
    write_capability_overrides: Sequence[WriteCapabilityOverride | dict[str, Any]]
    | None = None,
    model_overrides: Mapping[str, Any] | Sequence[Any] | None = None,
) -> None:
    """Validate corrections against kickoff card ``workers`` (cold peek has no plan blob)."""
    excluded = [str(x).strip() for x in (excluded_run_ids or []) if str(x).strip()]
    overrides = normalize_write_capability_overrides(write_capability_overrides)
    model_ovs = normalize_model_overrides(model_overrides)
    plan_ids = {
        str(w.get("run_id") or "").strip()
        for w in workers
        if isinstance(w, dict) and str(w.get("run_id") or "").strip()
    }

    unknown_excluded = sorted({rid for rid in excluded if rid not in plan_ids})
    if unknown_excluded:
        raise ValidationError(f"excluded_run_ids 含未知 run_id: {', '.join(unknown_excluded)}")

    override_ids = [o.run_id for o in overrides]
    unknown_overrides = sorted({rid for rid in override_ids if rid not in plan_ids})
    if unknown_overrides:
        raise ValidationError(
            f"write_capability_overrides 含未知 run_id: {', '.join(unknown_overrides)}"
        )

    model_ids = [o.run_id for o in model_ovs]
    unknown_models = sorted({rid for rid in model_ids if rid not in plan_ids})
    if unknown_models:
        raise ValidationError(
            f"model_overrides 含未知 run_id: {', '.join(unknown_models)}"
        )

    excluded_set = set(excluded)
    remaining = [rid for rid in plan_ids if rid not in excluded_set]
    # 空 workers + 无修正 = 冷 peek 无 plan blob 时的 no-op；有排除才要求 ≥1。
    if excluded_set and not remaining:
        raise ValidationError("排除后须至少保留一名队员")
    if not plan_ids:
        return

    for w in workers:
        if not isinstance(w, dict):
            continue
        rid = str(w.get("run_id") or "").strip()
        if not rid or rid in excluded_set:
            continue
        deps = w.get("depends_on") or []
        if not isinstance(deps, list):
            continue
        for dep in deps:
            if str(dep).strip() in excluded_set:
                raise ValidationError("仍有队员依赖此岗，无法排除")


def apply_team_preview_veto(
    plan: RunPlan,
    *,
    excluded_run_ids: Sequence[str] | None = None,
    write_capability_overrides: Sequence[WriteCapabilityOverride | dict[str, Any]]
    | None = None,
    model_overrides: Mapping[str, Any] | Sequence[Any] | None = None,
    seed_completed: dict[str, Any] | None = None,
) -> tuple[list[str], list[WriteCapabilityOverride], list[ModelOverride]]:
    """Prune excluded nodes + set ``form=prose`` + apply model route keys (in place).

    Caller must have validated (or call :func:`validate_team_preview_veto` first).
    Model overrides write ``RunSpec.model`` as the debate-family route key
    (``platform/{id}`` / ``{provider_id}/{id}``) — 人确认 > CEO.
    Returns applied excluded ids + write overrides + model overrides for resolved 对账.
    """
    excluded = [str(x).strip() for x in (excluded_run_ids or []) if str(x).strip()]
    overrides = normalize_write_capability_overrides(write_capability_overrides)
    model_ovs = normalize_model_overrides(model_overrides)
    excluded_set = set(excluded)

    if excluded_set:
        plan.nodes = [n for n in plan.nodes if n.run_id not in excluded_set]
        if seed_completed is not None:
            for rid in excluded_set:
                seed_completed.pop(rid, None)

    for item in overrides:
        if item.run_id in excluded_set:
            continue
        node = plan.by_id(item.run_id)
        if node is None:
            continue
        if node.deliverable is None:
            node.deliverable = Deliverable(form="prose")
        else:
            node.deliverable.form = "prose"

    for model_item in model_ovs:
        if model_item.run_id in excluded_set:
            continue
        node = plan.by_id(model_item.run_id)
        if node is None:
            continue
        ident = model_item.identity()
        route = ident.route_key()
        if not route:
            # normalize already shape-checked; belt-and-suspenders hard fail.
            raise ValidationError(
                f"model_overrides[{model_item.run_id}] 无法编成路由键（目录身份不完整）"
            )
        node.model = route

    applied_excluded = [rid for rid in excluded if rid]
    return applied_excluded, overrides, model_ovs


def veto_summary_for_resolved(
    *,
    excluded_run_ids: Sequence[str] | None,
    write_capability_overrides: Sequence[WriteCapabilityOverride | dict[str, Any]]
    | None,
    model_overrides: Mapping[str, Any] | Sequence[Any] | None = None,
) -> tuple[list[str], list[dict[str, str]], dict[str, dict[str, str]]]:
    """Wire-shaped correction summary (empty → omit from payload)."""
    excluded = [str(x).strip() for x in (excluded_run_ids or []) if str(x).strip()]
    overrides = normalize_write_capability_overrides(write_capability_overrides)
    override_rows = [{"run_id": o.run_id, "capability": o.capability} for o in overrides]
    model_ovs = normalize_model_overrides(model_overrides)
    model_map: dict[str, dict[str, str]] = {}
    for o in model_ovs:
        ident = o.identity()
        entry: dict[str, str] = {"model": ident.model}
        if ident.origin:
            entry["origin"] = ident.origin
        if ident.provider_id:
            entry["provider_id"] = ident.provider_id
        model_map[o.run_id] = entry
    return excluded, override_rows, model_map
