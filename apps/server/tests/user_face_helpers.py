"""Shared guard for the user half of ``tool_use_end`` (``failure.message``).

``result`` is written for the model and may say「不要原样重试」/「请落盘后调用 handoff」;
``failure.message`` is written for the person watching and may not. Engine deny paths that
published one string to both are what this guards against.
"""

from __future__ import annotations

# Engine vocabulary the product never exposes. A quoted tool name (``工具 '`` / ``Tool '``)
# or a bare tool identifier is the giveaway that a model-facing sentence leaked verbatim.
INTERNAL_VOCABULARY: tuple[str, ...] = (
    "收口",
    "台账",
    "handoff",
    "落盘",
    "写盘",
    "活性挂起",
    "白名单",
    "允许列表",
    "检索预算",
    "收尾窗口",
    "结构闸",
    "熔断",
    "交卷",
    "空转",
    "工具面",
    "本 run",
    # Role / orchestration nouns: the product says 队员, never these.
    "delegate",
    "worker",
    "CEO",
    # Tool identifiers.
    "web_search",
    "web_fetch",
    "file_read",
    "file_write",
    "str_replace",
    "工具 '",
    "Tool '",
)

# Orders that only make sense aimed at the model. ``让用户在可确认的界面重试`` is worse than
# jargon: on an unattended path the reader IS the user, and that surface does not exist.
MODEL_IMPERATIVES: tuple[str, ...] = (
    "不要再调用",
    # Covers 不要 / 禁止 / 请…原样重试 alike.
    "原样重试",
    "请改用其他方案",
    "让用户在可确认的界面重试",
    "请立即调用",
    "禁止继续",
    "如实标注",
    "勿用正文冒充",
    "勿仿调",
    "勿亲自调用",
)


def assert_user_face_clean(message: str) -> None:
    """The user sentence carries no engine word and no order meant for the model."""
    assert message.strip(), "user face must not be empty"
    for word in INTERNAL_VOCABULARY:
        assert word not in message, f"internal word {word!r} leaked to the user: {message!r}"
    for phrase in MODEL_IMPERATIVES:
        assert phrase not in message, (
            f"model-facing imperative {phrase!r} shown to the user: {message!r}"
        )
