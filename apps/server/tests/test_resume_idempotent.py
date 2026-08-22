"""冷 resume 幂等：帧已被消费的「继续」返回事实，不再 404。

三条线一起证明「点第二次不再必死」，且答的是**赢家**的结论：

- **判据** (:func:`classify_resume_miss`) — 扑空时读消费掉这一帧的那一方留下的结论行
  （``paused_turn_outcomes``），没有结论才是真失效，且「超保留期清理」由清扫自己盖的
  ``expired`` 章说了算，不再靠「assistant 行还在不在」猜。
- **路由** — peek 扑空 / claim 竞争落败都走 200 + ``resume_settled``；续跑仍在跑就把
  这条 SSE join 到它的流；peek / claim 是故障（帧还在）时不许冒充成功。
- **TTL 清扫** — 删帧连同消息行上的 ``usage.paused`` 闩一起清，否则前端还画那张
  点了必死的卡。
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from agentcore.api.routes.conversations import turns as turns_mod
from agentcore.config import settings
from agentcore.core.errors import NotFoundError
from agentcore.db.models import PAUSED_TURN_EXPIRED, PAUSED_TURN_SETTLED
from agentcore.runtime.events import EventSink, EventType, turn_warning
from agentcore.runtime.suspension import consumed as consumed_mod
from agentcore.runtime.suspension import retention as retention_mod
from agentcore.runtime.suspension.consumed import ResumeMiss, classify_resume_miss
from agentcore.runtime.turn.runs import turn_runs

DECIDED_AT = datetime(2026, 8, 13, 9, 30, tzinfo=UTC)
SETTLED_AT = "2026-08-13T09:30:00Z"


async def _never() -> None:
    await asyncio.Future()


@contextlib.asynccontextmanager
async def _live_run(conversation_id: str, message_id: str):
    """Register a never-ending run for the card, as a mid-continuation worker holds."""
    sink = EventSink(message_id=message_id)
    run = asyncio.create_task(_never())
    turn_runs.register(conversation_id=conversation_id, task=run, sink=sink)
    try:
        yield sink
    finally:
        run.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await run
        await asyncio.sleep(0)


# --- 判据：消费掉这一帧的那一方留下的结论行 -------------------------------------


def _stub_reads(monkeypatch, *, outcome: object | None, message: object | None) -> None:
    """Point the classifier at an in-memory outcome / message row (no DB)."""

    @contextlib.asynccontextmanager
    async def _session():
        yield None

    class _Paused:
        def __init__(self, _db) -> None:  # noqa: ANN001
            pass

        async def get_outcome(self, _message_id: str, *, conversation_id: str):  # noqa: ANN001
            return outcome

    class _Messages:
        def __init__(self, _db) -> None:  # noqa: ANN001
            pass

        async def get_by_id(self, _message_id: str, *, conversation_id: str):  # noqa: ANN001
            return message

    monkeypatch.setattr(consumed_mod, "async_session_factory", _session)
    monkeypatch.setattr(consumed_mod, "PausedTurnRepository", _Paused)
    monkeypatch.setattr(consumed_mod, "MessageRepository", _Messages)


def _outcome_row(**overrides) -> SimpleNamespace:
    fields = {
        "outcome": PAUSED_TURN_SETTLED,
        "card_kind": "plan_review",
        "checkpoint_id": "cp-1",
        "decision": "continue",
        "settled_by": "dev-a",
        "decided_at": DECIDED_AT,
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


async def test_the_conclusion_left_by_the_frames_consumer_is_the_answer(monkeypatch):
    _stub_reads(
        monkeypatch,
        outcome=_outcome_row(),
        message=SimpleNamespace(usage={"status": "complete"}),
    )

    miss = await classify_resume_miss(conversation_id="c1", message_id="m1")

    assert miss == ResumeMiss(
        kind="settled",
        card_kind="plan_review",
        checkpoint_id="cp-1",
        decision="continue",
        decided_at=SETTLED_AT,
        turn_status="complete",
        settled_by="dev-a",
    )


async def test_a_naive_stamp_reads_as_utc(monkeypatch):
    """所有写这列的人都盖 ``now(UTC)``；驱动去掉时区也不许把它当本地时间。"""
    _stub_reads(
        monkeypatch,
        outcome=_outcome_row(decided_at=DECIDED_AT.replace(tzinfo=None)),
        message=None,
    )

    miss = await classify_resume_miss(conversation_id="c1", message_id="m1")

    assert miss.decided_at == SETTLED_AT


async def test_a_swept_card_is_expired_even_though_its_turn_is_still_there(monkeypatch):
    """超期由清扫自己盖的章说了算——不再看 assistant 行还在不在。"""
    _stub_reads(
        monkeypatch,
        outcome=_outcome_row(outcome=PAUSED_TURN_EXPIRED, decision="", settled_by="retention_sweep"),
        message=SimpleNamespace(usage={"status": "running", "paused": True}),
    )

    miss = await classify_resume_miss(conversation_id="c1", message_id="m1")

    assert miss.kind == "expired"
    assert miss.decision == ""  # 没人决定过它，不许报成一次决策


async def test_no_conclusion_is_a_regenerated_turn_even_with_the_message_alive(monkeypatch):
    """没有结论行 = 没人消费过这张卡：回合被重新生成/删除（结论随消息一起走）。"""
    _stub_reads(
        monkeypatch,
        outcome=None,
        message=SimpleNamespace(usage={"status": "running", "paused": True}),
    )

    miss = await classify_resume_miss(conversation_id="c1", message_id="m1")

    assert miss.kind == "regenerated"


async def test_an_outcome_this_build_cannot_read_is_never_reported_as_a_decision(monkeypatch):
    _stub_reads(monkeypatch, outcome=_outcome_row(outcome="from_the_future"), message=None)

    miss = await classify_resume_miss(conversation_id="c1", message_id="m1")

    assert miss.kind == "regenerated"
    assert miss.decision == ""


# --- 路由：200 + resume_settled ------------------------------------------------


@pytest.fixture
def resume_route(monkeypatch):
    """Route deps stubbed down to the frame lookup: no rate limit, no billing, no DB."""
    monkeypatch.setattr(turns_mod, "enforce_user_message_rate_limit", AsyncMock())
    monkeypatch.setattr(
        turns_mod,
        "_preflight_owned_chat_turn",
        AsyncMock(
            return_value=SimpleNamespace(credentials=None, supports_tools=True, warnings=[])
        ),
    )
    monkeypatch.setattr(turns_mod, "release_request_db_before_sse", AsyncMock())
    monkeypatch.setattr(turns_mod, "prewrite_cold_resume_settlement", AsyncMock())
    # peek / claim 扑空后的「帧真没了」复核，默认答「真没了」；要故障面的用例自行覆盖。
    monkeypatch.setattr(turns_mod, "paused_turn_exists", AsyncMock(return_value=False))


def _settled(**overrides) -> ResumeMiss:
    fields = {
        "kind": "settled",
        "card_kind": "plan_review",
        "checkpoint_id": "cp-1",
        "decision": "continue",
        "decided_at": SETTLED_AT,
        "turn_status": "complete",
    }
    fields.update(overrides)
    return ResumeMiss(**fields)  # type: ignore[arg-type]


async def _resume(cid: str, message_id: str, *, decision: str = "continue"):
    from agentcore.api.schemas import ResumeTurnRequest
    from agentcore.runtime.checkpoints import CheckpointDecision

    return await turns_mod.resume_message(
        conversation_id=cid,
        message_id=message_id,
        body=ResumeTurnRequest(decision=CheckpointDecision(decision)),
        user=SimpleNamespace(user_id="u1"),
        session=None,
        x_client_platform=None,
    )


async def _next_frame(response) -> str:
    return await asyncio.wait_for(response.body_iterator.__anext__(), timeout=2.0)


async def test_consumed_frame_answers_with_the_settled_facts(monkeypatch, resume_route):
    """帧已被那次续跑吃掉、续跑也已结束：给事实，不给 404。"""
    monkeypatch.setattr(turns_mod, "load_paused_turn", AsyncMock(return_value=None))
    monkeypatch.setattr(turns_mod, "classify_resume_miss", AsyncMock(return_value=_settled()))

    response = await _resume("c-settled-done", "paused-1")
    try:
        frame = await _next_frame(response)
        assert "event: resume_settled" in frame
        assert '"decision": "continue"' in frame
        assert f'"decided_at": "{SETTLED_AT}"' in frame
        assert '"turn_status": "complete"' in frame
        assert '"kind": "plan_review"' in frame
        # 终态事实帧发完即收流——没有活着的续跑可跟。
        with pytest.raises(StopAsyncIteration):
            await _next_frame(response)
    finally:
        await response.body_iterator.aclose()


async def test_second_submit_joins_the_running_continuation(monkeypatch, resume_route):
    """同一张卡再点一次而续跑还在跑：这条 SSE 挂到它的事件流上，一起看完。"""
    cid = "c-settled-live"
    monkeypatch.setattr(turns_mod, "load_paused_turn", AsyncMock(return_value=None))
    monkeypatch.setattr(
        turns_mod,
        "classify_resume_miss",
        AsyncMock(return_value=_settled(turn_status="running")),
    )

    response = None
    async with _live_run(cid, "paused-1") as sink:
        try:
            response = await _resume(cid, "paused-1")
            first = await _next_frame(response)
            assert "event: resume_settled" in first
            assert '"turn_status": "running"' in first
            # 追平段边界之后才是续跑自己的帧。续跑的 sink 刚建、历史为空，追平段就该是空的
            # ——**不得**凭空补一帧 full_replay 段首：客户端手里握着暂停前的整轮正文，这条路
            # 又不走 journal 重放，清了就再也回不来。
            assert await _next_frame(response) == ": attach-caught-up\n\n"

            sink.emit(turn_warning("续跑还在说话"))
            assert "event: turn_warning" in await _next_frame(response)
        finally:
            if response is not None:
                await response.body_iterator.aclose()


async def test_turn_status_is_the_turns_own_state_not_this_workers_registry(
    monkeypatch, resume_route
):
    """挂上本机在跑的流只是传输捷径，不改回合状态——客户端据它决定收不收口气泡。

    多 worker 下进程内注册表根本不知道别处的续跑，让它来定这个字段，等于把
    「本机恰好有/没有」冒充成「回合到哪了」。
    """
    cid = "c-status-from-db"
    monkeypatch.setattr(turns_mod, "load_paused_turn", AsyncMock(return_value=None))
    monkeypatch.setattr(
        turns_mod,
        "classify_resume_miss",
        AsyncMock(return_value=_settled(turn_status="complete")),
    )

    response = None
    async with _live_run(cid, "paused-1"):
        try:
            response = await _resume(cid, "paused-1")
            first = await _next_frame(response)
            assert '"turn_status": "complete"' in first
        finally:
            if response is not None:
                await response.body_iterator.aclose()


async def test_claim_race_loser_gets_the_facts_instead_of_404(monkeypatch, resume_route):
    """两次提交撞在一起：输掉 claim 的那条也拿到事实（帧确实没了 = 有人处理了）。"""
    monkeypatch.setattr(turns_mod, "load_paused_turn", AsyncMock(return_value=object()))
    monkeypatch.setattr(turns_mod, "claim_paused_turn", AsyncMock(return_value=None))
    recheck = AsyncMock(return_value=False)
    monkeypatch.setattr(turns_mod, "paused_turn_exists", recheck)
    monkeypatch.setattr(turns_mod, "classify_resume_miss", AsyncMock(return_value=_settled()))

    response = await _resume("c-claim-race", "paused-1")
    try:
        assert "event: resume_settled" in await _next_frame(response)
        assert recheck.await_count == 1  # claim 扑空后先复核帧真没了，再谈「已被处理」
    finally:
        await response.body_iterator.aclose()


def _stub_live_outcome(monkeypatch, store: dict) -> None:
    """判据读的就是这个「库」——赢家 claim 时写进去的那行结论。"""

    @contextlib.asynccontextmanager
    async def _session():
        yield None

    class _Paused:
        def __init__(self, _db) -> None:  # noqa: ANN001
            pass

        async def get_outcome(self, _message_id: str, *, conversation_id: str):  # noqa: ANN001
            return store["outcome"]

    class _Messages:
        def __init__(self, _db) -> None:  # noqa: ANN001
            pass

        async def get_by_id(self, _message_id: str, *, conversation_id: str):  # noqa: ANN001
            return SimpleNamespace(usage={"status": "running"})

    monkeypatch.setattr(consumed_mod, "async_session_factory", _session)
    monkeypatch.setattr(consumed_mod, "PausedTurnRepository", _Paused)
    monkeypatch.setattr(consumed_mod, "MessageRepository", _Messages)


async def test_concurrent_submits_hand_the_loser_the_winners_conclusion(
    monkeypatch, resume_route
):
    """两端同时点同一张卡：落败方拿到的是**赢家**的决策，不是自己刚预写的那份。

    这正是本次重设计要拆掉的坑：两端在 claim 前各自把自己的 settlement 预写进 journal，
    赢家过去只删帧不留结论，落败方回头捞 journal 末条——捞到的往往是它自己那条，于是
    界面告诉用户「你的决策生效了」，真正跑的却是对面那份。
    """
    cid, mid = "c-two-ends", "paused-1"
    frame = {"kind": "ask_user", "checkpoint_id": "cp-9"}
    store: dict[str, object] = {"frame": frame, "outcome": None}
    lost: list[str] = []

    async def fake_claim(_message_id, *, conversation_id=None, decision, settled_by=""):
        # DELETE ... RETURNING：谁先摘走帧谁就是赢家，结论与删帧同一事务落库。
        if store.pop("frame", None) is None:
            lost.append(decision)
            return None
        store["outcome"] = _outcome_row(
            card_kind=str(frame["kind"]),
            checkpoint_id=str(frame["checkpoint_id"]),
            decision=decision,
            settled_by=settled_by,
        )
        return SimpleNamespace(message_id=_message_id)

    async def peek(_message_id, *, conversation_id=None):
        # 竞争的前提：两端都在帧被摘走之前看到了它，于是各自预写了自己的 settlement。
        return SimpleNamespace()

    async def frame_still_there(_message_id, *, conversation_id):
        return "frame" in store

    monkeypatch.setattr(turns_mod, "load_paused_turn", peek)
    monkeypatch.setattr(turns_mod, "claim_paused_turn", fake_claim)
    monkeypatch.setattr(turns_mod, "paused_turn_exists", frame_still_there)
    monkeypatch.setattr(turns_mod, "resume_chat", AsyncMock())
    # 两条都走 claim：忙槽 deferred 是另一条路（另有用例），这里要的是 claim 竞争。
    monkeypatch.setattr(turn_runs, "busy_reason_for_resume", lambda *_a, **_k: None)
    _stub_live_outcome(monkeypatch, store)

    responses = await asyncio.gather(
        _resume(cid, mid, decision="stop"),
        _resume(cid, mid, decision="continue"),
    )
    by_decision = dict(zip(["stop", "continue"], responses, strict=True))
    try:
        assert len(lost) == 1  # 原子 claim：恰好一个赢家
        loser_decision = lost[0]
        winner_decision = "continue" if loser_decision == "stop" else "stop"

        settled = await _next_frame(by_decision[loser_decision])
        assert "event: resume_settled" in settled
        assert f'"decision": "{winner_decision}"' in settled
        assert f'"decision": "{loser_decision}"' not in settled
        # 卡身份取自被消费的那一帧，落败方据它对上自己屏幕上的卡（空串会被整帧丢弃）。
        assert '"checkpoint_id": "cp-9"' in settled
        assert '"kind": "ask_user"' in settled
        # 旧坑是结构性拆掉的：判据里已经没有 journal 这条读路可走。
        assert not hasattr(consumed_mod, "TurnJournalRepository")
    finally:
        for response in responses:
            await response.body_iterator.aclose()
        await turn_runs.stop_and_drain(cid)


async def test_claim_fault_with_the_frame_still_there_is_not_faked_as_success(
    monkeypatch, resume_route
):
    """claim 吞了 DB 故障也返回 None——帧还在就绝不能拿本请求刚预写的 settlement 冒充已处理。"""
    monkeypatch.setattr(turns_mod, "load_paused_turn", AsyncMock(return_value=object()))
    monkeypatch.setattr(turns_mod, "claim_paused_turn", AsyncMock(return_value=None))
    monkeypatch.setattr(turns_mod, "paused_turn_exists", AsyncMock(return_value=True))
    classify = AsyncMock(return_value=_settled())
    monkeypatch.setattr(turns_mod, "classify_resume_miss", classify)

    with pytest.raises(HTTPException) as exc:
        await _resume("c-claim-fault", "paused-1")

    assert exc.value.status_code == 500
    assert exc.value.detail == {"code": "resume_claim_failed"}
    assert classify.await_count == 0


async def test_peek_fault_with_the_frame_still_there_is_not_faked_as_settled(
    monkeypatch, resume_route
):
    """peek 也会把 DB 故障吞成 None——帧还在就不许当「已被处理」，更不许 404 掉那张卡。"""
    monkeypatch.setattr(turns_mod, "load_paused_turn", AsyncMock(return_value=None))
    monkeypatch.setattr(turns_mod, "paused_turn_exists", AsyncMock(return_value=True))
    classify = AsyncMock(return_value=_settled())
    monkeypatch.setattr(turns_mod, "classify_resume_miss", classify)

    with pytest.raises(HTTPException) as exc:
        await _resume("c-peek-fault", "paused-1")

    assert exc.value.status_code == 500
    assert exc.value.detail == {"code": "resume_claim_failed"}
    assert classify.await_count == 0


async def test_unreadable_frame_after_a_claim_miss_never_reads_as_handled(
    monkeypatch, resume_route
):
    """复核本身读不动时如实报错——「读不出来」不等于「帧没了」，更不等于别人处理过。"""
    monkeypatch.setattr(turns_mod, "load_paused_turn", AsyncMock(return_value=object()))
    monkeypatch.setattr(turns_mod, "claim_paused_turn", AsyncMock(return_value=None))
    monkeypatch.setattr(
        turns_mod, "paused_turn_exists", AsyncMock(side_effect=RuntimeError("db down"))
    )
    classify = AsyncMock(return_value=_settled())
    monkeypatch.setattr(turns_mod, "classify_resume_miss", classify)

    with pytest.raises(RuntimeError):
        await _resume("c-recheck-down", "paused-1")

    assert classify.await_count == 0


async def test_expired_and_regenerated_get_their_own_honest_404(monkeypatch, resume_route):
    """真失效才 404，且说清是哪一种——「不存在或已处理」这种含糊话已废弃。"""
    monkeypatch.setattr(turns_mod, "load_paused_turn", AsyncMock(return_value=None))

    monkeypatch.setattr(
        turns_mod, "classify_resume_miss", AsyncMock(return_value=ResumeMiss(kind="expired"))
    )
    with pytest.raises(NotFoundError) as expired:
        await _resume("c-expired", "paused-1")
    assert "保留期" in expired.value.message

    monkeypatch.setattr(
        turns_mod, "classify_resume_miss", AsyncMock(return_value=ResumeMiss(kind="regenerated"))
    )
    with pytest.raises(NotFoundError) as regenerated:
        await _resume("c-regenerated", "paused-1")
    assert "重新生成" in regenerated.value.message

    assert "已处理" not in expired.value.message
    assert "已处理" not in regenerated.value.message


# --- TTL 清扫：删帧连闩一起清 ---------------------------------------------------


async def test_retention_sweep_clears_the_pause_latch_of_every_pruned_frame(monkeypatch):
    """帧被清而闩还在 = 前端继续画一张点了必死的卡。"""
    cleared: list[tuple[str, str]] = []
    swept = [[("m-1", "c-1"), ("m-2", "c-2")], []]

    @contextlib.asynccontextmanager
    async def _session():
        yield None

    class _Repo:
        def __init__(self, _db) -> None:  # noqa: ANN001
            pass

        async def delete_stale(self, *, before: datetime, limit: int):
            assert before < datetime.now(UTC)
            assert limit > 0
            return swept.pop(0)

    async def _clear(*, message_id: str, conversation_id: str) -> None:
        cleared.append((message_id, conversation_id))

    monkeypatch.setattr(settings, "structured_suspension_persist_enabled", True)
    monkeypatch.setattr(settings, "paused_turn_sweep_batch_limit", 2)
    monkeypatch.setattr(retention_mod, "async_session_factory", _session)
    monkeypatch.setattr(retention_mod, "PausedTurnRepository", _Repo)
    monkeypatch.setattr(retention_mod, "clear_message_pause_latch", _clear)

    assert await retention_mod.run_paused_turn_retention_sweep() == 2
    assert cleared == [("m-1", "c-1"), ("m-2", "c-2")]


def test_resume_settled_is_ephemeral_like_its_deferred_twin():
    """决策事实的权威是 journal；这帧只是传输态 ack，不许再落一份盘。"""
    from agentcore.runtime.events.disposition import EVENT_DISPOSITION, Disposition

    disposition, _reason = EVENT_DISPOSITION[EventType.RESUME_SETTLED]
    assert disposition is Disposition.EPHEMERAL
