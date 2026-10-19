"""续写既有回合：``run_and_persist(continue_message_id=…)``（崩溃重驱收口 · D5）。

恢复出来的成果归属原回合，所以收口跑在那条已存在的助手行上：不能再插一次
placeholder（行已在，且会把「曾中断恢复」标记和半截正文抹掉），恢复期已落的事实要
作为 journal 前缀继承下来，而不是被这一段当成新事实重写。
"""

from agentcore.conversation import turn_runner
from agentcore.runtime.events import EventSink, FinishReason
from agentcore.runtime.turn.snapshot import ConversationSnapshot


class _FakeBackend:
    location = "server"
    dirty = False


async def _run(monkeypatch, **overrides) -> dict:
    captured: dict = {"placeholders": []}

    async def _fake_pipeline(**kwargs):
        captured["pipeline"] = kwargs
        return {
            "finish_reason": FinishReason.END_TURN,
            "content": "终稿",
            "cost_runs": [],
            "message_id": kwargs.get("message_id"),
        }

    async def _fake_placeholder(**kwargs):
        captured["placeholders"].append(kwargs)

    async def _fake_persist(**kwargs):
        captured["persist"] = kwargs

    monkeypatch.setattr(turn_runner, "run_chat_pipeline", _fake_pipeline)
    monkeypatch.setattr(turn_runner, "create_assistant_placeholder", _fake_placeholder)
    monkeypatch.setattr(turn_runner, "persist_turn_result", _fake_persist)

    captured["result"] = await turn_runner.run_and_persist(
        conversation_id="c-cont",
        user_message="收口",
        user_id="u1",
        conversation=ConversationSnapshot.default(),
        sink=EventSink(),
        history=[],
        attachments=None,
        backend=_FakeBackend(),  # type: ignore[arg-type]
        llm_credentials=None,
        **overrides,
    )
    return captured


async def test_continuation_reuses_row_and_threads_journal_prefix(monkeypatch):
    prior = [{"kind": "run_plan", "payload": {"execution_id": "e1"}, "ts": "t0"}]
    captured = await _run(
        monkeypatch,
        continue_message_id="orig-turn-1",
        inherited_journal_entries=prior,
    )

    assert captured["placeholders"] == []
    assert captured["pipeline"]["message_id"] == "orig-turn-1"
    assert captured["pipeline"]["inherited_journal_entries"] == prior
    assert captured["result"]["message_id"] == "orig-turn-1"


async def test_fresh_turn_still_mints_row_and_starts_clean(monkeypatch):
    captured = await _run(monkeypatch)

    assert len(captured["placeholders"]) == 1
    minted = captured["placeholders"][0]["message_id"]
    assert minted and minted != "orig-turn-1"
    assert captured["pipeline"]["message_id"] == minted
    assert captured["pipeline"]["inherited_journal_entries"] is None
