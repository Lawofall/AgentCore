"""Failed-turn history notes for next-turn prompt attribution."""

from types import SimpleNamespace

from agentcore.conversation.history import (
    _failure_note,
    _fold_history_messages,
    _is_failed_empty_assistant,
)
from agentcore.core.error_codes import ErrorCode


def _msg(
    role,
    content="",
    *,
    status=None,
    error_code=None,
    finish_reason=None,
    error_message=None,
    attachments=None,
    origin=None,
    harvest_kind=None,
):
    usage = {}
    if status is not None:
        usage["status"] = status
    if error_code is not None:
        usage["error_code"] = error_code
    if finish_reason is not None:
        usage["finish_reason"] = finish_reason
    if error_message is not None:
        usage["error_message"] = error_message
    if origin is not None:
        usage["origin"] = origin
    if harvest_kind is not None:
        usage["harvest_kind"] = harvest_kind
    return SimpleNamespace(
        role=role, content=content, usage=usage or None, attachments=attachments
    )


def test_failed_empty_assistant_detection():
    assert _is_failed_empty_assistant(
        _msg("assistant", "", status="failed", error_code=ErrorCode.LLM_TIMEOUT)
    )
    assert not _is_failed_empty_assistant(_msg("assistant", "ok", status="failed"))
    assert not _is_failed_empty_assistant(_msg("user", "hi"))


def test_fold_merges_consecutive_failures_into_one_note():
    rows = [
        _msg("user", "第一问"),
        _msg("assistant", "", status="failed", error_code=ErrorCode.LLM_TIMEOUT),
        _msg("user", "再试"),
        _msg("assistant", "", status="failed", error_code=ErrorCode.LLM_TIMEOUT),
        _msg("assistant", "", status="failed", error_code=ErrorCode.LLM_KEY_INVALID),
        _msg("user", "换个说法"),
        _msg("assistant", "正常回答"),
    ]
    out = _fold_history_messages(rows)
    assert out[0] == {"role": "user", "content": "第一问"}
    assert "连接超时" in out[1]["content"]
    assert out[1]["role"] == "assistant"
    assert out[2] == {"role": "user", "content": "再试"}
    # Two consecutive failures → one note mentioning both categories.
    note = out[3]["content"]
    assert "连续 2 轮" in note
    assert "连接超时" in note
    assert "鉴权失败" in note
    assert out[4] == {"role": "user", "content": "换个说法"}
    assert out[5] == {"role": "assistant", "content": "正常回答"}


def test_fold_empty_failure_is_note_not_raw_error_body():
    """After stop dual-write: empty failed rows stay notes, not fake assistant prose."""
    rows = [
        _msg("user", "hi"),
        _msg(
            "assistant",
            "",
            status="failed",
            error_code=ErrorCode.LLM_TIMEOUT,
            error_message="连接超时，请稍后重试",
        ),
    ]
    out = _fold_history_messages(rows)
    assert len(out) == 2
    assert out[0] == {"role": "user", "content": "hi"}
    note = out[1]["content"]
    assert note.startswith("（系统注记：")
    assert "连接超时" in note
    assert "详情：连接超时，请稍后重试" in note
    # Must not look like a normal assistant reply that merely repeats the error.
    assert out[1]["content"] != "连接超时，请稍后重试"


def test_failure_note_single():
    note = _failure_note(["连接超时"])
    assert "上一轮" in note["content"]
    assert "连接超时" in note["content"]
    assert "不要编造" in note["content"]


def test_fold_empty_user_with_attachments_keeps_system_note():
    rows = [
        _msg(
            "user",
            "",
            attachments=[
                {"name": "截图.png", "workspace_path": "attachments/截图.png"},
            ],
        ),
        _msg("assistant", "收到"),
    ]
    out = _fold_history_messages(rows)
    assert len(out) == 2
    assert out[0]["role"] == "user"
    note = out[0]["content"]
    assert note.startswith("（系统注记：")
    assert "截图.png" in note
    assert "attachments/截图.png" in note
    assert out[1] == {"role": "assistant", "content": "收到"}


def test_fold_empty_user_without_attachments_still_dropped():
    rows = [
        _msg("user", "hi"),
        _msg("assistant", "ok"),
        _msg("user", ""),
        _msg("assistant", "later"),
        _msg("user", "", attachments=[]),
    ]
    out = _fold_history_messages(rows)
    assert out == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "ok"},
        {"role": "assistant", "content": "later"},
    ]


def test_fold_nonempty_user_with_attachments_keeps_original_content():
    rows = [
        _msg(
            "user",
            "请看这张图",
            attachments=[{"name": "a.png", "workspace_path": "attachments/a.png"}],
        ),
    ]
    out = _fold_history_messages(rows)
    assert out == [{"role": "user", "content": "请看这张图"}]


def test_fold_empty_user_many_attachments_truncates_note():
    atts = [
        {"name": f"f{i}.png", "workspace_path": f"attachments/f{i}.png"} for i in range(8)
    ]
    out = _fold_history_messages([_msg("user", "", attachments=atts)])
    assert len(out) == 1
    note = out[0]["content"]
    assert note.startswith("（系统注记：")
    assert "f0.png" in note
    assert "另有 5 个" in note
    assert "f7.png" not in note


def test_fold_harvest_user_is_system_note_not_bare_template():
    rows = [
        _msg("user", "帮我写报告"),
        _msg("assistant", "先派团队"),
        _msg(
            "user",
            "【系统收口】后台团队本波任务已全部完成。请综合队员产出，按终稿纪律向老板报告本波结果。\n\n"
            "团队成品：\n交付文件 ready.md",
            origin="execution_harvest",
            harvest_kind="success",
        ),
        _msg("assistant", "终稿在这里"),
    ]
    out = _fold_history_messages(rows)
    assert out[0] == {"role": "user", "content": "帮我写报告"}
    assert out[1] == {"role": "assistant", "content": "先派团队"}
    note = out[2]
    assert note["role"] == "user"
    assert note["content"].startswith("（系统注记：")
    assert "已收口" in note["content"]
    assert "已全部完成" not in note["content"]
    assert "【系统收口】" not in note["content"]
    assert "团队成品" in note["content"]
    assert "ready.md" in note["content"]
    assert out[3] == {"role": "assistant", "content": "终稿在这里"}


def test_fold_harvest_prefix_without_origin_still_notes():
    """Legacy rows may only have the structured prefix."""
    out = _fold_history_messages(
        [
            _msg(
                "user",
                "【系统收口】后台团队任务已结束，但有队员失败。请综合已有产出。",
            )
        ]
    )
    assert len(out) == 1
    assert out[0]["role"] == "user"
    assert out[0]["content"].startswith("（系统注记：")
    assert "已全部完成" not in out[0]["content"]
    assert "【系统收口】" not in out[0]["content"]


def test_fold_harvest_failure_kind_lead():
    out = _fold_history_messages(
        [
            _msg(
                "user",
                "【系统收口】后台团队任务已结束，但有队员失败。",
                origin="execution_harvest",
                harvest_kind="failure",
            )
        ]
    )
    assert "失败" in out[0]["content"]
    assert out[0]["content"].startswith("（系统注记：")
