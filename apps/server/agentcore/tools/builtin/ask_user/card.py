"""ask_user ``card`` parameter — typed confirmation cards."""

from __future__ import annotations

from typing import Any, Literal

from agentcore.runtime.checkpoints import AskCheckpointIntent

AskUserCard = Literal["proposal_pick", "risk_ack", "organize_plan", "daily_review"]

CARD_KINDS: frozenset[str] = frozenset(
    {"proposal_pick", "risk_ack", "organize_plan", "daily_review"}
)

# Appended to every card rejection: the fix is a silent resend, not something to
# narrate — without this the model tends to recount the failed call in its visible
# prose ("The ask_user call failed because…"), which reads as fumbling to the user.
CARD_RETRY_HINT = "改对后直接重发 ask_user 即可；不要在给用户看的正文里复述这次调用失败。"

# risk_ack risk-list may be longer than a normal choice question.
_RISK_ACK_MAX_OPTIONS = 10
_PROPOSAL_PICK_MIN_OPTIONS = 2
_PROPOSAL_PICK_MAX_OPTIONS = 6
_RISK_ACK_MIN_OPTIONS = 1
_ORGANIZE_PLAN_MIN_OPTIONS = 1
_ORGANIZE_PLAN_MAX_OPTIONS = 50
_DAILY_REVIEW_MIN_OPTIONS = 1
_DAILY_REVIEW_MAX_OPTIONS = 20


def parse_card(raw: Any) -> AskUserCard | None | str:
    """Return a known card, ``None`` when omitted/blank, or an error string when unknown."""
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    if text not in CARD_KINDS:
        return (
            f"未知 card={text!r}。仅支持 proposal_pick（方案挑选）、"
            "risk_ack（风险确认勾选）、organize_plan（整理方案清单）"
            "或 daily_review（每日复盘提案）。"
        )
    return text  # type: ignore[return-value]


def card_overrides_intent(card: AskUserCard) -> AskCheckpointIntent:
    """Explicit ``card`` becomes the checkpoint intent (overrides transcript derivation)."""
    return card


def card_max_options(card: AskUserCard | None) -> int:
    if card == "risk_ack":
        return _RISK_ACK_MAX_OPTIONS
    if card == "organize_plan":
        return _ORGANIZE_PLAN_MAX_OPTIONS
    if card == "daily_review":
        return _DAILY_REVIEW_MAX_OPTIONS
    return _PROPOSAL_PICK_MAX_OPTIONS


def validate_card_shape(
    card: AskUserCard,
    *,
    questions: list[dict[str, Any]],
) -> str | None:
    """Return a model-facing error when ``card`` + questions are non-compliant; else None."""
    if len(questions) != 1:
        if card == "proposal_pick":
            return (
                "card=proposal_pick 要求恰好 1 个 question（kind=choice、multiple=false、"
                "options 2–6 个候选方案）。两条出路：要问多个【不同】问题 → 去掉 card 用普通 "
                "ask_user（questions 最多 5）；这些其实是【同一决策】的候选方案 → 合并成 1 个 "
                "question 的多个 options（每个 label=方案名、detail=一行取舍）后再发。"
            )
        if card == "organize_plan":
            return (
                "card=organize_plan 要求恰好 1 个 question（kind=choice、multiple=true、"
                "options 1–50 条整理项）。请改成单题多选后再发。"
            )
        if card == "daily_review":
            return (
                "card=daily_review 要求恰好 1 个 question（kind=choice、multiple=true、"
                "options 1–20 条复盘提案）。请改成单题多选后再发。"
            )
        return (
            "card=risk_ack 要求恰好 1 个 question（kind=choice、multiple=true、"
            "options 1–10 条风险项）。请改成单题多选后再发。"
        )

    q = questions[0]
    kind = str(q.get("kind") or "").strip() or "choice"
    options = q.get("options") if isinstance(q.get("options"), list) else []
    multiple = bool(q.get("multiple") or False)

    if card == "proposal_pick":
        if kind != "choice":
            return (
                "card=proposal_pick 的 question 必须 kind=choice（从候选方案里挑一个）。"
            )
        if multiple:
            return (
                "card=proposal_pick 必须 multiple=false（单选一个方案）。"
                "若要勾选多项风险，请改用 card=risk_ack。"
            )
        n = len(options)
        if n < _PROPOSAL_PICK_MIN_OPTIONS or n > _PROPOSAL_PICK_MAX_OPTIONS:
            return (
                f"card=proposal_pick 要求 options 数量 {_PROPOSAL_PICK_MIN_OPTIONS}–"
                f"{_PROPOSAL_PICK_MAX_OPTIONS}（当前 {n}）。"
                "用于「N 个候选方案挑一个」。"
            )
        return None

    if card == "organize_plan":
        if kind != "choice":
            return "card=organize_plan 的 question 必须 kind=choice（勾选要执行的整理项）。"
        if not multiple:
            return (
                "card=organize_plan 必须 multiple=true（默认全选，取消勾选即剔除）。"
                "MVP 不支持改目标路径。"
            )
        n = len(options)
        if n < _ORGANIZE_PLAN_MIN_OPTIONS or n > _ORGANIZE_PLAN_MAX_OPTIONS:
            return (
                f"card=organize_plan 要求 options 数量 {_ORGANIZE_PLAN_MIN_OPTIONS}–"
                f"{_ORGANIZE_PLAN_MAX_OPTIONS}（当前 {n}）。"
                "每项为「原路径→新路径」整理条目。"
            )
        return None

    if card == "daily_review":
        if kind != "choice":
            return "card=daily_review 的 question 必须 kind=choice（勾选要落盘的提案）。"
        if not multiple:
            return (
                "card=daily_review 必须 multiple=true（默认全选，取消勾选即跳过）。"
                "每项须带 review_kind + body。"
            )
        n = len(options)
        if n < _DAILY_REVIEW_MIN_OPTIONS or n > _DAILY_REVIEW_MAX_OPTIONS:
            return (
                f"card=daily_review 要求 options 数量 {_DAILY_REVIEW_MIN_OPTIONS}–"
                f"{_DAILY_REVIEW_MAX_OPTIONS}（当前 {n}）。"
            )
        return None

    # risk_ack
    if kind != "choice":
        return "card=risk_ack 的 question 必须 kind=choice（勾选要处理的风险项）。"
    if not multiple:
        return (
            "card=risk_ack 必须 multiple=true（多选勾选风险清单）。"
            "若要单选一个方案，请改用 card=proposal_pick。"
        )
    n = len(options)
    if n < _RISK_ACK_MIN_OPTIONS or n > _RISK_ACK_MAX_OPTIONS:
        return (
            f"card=risk_ack 要求 options 数量 {_RISK_ACK_MIN_OPTIONS}–"
            f"{_RISK_ACK_MAX_OPTIONS}（当前 {n}）。"
            "用于「问题/风险清单勾选要处理哪些」。"
        )
    return None


def option_to_organize_op(opt: dict[str, Any]) -> dict[str, Any] | None:
    """Extract a file_batch-shaped op from an organize_plan option; None if malformed."""
    op = str(opt.get("op") or "").strip()
    if op in ("move", "copy"):
        source = str(opt.get("source") or "").strip()
        destination = str(opt.get("destination") or "").strip()
        if not source or not destination:
            return None
        return {"op": op, "source": source, "destination": destination}
    if op in ("delete", "mkdir"):
        path = str(opt.get("path") or "").strip()
        if not path:
            return None
        return {"op": op, "path": path}
    return None
