"""User-interaction SSE payload wire models (factories: ``runtime/events/interaction.py``).

Decision enums are reused from their runtime owners (``runtime/approvals.py`` /
``runtime/checkpoints.py``) so the wire contract and the gate logic share one source.
"""

from __future__ import annotations

from typing import Any, Literal

from agentcore.runtime.approvals import ApprovalDecision
from agentcore.runtime.checkpoints import AskCheckpointIntent, CheckpointDecision
from agentcore.runtime.events.payloads._base import WirePayload, absent
from agentcore.runtime.events.payloads.run import EscalationKind


class ApprovalRequiredPayload(WirePayload):
    approval_id: str
    conversation_id: str
    tool_call_id: str
    tool_name: str
    arguments: dict[str, Any]


class ApprovalResolvedPayload(WirePayload):
    approval_id: str
    tool_call_id: str
    decision: ApprovalDecision


class AskOption(WirePayload):
    """One selectable answer to a choice AskQuestion. `label` is both the displayed text
    and the value composed back into the answer. Tendency lives in the name
    (``（推荐）`` / ``(recommended)``), not a separate flag; the card does not pre-select.
    `action` marks an option that the desktop client fulfils with a
    native client action instead of a plain text answer (unknown/absent → plain option):
    `open_local_project` / `register_local_project` / `bind_local_folder` are
    **本机传统** wire enums（桌面默认同通道；云协作是选项：「云上做完再写入」/「从 Git 克隆」；
    ≠离线；
    网页/手机无本机盘；新开云文件夹走「我的文件」）。
    ``review_kind`` / ``body`` / ``slug`` / ``section`` remain on the wire for
    historical events."""

    label: str
    detail: str | None = absent()
    action: (
        Literal[
            "open_local_project",
            "register_local_project",
            "bind_local_folder",
        ]
        | None
    ) = absent()
    review_kind: (
        Literal["preference", "profile", "topic", "rule", "doc"] | None
    ) = absent()
    body: str | None = absent()
    slug: str | None = absent()
    section: str | None = absent()


class AskQuestion(WirePayload):
    """One ask_user / escalate card question.

    ``default`` remains parseable on leftover frames / journals. New events omit
    it; clients must not treat it as tendency or pre-select. Tendency is
    option-name markup (``（推荐）`` / ``(recommended)``).
    """

    id: str
    prompt: str
    kind: Literal["choice", "text"]
    options: list[AskOption]
    multiple: bool
    default: str | None = absent()


class CheckpointRequiredPayload(WirePayload):
    """The CEO paused the turn on an ask_user checkpoint (blocking)."""

    checkpoint_id: str
    conversation_id: str
    question: str
    questions: list[AskQuestion]
    intent: AskCheckpointIntent | None = absent(ts_type="CheckpointIntent")


class CheckpointResolvedPayload(WirePayload):
    checkpoint_id: str
    decision: CheckpointDecision
    note: str
    selected: list[str] | None = absent()


class EscalationRequiredPayload(WirePayload):
    """``escalate(reason=wait)``: worker SUSPENDED awaiting a decision.

    JOURNALED; the turn never flips to ``paused`` (siblings keep running).

    ``awaiting``: ``user`` (经典路径，可答卡) or ``ceo`` (协调模式下等主管仲裁，初始不可答)。
    """

    escalation_id: str
    run_id: str
    agent_id: str
    question: str
    assumption: str
    questions: list[AskQuestion] | None = absent(
        "Structured forks (同 ask_user 的 questions). Absent on old journaled events "
        "(fold with `?? []`); empty for a free-text ask."
    )
    kind: EscalationKind | None = absent("wait / adjust。缺省按 wait。")
    awaiting: Literal["user", "ceo"] | None = absent(
        "谁在仲裁：user=经典可答卡；ceo=协调模式等主管。旧流缺字段按 user。"
    )
    ownership_paths: list[str] | None = absent(
        "写权冲突路径列表；有值时前端呈现「移交写权 / 保持原主」。旧流缺字段按无。"
    )
    lock_owner_run_id: str | None = absent(
        "当前写权持有者 run_id。旧流缺字段按无。"
    )
    timeout_seconds: float | None = absent(
        "本次挂起的墙钟上限（秒）——仅运维配了 checkpoint_timeout_seconds 才有值，届时"
        "回落 assumption 发 timed_out。缺省 = 默认的无限期等待（D2）：不答就不会自动继续。"
        "卡面文案据此二选一，不得无条件承诺「未答则按假设继续」。"
    )


class EscalationResolvedPayload(WirePayload):
    """阻塞式求决策 settlement. Emitted by the suspending tool's awaiter ONLY; journaled.

    ``status`` 三分（对 worker 回落假设的实现可共享，对外语义必须分开）：
    - ``resolved`` — 有裁决/答复（``answer`` 非空语义由调用方保证）
    - ``assumed`` — 用户或主管显式选了「按假设继续」
    - ``timed_out`` — 墙钟时限内未答复

    ``arbitrated_by`` / ``via_user`` annotate CEO 协调仲裁可见性（经典用户直答路径可缺省）。
    """

    escalation_id: str
    run_id: str
    agent_id: str
    status: Literal["resolved", "assumed", "timed_out", "orphaned"]
    answer: str
    arbitrated_by: Literal["user", "ceo"] | None = absent(
        "裁决方：user=用户直答；ceo=主管仲裁。旧流缺字段按 user。"
    )
    via_user: bool | None = absent(
        "仅 arbitrated_by=ceo 时有意义：true=主管经 ask_user 转交用户后再 resolve。"
    )


class InteractionOrphanedPayload(WirePayload):
    """pending 交互失效（假卡消灭）。热路 kind + 辩论轮。"""

    interaction_id: str
    kind: Literal[
        "approval",
        "escalation",
        "debate_round",
    ]
    reason: str | None = absent(
        "可选失效原因；缺省不传，旧客户端忽略。"
    )
