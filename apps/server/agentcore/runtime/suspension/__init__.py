"""TurnSuspension — the durable snapshot of a turn paused at a client checkpoint.

Public import: ``agentcore.runtime.suspension`` / ``.<leaf>`` (no flat root shims).

结构化挂起 2b (turn 级落盘 + ``POST .../resume``): 2a suspends a turn on an
*in-memory* Future — a process restart or client disconnect loses the whole turn
(an asyncio task + any already-finished workers). This module is the inert data
layer that makes that pause **durable**: a frozen frame carrying everything
``POST .../resume`` needs to rebuild and continue the turn on a fresh process.

Two suspend points are persisted, sharing one frame via a ``kind`` discriminated
union (base :class:`TurnSuspension` + :class:`PlanReviewSuspension` /
:class:`AskUserSuspension` / :class:`TeamPreviewSuspension`):

- **plan_review** — the ``WaveScheduler`` paused at a wave boundary after a
  ``checkpoint_after`` step (inside ``delegate``). Resume re-drives the remaining
    plan tail, feeds the workers' product back as the suspended ``delegate`` tool
  result, then continues the CEO loop. Carries only the reviewed ``steps`` / gated
  ``pending`` (display re-render): the ``plan`` (with minted run_ids) and the
  finished-worker ``completed`` seed are BOTH re-projected from the journal on resume
  (``plan_from_journal`` / ``completed_from_journal``), not serialized — 执行级事件溯源 Phase 2.
- **team_preview** — orchestration kickoff gate paused BEFORE fan-out /
  moderator start (``delegate`` workers or ``debate`` loop). Resume branches on
  ``primitive``: delegate uses ``delegate.resume_plan``; debate re-enters
  ``DebateTool.execute`` (skip kickoff). Carries workers (delegate) or
  motion/sides/budget (debate) plus optional capability ``tools``.
- **ask_user** — the CEO paused mid-loop on its ``ask_user`` checkpoint (the one
  asking primitive — opening 引导 or mid-task fork). Resume maps the user's answer
  to the ``ask_user`` tool result and continues the CEO loop (no plan tail). Carries
  the card payload (message / assumptions / questions) so
  resume can re-emit it.

Every frame shares: the CEO ``transcript`` at the pause (system + history + user +
the assistant message carrying the suspended tool_call), the ``tool_call_id`` that
result must echo (so the rebuilt transcript stays a valid tool-call/result pair),
the ``base_system_prompt`` + ``user_message`` (to re-wire the CEO toolset), and the
``checkpoint_id`` (so resume re-emits the resolution).

The journal-so-far is NOT in the frame: it is the §8.3 ``turn_journal`` (唯一事实源),
written at pause and re-hydrated onto :attr:`TurnSuspension.journal_entries` when the
resume claims the frame (see ``runtime/suspension/persistence.py``). The display
:attr:`TurnSuspension.journal` (the resume seed) is a DERIVED projection of those
entries — a property, never stored (P0-B Phase 3). The frame thus carries only the
resume *control* state, not a second copy of the replay stream.

The frame is captured by the suspending face (the ``delegate`` checkpoint hook /
``AskUserTool``) — both read the live CEO transcript off :data:`captain_transcript`,
published by the captain executor — and persisted by
``runtime/suspension/persistence.py``. Pure data + a contextvar here; no DB, no engine.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from contextvars import ContextVar
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any, ClassVar

from agentcore.runtime.checkpoints import AskCheckpointIntent
from agentcore.runtime.interaction import (
    DURABLE_INTERACTION_KINDS as DURABLE_INTERACTION_KINDS,
)
from agentcore.runtime.interaction import (
    InteractionKind,
)

# NOTE: serialize helpers are imported lazily inside from_json codecs so this
# module stays import-light (stdlib + interaction at import time). The captain
# executor — itself imported during the ``runs`` package init — imports
# ``captain_transcript`` from here, so a top-level ``runs.serialize`` import
# could risk an init-order cycle.

if TYPE_CHECKING:
    from agentcore.llm.provider.protocol import LLMMessage
    from agentcore.runtime.runs.plan import RunPlan
    from agentcore.runtime.runs.types import RunState


# The CEO captain's live message transcript for the current turn, published by the
# captain executor before it runs the ReAct loop and read by a suspending face (the
# ``delegate`` checkpoint hook / ``AskUserTool``) when it captures a suspension
# frame. A contextvar (not a parameter) because those faces are constructed by the
# pipeline and invoked deep inside the captain's loop — they have no handle on the
# messages list the loop mutates. The loop, the tool call, and the capture all run
# in the SAME asyncio task, so the task-local contextvar carries the up-to-date list
# (it holds the live reference; the capture serializes a snapshot at the pause).
# ``None`` outside a captain loop (e.g. a delegated worker's own loop, or tests) →
# the face skips durable capture.
captain_transcript: ContextVar[list[LLMMessage] | None] = ContextVar(
    "captain_transcript", default=None
)

# The turn's prior-turn history (the CEO window's history prefix), bound by the pipeline
# at turn start so a suspending face can capture it into the durable frame — the resume
# splices it back ahead of the journal-folded rounds (执行级事件溯源 Phase 2 ⑤; the journal
# stores only history's LENGTH). Symmetric with :data:`captain_transcript`: a contextvar
# because the faces run deep inside the captain loop with no handle on the history list.
# ``None`` outside a turn (tests / standalone) → the face captures no history.
turn_history: ContextVar[list[dict[str, Any]] | None] = ContextVar("turn_history", default=None)

# The turn's live web-source pool (the CEO loop's ``citation_sink``), bound by the pipeline
# right after it creates the list — same pattern as :data:`turn_history`. At pause the pool
# is snapshotted into the trailing ``turn_paused`` journal fact (not ``paused_turns.frame``);
# resume rehydrates from that fact (legacy frames without it still read ``frame.citations``).
# ``None`` outside a turn → capture writes an empty pool on the fact.
turn_citations: ContextVar[list[dict[str, Any]] | None] = ContextVar("turn_citations", default=None)

# 回合共享调研台账（引用即出处 P1 · ``EvidenceLedgerCore`` id_prefix=``#r``）。
# 与 :data:`turn_citations` 同级：pipeline 回合入口创建并 bind；captain / 调研 worker
# 经显式参数注入同一对象（并行登记不撞号）。辩论场级台账仍走 ``debate.EvidenceLedger``
# （``#e``），不读本 ContextVar。挂起快照 / 再水化见提案 §十第 4 步。
turn_evidence_ledger: ContextVar[Any | None] = ContextVar("turn_evidence_ledger", default=None)


# InteractionKind members that persist to ``paused_turns`` (设计 §4.7). Derived
# from INTERACTION_KIND_SPECS (``pauses_turn and not hot``) — see
# :data:`agentcore.runtime.interaction.DURABLE_INTERACTION_KINDS`. :class:`SuspensionKind`
# values are taken from these members (not hand-copied strings).


class SuspensionKind(StrEnum):
    """Which suspend point a durable frame captured (the JSON discriminator).

    Interaction kinds (plan_review / ask_user / team_preview) mirror
    :data:`DURABLE_INTERACTION_KINDS`.
    """

    PLAN_REVIEW = InteractionKind.PLAN_REVIEW.value
    ASK_USER = InteractionKind.ASK_USER.value
    TEAM_PREVIEW = InteractionKind.TEAM_PREVIEW.value


@dataclass(kw_only=True)
class TurnSuspension:
    """The shared substrate of a durably-paused turn (结构化挂起 2b) — abstract base.

    Keyed (in storage) by ``message_id`` (the pipeline's minted assistant id, reused
    when the resumed turn finally persists). Concrete subclasses
    (:class:`PlanReviewSuspension` / :class:`AskUserSuspension`) add their kind's
    resume substrate and set :attr:`kind`. Everything but :attr:`journal` (which
    lives in ``turn_journal``) is JSON-round-trippable (:meth:`to_json` /
    :func:`suspension_from_json`) into the ``paused_turns.frame`` column.
    """

    # Set by each concrete subclass; written into / read from the JSON discriminator.
    kind: ClassVar[SuspensionKind]

    message_id: str
    conversation_id: str
    user_id: str
    captain_run_id: str
    # The suspended interaction's id (the ``checkpoint_id`` of the plan_review /
    # ask_user pause) — re-emitted on resume so the client flips the same card.
    checkpoint_id: str
    # The id of the suspended tool_call (``delegate`` / ``ask_user``) in the captured
    # CEO transcript; the resumed tool result must echo it so the rebuilt transcript
    # is a valid assistant-tool_call → tool-result pair.
    tool_call_id: str
    # The CLEAN base system prompt (no CEO chat hints), so the re-wired toolset hands
    # workers the SAME opening as the pre-pause ones.
    base_system_prompt: str
    user_message: str
    # The cloud project (= workspace folder) scope this turn ran in, captured so the resumed
    # CEO toolset re-wires consult_memory to the SAME project scope (project 主题 first, then
    # global) instead of degrading to global-only — Agent记忆与知识系统 §二. ``None`` for a
    # 裸聊 / local turn with no cloud folder. Serialized into the frame (resume control state).
    folder_id: str | None = None
    # Sidecar/desktop-injected Folder local bind for explore workspace_key (mirrors
    # startTurn ``localRootId`` / ``localSubpath``). Captured at pause so resume can
    # rebuild the key without opening local PG. Legacy frames lack these → False.
    folder_binding_injected: bool = False
    folder_local_root_id: str | None = None
    folder_local_subpath: str | None = None
    # The CEO window at pause is a PROJECTION of the turn journal, NOT a stored blob
    # (执行级事件溯源 Phase 2 ⑤): resume folds ``journal_entries`` + ``history`` via
    # ``window_from_journal``. Kept as an in-memory carrier (the suspending face captures it
    # off ``captain_transcript`` for the conformance golden + a live re-pause-during-settle),
    # but NO LONGER serialized into the frame — so it defaults empty on a claimed frame.
    transcript: list[LLMMessage] = field(default_factory=list)
    # The prior-turn context the resumed CEO window splices in (its history prefix). The
    # journal stores only its LENGTH (history is itself a projection of earlier turns), so
    # resume re-supplies it: the cloud reloads from the message DB, the Sidecar (no DB)
    # persists it in its local frame record (set here from the ``turn_history`` contextvar at
    # capture). NOT serialized into the cloud ``paused_turns.frame``.
    history: list[dict[str, Any]] = field(default_factory=list)
    # The §8.3 fact-log stream: the turn's single ordered log (execution facts —
    # turn_started / round_boundary / llm_call — interleaved with the forwarded display
    # facts) up to and including the suspending ``*_required`` event. THE 唯一权威载体 for
    # the replay stream (P0-B Phase 3): a transient in-memory carrier (NOT serialized into
    # ``paused_turns.frame``) that the suspending face captures from the ambient
    # ``current_fact_log`` (``window_from_journal``-rebuildable) and both hydration paths
    # re-hydrate — the cloud from ``turn_journal`` (:func:`claim_paused_turn`), the Sidecar
    # from its local frame record. The display :attr:`journal` (resume seed) is DERIVED from
    # this (a property), never stored independently, so cloud + sidecar seed identically.
    journal_entries: list[dict[str, Any]] = field(default_factory=list)
    # Set when the best-effort ``turn_journal`` mirror failed at pause time. Resume
    # checks this to surface a clear error instead of silently rebuilding an empty CEO
    # window (the frame alone is not enough without the journal facts).
    journal_degraded: bool = False
    # The turn's web-source pool at pause (captured off :data:`turn_citations` into the
    # trailing ``turn_paused`` fact — the authoritative durable copy). This in-memory field
    # is NOT serialized into new ``paused_turns.frame`` rows; legacy frames may still carry
    # ``frame.citations``, which resume reads as fallback when the fact omits citations.
    citations: list[dict[str, Any]] = field(default_factory=list)
    # Kickoff 段已 consult_memory 的主题正文；resume 复用，避免同 key 再拉一遍。
    consulted_memory: dict[str, str] = field(default_factory=dict)
    trace_id: str | None = None

    @property
    def journal(self) -> list[dict[str, Any]]:
        """DISPLAY replay events for the resume seed — a DERIVED projection of
        :attr:`journal_entries` (P0-B Phase 3: single fact source).

        Was a stored field that could drift from the fact stream (the Sidecar kept a
        surface-gate-truncated live copy; the cloud already derived). Now both hydration
        paths read this projection, so the cloud and Sidecar resume seeds are byte-for-byte
        identical. ``runs_from_entries`` is imported lazily to keep this module import-light
        (see the module docstring).
        """
        from agentcore.runtime.journal import runs_from_entries

        runs = runs_from_entries(self.journal_entries)
        return list((runs or {}).get("events") or [])

    def _base_json(self) -> dict[str, Any]:
        """The shared fields (incl. the ``kind`` discriminator) for ``paused_turns.frame``."""
        return {
            "kind": self.kind.value,
            "message_id": self.message_id,
            "conversation_id": self.conversation_id,
            "user_id": self.user_id,
            "captain_run_id": self.captain_run_id,
            "checkpoint_id": self.checkpoint_id,
            "tool_call_id": self.tool_call_id,
            "base_system_prompt": self.base_system_prompt,
            "user_message": self.user_message,
            "folder_id": self.folder_id,
            "folder_binding_injected": self.folder_binding_injected,
            "folder_local_root_id": self.folder_local_root_id,
            "folder_local_subpath": self.folder_local_subpath,
            # NOTE: ``transcript`` / ``history`` / ``journal_entries`` are deliberately NOT
            # serialized into the frame (执行级事件溯源 Phase 2 ⑤): the CEO window is rebuilt by
            # ``window_from_journal`` from the turn_journal facts (§8.3) + reloaded history, so
            # the frame holds only resume CONTROL metadata. The display ``journal`` is a derived
            # property (never stored). See the module docstring + ``runtime/journal.py``.
            # ``citations`` is NOT serialized — new pauses persist the pool on ``turn_paused``.
            "journal_degraded": self.journal_degraded,
            "citations": [],
            "consulted_memory": dict(self.consulted_memory or {}),
            "trace_id": self.trace_id,
        }

    def to_json(self) -> dict[str, Any]:
        """Flatten to the JSON dict stored in ``paused_turns.frame``.

        Kind-specific extras come from :data:`SUSPENSION_KIND_CODECS` (single
        registration site — not duplicated per subclass).
        """
        codec = SUSPENSION_KIND_CODECS[self.kind]
        return {**self._base_json(), **codec.frame_extras(self)}

    @staticmethod
    def _base_kwargs(data: dict[str, Any]) -> dict[str, Any]:
        """The shared constructor kwargs from a stored frame dict (tolerates missing keys)."""
        data = dict(data or {})
        return {
            "message_id": data.get("message_id", ""),
            "conversation_id": data.get("conversation_id", ""),
            "user_id": data.get("user_id", ""),
            "captain_run_id": data.get("captain_run_id", ""),
            "checkpoint_id": data.get("checkpoint_id", ""),
            "tool_call_id": data.get("tool_call_id") or "",
            "base_system_prompt": data.get("base_system_prompt", "") or "",
            "user_message": data.get("user_message", "") or "",
            "folder_id": data.get("folder_id"),
            # Legacy frames (pre-inject) lack bind keys → not injected (DB / degrade on resume).
            "folder_binding_injected": bool(data.get("folder_binding_injected")),
            "folder_local_root_id": data.get("folder_local_root_id"),
            "folder_local_subpath": (
                None
                if data.get("folder_local_subpath") is None
                else str(data.get("folder_local_subpath"))
            ),
            # NOTE: ``transcript`` / ``history`` / ``journal_entries`` are NOT in the frame
            # (Phase 2 ⑤) — the CEO window is rebuilt from the turn_journal facts on claim
            # (``window_from_journal``), so they default empty here; the display ``journal`` is a
            # derived property (never stored). The Sidecar's local record carries journal_entries
            # + history separately (it has no DB).
            "journal_degraded": bool(data.get("journal_degraded")),
            # Legacy frames (pre-field) lack the key → empty pool (pre-fix behavior).
            "citations": list(data.get("citations") or []),
            "consulted_memory": {
                str(k): str(v)
                for k, v in dict(data.get("consulted_memory") or {}).items()
                if str(k).strip() and str(v)
            },
            "trace_id": data.get("trace_id"),
        }


@dataclass(kw_only=True)
class PlanReviewSuspension(TurnSuspension):
    """A turn frozen at a ``plan_review`` checkpoint — the WaveScheduler resume substrate.

    The ``plan`` (with its already-minted run_ids) and the finished-node ``completed`` seed
    are BOTH rebuilt from the journal on resume (``plan_from_journal`` / ``completed_from_journal``
    — NOT serialized blobs, 执行级事件溯源 Phase 2), so the resumed drive re-mints nothing and
    runs only the downstream tail; only the reviewed ``steps`` + gated ``pending`` (the card's
    display re-render on reopen) ride in the frame.
    """

    kind: ClassVar[SuspensionKind] = SuspensionKind.PLAN_REVIEW

    # The delegate's DAG (with minted run_ids). An in-memory carrier ONLY (执行级事件溯源
    # Phase 2, frame.plan 退场): NOT serialized — resume rebuilds it from the journal's
    # ``plan_snapshot`` fact (``plan_from_journal``); the delegate captures it here live for
    # the conformance golden. An empty RunPlan placeholder on a claimed frame.
    plan: RunPlan
    # run_id → finished RunState (the WaveScheduler ``seed_completed`` for resume). An
    # in-memory carrier ONLY (执行级事件溯源 Phase 2 ⑥): NOT serialized into the frame — resume
    # re-seeds it from the journal's run-final facts (``completed_from_journal``), the
    # delegate still captures it here live for the conformance golden. Empty on a claim.
    completed: dict[str, RunState] = field(default_factory=dict)
    # The just-completed checkpoint nodes the user is reviewing ({run_id, role, summary})
    # and a peek at the gated downstream nodes ({run_id, role}) — re-emitted on resume.
    steps: list[dict[str, Any]] = field(default_factory=list)
    pending: list[dict[str, Any]] = field(default_factory=list)
    # 批次协作参数（便签墙模式 + 团队简报）：只活在 DelegateTool 实例上，耐久恢复换新工具
    # 实例后若不随帧回灌，后续波次的 worker 会被剥掉便签三件套（collaboration=False）。
    coordination: str = "none"
    team_brief: str | None = None
    # CEO 评审前置把关摘要（随帧持久化；CONTINUE 时 llm 压缩注入下游 gate_notes）。
    # 旧帧缺省 None → 行为与今日相同。
    ceo_review: dict[str, Any] | None = None

    @property
    def checkpoint_run_ids(self) -> set[str]:
        """run_ids of the reviewed checkpoint nodes — the roots an ``adjust`` steer
        scopes to (its not-yet-run transitive dependents)."""
        return {s["run_id"] for s in self.steps if "run_id" in s}


@dataclass(kw_only=True)
class TeamPreviewSuspension(TurnSuspension):
    """A turn frozen at the kickoff gate (开工卡) — plan + capability auth before fan-out.

    Shared by leftover ``delegate`` / ``debate`` frames for journal fold.
    Resume of this kind fails honestly (开工卡已退役). ``plan`` / ``completed``
    are in-memory carriers only for deserialize.
    """

    kind: ClassVar[SuspensionKind] = SuspensionKind.TEAM_PREVIEW

    plan: RunPlan
    completed: dict[str, RunState] = field(default_factory=dict)
    # Upcoming workers the user is confirming ({run_id, role, task, depends_on}).
    workers: list[dict[str, Any]] = field(default_factory=list)
    # Execution-class tools the kickoff grant covers（将授权的执行能力；文件类由会话档信任）.
    tools: list[str] = field(default_factory=list)
    # Orchestration primitive discriminant (delegate | debate).
    primitive: str = "delegate"
    # Debate card fields (empty for delegate).
    motion: str = ""
    form: str = ""
    sides: list[dict[str, Any]] = field(default_factory=list)
    max_rounds: int = 0
    thorough: bool = True
    # Resume blob for debate.execute (motion/form/sides/thorough).
    debate_arguments: dict[str, Any] = field(default_factory=dict)
    # 主文案（交付档 + 人数）；缺省空 = 旧帧兼容。
    headline: str = ""
    # 修订谱系（与 team_preview_required 同名字段）；旧帧 revision 缺省 1。
    revision: int = 1
    revised_from: str = ""
    revision_note: str = ""
    # 委派批次协作参数：开工卡挂在 setup_note_wall **之前**，coordination / team_brief /
    # seed_notes 此刻只活在 DelegateTool 实例上（未上墙、未进 journal）。耐久恢复走全新
    # 工具实例（_coordination 缺省 "none"），不随帧回灌则 wall 批降级为 none —— worker 被
    # 剥掉便签三件套、CEO 预贴便签永久丢失（2026-07-20 P2 手驱真跑抓获）。debate 帧恒缺省。
    coordination: str = "none"
    team_brief: str | None = None
    seed_notes: list[dict[str, str]] = field(default_factory=list)

    @property
    def checkpoint_run_ids(self) -> set[str]:
        """Empty roots → ``apply_steer`` targets every not-yet-run node (all workers)."""
        return set()


@dataclass(kw_only=True)
class AskUserSuspension(TurnSuspension):
    """A turn frozen at the CEO's ``ask_user`` checkpoint — the CEO-loop resume substrate.

    No plan tail: resume just maps the user's answer to the ``ask_user`` tool result
    and continues the CEO loop. Carries the unified card payload so resume re-emits the
    full prompt: ``question`` (the framing / opening line — the tool's ``message``),
    plus the rich opening content ``assumptions`` (起步计划
    chips) and ``questions`` (the askable items, each with kind/options/multiple/default).
    All but ``question`` are empty for a compact mid-task fork.
    """

    kind: ClassVar[SuspensionKind] = SuspensionKind.ASK_USER

    question: str = ""
    assumptions: list[dict[str, Any]] = field(default_factory=list)
    questions: list[dict[str, Any]] = field(default_factory=list)
    intent: AskCheckpointIntent = "decision"
    # CEO browser login gate (ask_user browser_login=true) — resume card mirrors
    # escalate's「需要你登录 / 已登录，继续」; absent/false on older frames.
    browser_login: bool = False


def _revision_from_frame(raw: Any) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return 1
    return value if value >= 1 else 1


# ---------------------------------------------------------------------------
# Per-kind codec registry (S2) — single site for frame extras + wire summary.
# Adding an Interaction durable kind: set ``pauses_turn and not hot`` on the spec,
# extend SuspensionKind, subclass, register codec.

# ---------------------------------------------------------------------------

# Shared empty slots for the resume-card wire shape (unused keys stay empty for
# the other kinds — mirrors historical cloud/sidecar paused_summary posture).
_EMPTY_SUMMARY_EXTRAS: dict[str, Any] = {
    "steps": [],
    "pending": [],
    "workers": [],
    "tools": [],
    "primitive": "delegate",
    "motion": "",
    "form": "",
    "sides": [],
    "max_rounds": 0,
    "thorough": True,
    "question": "",
    "assumptions": [],
    "questions": [],
    "intent": None,
    "browser_login": False,
}


@dataclass(frozen=True, slots=True)
class SuspensionKindCodec:
    """One durable kind's frame serialization + summary projection."""

    kind: SuspensionKind
    cls: type[TurnSuspension]
    frame_extras: Callable[[TurnSuspension], dict[str, Any]]
    from_extras: Callable[[dict[str, Any]], dict[str, Any]]
    summary_extras: Callable[[TurnSuspension], dict[str, Any]]


def _plan_review_frame_extras(s: TurnSuspension) -> dict[str, Any]:
    assert isinstance(s, PlanReviewSuspension)
    # NOTE: NEITHER ``plan`` NOR ``completed`` is serialized (执行级事件溯源 Phase 2).
    extras: dict[str, Any] = {"steps": list(s.steps), "pending": list(s.pending)}
    # 非缺省才落帧（帧保持紧凑；旧帧读回走缺省，行为等价）。
    if s.coordination != "none":
        extras["coordination"] = s.coordination
    if s.team_brief:
        extras["team_brief"] = s.team_brief
    if s.ceo_review:
        extras["ceo_review"] = dict(s.ceo_review)
    return extras


def _plan_review_from_extras(data: dict[str, Any]) -> dict[str, Any]:
    from agentcore.runtime.runs.serialize import plan_from_json

    # Empty RunPlan placeholder (field required); resume fold replaces from journal.
    raw_review = data.get("ceo_review")
    return {
        "plan": plan_from_json({}),
        "steps": list(data.get("steps") or []),
        "pending": list(data.get("pending") or []),
        "coordination": str(data.get("coordination") or "none"),
        "team_brief": data.get("team_brief") or None,
        "ceo_review": dict(raw_review) if isinstance(raw_review, dict) else None,
    }


def _plan_review_summary_extras(s: TurnSuspension) -> dict[str, Any]:
    assert isinstance(s, PlanReviewSuspension)
    out: dict[str, Any] = {
        **_EMPTY_SUMMARY_EXTRAS,
        "steps": list(s.steps),
        "pending": list(s.pending),
    }
    if s.ceo_review:
        out["ceo_review"] = dict(s.ceo_review)
    return out


def _team_preview_frame_extras(s: TurnSuspension) -> dict[str, Any]:
    assert isinstance(s, TeamPreviewSuspension)
    extras: dict[str, Any] = {
        "workers": list(s.workers),
        "tools": list(s.tools),
        "primitive": s.primitive,
        "motion": s.motion,
        "form": s.form,
        "sides": list(s.sides),
        "max_rounds": s.max_rounds,
        "thorough": s.thorough,
        "debate_arguments": dict(s.debate_arguments),
    }
    if s.headline:
        extras["headline"] = s.headline
    extras["revision"] = s.revision if s.revision >= 1 else 1
    if s.revised_from:
        extras["revised_from"] = s.revised_from
    if s.revision_note:
        extras["revision_note"] = s.revision_note
    # 委派批次协作参数（见类注释）：非缺省才落帧，旧帧读回走缺省。
    if s.coordination != "none":
        extras["coordination"] = s.coordination
    if s.team_brief:
        extras["team_brief"] = s.team_brief
    if s.seed_notes:
        extras["seed_notes"] = [dict(n) for n in s.seed_notes]
    return extras


def _team_preview_from_extras(data: dict[str, Any]) -> dict[str, Any]:
    from agentcore.runtime.runs.serialize import plan_from_json

    return {
        "plan": plan_from_json({}),
        "workers": list(data.get("workers") or []),
        "tools": list(data.get("tools") or []),
        "primitive": data.get("primitive") or "delegate",
        "motion": data.get("motion") or "",
        "form": data.get("form") or "",
        "sides": list(data.get("sides") or []),
        "max_rounds": int(data.get("max_rounds") or 0),
        "thorough": bool(data.get("thorough", True)),
        "debate_arguments": dict(data.get("debate_arguments") or {}),
        "headline": str(data.get("headline") or ""),
        "revision": _revision_from_frame(data.get("revision")),
        "revised_from": str(data.get("revised_from") or ""),
        "revision_note": str(data.get("revision_note") or ""),
        "coordination": str(data.get("coordination") or "none"),
        "team_brief": data.get("team_brief") or None,
        "seed_notes": list(data.get("seed_notes") or []),
    }


def _team_preview_summary_extras(s: TurnSuspension) -> dict[str, Any]:
    assert isinstance(s, TeamPreviewSuspension)
    out: dict[str, Any] = {
        **_EMPTY_SUMMARY_EXTRAS,
        "workers": list(s.workers),
        "tools": list(s.tools),
        "primitive": s.primitive,
        "motion": s.motion,
        "form": s.form,
        "sides": list(s.sides),
        "max_rounds": s.max_rounds,
        "thorough": s.thorough,
    }
    if s.headline:
        out["headline"] = s.headline
    out["revision"] = s.revision if s.revision >= 1 else 1
    if s.revised_from:
        out["revised_from"] = s.revised_from
    if s.revision_note:
        out["revision_note"] = s.revision_note
    return out


def _ask_user_frame_extras(s: TurnSuspension) -> dict[str, Any]:
    assert isinstance(s, AskUserSuspension)
    extras: dict[str, Any] = {
        "question": s.question,
        "assumptions": list(s.assumptions),
        "questions": list(s.questions),
        "intent": s.intent,
    }
    if s.browser_login:
        extras["browser_login"] = True
    return extras


def _ask_user_from_extras(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "question": data.get("question", "") or "",
        "assumptions": list(data.get("assumptions") or []),
        "questions": list(data.get("questions") or []),
        "intent": data.get("intent") or "decision",
        "browser_login": data.get("browser_login") is True,
    }


def _ask_user_summary_extras(s: TurnSuspension) -> dict[str, Any]:
    assert isinstance(s, AskUserSuspension)
    return {
        **_EMPTY_SUMMARY_EXTRAS,
        "question": s.question,
        "assumptions": list(s.assumptions),
        "questions": list(s.questions),
        "intent": s.intent,
        "browser_login": bool(s.browser_login),
    }


SUSPENSION_KIND_CODECS: Mapping[SuspensionKind, SuspensionKindCodec] = {
    SuspensionKind.PLAN_REVIEW: SuspensionKindCodec(
        kind=SuspensionKind.PLAN_REVIEW,
        cls=PlanReviewSuspension,
        frame_extras=_plan_review_frame_extras,
        from_extras=_plan_review_from_extras,
        summary_extras=_plan_review_summary_extras,
    ),
    SuspensionKind.ASK_USER: SuspensionKindCodec(
        kind=SuspensionKind.ASK_USER,
        cls=AskUserSuspension,
        frame_extras=_ask_user_frame_extras,
        from_extras=_ask_user_from_extras,
        summary_extras=_ask_user_summary_extras,
    ),
    SuspensionKind.TEAM_PREVIEW: SuspensionKindCodec(
        kind=SuspensionKind.TEAM_PREVIEW,
        cls=TeamPreviewSuspension,
        frame_extras=_team_preview_frame_extras,
        from_extras=_team_preview_from_extras,
        summary_extras=_team_preview_summary_extras,
    ),
}


def suspension_summary_fields(suspension: TurnSuspension) -> dict[str, Any]:
    """Kind-specific resume-card fields (shared wire shape for cloud + sidecar).

    Returns the same keys for every kind; unused slots are empty defaults.
    Callers add the shared id/kind/context envelope.
    """
    return SUSPENSION_KIND_CODECS[suspension.kind].summary_extras(suspension)


def suspension_paused_summary(suspension: TurnSuspension) -> dict[str, Any]:
    """Full paused-turn wire summary dict (sidecar shape; cloud wraps into the schema)."""
    return {
        "message_id": suspension.message_id,
        "kind": suspension.kind.value,
        "checkpoint_id": suspension.checkpoint_id,
        "user_message": suspension.user_message,
        **suspension_summary_fields(suspension),
    }


def suspension_from_json(data: dict[str, Any]) -> TurnSuspension:
    """Rebuild the right :class:`TurnSuspension` subclass from a stored frame dict."""
    data = dict(data or {})
    kind_raw = data.get("kind")
    try:
        kind = SuspensionKind(kind_raw)
    except ValueError:
        raise ValueError(f"missing or unknown suspension kind: {kind_raw!r}") from None
    codec = SUSPENSION_KIND_CODECS[kind]
    return codec.cls(**TurnSuspension._base_kwargs(data), **codec.from_extras(data))


# Persistence closures threaded from the pipeline into the suspending faces (so the
# tools package stays free of a DB import). The saver persists a frame before the
# suspend wait; the deleter drops it after a live in-process resolve. Wired to
# ``runtime/suspension/persistence.py`` by the pipeline; ``None`` ⇒ 2a in-memory only.
SuspensionSaver = Callable[["TurnSuspension"], Awaitable[None]]
SuspensionDeleter = Callable[[str], Awaitable[None]]


# Parallel same-batch pauses (two ``ask_user`` in one assistant message) must each
# claim a distinct tool_call_id. Keyed by message_id so gather tasks share it.
_CLAIMED_PAUSE_TOOL_CALLS: dict[str, set[str]] = {}


def find_tool_call_id(
    transcript: list[LLMMessage],
    tool_name: str,
    *,
    exclude_ids: set[str] | None = None,
) -> str:
    """The id of the trailing ``tool_name`` tool_call in a captured CEO transcript.

    The pause happened inside that call, so the transcript ends with the assistant
    message that issued it; the resumed tool result must echo this id. Scans from the
    end for the last assistant message carrying a ``tool_name`` tool_call. ``exclude_ids``
    skips already-claimed siblings in the same batch. Empty string when none is found
    (capture then degrades — the face skips it).
    """
    skip = exclude_ids or set()
    for msg in reversed(transcript):
        if msg.role != "assistant" or not msg.tool_calls:
            continue
        for tc in msg.tool_calls:
            if tc.function.name == tool_name and tc.id not in skip:
                return tc.id
    return ""


def claim_next_tool_call_id(
    message_id: str, transcript: list[LLMMessage], tool_name: str
) -> str:
    """Claim the next unused ``tool_name`` id on the trailing assistant message.

    Does not pop the claim set: a later claim in the same persist batch must
    still see occupied ids (the third claim returns empty, not the first id
    again). :func:`release_claimed_pause_tool_calls_if_complete` drops the
    entry after the batch has claimed every id.
    """
    key = message_id or ""
    claimed = _CLAIMED_PAUSE_TOOL_CALLS.setdefault(key, set())
    tool_call_id = find_tool_call_id(transcript, tool_name, exclude_ids=claimed)
    if tool_call_id:
        claimed.add(tool_call_id)
    return tool_call_id


def _trailing_named_tool_call_ids(
    transcript: list[LLMMessage], tool_name: str
) -> set[str]:
    """``tool_name`` ids on the last assistant message that issued any."""
    for msg in reversed(transcript):
        if msg.role != "assistant" or not msg.tool_calls:
            continue
        ids = {tc.id for tc in msg.tool_calls if tc.function.name == tool_name}
        if ids:
            return ids
    return set()


def release_claimed_pause_tool_calls_if_complete(
    message_id: str,
    transcript: list[LLMMessage],
    tool_name: str = "ask_user",
) -> None:
    """Pop the claim set once this round's ``tool_name`` ids are all claimed.

    Persist is serialized per ``message_id``; the process dict must survive the
    batch so sibling claims stay distinct. Pop only after a successful save
    has claimed every trailing id — a later round on the same ``message_id``
    can then reclaim reused model ids (e.g. ``call_0``). Not a
    WeakValueDictionary: the entry must live across sequential persists.
    """
    key = message_id or ""
    claimed = _CLAIMED_PAUSE_TOOL_CALLS.get(key)
    if not claimed:
        return
    trailing = _trailing_named_tool_call_ids(transcript, tool_name)
    if trailing and trailing <= claimed:
        _CLAIMED_PAUSE_TOOL_CALLS.pop(key, None)
