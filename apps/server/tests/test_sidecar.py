"""Sidecar Slice 1 walking-skeleton tests (双模式工作区 §十).

Two layers, both zero-LLM (a scripted provider stands in for DeepSeek, mirroring
``test_evals_smoke``):

- **protocol** — line framing round-trips and rejects garbage (pure).
- **server** — a full turn driven over ``handle_line``: ``initialize`` binds a real
  local directory, ``startTurn`` runs ``run_chat_pipeline`` against it, the engine's
  events surface as ``turn/event`` notifications, and the deferred startTurn
  response carries the final answer. The turn issues a ``file_list`` against the
  bound temp dir, proving the engine touches the REAL local disk (not a channel).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any

import pytest

from agentcore.llm.credentials import (
    INFERENCE_CONVERSATION_HEADER,
    INFERENCE_MESSAGE_HEADER,
    INFERENCE_TRACE_HEADER,
    LLMCredentials,
)
from agentcore.llm.provider.protocol import LLMChunk, TokenUsage, ToolCallDelta
from agentcore.runtime.approvals import ApprovalDecision
from agentcore.runtime.interaction import InteractionKind, default_interaction_registry
from agentcore.sidecar import protocol
from agentcore.sidecar.server import SidecarServer


class _ScriptedProvider:
    """Yields one pre-scripted round of chunks per ``stream`` call (duck-typed)."""

    def __init__(self, rounds: list[list[LLMChunk]]) -> None:
        self._rounds = rounds
        self.calls = 0

    async def stream(self, request):  # noqa: ANN001 - duck-typed stand-in
        chunks = self._rounds[self.calls] if self.calls < len(self._rounds) else []
        self.calls += 1
        for chunk in chunks:
            yield chunk

    async def close(self) -> None:  # pipeline calls this in its finally
        return None


# --- protocol (pure) ---------------------------------------------------------


def test_protocol_round_trip_keeps_one_physical_line():
    line = protocol.encode_line(
        protocol.make_notification("turn/event", {"x": "中文\nwith newline"})
    )
    assert line.endswith("\n")
    # The newline INSIDE the string must be JSON-escaped, so the only raw newline
    # is the trailing frame terminator — one message is always one readline.
    assert "\n" not in line[:-1]

    message = protocol.decode_line(line)
    assert message["method"] == "turn/event"
    assert message["params"]["x"] == "中文\nwith newline"


def test_protocol_rejects_non_object():
    with pytest.raises(protocol.ProtocolError):
        protocol.decode_line("[1, 2, 3]")
    with pytest.raises(protocol.ProtocolError):
        protocol.decode_line("{not json}")


def test_protocol_tolerates_leading_bom():
    # A stray UTF-8 BOM on the first line (some text producers prepend one) must
    # not break the decode — json.loads alone would reject it.
    message = protocol.decode_line('\ufeff{"jsonrpc":"2.0","id":1,"method":"x"}')
    assert message["method"] == "x"


# --- server ------------------------------------------------------------------


def _recorder() -> tuple[list[dict[str, Any]], Any]:
    sent: list[dict[str, Any]] = []

    async def write_line(line: str) -> None:
        sent.append(json.loads(line))

    return sent, write_line


# Present on initialize / startTurn so the missing-inference gate does not fire;
# pipelines are mocked in these unit tests.
_FAKE_INFERENCE = {
    "baseUrl": "http://test.local/v1/inference/v1",
    "apiKey": "test-inference-tok",
    "model": "test-model",
}

_CLIENT_TURN_IDS = {
    "userMessageId": "11111111-1111-4111-8111-111111111111",
    "messageId": "22222222-2222-4222-8222-222222222222",
    "traceId": "a" * 32,
}


def _response(sent: list[dict[str, Any]], request_id: Any) -> dict[str, Any]:
    return next(m for m in sent if m.get("id") == request_id)


def _events(sent: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [m["params"]["event"] for m in sent if m.get("method") == "turn/event"]


@pytest.fixture(autouse=True)
def _stub_conversation_folder_id(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest):
    """Unit tests without Postgres: bare folder_id=None unless the dedicated DB-mock test."""
    if request.node.name in {
        "test_sidecar_start_turn_passes_conversation_folder_id",
        "test_load_conversation_folder_id_normalizes_blank",
        "test_load_conversation_folder_id_connection_refused",
        "test_sidecar_start_turn_db_unavailable_seals_outbox",
        "test_sidecar_start_turn_folder_id_param_skips_db",
        "test_sidecar_start_turn_local_binding_reaches_pipeline",
        "test_sidecar_start_turn_explicit_null_folder_id_skips_db",
        "test_sidecar_start_turn_absent_folder_id_still_loads_db",
        "test_resolve_start_turn_folder_id_key_present",
        "test_resolve_rpc_folder_binding_key_presence",
        "test_apply_rpc_folder_binding_overlays_folder_id",
    }:
        return

    async def _none(_conversation_id: str) -> None:
        return None

    monkeypatch.setattr(
        "agentcore.sidecar.server_pkg.turns.load_conversation_folder_id",
        _none,
    )


def test_initialize_rejects_missing_root(tmp_path):
    sent, write_line = _recorder()
    server = SidecarServer(write_line)
    missing = tmp_path / "does-not-exist"

    asyncio.run(
        server.handle_line(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {"userId": "u", "workspaceRoot": str(missing)},
                }
            )
        )
    )

    resp = _response(sent, 1)
    assert "error" in resp
    assert resp["error"]["code"] == protocol.INVALID_PARAMS


def test_start_turn_before_initialize_is_refused(tmp_path):
    sent, write_line = _recorder()
    server = SidecarServer(write_line)

    asyncio.run(
        server.handle_line(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 9,
                    "method": "startTurn",
                    "params": {"turnId": "t", "conversationId": "c", "userMessage": "hi"},
                }
            )
        )
    )

    resp = _response(sent, 9)
    assert resp["error"]["code"] == protocol.NOT_INITIALIZED


def test_sidecar_runs_a_turn_on_the_local_dir(tmp_path, monkeypatch):
    # Seed a real file in the directory the sidecar will be bound to.
    (tmp_path / "hello.txt").write_text("hi from disk", encoding="utf-8")

    # Round 0: the CEO calls file_list against the bound dir. Round 1: it answers.
    provider = _ScriptedProvider(
        [
            [
                LLMChunk(
                    delta_tool_calls=[
                        ToolCallDelta(
                            index=0,
                            id="call_ls",
                            function_name="file_list",
                            arguments_delta='{"directory": ".", "pattern": "*"}',
                        )
                    ]
                ),
                LLMChunk(
                    finish_reason="tool_calls",
                    usage=TokenUsage(input_tokens=10, output_tokens=4),
                ),
            ],
            [
                LLMChunk(delta_content="已列出本地文件。"),
                LLMChunk(
                    finish_reason="stop",
                    usage=TokenUsage(input_tokens=5, output_tokens=3),
                ),
            ],
        ]
    )
    # The engine builds its provider via build_turn_router — swap that seam for the
    # scripted one (mirrors the eval harness note: team path has no provider injection seam).
    async def _fake_build_turn_router(*_a, **_k):
        return provider

    monkeypatch.setattr(
        "agentcore.runtime.pipeline.build_turn_router", _fake_build_turn_router
    )

    sent, write_line = _recorder()
    server = SidecarServer(write_line)

    async def drive() -> None:
        await server.handle_line(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "userId": "u",
                        "workspaceRoot": str(tmp_path),
                        "approvalsEnabled": False,
                        "inference": _FAKE_INFERENCE,
                    },
                }
            )
        )
        await server.handle_line(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "startTurn",
                    "params": {
                        **_CLIENT_TURN_IDS,
                        "turnId": "t1",
                        "conversationId": "c1",
                        "userMessage": "列出本地文件",
                    },
                }
            )
        )
        # The startTurn response is deferred to turn completion — await the task.
        await asyncio.gather(*list(server._turns.values()))

    asyncio.run(drive())

    # initialize acknowledged.
    init = _response(sent, 1)
    assert init["result"]["ok"] is True
    assert init["result"]["protocolVersion"] == protocol.PROTOCOL_VERSION

    # The turn streamed events, ran the tool against the REAL dir, and answered.
    events = _events(sent)
    types = [e["type"] for e in events]
    assert "tool_use_start" in types
    assert "content_delta" in types
    assert "message_end" in types
    for note in sent:
        if note.get("method") != "turn/event":
            continue
        assert note["params"]["conversationId"] == "c1"
        assert note["params"]["turnId"] == "t1"

    tool_start = next(e for e in events if e["type"] == "tool_use_start")
    assert tool_start["payload"]["tool_name"] == "file_list"

    tool_end = next(e for e in events if e["type"] == "tool_use_end")
    # The engine listed the bound temp dir → it saw the seeded file (real disk).
    assert "hello.txt" in tool_end["payload"]["result"]

    # The deferred startTurn response carries the final answer.
    done = _response(sent, 2)
    assert done["result"]["content"] == "已列出本地文件。"
    assert done["result"]["finishReason"] == "end_turn"
    assert done["result"]["turnId"] == "t1"
    # initialize 传入了 inference ⇒ 结果如实回报该档模型（非 platform 回退）。
    assert done["result"]["model"] == _FAKE_INFERENCE["model"]


# --- respond (审批 / 交互结算回 sidecar) -------------------------------------


def test_respond_settles_approval_with_enum_decision():
    """respond builds the SAME typed result the cloud route does: an approval settles
    with an ApprovalDecision *enum*, not a bare string. The gate's grant check uses
    identity (``decision is ApprovalDecision.APPROVE_ALWAYS``), so a raw string would
    silently fail it — this guards that the sidecar/cloud construction stays shared.
    """
    registry = default_interaction_registry()
    sent, write_line = _recorder()
    server = SidecarServer(write_line)

    async def drive() -> Any:
        fut = registry.create("call_1", "c1", kind=InteractionKind.APPROVAL)
        try:
            await server.handle_line(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "respond",
                        "params": {
                            "requestId": "call_1",
                            "conversationId": "c1",
                            "result": {
                                "kind": "approval",
                                "decision": "approve_always",
                            },
                        },
                    }
                )
            )
            return fut.result() if fut.done() else None
        finally:
            registry.discard("call_1")

    decision = asyncio.run(drive())
    assert _response(sent, 1)["result"]["resolved"] is True
    assert decision is ApprovalDecision.APPROVE_ALWAYS


def test_respond_refuses_kind_mismatch():
    """A respond whose kind ≠ the pending interaction's kind is refused
    (``resolved: false``) and leaves the Future pending — mirrors the cloud route's
    kind guard, so a stray approval can't settle a plan_review (or vice versa)."""
    registry = default_interaction_registry()
    sent, write_line = _recorder()
    server = SidecarServer(write_line)

    async def drive() -> bool:
        fut = registry.create("cp_1", "c1", kind=InteractionKind.PLAN_REVIEW)
        try:
            await server.handle_line(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "respond",
                        "params": {
                            "requestId": "cp_1",
                            "conversationId": "c1",
                            "result": {"kind": "approval", "decision": "approve"},
                        },
                    }
                )
            )
            return fut.done()
        finally:
            registry.discard("cp_1")

    settled = asyncio.run(drive())
    assert _response(sent, 1)["result"]["resolved"] is False
    assert settled is False


def test_respond_rejects_malformed_result():
    """A respond whose result fails validation (the kind's required field missing)
    returns INVALID_PARAMS, not a silent no-op."""
    sent, write_line = _recorder()
    server = SidecarServer(write_line)

    asyncio.run(
        server.handle_line(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "respond",
                    "params": {
                        "requestId": "x",
                        "conversationId": "c1",
                        "result": {"kind": "approval"},  # missing `decision`
                    },
                }
            )
        )
    )

    resp = _response(sent, 1)
    assert resp["error"]["code"] == protocol.INVALID_PARAMS


def test_sidecar_binds_local_backend_with_approvals(tmp_path, monkeypatch):
    """The sidecar binds a ``location="local"`` workspace (root = the user's real disk)
    and runs the turn with approvals on — so the engine forwards the gate to a worker's
    machine-touching tools (delegate keys off ``backend.location == "local"``). A default
    ``"server"`` backend would leave workers un-gated even with approvals enabled.
    """
    captured: dict[str, Any] = {}

    async def fake_pipeline(**kwargs: Any) -> dict[str, Any]:
        captured["location"] = kwargs["backend"].location
        captured["approvals_enabled"] = kwargs["approvals_enabled"]
        kwargs["sink"].close()  # let the event pump drain and the turn finish
        return {"finish_reason": "end_turn", "content": "ok", "rounds": 1}

    monkeypatch.setattr("agentcore.sidecar.server.run_chat_pipeline", fake_pipeline)

    sent, write_line = _recorder()
    server = SidecarServer(write_line)

    async def drive() -> None:
        await server.handle_line(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "userId": "u",
                        "workspaceRoot": str(tmp_path),
                        "approvalsEnabled": True,
                        "inference": _FAKE_INFERENCE,
                    },
                }
            )
        )
        await server.handle_line(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "startTurn",
                    "params": {
                        **_CLIENT_TURN_IDS,
                        "turnId": "t1",
                        "conversationId": "c1",
                        "userMessage": "改个文件",
                    },
                }
            )
        )
        await asyncio.gather(*list(server._turns.values()))

    asyncio.run(drive())
    assert captured["location"] == "local"
    assert captured["approvals_enabled"] is True


def test_sidecar_start_turn_passes_conversation_folder_id(tmp_path, monkeypatch):
    """startTurn loads conversation.folder_id from DB (cloud-shaped) into the pipeline.

    Hardcoding folder_id=None broke project memory scope + suspension.folder_id on
    local turns. Mock the unscoped repo lookup; assert run_chat_pipeline gets it.
    Absent ``folderId`` key (old desktop) still uses this path.
    """
    captured: dict[str, Any] = {}

    class _Conv:
        folder_id = "folder-from-db"

    class _Repo:
        def __init__(self, _session: Any) -> None:
            pass

        async def get_by_id_unscoped(self, conversation_id: str) -> _Conv:
            assert conversation_id == "c1"
            return _Conv()

    class _SessionCtx:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, *args: Any) -> None:
            return None

    async def fake_pipeline(**kwargs: Any) -> dict[str, Any]:
        captured["folder_id"] = kwargs.get("folder_id")
        kwargs["sink"].close()
        return {"finish_reason": "end_turn", "content": "ok", "rounds": 1}

    async def fake_baseline(**kwargs: Any) -> None:
        captured["baseline_folder_id"] = kwargs.get("folder_id")

    monkeypatch.setattr("agentcore.sidecar.server.run_chat_pipeline", fake_pipeline)
    monkeypatch.setattr(
        "agentcore.workspace.turn_baseline.maybe_capture_turn_baseline",
        fake_baseline,
    )
    monkeypatch.setattr(
        "agentcore.db.base.async_session_factory",
        lambda: _SessionCtx(),
    )
    monkeypatch.setattr(
        "agentcore.db.repositories.ConversationRepository",
        _Repo,
    )

    sent, write_line = _recorder()
    server = SidecarServer(write_line)

    async def drive() -> None:
        await server.handle_line(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "userId": "u",
                        "workspaceRoot": str(tmp_path),
                        "approvalsEnabled": True,
                        "inference": _FAKE_INFERENCE,
                    },
                }
            )
        )
        await server.handle_line(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "startTurn",
                    "params": {
                        **_CLIENT_TURN_IDS,
                        "turnId": "t1",
                        "conversationId": "c1",
                        "userMessage": "项目里查记忆",
                    },
                }
            )
        )
        await asyncio.gather(*list(server._turns.values()))

    asyncio.run(drive())
    assert captured["folder_id"] == "folder-from-db"
    assert captured["baseline_folder_id"] == "folder-from-db"


@pytest.mark.asyncio
async def test_resolve_start_turn_folder_id_key_present(monkeypatch):
    """``folderId`` key present → normalize, never call DB loader (PG may be down)."""
    from agentcore.sidecar.server_pkg.turns import resolve_start_turn_folder_id

    called = {"db": False}

    async def boom(_conversation_id: str) -> str | None:
        called["db"] = True
        raise AssertionError("DB loader must not run when folderId key is present")

    monkeypatch.setattr(
        "agentcore.sidecar.server_pkg.turns.load_conversation_folder_id",
        boom,
    )
    assert await resolve_start_turn_folder_id({"folderId": "  proj-1  "}, "c1") == "proj-1"
    assert await resolve_start_turn_folder_id({"folderId": None}, "c1") is None
    assert await resolve_start_turn_folder_id({"folderId": ""}, "c1") is None
    assert await resolve_start_turn_folder_id({"folderId": "  "}, "c1") is None
    assert called["db"] is False


def test_resolve_rpc_folder_binding_key_presence():
    """``localRootId`` key present → injected; absent → not (DB fallback later)."""
    from agentcore.sidecar.server_pkg.turns import resolve_rpc_folder_binding

    assert resolve_rpc_folder_binding({}) == (False, None, "")
    assert resolve_rpc_folder_binding({"localRootId": "  root-1  "}) == (
        True,
        "root-1",
        "",
    )
    assert resolve_rpc_folder_binding(
        {"localRootId": "root-1", "localSubpath": "  apps/api  "}
    ) == (True, "root-1", "apps/api")
    assert resolve_rpc_folder_binding({"localRootId": None}) == (True, None, "")
    assert resolve_rpc_folder_binding({"localRootId": ""}) == (True, None, "")


def test_apply_rpc_folder_binding_overlays_folder_id():
    """Resume RPC ``folderId`` key present → overwrite frame; absent → keep."""
    from agentcore.runtime.suspension import AskUserSuspension
    from agentcore.sidecar.server_pkg.turns import apply_rpc_folder_binding_to_suspension

    def _frame(*, folder_id: str | None = "fold-old") -> AskUserSuspension:
        return AskUserSuspension(
            message_id="m1",
            conversation_id="c1",
            user_id="u1",
            captain_run_id="r1",
            checkpoint_id="cp-1",
            tool_call_id="tc1",
            base_system_prompt="sys",
            user_message="q",
            folder_id=folder_id,
            question="?",
        )

    kept = _frame()
    apply_rpc_folder_binding_to_suspension(kept, {})
    assert kept.folder_id == "fold-old"

    overwritten = _frame()
    apply_rpc_folder_binding_to_suspension(overwritten, {"folderId": "  fold-new  "})
    assert overwritten.folder_id == "fold-new"

    cleared = _frame()
    apply_rpc_folder_binding_to_suspension(cleared, {"folderId": None})
    assert cleared.folder_id is None

    blank = _frame()
    apply_rpc_folder_binding_to_suspension(blank, {"folderId": "  "})
    assert blank.folder_id is None

    # Binding overlay still works alongside folderId.
    bound = _frame(folder_id="fold-keep")
    apply_rpc_folder_binding_to_suspension(
        bound,
        {"folderId": "fold-rpc", "localRootId": "root-1", "localSubpath": "apps"},
    )
    assert bound.folder_id == "fold-rpc"
    assert bound.folder_binding_injected is True
    assert bound.folder_local_root_id == "root-1"
    assert bound.folder_local_subpath == "apps"


def test_sidecar_start_turn_local_binding_reaches_pipeline(tmp_path, monkeypatch):
    """Injected localRootId/localSubpath reach run_chat_pipeline (no Folder PG)."""
    captured: dict[str, Any] = {}

    async def fake_pipeline(**kwargs: Any) -> dict[str, Any]:
        captured["folder_id"] = kwargs.get("folder_id")
        captured["folder_binding_injected"] = kwargs.get("folder_binding_injected")
        captured["folder_local_root_id"] = kwargs.get("folder_local_root_id")
        captured["folder_local_subpath"] = kwargs.get("folder_local_subpath")
        kwargs["sink"].close()
        return {"finish_reason": "end_turn", "content": "ok", "rounds": 1}

    async def fake_baseline(**kwargs: Any) -> None:
        return None

    async def boom_db(_conversation_id: str) -> str | None:
        raise AssertionError("load_conversation_folder_id must not run")

    monkeypatch.setattr("agentcore.sidecar.server.run_chat_pipeline", fake_pipeline)
    monkeypatch.setattr(
        "agentcore.workspace.turn_baseline.maybe_capture_turn_baseline",
        fake_baseline,
    )
    monkeypatch.setattr(
        "agentcore.sidecar.server_pkg.turns.load_conversation_folder_id",
        boom_db,
    )

    sent, write_line = _recorder()
    server = SidecarServer(write_line)

    async def drive() -> None:
        await server.handle_line(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "userId": "u",
                        "workspaceRoot": str(tmp_path),
                        "approvalsEnabled": True,
                        "inference": _FAKE_INFERENCE,
                    },
                }
            )
        )
        await server.handle_line(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "startTurn",
                    "params": {
                        **_CLIENT_TURN_IDS,
                        "turnId": "t-bind",
                        "conversationId": "c-bind",
                        "userMessage": "hello",
                        "folderId": "fold-proj",
                        "localRootId": "root-xyz",
                        "localSubpath": "repos/app",
                    },
                }
            )
        )
        await asyncio.gather(*list(server._turns.values()))

    asyncio.run(drive())
    assert captured["folder_id"] == "fold-proj"
    assert captured["folder_binding_injected"] is True
    assert captured["folder_local_root_id"] == "root-xyz"
    assert captured["folder_local_subpath"] == "repos/app"
    assert "error" not in _response(sent, 2)


def test_rpc_agent_mentions_accepts_camel_and_snake():
    from agentcore.sidecar.server_pkg.turns import rpc_agent_mentions

    mentions = [{"agent_id": "w1", "role": "研究员"}]
    assert rpc_agent_mentions({"agentMentions": mentions}) == mentions
    assert rpc_agent_mentions({"agent_mentions": mentions}) == mentions
    assert rpc_agent_mentions({}) == []
    assert rpc_agent_mentions({"agentMentions": [{"agent_id": "", "role": "x"}]}) == []


def test_sidecar_start_turn_forwards_agent_mentions(tmp_path, monkeypatch):
    captured: dict[str, Any] = {}
    mentions = [{"agent_id": "agent_research", "role": "研究员"}]

    async def fake_pipeline(**kwargs: Any) -> dict[str, Any]:
        captured["agent_mentions"] = kwargs.get("agent_mentions")
        kwargs["sink"].close()
        return {"finish_reason": "end_turn", "content": "ok", "rounds": 1}

    async def fake_baseline(**kwargs: Any) -> None:
        return None

    monkeypatch.setattr("agentcore.sidecar.server.run_chat_pipeline", fake_pipeline)
    monkeypatch.setattr(
        "agentcore.workspace.turn_baseline.maybe_capture_turn_baseline",
        fake_baseline,
    )

    sent, write_line = _recorder()
    server = SidecarServer(write_line)

    async def drive() -> None:
        await server.handle_line(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "userId": "u",
                        "workspaceRoot": str(tmp_path),
                        "approvalsEnabled": True,
                        "inference": _FAKE_INFERENCE,
                    },
                }
            )
        )
        await server.handle_line(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "startTurn",
                    "params": {
                        **_CLIENT_TURN_IDS,
                        "turnId": "t-mention",
                        "conversationId": "c-mention",
                        "userMessage": "让研究员看一下",
                        "folderId": None,
                        "agentMentions": mentions,
                    },
                }
            )
        )
        await asyncio.gather(*list(server._turns.values()))

    asyncio.run(drive())
    assert captured["agent_mentions"] == mentions
    assert "error" not in _response(sent, 2)


def test_sidecar_start_turn_folder_id_param_skips_db(tmp_path, monkeypatch):
    """Injected folderId reaches pipeline without opening local PG."""
    captured: dict[str, Any] = {}
    db_calls = {"n": 0}

    async def fake_pipeline(**kwargs: Any) -> dict[str, Any]:
        captured["folder_id"] = kwargs.get("folder_id")
        kwargs["sink"].close()
        return {"finish_reason": "end_turn", "content": "ok", "rounds": 1}

    async def fake_baseline(**kwargs: Any) -> None:
        captured["baseline_folder_id"] = kwargs.get("folder_id")

    async def boom_db(_conversation_id: str) -> str | None:
        db_calls["n"] += 1
        raise AssertionError("load_conversation_folder_id must not run")

    monkeypatch.setattr("agentcore.sidecar.server.run_chat_pipeline", fake_pipeline)
    monkeypatch.setattr(
        "agentcore.workspace.turn_baseline.maybe_capture_turn_baseline",
        fake_baseline,
    )
    monkeypatch.setattr(
        "agentcore.sidecar.server_pkg.turns.load_conversation_folder_id",
        boom_db,
    )

    sent, write_line = _recorder()
    server = SidecarServer(write_line)

    async def drive() -> None:
        await server.handle_line(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "userId": "u",
                        "workspaceRoot": str(tmp_path),
                        "approvalsEnabled": True,
                        "inference": _FAKE_INFERENCE,
                    },
                }
            )
        )
        await server.handle_line(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "startTurn",
                    "params": {
                        **_CLIENT_TURN_IDS,
                        "turnId": "t1",
                        "conversationId": "c1",
                        "userMessage": "项目里查记忆",
                        "folderId": "folder-from-rpc",
                    },
                }
            )
        )
        await asyncio.gather(*list(server._turns.values()))

    asyncio.run(drive())
    assert db_calls["n"] == 0
    assert captured["folder_id"] == "folder-from-rpc"
    assert captured["baseline_folder_id"] == "folder-from-rpc"
    assert "error" not in _response(sent, 2)


def test_sidecar_start_turn_explicit_null_folder_id_skips_db(tmp_path, monkeypatch):
    """Explicit null folderId = bare chat; must not query PG (PG down still OK)."""
    from agentcore.db.errors import DATABASE_UNAVAILABLE_MESSAGE, DatabaseUnavailableError

    captured: dict[str, Any] = {}

    async def fake_pipeline(**kwargs: Any) -> dict[str, Any]:
        captured["folder_id"] = kwargs.get("folder_id")
        kwargs["sink"].close()
        return {"finish_reason": "end_turn", "content": "ok", "rounds": 1}

    async def boom_db(_conversation_id: str) -> str | None:
        raise DatabaseUnavailableError(DATABASE_UNAVAILABLE_MESSAGE)

    monkeypatch.setattr("agentcore.sidecar.server.run_chat_pipeline", fake_pipeline)
    monkeypatch.setattr(
        "agentcore.sidecar.server_pkg.turns.load_conversation_folder_id",
        boom_db,
    )

    sent, write_line = _recorder()
    server = SidecarServer(write_line)

    async def drive() -> None:
        await server.handle_line(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "userId": "u",
                        "workspaceRoot": str(tmp_path),
                        "approvalsEnabled": True,
                        "inference": _FAKE_INFERENCE,
                    },
                }
            )
        )
        await server.handle_line(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "startTurn",
                    "params": {
                        **_CLIENT_TURN_IDS,
                        "turnId": "t1",
                        "conversationId": "c1",
                        "userMessage": "裸聊",
                        "folderId": None,
                    },
                }
            )
        )
        await asyncio.gather(*list(server._turns.values()))

    asyncio.run(drive())
    assert captured["folder_id"] is None
    assert "error" not in _response(sent, 2)


def test_sidecar_start_turn_absent_folder_id_still_loads_db(tmp_path, monkeypatch):
    """Old desktop (no folderId key) still falls back to DB loader."""
    captured: dict[str, Any] = {}
    db_calls = {"n": 0}

    async def fake_pipeline(**kwargs: Any) -> dict[str, Any]:
        captured["folder_id"] = kwargs.get("folder_id")
        kwargs["sink"].close()
        return {"finish_reason": "end_turn", "content": "ok", "rounds": 1}

    async def fake_db(_conversation_id: str) -> str | None:
        db_calls["n"] += 1
        return "from-legacy-db"

    monkeypatch.setattr("agentcore.sidecar.server.run_chat_pipeline", fake_pipeline)
    monkeypatch.setattr(
        "agentcore.sidecar.server_pkg.turns.load_conversation_folder_id",
        fake_db,
    )

    sent, write_line = _recorder()
    server = SidecarServer(write_line)

    async def drive() -> None:
        await server.handle_line(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "userId": "u",
                        "workspaceRoot": str(tmp_path),
                        "approvalsEnabled": True,
                        "inference": _FAKE_INFERENCE,
                    },
                }
            )
        )
        await server.handle_line(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "startTurn",
                    "params": {
                        **_CLIENT_TURN_IDS,
                        "turnId": "t1",
                        "conversationId": "c1",
                        "userMessage": "旧桌面无 folderId",
                    },
                }
            )
        )
        await asyncio.gather(*list(server._turns.values()))

    asyncio.run(drive())
    assert db_calls["n"] == 1
    assert captured["folder_id"] == "from-legacy-db"


@pytest.mark.asyncio
async def test_load_conversation_folder_id_normalizes_blank(monkeypatch):
    """DB blank / whitespace folder_id → None (bare), never empty str."""
    from agentcore.sidecar.server_pkg.turns import load_conversation_folder_id

    class _Conv:
        folder_id = "  "

    class _Repo:
        def __init__(self, _session: object) -> None:
            pass

        async def get_by_id_unscoped(self, _conversation_id: str) -> _Conv:
            return _Conv()

    class _SessionCtx:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, *args: object) -> None:
            return None

    monkeypatch.setattr(
        "agentcore.db.base.async_session_factory",
        lambda: _SessionCtx(),
    )
    monkeypatch.setattr(
        "agentcore.db.repositories.ConversationRepository",
        _Repo,
    )
    assert await load_conversation_folder_id("c-blank") is None


@pytest.mark.asyncio
async def test_load_conversation_folder_id_connection_refused(monkeypatch):
    """PG connection refuse → DatabaseUnavailableError, not raw WinError narrative."""
    from sqlalchemy.exc import OperationalError

    from agentcore.db.errors import DATABASE_UNAVAILABLE_MESSAGE, DatabaseUnavailableError
    from agentcore.sidecar.server_pkg.turns import load_conversation_folder_id

    class _FailSession:
        async def __aenter__(self) -> object:
            raise OperationalError(
                "SELECT 1",
                {},
                ConnectionRefusedError("connection refused"),
            )

        async def __aexit__(self, *args: object) -> None:
            return None

    monkeypatch.setattr(
        "agentcore.db.base.async_session_factory",
        lambda: _FailSession(),
    )

    with pytest.raises(DatabaseUnavailableError) as ei:
        await load_conversation_folder_id("c1")
    assert str(ei.value) == DATABASE_UNAVAILABLE_MESSAGE
    assert "1225" not in str(ei.value)


def test_sidecar_start_turn_db_unavailable_seals_outbox(tmp_path, monkeypatch):
    """After begin_turn, folder_id connect refuse must not leave outbox permanently open.

    方案一 · 诚实失败：回合干净失败，错误可识别为数据库问题；禁止静默当裸聊继续。
    """
    from agentcore.conversation.store.outbox import PHASE_OPEN, PHASE_READY, list_outbox_records
    from agentcore.db.errors import DATABASE_UNAVAILABLE_MESSAGE, DatabaseUnavailableError

    pipeline_ran = {"value": False}

    async def fake_pipeline(**kwargs: Any) -> dict[str, Any]:
        pipeline_ran["value"] = True
        kwargs["sink"].close()
        return {"finish_reason": "end_turn", "content": "ok", "rounds": 1}

    async def boom_folder(_conversation_id: str) -> str | None:
        raise DatabaseUnavailableError(DATABASE_UNAVAILABLE_MESSAGE)

    monkeypatch.setattr("agentcore.sidecar.server.run_chat_pipeline", fake_pipeline)
    monkeypatch.setattr(
        "agentcore.sidecar.server_pkg.turns.load_conversation_folder_id",
        boom_folder,
    )

    data_dir = tmp_path / "data"
    sent, write_line = _recorder()
    server = SidecarServer(write_line)

    async def drive() -> None:
        await server.handle_line(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "userId": "u",
                        "workspaceRoot": str(tmp_path),
                        "approvalsEnabled": True,
                        "dataDir": str(data_dir),
                        "inference": _FAKE_INFERENCE,
                    },
                }
            )
        )
        await server.handle_line(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "startTurn",
                    "params": {
                        **_CLIENT_TURN_IDS,
                        "turnId": "t1",
                        "conversationId": "c1",
                        "userMessage": "查项目记忆",
                        "userMessageId": "um-db-down",
                        "traceId": "a" * 32,
                    },
                }
            )
        )
        await asyncio.gather(*list(server._turns.values()))

    asyncio.run(drive())

    assert pipeline_ran["value"] is False
    err = _response(sent, 2)["error"]
    assert err["code"] == protocol.INTERNAL_ERROR
    assert DATABASE_UNAVAILABLE_MESSAGE in err["message"]
    assert "1225" not in err["message"]

    records = list_outbox_records(data_dir / "outbox")
    assert records, "begin_turn must have created an outbox record"
    assert all(r.get("phase") != PHASE_OPEN for r in records)
    assert all(r.get("phase") == PHASE_READY for r in records)
    assert any("salvage" in (r.get("ops") or []) for r in records)


def test_sidecar_start_turn_passes_desktop_client_platform(tmp_path, monkeypatch):
    """Local engine turns must advertise desktop so MCP/Host ClientTool channel mounts.

    Sidecar omits X-Client-Platform historically → fail-closed desktop_online=False →
    mcp/host 未装配 (定案 P0). Passing ``x_client_platform=\"desktop\"`` is the single seam.
    """
    captured: dict[str, Any] = {}

    async def fake_pipeline(**kwargs: Any) -> dict[str, Any]:
        captured["x_client_platform"] = kwargs.get("x_client_platform")
        kwargs["sink"].close()
        return {"finish_reason": "end_turn", "content": "ok", "rounds": 1}

    monkeypatch.setattr("agentcore.sidecar.server.run_chat_pipeline", fake_pipeline)

    sent, write_line = _recorder()
    server = SidecarServer(write_line)

    async def drive() -> None:
        await server.handle_line(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "userId": "u",
                        "workspaceRoot": str(tmp_path),
                        "approvalsEnabled": True,
                        "inference": _FAKE_INFERENCE,
                    },
                }
            )
        )
        await server.handle_line(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "startTurn",
                    "params": {
                        **_CLIENT_TURN_IDS,
                        "turnId": "t1",
                        "conversationId": "c1",
                        "userMessage": "能用本地 MCP 吗",
                    },
                }
            )
        )
        await asyncio.gather(*list(server._turns.values()))

    asyncio.run(drive())
    assert captured["x_client_platform"] == "desktop"


def test_sidecar_threads_permission_axes_per_turn(tmp_path, monkeypatch):
    """Conversation permission axes reach the local engine: initialize seeds them,
    a per-turn ``permissionAxes`` refreshes them, and an absent param keeps the
    current value — never a silent reset to the default.
    """
    from agentcore.core.types import AutonomyPolicy, PermissionAxes, recipe_to_axes

    captured: list[PermissionAxes] = []

    async def fake_pipeline(**kwargs: Any) -> dict[str, Any]:
        captured.append(kwargs["permission_axes"])
        kwargs["sink"].close()
        return {"finish_reason": "end_turn", "content": "ok", "rounds": 1}

    monkeypatch.setattr("agentcore.sidecar.server.run_chat_pipeline", fake_pipeline)

    sent, write_line = _recorder()
    server = SidecarServer(write_line)

    async def start_turn(turn_id: str, extra: dict[str, Any]) -> None:
        await server.handle_line(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": turn_id,
                    "method": "startTurn",
                    "params": {
                        **_CLIENT_TURN_IDS,
                        "turnId": turn_id,
                        "conversationId": "c1",
                        "userMessage": "跑点代码",
                        **extra,
                    },
                }
            )
        )
        await asyncio.gather(*list(server._turns.values()))

    managed = recipe_to_axes(AutonomyPolicy.MANAGED)
    cautious = recipe_to_axes(AutonomyPolicy.CAUTIOUS)

    async def drive() -> None:
        await server.handle_line(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "userId": "u",
                        "workspaceRoot": str(tmp_path),
                        "permissionAxes": managed.to_dict(),
                        "inference": _FAKE_INFERENCE,
                    },
                }
            )
        )
        await start_turn("t1", {})  # no per-turn value → the initialize seed applies
        await start_turn("t2", {"permissionAxes": cautious.to_dict()})  # per-turn refresh
        await start_turn("t3", {})  # absent again → keeps the refreshed value

    asyncio.run(drive())
    assert captured == [managed, cautious, cautious]


def test_creds_for_stamps_conversation_and_trace_headers():
    """Each turn's proxy creds carry the conversation header (spend attribution) AND the
    trace header (so every proxied LLM call joins the turn's trace, 打通气泡↔日志). An
    empty trace_id (untraced caller) omits the header rather than sending a blank."""
    server = SidecarServer(_recorder()[1])
    server._creds = LLMCredentials(api_key="tok", base_url="https://x/v1/inference")

    traced = server._creds_for("conv-1", "0123456789abcdef0123456789abcdef")
    assert traced.extra_headers[INFERENCE_CONVERSATION_HEADER] == "conv-1"
    assert traced.extra_headers[INFERENCE_TRACE_HEADER] == ("0123456789abcdef0123456789abcdef")

    untraced = server._creds_for("conv-1")
    assert untraced.extra_headers[INFERENCE_CONVERSATION_HEADER] == "conv-1"
    assert INFERENCE_TRACE_HEADER not in untraced.extra_headers

    with_message = server._creds_for("conv-1", "trace-1", "msg-42")
    assert with_message.extra_headers[INFERENCE_MESSAGE_HEADER] == "msg-42"


def test_creds_for_none_when_no_session_creds():
    """No session inference JWT ⇒ no per-turn creds to stamp (startTurn will refuse)."""
    server = SidecarServer(_recorder()[1])
    assert server._creds is None
    assert server._creds_for("conv-1", "trace-1") is None


def test_parse_inference_carries_server_resolved_model():
    creds = SidecarServer._parse_inference(
        {
            "baseUrl": "http://localhost:8000/v1/inference/v1",
            "apiKey": "tok",
            "model": "deepseek-v4-flash",
        }
    )
    assert creds is not None
    assert creds.default_model == "deepseek-v4-flash"
    assert creds.base_url.endswith("/v1/inference/v1")


def test_start_turn_result_reports_cloud_proxy_model(tmp_path, monkeypatch):
    """With inference creds (cloud proxy present), the turn result reports the
    server-resolved account model (resolve_turn_model over the creds)."""

    async def fake_pipeline(**kwargs: Any) -> dict[str, Any]:
        # The turn must run on the cloud-proxy creds (no silent platform key).
        assert kwargs["llm_credentials"] is not None
        kwargs["sink"].close()  # let the pump drain so the turn finishes
        return {"finish_reason": "end_turn", "content": "ok", "rounds": 1}

    monkeypatch.setattr("agentcore.sidecar.server.run_chat_pipeline", fake_pipeline)

    sent, write_line = _recorder()
    server = SidecarServer(write_line)

    async def drive() -> None:
        await server.handle_line(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "userId": "u",
                        "workspaceRoot": str(tmp_path),
                        "approvalsEnabled": False,
                        "inference": {
                            "baseUrl": "http://localhost:8000/v1/inference/v1",
                            "apiKey": "tok",
                            "model": "deepseek-v4-flash",
                        },
                    },
                }
            )
        )
        await server.handle_line(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "startTurn",
                    "params": {
                        **_CLIENT_TURN_IDS,
                        "turnId": "t1",
                        "conversationId": "c1",
                        "userMessage": "hi",
                    },
                }
            )
        )
        await asyncio.gather(*list(server._turns.values()))

    asyncio.run(drive())

    done = _response(sent, 2)
    assert done["result"]["model"] == "deepseek-v4-flash"


def test_start_turn_rejects_conversation_slot_busy(tmp_path, monkeypatch):
    """Same conversation, different turnId: startTurn refuses while a live turn holds the slot."""

    async def _hang(**kwargs: Any) -> dict[str, Any]:
        await asyncio.Event().wait()
        return {"finish_reason": "end_turn", "content": "ok", "rounds": 1}

    monkeypatch.setattr("agentcore.sidecar.server.run_chat_pipeline", _hang)
    sent, write_line = _recorder()
    server = SidecarServer(write_line)

    async def drive() -> None:
        await server.handle_line(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "userId": "u",
                        "workspaceRoot": str(tmp_path),
                        "inference": _FAKE_INFERENCE,
                    },
                }
            )
        )
        await server.handle_line(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "startTurn",
                    "params": {
                        **_CLIENT_TURN_IDS,
                        "turnId": "t-live",
                        "conversationId": "c1",
                        "userMessage": "first",
                    },
                }
            )
        )
        await asyncio.sleep(0)
        await server.handle_line(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "startTurn",
                    "params": {
                        **_CLIENT_TURN_IDS,
                        "turnId": "t-second",
                        "conversationId": "c1",
                        "userMessage": "second",
                    },
                }
            )
        )
        err = _response(sent, 3)
        assert "error" in err
        assert err["error"]["code"] == protocol.INVALID_PARAMS
        assert "turn already running" in err["error"]["message"]
        for task in list(server._turns.values()):
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    asyncio.run(drive())


def test_shutdown_keeps_active_sidecar_while_turns_live(tmp_path):
    from agentcore.sidecar.server_pkg.core import (
        get_active_sidecar,
        reset_active_sidecar_for_tests,
        set_active_sidecar,
    )

    sent, write_line = _recorder()
    server = SidecarServer(write_line)
    server._initialized = True
    set_active_sidecar(server)
    live: asyncio.Task[None] | None = None

    async def _hang() -> None:
        await asyncio.Event().wait()

    async def drive() -> None:
        nonlocal live
        live = asyncio.create_task(_hang())
        server._register_turn("t-live", live, conversation_id="c1")
        await server.handle_line(
            json.dumps({"jsonrpc": "2.0", "id": 9, "method": "shutdown", "params": {}})
        )
        assert get_active_sidecar() is server
        assert _response(sent, 9)["result"]["ok"] is True
        live.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await live

    try:
        asyncio.run(drive())
    finally:
        reset_active_sidecar_for_tests()
