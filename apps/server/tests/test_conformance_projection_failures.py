"""Independent guard for the ProjectedTurn oracle — failure / abort / gate-lifecycle faces.

Sibling of :mod:`tests.test_conformance_projection` (same contract, same HAND-VERIFIED
discipline: expectations are derived from the vector's events + the designed semantics,
never copied from the committed golden). Split out because this family is what a
correlated oracle+fold bug hides in best: every scenario here folds to a turn whose body
is empty or truncated, so "both sides agree" can mean "both sides silently dropped the
only thing the user was going to see".

Covered here: the ``empty_face_*`` family (empty bubble + structured error), whole-turn
stop vs per-worker stop, cascade skip, hot/cold redirect handoff, secondary-delegate
merge, and the approval / delegation-authorization / checkpoint lifecycles that must
never leave a phantom pending card.
"""

from __future__ import annotations

import pytest

from agentcore.conformance.export import build_fixtures
from agentcore.conformance.vectors import VECTORS
from agentcore.runtime.interaction import GATE_KINDS
from agentcore.workspace.limits import CHANNEL_DEAD_PREPARE_ABORT


def _pending_gates(p: dict) -> list[dict]:
    """Gate interactions still awaiting the user (legacy pendingInteraction slot)."""
    return [
        i
        for i in p["interactions"]
        if i.get("status") == "pending" and i.get("kind") in GATE_KINDS
    ]


@pytest.fixture(scope="module")
def projected() -> dict[str, dict]:
    return {fx["name"]: fx["projected"] for fx in build_fixtures()}


# ── 空泡族（empty_face_*）──────────────────────────────────────────────────────
#
# Every one of these turns ends with an EMPTY body, so the structured `error` is the
# entire user-visible face. Hand-verified per vector: the wire error must survive the
# fold verbatim, and the terminal finish must map to the honest status — `degraded` is
# still a completed turn (the model answered, badly), `error` fails it, `paused` parks
# it. Getting that mapping wrong is exactly the correlated bug the golden can't catch:
# oracle and fold both defaulting an unknown finish to "completed" agree with each other.
_EMPTY_FACE_EXPECTED: dict[str, tuple[str, str, str, str]] = {
    # name: (error code, error message, finishReason, status)
    "empty_face_degraded": (
        "LLM_EMPTY_RESPONSE",
        "模型多次空响应 · 模型返回空内容",
        "degraded",
        "completed",
    ),
    "empty_face_empty_response": (
        "LLM_EMPTY_RESPONSE",
        "模型空响应 · 输出长度截断 · 返回空内容",
        "degraded",
        "completed",
    ),
    "empty_face_paused": (
        "PIPELINE_ERROR",
        "本轮未能完成，请重试。",
        "paused",
        "paused",
    ),
    "empty_face_channel_dead": (
        "STREAM_ERROR",
        CHANNEL_DEAD_PREPARE_ABORT,
        "error",
        "failed",
    ),
    "empty_face_insufficient_balance": (
        "LLM_INSUFFICIENT_BALANCE",
        "上游账户余额不足，请充值或更换 Key。",
        "error",
        "failed",
    ),
    "empty_face_model_acl": (
        "LLM_ERROR",
        "This token has no access to model kimi-k3",
        "error",
        "failed",
    ),
    "empty_face_invalid_temperature": (
        "LLM_ERROR",
        "invalid temperature: only 1 is allowed for this model",
        "error",
        "failed",
    ),
    "empty_face_timeout": (
        "LLM_TIMEOUT",
        "连接超时，请检查网络后重试。",
        "error",
        "failed",
    ),
}


def test_empty_face_family_is_fully_enumerated():
    """A new empty_face_* vector must land in the table above, not slip in uncovered.

    The family exists because these turns have no body to inspect; leaving one of them
    to `pnpm conformance` alone puts it back in the blind spot the family was created for.
    """
    assert set(_EMPTY_FACE_EXPECTED) == {
        n for n in VECTORS if n.startswith("empty_face_")
    }


@pytest.mark.parametrize(
    ("name", "code", "message", "finish", "status"),
    [(n, *v) for n, v in _EMPTY_FACE_EXPECTED.items()],
)
def test_empty_face_keeps_a_face(projected, name, code, message, finish, status):
    p = projected[name]
    # Body really is empty — nothing but `error` stands between the user and a blank bubble.
    assert p["content"] == ""
    assert p["reasoning"] == ""
    assert p["process"] == []
    assert p["runs"] == []
    # The wire error rides through the fold verbatim (no swallow, no rewrite).
    assert p["error"] == {"code": code, "message": message}
    assert p["finishReason"] == finish
    assert p["status"] == status


def test_empty_face_paused_has_no_card_to_hide_behind(projected):
    """`paused` normally means "a card owns the UI" — here there is no card.

    The exemption in the failure-face predicate is "paused + a dedicated pause/ask
    interaction"; this vector is the arm WITHOUT one, so dropping the error would leave a
    silently parked, blank turn. Pin the absence so the exemption can't quietly widen.
    """
    p = projected["empty_face_paused"]
    assert p["interactions"] == []
    assert _pending_gates(p) == []
    assert p["status"] == "paused"
    assert (p["error"] or {}).get("code") == "PIPELINE_ERROR"


# ── 审批 / 授权 / 检查点生命周期：不留幽灵待答卡 ────────────────────────────────


def test_approval_orphaned_is_settled_not_pending(projected):
    """重启假卡: `interaction_orphaned` must SETTLE the card, not drop it and not leave
    it pending. A restart kills the waiting tool call — the user can never answer it, so
    a fold that keeps `pending` renders a card whose buttons do nothing forever.
    Orphaning is also NOT terminal for the turn: no message_end here → still running."""
    p = projected["approval_orphaned"]
    assert p["status"] == "running"
    assert p["finishReason"] is None
    assert _pending_gates(p) == []
    assert p["interactions"] == [
        {
            "kind": "approval",
            "id": "tc1",
            "status": "orphaned",
            "toolCallId": "tc1",
            "toolName": "code_execute",
            "arguments": {"code": "print(1)"},
        }
    ]
    # The timeline still records WHERE the (now dead) card sat.
    assert [s["kind"] for s in p["process"]] == ["content", "approval"]
    assert p["content"] == "我需要运行代码。"


def test_approval_sibling_sweep_settles_every_card(projected):
    """一键放行 sibling 清扫: `approve_always` on a1 sweeps its siblings, and each card
    must settle on its OWN record. A fold that settles only the clicked card leaves a2/a3
    pending forever; one that collapses them into a single record loses which paths were
    approved. Hand-verified: three resolved cards, post order, arguments intact."""
    p = projected["approval_sibling_sweep"]
    assert p["status"] == "completed"
    assert p["finishReason"] == "end_turn"
    assert _pending_gates(p) == []
    assert p["interactions"] == [
        {
            "kind": "approval",
            "id": "a1",
            "status": "resolved",
            "toolCallId": "a1",
            "toolName": "file_write",
            "arguments": {"path": "a.txt", "content": "1"},
        },
        {
            "kind": "approval",
            "id": "a2",
            "status": "resolved",
            "toolCallId": "a2",
            "toolName": "file_write",
            "arguments": {"path": "b.txt", "content": "2"},
        },
        {
            "kind": "approval",
            "id": "a3",
            "status": "resolved",
            "toolCallId": "a3",
            "toolName": "file_write",
            "arguments": {"path": "c.txt", "content": "3"},
        },
    ]
    assert [s["kind"] for s in p["process"]] == [
        "content",
        "approval",
        "approval",
        "approval",
        "content",
    ]
    assert p["content"] == "需要写几个文件。 三个文件都写好了。"


def test_checkpoint_resolved_reload_has_no_fake_pending(projected):
    """不变量 4 / 实证故障 1: required + resolved are BOTH in the journal, so a reload
    must fold the answered shape. The single-slot era regressed to a fake pending card
    whenever the resolved write lost the race — pinned here as a ratchet.

    Also pins: the pre-question narration ("开始前我确认一下方向：") stays in the
    bubble when the card question is a different, model-owned ``message``.
    """
    p = projected["checkpoint_resolved_reload"]
    assert p["status"] == "completed"
    assert p["finishReason"] == "end_turn"
    assert _pending_gates(p) == []
    assert p["interactions"] == [
        {
            "kind": "ask_user",
            "id": "cp1",
            "status": "resolved",
            "question": "先做 A 还是 B？\n两条路线各有取舍。",
        }
    ]
    assert [s["kind"] for s in p["process"]] == ["content", "checkpoint", "content"]
    assert p["process"][0]["text"] == "开始前我确认一下方向："
    assert p["content"] == "开始前我确认一下方向：好，按 A 推进。"


# ── 中止族：整轮 stop / 单点 stop / 级联跳过 / 改方向接手 ──────────────────────


def test_run_stop_cancels_workers_without_followup_nodes(projected):
    """整轮 stop: every in-flight worker gets `run_cancelled(reason=stop)` and the turn
    itself ends cancelled. Whole-turn abort has NO per-worker follow-up — a fold that
    reuses the redirect path would invent a `_rev1` / `_redir` node here. Cancelled work
    is not finished work: progress stays 0/2, but the partial transcript survives so the
    user can read what was salvaged."""
    p = projected["multi_agent_run_stop_cancels_workers"]
    assert p["status"] == "cancelled"
    assert p["finishReason"] == "cancelled"
    assert [r["id"] for r in p["runs"]] == ["r1", "r2"]
    assert all(r["status"] == "cancelled" for r in p["runs"])
    assert all(r["continuesRunId"] is None and r["replacesRunId"] is None for r in p["runs"])
    assert p["progress"] == {"completed": 0, "total": 2}
    by_id = {r["id"]: r for r in p["runs"]}
    assert by_id["r1"]["process"] == [{"kind": "content", "text": "调研进行中……"}]
    assert p["interactions"] == []


def test_run_user_stop_worker_keeps_the_turn_alive(projected):
    """单点 stop 的对偶（与整轮 stop 最易互相串味）: `reason=user_stop` cancels ONE worker;
    the delegate still returns, the sibling completes and the CEO keeps writing, so the
    turn ends `end_turn` — NOT cancelled. Progress counts only the survivor."""
    p = projected["multi_agent_run_user_stop_worker"]
    assert p["status"] == "completed"
    assert p["finishReason"] == "end_turn"
    by_id = {r["id"]: r for r in p["runs"]}
    assert [r["id"] for r in p["runs"]] == ["r1", "r2"]
    assert by_id["r1"]["status"] == "cancelled"
    assert by_id["r2"]["status"] == "completed"
    assert p["progress"] == {"completed": 1, "total": 2}
    assert p["content"] == "我来安排两位并行推进。 撰写已完成；调研被用户中途停下，我继续收口。"


def test_stop_gate_run_frames_after_message_end_still_fold(projected):
    """停止诚实过渡态: the cascade of terminal `run_*` frames lands AFTER
    `message_end(cancelled)`. A fold that stops consuming at message_end freezes both
    nodes on "running" forever — the graph would keep spinning under a finished turn."""
    p = projected["multi_agent_stop_gate_run_frames"]
    assert p["status"] == "cancelled"
    assert p["finishReason"] == "cancelled"
    assert [r["status"] for r in p["runs"]] == ["cancelled", "cancelled"]
    assert p["progress"] == {"completed": 0, "total": 2}


def test_run_skipped_cascade_is_terminal_not_queued(projected):
    """级联跳过: r1 fails → dependent r2 is never dispatched (`cascade`), and the
    independent r3 dies with the wave (`abort`). Both must fold to the TERMINAL `skipped`
    state — the bug this pins is a graph stuck on "排队中" forever. The failed upstream
    counts as neither, so progress is 0/3 while the turn still closes end_turn."""
    p = projected["multi_agent_run_skipped_cascade"]
    assert p["status"] == "completed"
    assert p["finishReason"] == "end_turn"
    by_id = {r["id"]: r for r in p["runs"]}
    assert by_id["r1"]["status"] == "failed"
    assert by_id["r2"]["status"] == "skipped"
    assert by_id["r3"]["status"] == "skipped"
    assert by_id["r2"]["dependsOn"] == ["r1"]
    assert p["progress"] == {"completed": 0, "total": 3}


def test_run_redirect_hot_continues_the_cancelled_run(projected):
    """跑一半改方向 · 热续写: a salvageable worker is cancelled (`redirect`) and its draft
    continues in a synthesized revision child — linked by `continuesRunId`, NOT
    `replacesRunId`. The two link fields drive different UI (续写 vs 接手), so swapping
    them is a silent, correlated-looking bug; the cold arm below pins the mirror image."""
    p = projected["multi_agent_run_redirect_hot"]
    assert p["status"] == "completed"
    by_id = {r["id"]: r for r in p["runs"]}
    assert set(by_id) == {"r1", "r2", "r1_rev1"}
    assert by_id["r1"]["status"] == "cancelled"
    rev = by_id["r1_rev1"]
    assert rev["status"] == "completed"
    assert rev["continuesRunId"] == "r1"
    assert rev["replacesRunId"] is None
    assert rev["process"] == [{"kind": "content", "text": "修订稿：按功能差异横评 A/B/C……"}]
    assert p["progress"] == {"completed": 2, "total": 3}


def test_run_redirect_cold_fallback_hands_off_to_a_replacement(projected):
    """跑一半改方向 · 冷诚实回落: nothing worth salvaging → a fresh `_redir` node takes
    over, linked by `replacesRunId` (mirror of the hot arm's `continuesRunId`)."""
    p = projected["multi_agent_run_redirect_cold_fallback"]
    assert p["status"] == "completed"
    by_id = {r["id"]: r for r in p["runs"]}
    assert set(by_id) == {"r1", "r2", "r1_redir"}
    assert by_id["r1"]["status"] == "cancelled"
    handoff = by_id["r1_redir"]
    assert handoff["status"] == "completed"
    assert handoff["replacesRunId"] == "r1"
    assert handoff["continuesRunId"] is None
    assert p["progress"] == {"completed": 2, "total": 3}


def test_merge_race_secondary_delegate_joins_one_execution(projected):
    """同回合二次 delegate: the second `run_plan` carries the SAME execution_id and only
    the new node. It must merge into the live graph — one team marker, one graph, the
    cross-batch `depends_on` preserved. A fold that resets on the second plan drops r1
    (and its result) off the board mid-turn."""
    p = projected["multi_agent_merge_race_secondary_delegate"]
    assert p["status"] == "completed"
    assert [s["kind"] for s in p["process"]] == ["content", "team", "content"]
    assert [s["execution_id"] for s in p["process"] if s["kind"] == "team"] == ["exec-merge"]
    by_id = {r["id"]: r for r in p["runs"]}
    assert [r["id"] for r in p["runs"]] == ["r1", "r2"]
    assert all(r["status"] == "completed" for r in p["runs"])
    assert by_id["r1"]["dependsOn"] == []
    assert by_id["r2"]["dependsOn"] == ["r1"]
    assert p["progress"] == {"completed": 2, "total": 2}
    assert p["content"] == "先调研。 再追加校对。都完成了。"


def test_multi_agent_worker_rate_limit_partial_pins_landed_transient(projected):
    """委派回合：worker 落盘 3 CSV 后撞 429，CEO 汇总再撞 429，交代成回复。

    手工推导（不抄 golden）。生产实测 trace 933d81fea6cf4b278ee6ce1e0d607e86。
    期望从 ``LLMRateLimitError`` + ``run_error_signal`` + ``build_delivery_status``
    独立算出，四处事故面任一回潮都必须红：

    1. 该 worker 全链路只有一帧终态（历史：同 run_id 两帧 run_failed + fold
       last-write-wins → 直播 2 / 重载 1）。
    2. run_failed 载荷 error_code=LLM_RATE_LIMIT、retryable=true
       （叶层用尽后 ``exc.retryable`` 已是 False，瞬时性读 ``llm_failure_class``）。
       未 attested 的退避秒数不进 ``retry_after`` / 用户文案。
    3. delivery_status=partial 且认到 3 个产物（历史：blocked / artifacts_count=0）。
    4. 回合结果 ``partial`` 且回复点名三份 CSV（历史：空正文 + 二元 error）。
    """
    from agentcore.core.error_codes import ErrorCode
    from agentcore.core.errors import LLMRateLimitError, mark_llm_leaf_exhausted
    from agentcore.runtime.events import EventType
    from agentcore.runtime.runs.error_signal import run_error_signal

    retry_after = 4.0
    exc = LLMRateLimitError(retry_after=retry_after)
    mark_llm_leaf_exhausted(exc)
    signal = run_error_signal(exc)
    # 叶层用尽把 HTTP 预算翻成 False；限流分类仍是瞬时。
    assert exc.retryable is False
    assert signal.retryable is True
    assert signal.error_code == ErrorCode.LLM_RATE_LIMIT
    # 引擎仍握着退避秒数；线上不把它当 Retry-After。
    assert exc.retry_after == retry_after
    assert signal.retry_after is None
    error = str(exc)
    assert "请约" not in error
    assert "4 秒" not in error

    events = VECTORS["multi_agent_worker_rate_limit_partial"][1]()
    terminals = [
        e
        for e in events
        if e.type
        in {
            EventType.RUN_FAILED,
            EventType.RUN_COMPLETED,
            EventType.RUN_CANCELLED,
            EventType.RUN_SKIPPED,
        }
        and e.payload.get("run_id") == "r1"
    ]
    assert len(terminals) == 1
    failed = terminals[0]
    assert failed.type is EventType.RUN_FAILED
    assert failed.payload["error_code"] == ErrorCode.LLM_RATE_LIMIT
    assert failed.payload["retryable"] is True
    assert failed.payload.get("retry_after") is None
    assert failed.payload["error"] == error
    assert failed.payload["failure_kind"] == "call"
    assert failed.payload["product_landed"] is True

    csv_paths = ["订单.csv", "明细.csv", "汇总.csv"]
    p = projected["multi_agent_worker_rate_limit_partial"]
    assert p["content"]
    assert all(name in p["content"] for name in csv_paths)
    assert p["outcome"] == "partial"
    assert p["status"] == "completed"
    assert p["finishReason"] == "degraded"
    assert p["error"] == {"code": ErrorCode.LLM_RATE_LIMIT, "message": error}
    assert [r["id"] for r in p["runs"]] == ["r1"]
    run = p["runs"][0]
    assert run["status"] == "failed"
    assert run["error"] == error
    assert run["failureKind"] == "call"
    assert run["productLanded"] is True
    tools = [s for s in run["process"] if s["kind"] == "tool"]
    assert [t["tool_name"] for t in tools] == ["file_write", "file_write", "file_write"]
    assert all(t["status"] == "success" for t in tools)
    assert [t["arguments"]["path"] for t in tools] == csv_paths
    assert p["progress"] == {"completed": 0, "total": 1}
    assert [s["kind"] for s in p["process"]] == ["team", "content"]
    assert p["process"][1]["text"] == p["content"]

    ds = p["deliveryStatus"]
    assert ds is not None
    assert ds["state"] == "partial"
    assert ds["delivered_files"] == csv_paths
    assert [a["path"] for a in ds["artifacts"]] == csv_paths
    assert all(a["status"] == "accepted" for a in ds["artifacts"])
    assert ds["summary"] == "已交付 3 个文件；1 项未完成"
    assert any(g.get("reason") == "node_failed" for g in ds["gaps"])


def test_multi_agent_ceo_rate_limit_paused_pins_continue_face(projected):
    """CEO 限流暂停：worker 一帧失败、delegate 闭合、无卡、无收口、outcome=paused。

    手工推导（不抄 golden）。与 ``multi_agent_worker_rate_limit_partial`` 对照：
    那条是 CEO 收口成功的 partial；本条是回合权威 paused，pending 闸为空。
    """
    from agentcore.core.error_codes import ErrorCode
    from agentcore.core.errors import LLMRateLimitError, mark_llm_leaf_exhausted
    from agentcore.runtime.events import EventType
    from agentcore.runtime.runs.error_signal import run_error_signal

    retry_after = 4.0
    exc = LLMRateLimitError(retry_after=retry_after)
    mark_llm_leaf_exhausted(exc)
    signal = run_error_signal(exc)
    error = str(exc)
    assert signal.retry_after is None
    assert "请约" not in error
    assert "4 秒" not in error

    events = VECTORS["multi_agent_ceo_rate_limit_paused"][1]()
    kinds = [e.type for e in events]
    assert EventType.PLAN_REVIEW_REQUIRED not in kinds
    assert EventType.CHECKPOINT_REQUIRED not in kinds
    assert "team_preview_required" not in kinds

    starts = [e for e in events if e.type is EventType.MESSAGE_START]
    assert len(starts) == 1

    terminals = [
        e
        for e in events
        if e.type
        in {
            EventType.RUN_FAILED,
            EventType.RUN_COMPLETED,
            EventType.RUN_CANCELLED,
            EventType.RUN_SKIPPED,
        }
        and e.payload.get("run_id") == "r1"
    ]
    assert len(terminals) == 1
    failed = terminals[0]
    assert failed.type is EventType.RUN_FAILED
    assert failed.payload["error_code"] == ErrorCode.LLM_RATE_LIMIT
    assert failed.payload["retryable"] is True
    assert failed.payload.get("retry_after") is None
    assert failed.payload["error"] == error

    ends = [e for e in events if e.type is EventType.MESSAGE_END]
    assert len(ends) == 1
    assert ends[0].payload["finish_reason"] == "paused"
    assert ends[0].payload["outcome"] == "paused"

    p = projected["multi_agent_ceo_rate_limit_paused"]
    assert p["outcome"] == "paused"
    assert p["finishReason"] == "paused"
    assert p["status"] == "paused"
    assert _pending_gates(p) == []
    assert p["error"] == {"code": ErrorCode.LLM_RATE_LIMIT, "message": error}
    assert [r["id"] for r in p["runs"]] == ["r1"]
    assert p["runs"][0]["status"] == "failed"


# ── turnOutcome 旁路（turn_verdict_*）：协议外判决对账，不抄 golden ──────────────


_TURN_VERDICT_PROBES = (
    "turn_verdict_team_host",
    "turn_verdict_unproductive_body_tool",
)


def test_turn_verdict_probes_are_enumerated():
    """A new turn_verdict_* vector must land here, not slip in uncovered."""
    assert set(_TURN_VERDICT_PROBES) == {n for n in VECTORS if n.startswith("turn_verdict_")}


def test_turn_verdict_team_host_has_a_team_error_face(projected):
    """Hand-derived: run_plan + run_failed + attested outcome=error → strip host."""
    p = projected["turn_verdict_team_host"]
    assert p["runs"], "team graph must exist so the strip can own the verdict"
    assert p["outcome"] == "error"
    assert p["status"] == "failed"
    assert p["finishReason"] == "error"
    assert (p["error"] or {}).get("code") == "LLM_ERROR"


def test_turn_verdict_unproductive_body_tool_keeps_failed_tool(projected):
    """Hand-derived: non-empty body + unproductive + host status=error."""
    p = projected["turn_verdict_unproductive_body_tool"]
    assert (p["content"] or "").strip()
    assert p["finishReason"] == "unproductive"
    assert p["runs"] == []
    tools = [s for s in p["process"] if s.get("kind") == "tool"]
    assert any(s.get("tool_name") == "host" and s.get("status") == "error" for s in tools)


def test_turn_verdict_sidecar_is_exported():
    """Export attaches the partial envelope; values are wire / documented host rule."""
    from agentcore.conformance.export import build_fixtures
    from agentcore.conformance.turn_verdict import project_turn_verdict

    by_name = {fx["name"]: fx for fx in build_fixtures()}
    team = by_name["turn_verdict_team_host"]
    assert team["turnVerdict"] == project_turn_verdict("turn_verdict_team_host", team["projected"])
    assert team["turnVerdict"] == {
        "hasTeamStrip": True,
        "supportPackHost": "strip",
    }
    body = by_name["turn_verdict_unproductive_body_tool"]
    assert body["turnVerdict"] == project_turn_verdict(
        "turn_verdict_unproductive_body_tool",
        body["projected"],
    )
    assert body["turnVerdict"]["failedToolHintNames"] == ["host"]
