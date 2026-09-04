"""browser snapshot tree projection: keep newest full tree, omit older elements."""

from __future__ import annotations

import json

from agentcore.llm.provider.protocol import LLMMessage, ToolCall, ToolCallFunction
from agentcore.runtime.engine.browser_snapshot_clear import (
    compute_ref_delta,
    extract_element_refs,
    has_browser_tree_fields,
    omit_browser_tree_fields,
    project_omitted_browser_snapshots,
)
from agentcore.runtime.engine.round import build_request_window


def _snapshot_payload(
    *, elements: str, aria: str, version: int, url: str = "https://ex.com/"
) -> str:
    return json.dumps(
        {
            "action": "snapshot",
            "final_url": url,
            "snapshot_version": version,
            "keyframe": None,
            "untrusted_web_content": {
                "source_url": url,
                "title": f"Page v{version}",
                "elements": elements,
                "accessibility_tree": aria,
                "visible_text": f"body v{version}",
            },
        },
        ensure_ascii=False,
    )


def _snapshot_pair(call_id: str, *, version: int, elements: str) -> list[LLMMessage]:
    return [
        LLMMessage(
            role="assistant",
            content=None,
            tool_calls=[
                ToolCall(
                    id=call_id,
                    function=ToolCallFunction(name="browser_snapshot", arguments="{}"),
                )
            ],
        ),
        LLMMessage(
            role="tool",
            content=_snapshot_payload(
                elements=elements,
                aria=f"- document v{version}",
                version=version,
            ),
            tool_call_id=call_id,
        ),
    ]


def _tool_content(messages: list[LLMMessage], call_id: str) -> str:
    for message in messages:
        if message.role == "tool" and message.tool_call_id == call_id:
            return message.content or ""
    raise AssertionError(f"missing tool result {call_id}")


def test_extract_element_refs_ordered_unique():
    assert extract_element_refs("[e1] a\n[e2] b\n[e1] again") == ["e1", "e2"]
    assert extract_element_refs(None) == []


def test_compute_ref_delta_basic_and_cap():
    delta = compute_ref_delta("[e1] a\n[e2] b", "[e2] b\n[e3] c")
    assert delta["added"] == ["e3"]
    assert delta["removed"] == ["e1"]
    assert "truncated" not in delta

    many_before = "\n".join(f"[e{i}] x" for i in range(1, 120))
    many_after = "\n".join(f"[e{i}] x" for i in range(50, 200))
    capped = compute_ref_delta(many_before, many_after, max_refs=10)
    assert len(capped["added"]) == 10
    assert len(capped["removed"]) == 10
    assert capped["truncated"] is True


def test_two_snapshots_only_latest_keeps_elements():
    msgs: list[LLMMessage] = [LLMMessage(role="user", content="go")]
    msgs += _snapshot_pair("s0", version=1, elements="[e1] old")
    msgs += _snapshot_pair("s1", version=2, elements="[e2] new")

    out = project_omitted_browser_snapshots(msgs, keep_recent=1)

    old = json.loads(_tool_content(out, "s0"))
    new = json.loads(_tool_content(out, "s1"))

    uw_old = old["untrusted_web_content"]
    assert "elements" not in uw_old
    assert "accessibility_tree" not in uw_old
    assert uw_old["omitted"] is True
    assert uw_old["visible_text"] == "body v1"
    assert old["action"] == "snapshot"
    assert old["snapshot_version"] == 1
    assert old["final_url"] == "https://ex.com/"
    assert old["ref_delta"] == {"added": ["e2"], "removed": ["e1"]}

    uw_new = new["untrusted_web_content"]
    assert uw_new["elements"] == "[e2] new"
    assert uw_new["accessibility_tree"] == "- document v2"
    assert "omitted" not in uw_new
    assert "ref_delta" not in new


def test_ref_delta_stable_across_multi_mutation_fold():
    """Three trees: s0 delta vs s1 stays fixed when s2 arrives; s1 gets delta vs s2."""
    base: list[LLMMessage] = [LLMMessage(role="user", content="go")]
    base += _snapshot_pair("s0", version=1, elements="[e1] a\n[e2] b")
    mid = base + _snapshot_pair("s1", version=2, elements="[e2] b\n[e3] c")
    win1 = project_omitted_browser_snapshots(mid, keep_recent=1)
    full = mid + _snapshot_pair("s2", version=3, elements="[e3] c\n[e4] d")
    win2 = project_omitted_browser_snapshots(full, keep_recent=1)

    assert _tool_content(win1, "s0") == _tool_content(win2, "s0")
    s0 = json.loads(_tool_content(win2, "s0"))
    assert s0["ref_delta"] == {"added": ["e3"], "removed": ["e1"]}

    s1 = json.loads(_tool_content(win2, "s1"))
    assert s1["untrusted_web_content"]["omitted"] is True
    assert s1["ref_delta"] == {"added": ["e4"], "removed": ["e2"]}
    assert "elements" not in s1["untrusted_web_content"]

    s2 = json.loads(_tool_content(win2, "s2"))
    assert s2["untrusted_web_content"]["elements"] == "[e3] c\n[e4] d"
    assert "ref_delta" not in s2


def test_single_snapshot_noop_same_object():
    msgs: list[LLMMessage] = [LLMMessage(role="user", content="go")]
    msgs += _snapshot_pair("s0", version=1, elements="[e1] only")
    out = project_omitted_browser_snapshots(msgs, keep_recent=1)
    assert out is msgs


def test_omit_stable_across_rounds():
    """Prefix-cache: same original → identical omitted bytes after a newer snapshot arrives."""
    base: list[LLMMessage] = [LLMMessage(role="user", content="go")]
    base += _snapshot_pair("s0", version=1, elements="[e1] old")
    win1 = project_omitted_browser_snapshots(
        base + _snapshot_pair("s1", version=2, elements="[e2] mid"),
        keep_recent=1,
    )
    win2 = project_omitted_browser_snapshots(
        base
        + _snapshot_pair("s1", version=2, elements="[e2] mid")
        + _snapshot_pair("s2", version=3, elements="[e3] new"),
        keep_recent=1,
    )
    assert _tool_content(win1, "s0") == _tool_content(win2, "s0")
    assert has_browser_tree_fields(_tool_content(win2, "s2"))
    assert not has_browser_tree_fields(_tool_content(win2, "s1"))


def test_idempotent():
    msgs: list[LLMMessage] = [LLMMessage(role="user", content="go")]
    msgs += _snapshot_pair("s0", version=1, elements="[e1] old")
    msgs += _snapshot_pair("s1", version=2, elements="[e2] new")
    once = project_omitted_browser_snapshots(msgs, keep_recent=1)
    twice = project_omitted_browser_snapshots(once, keep_recent=1)
    assert _tool_content(once, "s0") == _tool_content(twice, "s0")
    assert twice is once  # second pass no-ops (only one tree left)


def test_non_browser_and_console_untouched():
    console = json.dumps(
        {
            "action": "console",
            "final_url": "https://ex.com/",
            "untrusted_web_content": {
                "source_url": "https://ex.com/",
                "console_messages": [{"level": "error", "text": "x"}],
            },
        },
        ensure_ascii=False,
    )
    msgs = [
        LLMMessage(role="user", content="go"),
        LLMMessage(
            role="assistant",
            content=None,
            tool_calls=[
                ToolCall(
                    id="c0",
                    function=ToolCallFunction(name="browser_console", arguments="{}"),
                ),
                ToolCall(
                    id="f0",
                    function=ToolCallFunction(
                        name="file_read",
                        arguments=json.dumps({"path": "a.py"}),
                    ),
                ),
            ],
        ),
        LLMMessage(role="tool", content=console, tool_call_id="c0"),
        LLMMessage(role="tool", content="print('hi')", tool_call_id="f0"),
    ]
    msgs += _snapshot_pair("s0", version=1, elements="[e1]")
    msgs += _snapshot_pair("s1", version=2, elements="[e2]")
    out = project_omitted_browser_snapshots(msgs, keep_recent=1)
    assert _tool_content(out, "c0") == console
    assert _tool_content(out, "f0") == "print('hi')"


def test_omit_helper_preserves_small_fields():
    raw = _snapshot_payload(elements="BIG", aria="TREE", version=7, url="https://a.test/")
    omitted = omit_browser_tree_fields(raw, ref_delta={"added": ["e9"], "removed": []})
    data = json.loads(omitted)
    assert data["snapshot_version"] == 7
    assert data["final_url"] == "https://a.test/"
    assert data["action"] == "snapshot"
    assert data["ref_delta"] == {"added": ["e9"], "removed": []}
    assert data["untrusted_web_content"]["omitted"] is True
    assert data["untrusted_web_content"]["title"] == "Page v7"
    assert data["untrusted_web_content"]["visible_text"] == "body v7"
    assert "elements" not in data["untrusted_web_content"]


def test_unified_browser_name_folds_like_legacy_snapshot():
    """Live ``browser`` + action=snapshot is a tree candidate; old name still works."""
    msgs: list[LLMMessage] = [LLMMessage(role="user", content="go")]
    msgs += [
        LLMMessage(
            role="assistant",
            content=None,
            tool_calls=[
                ToolCall(
                    id="s0",
                    function=ToolCallFunction(
                        name="browser", arguments='{"action":"snapshot"}'
                    ),
                )
            ],
        ),
        LLMMessage(
            role="tool",
            content=_snapshot_payload(
                elements="[e1] old", aria="- document v1", version=1
            ),
            tool_call_id="s0",
        ),
    ]
    msgs += _snapshot_pair("s1", version=2, elements="[e2] new")
    out = project_omitted_browser_snapshots(msgs, keep_recent=1)
    old = json.loads(_tool_content(out, "s0"))
    assert old["untrusted_web_content"].get("omitted") is True
    assert "elements" not in old["untrusted_web_content"]


def test_build_request_window_applies_projection():
    msgs: list[LLMMessage] = [LLMMessage(role="user", content="go")]
    msgs += _snapshot_pair("s0", version=1, elements="[e1] old")
    msgs += _snapshot_pair("s1", version=2, elements="[e2] new")

    out = build_request_window(msgs, investigation_tools=frozenset(), round_idx=0)
    assert out is not msgs
    old = json.loads(_tool_content(out, "s0"))
    new = json.loads(_tool_content(out, "s1"))
    assert old["untrusted_web_content"].get("omitted") is True
    assert "elements" not in old["untrusted_web_content"]
    assert old["ref_delta"]["added"] == ["e2"]
    assert new["untrusted_web_content"]["elements"] == "[e2] new"
