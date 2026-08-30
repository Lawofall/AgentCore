"""Cross-turn futile tool_call → one-shot prior_futile_retries prompt hint."""

from __future__ import annotations

import json

import pytest

from agentcore.runtime.delegate.prior_futile_retries import (
    _MAX_ITEMS,
    _MAX_LINE_CHARS,
    extract_prior_futile_retries,
    render_prior_futile_retries,
)
from agentcore.runtime.facts import CROSS_TURN_RETRY_KEY, CrossTurnRetry, FactKind
from agentcore.runtime.journal.entries import KIND_TURN_END


def _tool_call(
    *,
    name: str = "file_write",
    arguments: object = None,
    success: bool = False,
    cross_turn_retry: str | None = CrossTurnRetry.FUTILE.value,
    extra: dict | None = None,
) -> dict:
    if arguments is None:
        arguments = json.dumps({"path": "src/a.py", "content": "FULL-JSON-BODY"})
    elif isinstance(arguments, dict):
        arguments = json.dumps(arguments)
    payload: dict = {
        "run_id": "run-1",
        "tool_call_id": "tc-1",
        "name": name,
        "arguments": arguments,
        "result": "denied",
        "success": success,
    }
    if extra:
        payload.update(extra)
    if cross_turn_retry is not None:
        payload[CROSS_TURN_RETRY_KEY] = cross_turn_retry
    return {"kind": FactKind.TOOL_CALL.value, "payload": payload, "ts": None}


def test_no_futile_yields_empty_extract_and_empty_render():
    assert extract_prior_futile_retries(None) == []
    assert extract_prior_futile_retries([]) == []
    assert (
        extract_prior_futile_retries(
            [{"kind": KIND_TURN_END, "payload": {"finish_reason": "end_turn"}}]
        )
        == []
    )
    assert extract_prior_futile_retries([_tool_call(success=True, cross_turn_retry=None)]) == []
    assert (
        extract_prior_futile_retries(
            [_tool_call(cross_turn_retry=CrossTurnRetry.NOT_FUTILE.value)]
        )
        == []
    )
    assert extract_prior_futile_retries([_tool_call(cross_turn_retry="maybe")]) == []
    assert render_prior_futile_retries([]) == ""


def test_futile_renders_bounded_identifiers_not_full_json():
    args = {
        "path": "src/a.py",
        "content": "hello world " * 40,
        "metadata": {"nested": True, "blob": "x" * 200},
    }
    rows = extract_prior_futile_retries([_tool_call(arguments=args)])
    assert rows == [{"name": "file_write", "identifier": "path=src/a.py"}]
    text = render_prior_futile_retries(rows)
    assert text.startswith("<上轮徒劳重试>\n")
    assert text.endswith("</上轮徒劳重试>")
    assert "path=src/a.py" in text
    assert "file_write" in text
    assert "一次性" in text and "可忽略" in text
    assert "新目标优先" in text
    assert "不得据此拒绝" in text
    assert json.dumps(args) not in text
    assert "hello world" not in text
    assert "FULL-JSON-BODY" not in text
    assert '"content"' not in text
    body_lines = [
        ln for ln in text.splitlines() if ln.startswith("- ")
    ]
    assert len(body_lines) == 1
    assert len(body_lines[0]) <= _MAX_LINE_CHARS + 1


def test_item_cap_and_line_clip():
    entries = [
        _tool_call(arguments={"path": f"src/f{i}.py"}) for i in range(_MAX_ITEMS + 5)
    ]
    rows = extract_prior_futile_retries(entries)
    assert len(rows) == _MAX_ITEMS
    text = render_prior_futile_retries(rows)
    assert text.count("\n- ") == _MAX_ITEMS

    long_path = "AgentCore/" + ("very-long-segment/" * 8) + "out.md"
    clipped = render_prior_futile_retries(
        extract_prior_futile_retries([_tool_call(arguments={"path": long_path})])
    )
    line = next(ln for ln in clipped.splitlines() if ln.startswith("- "))
    assert len(line) <= _MAX_LINE_CHARS + 1
    assert line.endswith("…")
    assert json.dumps({"path": long_path}) not in clipped


def test_unknown_and_not_futile_mixed_out_only_futile_in():
    entries = [
        _tool_call(name="file_read", arguments={"path": "ok.md"}, success=True, cross_turn_retry=None),
        _tool_call(
            name="file_write",
            arguments={"path": "timeout.md"},
            cross_turn_retry=CrossTurnRetry.NOT_FUTILE.value,
        ),
        _tool_call(name="file_write", arguments={"path": "wall.md"}),
        _tool_call(name="file_write", arguments={"path": "wall.md"}),  # dup, not a counter
        _tool_call(name="grep", arguments={"pattern": "TODO", "path": "src"}, extra={"code": "x"}),
    ]
    rows = extract_prior_futile_retries(entries)
    assert [r["identifier"] for r in rows] == ["path=wall.md", "path=src"]
    text = render_prior_futile_retries(rows)
    assert "wall.md" in text
    assert "timeout.md" not in text
    assert "ok.md" not in text
    assert "TODO" not in text  # pattern loses to path in the identifier key order


@pytest.mark.asyncio
async def test_build_hint_empty_when_prior_has_no_futile(monkeypatch):
    from agentcore.runtime.delegate import prior_futile_retries as mod

    async def _empty(**_kwargs):
        return []

    async def _not_futile(**_kwargs):
        return [_tool_call(cross_turn_retry=CrossTurnRetry.NOT_FUTILE.value)]

    async def _missing(**_kwargs):
        return [_tool_call(cross_turn_retry=None)]

    monkeypatch.setattr(mod, "_load_latest_prior_journal", _empty)
    assert await mod.build_prior_futile_retries_hint(conversation_id="c1") == ""

    monkeypatch.setattr(mod, "_load_latest_prior_journal", _not_futile)
    assert await mod.build_prior_futile_retries_hint(conversation_id="c1") == ""

    monkeypatch.setattr(mod, "_load_latest_prior_journal", _missing)
    assert await mod.build_prior_futile_retries_hint(conversation_id="c1") == ""


@pytest.mark.asyncio
async def test_build_hint_excludes_current_turn_id(monkeypatch):
    from agentcore.runtime.delegate import prior_futile_retries as mod

    captured: dict[str, object] = {}

    async def _hit(**kwargs):
        captured.update(kwargs)
        return [_tool_call(arguments={"path": "src/wall.py"})]

    monkeypatch.setattr(mod, "_load_latest_prior_journal", _hit)
    text = await mod.build_prior_futile_retries_hint(
        conversation_id="c1",
        exclude_message_id="turn-now",
    )
    assert captured["conversation_id"] == "c1"
    assert captured["exclude_turn_id"] == "turn-now"
    assert "<上轮徒劳重试>" in text
    assert text.count("<上轮徒劳重试>") == 1
    assert "path=src/wall.py" in text
