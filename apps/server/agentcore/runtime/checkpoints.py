"""User checkpoint coordination — the CEO pausing a turn to ask the user.

The ask_user checkpoint's typed result, settled over the unified interaction
bridge (``runtime/interaction.py``), alongside tool approvals
(``runtime/approvals.py``) and local-workspace ops (``workspace/channel.py``). A
GRANTABLE tool approval carries a one-shot *decision*; a checkpoint carries the
user's answer to a question the CEO raised mid-turn (continue / adjust / stop).

Unlike approvals and ops (pure transport), a checkpoint's question + answer are
journaled to the turn_journal table (see ``events._JOURNAL_EVENT_TYPES``) and
projected into the assistant message's runs payload, so a reload replays the
exchange inline — it is part of the conversation, not just gating.

State is in-process (single-worker posture, same as the approval gate); front
with Redis to scale to multiple workers (see ``config.py``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal

AskCheckpointIntent = Literal[
    "kickoff",
    "decision",
    "proposal_pick",
    "risk_ack",
    "organize_plan",
    "daily_review",
]


class CheckpointDecision(StrEnum):
    """How the user (or a timeout / orphan) settled a checkpoint the CEO raised.

    ``CONTINUE`` / ``ADJUST`` / ``STOP`` are shared by ask_user / plan_review /
    team_preview (开工卡). On the kickoff card, ``CONTINUE`` means grant + start
    (non-empty ``note`` steers all unrun workers — 嘱咐, **not** a substitute
    for ``ADJUST``). ``ADJUST`` on team_preview does **not** grant or start:
    user ``note`` (required, non-empty on resume) is fed back so the CEO
    revises and resubmits through the kickoff gate (可多轮). ``ADJUST`` on
    plan_review still steers then continues. ask_user rejects ``ADJUST``.
    ``RESEARCH_FIRST`` is debate kickoff only: 不开赛，回灌固定文案令 CEO 立即挂
    ``multi_lens_research``（与 STOP 同构的恢复分支；非辩论开工卡须拒绝/降级）。
    """

    CONTINUE = "continue"  # proceed (kickoff: grant + start; note → steer)
    ADJUST = "adjust"  # plan_review: steer then continue; kickoff: no grant, feed CEO
    STOP = "stop"  # end this turn gracefully
    RESEARCH_FIRST = "research_first"  # debate kickoff only: 先多视角调研再辩
    TIMEOUT = "timeout"  # 运维上限触发；开工卡不 grant / 不开工，回灌 CEO 收尾（对齐 ask）
    ORPHANED = "orphaned"  # 热路失效终态（冷路检查点一般不走 orphan；枚举公共尾部对齐）


@dataclass
class CheckpointResponse:
    """The settled outcome of a checkpoint: a decision + an optional note + picks.

    ``note`` carries the user's steer for plan_review ``ADJUST``, the revise
    opinion on kickoff ``ADJUST`` (fed back to the CEO; no grant), an optional
    嘱咐 on kickoff ``CONTINUE`` (steers unrun workers), and an optional closing
    remark for ``STOP``; it is empty for ``TIMEOUT``. ``selected`` holds the option(s) the
    user picked from the CEO's ``options`` menu — one for a single-select ask,
    several when the ask is ``multiple`` — and is a first-class part of the
    answer (no longer folded into ``note``), so ``CONTINUE`` carries the pick
    too. Empty when the ask offered no options or the user chose none.

    ``excluded_run_ids`` / ``write_capability_overrides`` are
    delegate ``team_preview`` continue corrections (开工组队有限否决).
    ``model_overrides`` apply to delegate continue (人盖队员) and debate continue
    (人盖辩手 / 主持人 → debate_arguments). Ignored for ask_user / plan_review / stop
    and for debate excluded/write fields. Write override shape:
    ``{run_id, capability: "text_only"}`` only (tighten write → ``form=prose``;
    never hard-strip tools). ``model_overrides``: ``run_id → {model, origin?,
    provider_id?}`` (空/缺=不改；非法三元组硬失败).
    """

    decision: CheckpointDecision
    note: str = ""
    selected: list[str] = field(default_factory=list)
    excluded_run_ids: list[str] = field(default_factory=list)
    write_capability_overrides: list[dict[str, str]] = field(default_factory=list)
    model_overrides: dict[str, dict[str, str]] = field(default_factory=dict)
