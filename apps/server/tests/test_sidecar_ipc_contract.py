"""Lock the Sidecar IPC wire shape (TS ``sidecar-contract`` ↔ Python ``trim_result`` / resume)."""

from __future__ import annotations

from typing import Any

from agentcore.sidecar.server_pkg.ipc_contract import (
    resume_rpc_param_keys,
    resume_rpc_required_keys,
    turn_result_keys,
    turn_result_usage_keys,
)
from agentcore.sidecar.server_pkg.result import trim_result


def test_trim_result_keys_match_contract():
    """``trim_result`` must emit exactly the ``SidecarTurnResult`` keys (camelCase)."""
    out = trim_result(
        "t1",
        {
            "message_id": "m1",
            "content": "hi",
            "reasoning_content": None,
            "finish_reason": "stop",
            "rounds": 2,
            "input_tokens": 1,
            "output_tokens": 2,
            "reasoning_tokens": 3,
            "cache_hit_tokens": 4,
            "cache_miss_tokens": 5,
            "citations": [],
            "error": None,
        },
        model="deepseek-v4-flash",
    )
    assert tuple(out.keys()) == turn_result_keys()
    assert tuple(out["usage"].keys()) == turn_result_usage_keys()


def test_trim_result_always_surfaces_model_string():
    """``model`` is required on the wire — never omitted/null (badge + fallback warning)."""
    out = trim_result("t1", {"finish_reason": "stop"}, model="gpt-4o")
    assert out["model"] == "gpt-4o"
    assert isinstance(out["model"], str)


def test_resume_rpc_contract_documents_python_consumer_keys():
    """Python ``_on_resume`` reads only these JSON-RPC param keys (+ optional inference)."""
    expected = {
        "messageId",
        "conversationId",
        "traceId",
        "decision",
        "note",
        "selected",
        "inference",
        "foldersAuth",
        "accountAuth",
        "browserBridge",
        "permissionAxes",
        "userId",
        "folderId",
        "localRootId",
        "localSubpath",
    }
    assert set(resume_rpc_param_keys()) == expected
    assert set(resume_rpc_required_keys()) == {
        "messageId",
        "conversationId",
        "traceId",
        "decision",
        "note",
    }


def test_trim_result_runs_null_when_no_journal():
    """Plain chat turns surface ``runs: null`` — same optional/null contract as TS."""
    out = trim_result("t1", {"finish_reason": "stop"}, model="m")
    assert out["runs"] is None


def test_trim_result_forwards_citations_and_runs_verbatim():
    citations: list[dict[str, Any]] = [
        {"url": "https://x", "title": "t", "snippet": "s", "site": "x"}
    ]
    out = trim_result(
        "t1",
        {
            "finish_reason": "stop",
            "citations": citations,
            "journal_entries": [{"kind": "turn_end", "payload": {"finish_reason": "stop"}}],
        },
        model="m",
    )
    assert out["citations"] == citations
    assert out["runs"] is not None
    assert "events" in out["runs"]
