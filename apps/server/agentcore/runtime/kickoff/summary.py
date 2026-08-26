"""Structured job-plan summary for the kickoff card (开工卡)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from agentcore.runtime.debate.types import DebateConfig
    from agentcore.runtime.runs.plan import RunPlan

KickoffPrimitive = Literal["delegate", "debate"]

# playbook_args.intensity → 用户可见交付档短标（结构槽，非意图分类）。
# 未知值不映射——勿假造档名，回退仅人数文案。
_INTENSITY_SHORT_LABEL: dict[str, str] = {
    "lean": "MVP主流程",
    "full": "模块流水线",
}


def intensity_short_label(intensity: str | None) -> str | None:
    """Map known intensity tokens to Chinese short labels; unknown → None."""
    if not isinstance(intensity, str):
        return None
    key = intensity.strip().lower()
    return _INTENSITY_SHORT_LABEL.get(key)


def format_kickoff_headline(
    *,
    headcount: int,
    intensity: str | None = None,
    primitive: KickoffPrimitive = "delegate",
) -> str:
    """User-facing kickoff lead: delivery tier + headcount (roles stay secondary).

    Delegate: ``{档短标} · 预计 N 人`` when intensity is known; else
    ``预计 N 人开工``. Debate: ``预计 N 方开赛`` (no delivery-tier inventing).
    """
    n = max(0, int(headcount))
    if primitive == "debate":
        return f"预计 {n} 方开赛"
    label = intensity_short_label(intensity)
    if label:
        return f"{label} · 预计 {n} 人"
    return f"预计 {n} 人开工"


@dataclass(frozen=True)
class KickoffSummary:
    """Fan-out-facing job plan the kickoff gate shows / persists.

    ``primitive`` discriminates card layout. ``workers`` is the delegate分工表;
    debate fills ``motion`` / ``sides`` / ``max_rounds`` / ``thorough`` instead
    (``workers`` stays empty). ``debate_arguments`` is the resume blob so
    ``recover_turn`` can re-enter ``DebateTool.execute`` after CONTINUE.
    ``headline`` is the user-facing lead (交付档 + 人数); empty = old frames.
    """

    primitive: KickoffPrimitive
    workers: list[dict[str, Any]] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    motion: str = ""
    form: str = ""
    sides: list[dict[str, Any]] = field(default_factory=list)
    max_rounds: int = 0
    thorough: bool = True
    debate_arguments: dict[str, Any] = field(default_factory=dict)
    # §7.5 裁判选型（开赛卡展示；可与辩手同模）。
    moderator_model: str = ""
    moderator_origin: str = ""
    moderator_provider_id: str = ""
    # 开赛前预分配主持人稳定 id（人盖 model_overrides 键）；缺省空 = 旧帧。
    moderator_run_id: str = ""
    same_model_debate: bool = False
    # §7.5 D：消歧候选（开赛卡展示）；缺省空，旧 journal 兼容。
    model_candidates: list[dict[str, Any]] = field(default_factory=list)
    # 主文案：交付档短标 + 预计人数；缺省空 = 旧帧 / 前端本地回退。
    headline: str = ""
    # 修订谱系：首版 revision=1；revised_from / revision_note 仅修订卡。
    revision: int = 1
    revised_from: str = ""
    revision_note: str = ""

    def card_payload(self) -> dict[str, Any]:
        """Wire fields for ``team_preview_required`` / suspension extras."""
        out: dict[str, Any] = {
            "primitive": self.primitive,
            "workers": list(self.workers),
            "tools": list(self.tools),
            "motion": self.motion,
            "form": self.form,
            "sides": list(self.sides),
            "max_rounds": self.max_rounds,
            "thorough": self.thorough,
            "revision": self.revision if self.revision >= 1 else 1,
        }
        if self.headline:
            out["headline"] = self.headline
        if self.revised_from:
            out["revised_from"] = self.revised_from
        if self.revision_note:
            out["revision_note"] = self.revision_note
        if self.moderator_run_id:
            out["moderator_run_id"] = self.moderator_run_id
        if self.moderator_model:
            out["moderator_model"] = self.moderator_model
            if self.moderator_origin:
                out["moderator_origin"] = self.moderator_origin
            if self.moderator_provider_id:
                out["moderator_provider_id"] = self.moderator_provider_id
        if self.same_model_debate:
            out["same_model_debate"] = True
        if self.model_candidates:
            out["model_candidates"] = [dict(c) for c in self.model_candidates]
        return out


# 无 Folder 时（裸聊 scratch / 未绑会话工作区）的人审可见落座文案——勿留空让前端猜。
SESSION_DESK_LABEL = "本会话工作区"
# Folder 行存在但名册未解析到显示名时的兜底（enrich 失败 / 空 name）。
UNNAMED_DESK_LABEL = "未命名文件夹"


def worker_rows(
    plan: RunPlan,
    *,
    session_folder_id: str | None = None,
) -> list[dict[str, Any]]:
    """Delegate card rows: role / task / depends_on / write / model / desk seat.

    Desk seat: ``RunSpec.target_folder_id`` else session birth folder; neither →
    name-only ``本会话工作区`` (scratch). Call :func:`enrich_worker_desk_names`
    before emit so Folder ids get real display names.
    """
    from agentcore.runtime.debate.models import identity_from_route_key
    from agentcore.runtime.runs.constants import PLAN_REVIEW_SUMMARY_CHARS

    limit = PLAN_REVIEW_SUMMARY_CHARS
    session_desk = (
        session_folder_id.strip()
        if isinstance(session_folder_id, str) and session_folder_id.strip()
        else None
    )
    rows: list[dict[str, Any]] = []
    for n in plan.nodes:
        task = (n.task or "").strip()
        if len(task) > limit:
            task = task[:limit] + "…"
        form = getattr(n.deliverable, "form", None) if n.deliverable else None
        # form=prose → 仅文字；files / workspace / omitted → 可改文件。
        if form == "prose":
            write_capability = "text_only"
            write_capability_label = "仅文字报告"
        else:
            write_capability = "can_write_files"
            write_capability_label = "可改文件"
        row: dict[str, Any] = {
            "run_id": n.run_id,
            "role": n.role or n.agent_name or n.run_id,
            "task": task,
            "depends_on": list(n.depends_on),
            "form": form,
            "write_capability": write_capability,
            "write_capability_label": write_capability_label,
        }
        # Per-worker 模型：优先显式身份 attrs（若执行链写入）；否则从 RunSpec.model 路由键还原。
        origin_attr = str(getattr(n, "origin", "") or "").strip().lower()
        provider_attr = str(getattr(n, "provider_id", "") or "").strip()
        model_raw = (n.model or "").strip()
        if origin_attr in ("platform", "byok") and model_raw:
            # model 可能已是路由键——展示用裸 id。
            from agentcore.runtime.debate.models import priced_model_from_route

            row["model"] = priced_model_from_route(model_raw)
            row["origin"] = origin_attr
            if origin_attr == "byok" and provider_attr:
                row["provider_id"] = provider_attr
        elif model_raw:
            ident = identity_from_route_key(model_raw)
            if not ident.is_empty():
                row["model"] = ident.model
                if ident.origin:
                    row["origin"] = ident.origin
                if ident.provider_id:
                    row["provider_id"] = ident.provider_id
        # 落座桌：节点 target 优先，否则本会话工作区；皆无 → 仅显示名「本会话工作区」。
        raw_target = getattr(n, "target_folder_id", None)
        node_target = (
            raw_target.strip()
            if isinstance(raw_target, str) and raw_target.strip()
            else None
        )
        desk_id = node_target or session_desk
        if desk_id:
            row["target_folder_id"] = desk_id
            # Provisional until enrich; keeps new frames non-empty if enrich skipped.
            row["target_folder_name"] = UNNAMED_DESK_LABEL
        else:
            row["target_folder_name"] = SESSION_DESK_LABEL
        rows.append(row)
    return rows


async def enrich_worker_desk_names(
    rows: list[dict[str, Any]],
    *,
    user_id: str,
) -> None:
    """Fill ``target_folder_name`` from the folder roster (in-place; soft on miss)."""
    from agentcore.runtime.delegate.target_desktop import lookup_folder_display_names

    ids = {
        str(r["target_folder_id"]).strip()
        for r in rows
        if isinstance(r.get("target_folder_id"), str) and str(r["target_folder_id"]).strip()
    }
    names = await lookup_folder_display_names(ids, user_id=user_id) if ids else {}
    for r in rows:
        fid_raw = r.get("target_folder_id")
        if not isinstance(fid_raw, str) or not fid_raw.strip():
            r.pop("target_folder_id", None)
            r["target_folder_name"] = SESSION_DESK_LABEL
            continue
        fid = fid_raw.strip()
        r["target_folder_id"] = fid
        resolved = names.get(fid)
        if isinstance(resolved, str) and resolved.strip():
            r["target_folder_name"] = resolved.strip()
        else:
            r["target_folder_name"] = UNNAMED_DESK_LABEL


def delegate_kickoff_summary(
    plan: RunPlan,
    *,
    tools: list[str] | None = None,
    intensity: str | None = None,
    session_folder_id: str | None = None,
) -> KickoffSummary:
    workers = worker_rows(plan, session_folder_id=session_folder_id)
    return KickoffSummary(
        primitive="delegate",
        workers=workers,
        tools=list(tools or []),
        headline=format_kickoff_headline(
            headcount=len(workers),
            intensity=intensity,
            primitive="delegate",
        ),
    )


def debate_kickoff_summary(
    config: DebateConfig,
    *,
    arguments: dict[str, Any],
    tools: list[str] | None = None,
) -> KickoffSummary:
    from agentcore.runtime.debate.models import side_wire_fields

    sides = [side_wire_fields(s) for s in config.sides]
    return KickoffSummary(
        primitive="debate",
        tools=list(tools or []),
        motion=config.motion,
        form=config.form.value if hasattr(config.form, "value") else str(config.form),
        sides=sides,
        max_rounds=int(config.policy.max_rounds),
        thorough=bool(config.policy.thorough),
        debate_arguments=dict(arguments),
        moderator_model=config.moderator_model or "",
        moderator_origin=config.moderator_origin or "",
        moderator_provider_id=config.moderator_provider_id or "",
        moderator_run_id=getattr(config, "moderator_run_id", "") or "",
        same_model_debate=bool(config.same_model_debate),
        model_candidates=list(getattr(config, "model_candidates", None) or []),
        headline=format_kickoff_headline(
            headcount=len(sides),
            primitive="debate",
        ),
    )
