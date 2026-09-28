"""Persist and replay CEO ``[系统提示]`` envelopes in the chat window."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from agentcore.conversation.history import (
    IN_HISTORY_SYSTEM_ORIGIN,
    IN_HISTORY_SYSTEM_USAGE_KEY,
    TURN_ENVELOPE_USAGE_KEY,
    _fold_history_messages,
    drop_trailing_user_turn,
    stamp_user_turn_envelope,
)
from agentcore.llm.provider.protocol import LLMMessage
from agentcore.runtime.facts import TurnStartedFact
from agentcore.runtime.journal.persist import persist_turn_journal
from agentcore.runtime.resolve.prompt.envelope import (
    TURN_ENVELOPE_FENCE,
    opening_ceo_messages,
)

_ENV = f"{TURN_ENVELOPE_FENCE}\n<运行时/>"


def _msg(
    role: str,
    content: str = "",
    *,
    id: str | None = None,
    turn_envelope: str | None = None,
    origin: str | None = None,
    harvest_kind: str | None = None,
    attachments: list | None = None,
    in_history_system: str | None = None,
) -> SimpleNamespace:
    usage: dict[str, Any] = {}
    if turn_envelope is not None:
        usage[TURN_ENVELOPE_USAGE_KEY] = turn_envelope
    if in_history_system is not None:
        usage[IN_HISTORY_SYSTEM_USAGE_KEY] = in_history_system
    if origin is not None:
        usage["origin"] = origin
    if harvest_kind is not None:
        usage["harvest_kind"] = harvest_kind
    return SimpleNamespace(
        id=id,
        role=role,
        content=content,
        usage=usage or None,
        attachments=attachments,
        agent_mentions=None,
    )


def test_fold_splices_stored_envelope_before_user_utterance():
    out = _fold_history_messages(
        [
            _msg("user", "q1", turn_envelope=_ENV),
            _msg("assistant", "a1"),
            _msg("user", "q2"),
        ]
    )
    assert out == [
        {"role": "user", "content": _ENV},
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "q2"},
    ]


def test_fold_skips_consecutive_identical_envelope():
    out = _fold_history_messages(
        [
            _msg("user", "q1", turn_envelope=_ENV),
            _msg("assistant", "a1"),
            _msg("user", "q2", turn_envelope=_ENV),
        ]
    )
    assert out == [
        {"role": "user", "content": _ENV},
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "q2"},
    ]


def test_fold_skips_consecutive_identical_in_history_system():
    out = _fold_history_messages(
        [
            _msg("user", "q1", turn_envelope=_ENV, in_history_system="SYS v2"),
            _msg("assistant", "a1"),
            _msg("user", "q2", turn_envelope=_ENV, in_history_system="SYS v2"),
        ]
    )
    assert out == [
        {
            "role": "system",
            "content": "SYS v2",
            "origin": IN_HISTORY_SYSTEM_ORIGIN,
        },
        {"role": "user", "content": _ENV},
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "q2"},
    ]


def test_fold_appends_new_in_history_when_text_changes():
    out = _fold_history_messages(
        [
            _msg("user", "q1", in_history_system="SYS v2"),
            _msg("assistant", "a1"),
            _msg("user", "q2", in_history_system="SYS v3"),
        ]
    )
    systems = [row for row in out if row.get("role") == "system"]
    assert [row["content"] for row in systems] == ["SYS v2", "SYS v3"]


def test_fold_ignores_unfenced_usage_blob():
    out = _fold_history_messages(
        [_msg("user", "q1", turn_envelope="not an envelope")]
    )
    assert out == [{"role": "user", "content": "q1"}]


def test_fold_splices_in_history_system_before_envelope():
    out = _fold_history_messages(
        [
            _msg("user", "q1", turn_envelope=_ENV, in_history_system="SYS v2"),
            _msg("assistant", "a1"),
        ]
    )
    assert out == [
        {
            "role": "system",
            "content": "SYS v2",
            "origin": IN_HISTORY_SYSTEM_ORIGIN,
        },
        {"role": "user", "content": _ENV},
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "a1"},
    ]


def test_fold_harvest_user_is_not_spliced_with_envelope():
    out = _fold_history_messages(
        [
            _msg(
                "user",
                "【系统收口】后台团队本波任务已全部完成。",
                origin="execution_harvest",
                harvest_kind="success",
                turn_envelope=_ENV,
            )
        ]
    )
    assert len(out) == 1
    assert out[0]["role"] == "user"
    assert out[0]["content"].startswith("（系统注记：")
    assert _ENV not in out[0]["content"]


def test_drop_trailing_user_pops_envelope_pair():
    history = [
        {"role": "user", "content": _ENV},
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": _ENV},
        {"role": "user", "content": "q2"},
    ]
    assert drop_trailing_user_turn(history) == [
        {"role": "user", "content": _ENV},
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "a1"},
    ]


def test_drop_trailing_user_only_if_content_skips_mismatch():
    history = [
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "older"},
    ]
    assert drop_trailing_user_turn(history, only_if_content="newer") == history


def test_drop_trailing_user_leaves_assistant_tail():
    history = [
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "a1"},
    ]
    assert drop_trailing_user_turn(history, only_if_content="q2") == history


def test_drop_trailing_user_pops_in_history_system():
    history = [
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "a1"},
        {
            "role": "system",
            "content": "SYS v2",
            "origin": IN_HISTORY_SYSTEM_ORIGIN,
        },
        {"role": "user", "content": _ENV},
        {"role": "user", "content": "q2"},
    ]
    assert drop_trailing_user_turn(history) == [
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "a1"},
    ]


def test_drop_trailing_user_keeps_unrelated_system_note():
    history = [
        {"role": "system", "content": "（系统注记：失败）"},
        {"role": "user", "content": _ENV},
        {"role": "user", "content": "q2"},
    ]
    assert drop_trailing_user_turn(history) == [
        {"role": "system", "content": "（系统注记：失败）"},
    ]


@pytest.mark.asyncio
async def test_stamp_user_turn_envelope_merges_usage(monkeypatch: pytest.MonkeyPatch):
    merged: dict[str, Any] = {}

    class _Repo:
        def __init__(self, _session: object) -> None:
            pass

        async def user_message_for_assistant(self, **_kw: object) -> SimpleNamespace:
            return SimpleNamespace(id="u1")

        async def merge_usage(
            self, message_id: str, *, conversation_id: str, usage: dict
        ) -> None:
            merged["id"] = message_id
            merged["conversation_id"] = conversation_id
            merged["usage"] = usage

    class _SessionCM:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, *_a: object) -> None:
            return None

    monkeypatch.setattr("agentcore.db.base.async_session_factory", lambda: _SessionCM())
    monkeypatch.setattr("agentcore.db.repositories.MessageRepository", _Repo)

    await stamp_user_turn_envelope(
        conversation_id="c1",
        assistant_message_id="a1",
        envelope=f"  {_ENV}\n",
    )
    assert merged["id"] == "u1"
    assert merged["conversation_id"] == "c1"
    assert merged["usage"] == {TURN_ENVELOPE_USAGE_KEY: _ENV}


@pytest.mark.asyncio
async def test_stamp_user_turn_envelope_header_only(monkeypatch: pytest.MonkeyPatch):
    from agentcore.observability.session_llm_header import (
        CHAT_HEADER_MODEL_USAGE_KEY,
        CHAT_HEADER_TOOLS_USAGE_KEY,
        FROZEN_CHAT_SYSTEM_USAGE_KEY,
        SessionLlmHeader,
    )

    merged: dict[str, Any] = {}

    class _Repo:
        def __init__(self, _session: object) -> None:
            pass

        async def user_message_for_assistant(self, **_kw: object) -> SimpleNamespace:
            return SimpleNamespace(id="u1")

        async def merge_usage(
            self, message_id: str, *, conversation_id: str, usage: dict
        ) -> None:
            merged["id"] = message_id
            merged["conversation_id"] = conversation_id
            merged["usage"] = usage

    class _SessionCM:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, *_a: object) -> None:
            return None

    monkeypatch.setattr("agentcore.db.base.async_session_factory", lambda: _SessionCM())
    monkeypatch.setattr("agentcore.db.repositories.MessageRepository", _Repo)

    header = SessionLlmHeader(
        system="FROZEN CEO",
        tools=({"type": "function", "function": {"name": "delegate"}},),
        model="deepseek-v4-pro",
    )
    await stamp_user_turn_envelope(
        conversation_id="c1",
        assistant_message_id="a1",
        envelope="plain user text",
        header=header,
    )
    assert merged["usage"] == {
        FROZEN_CHAT_SYSTEM_USAGE_KEY: "FROZEN CEO",
        CHAT_HEADER_TOOLS_USAGE_KEY: [
            {"type": "function", "function": {"name": "delegate"}}
        ],
        CHAT_HEADER_MODEL_USAGE_KEY: "deepseek-v4-pro",
    }


@pytest.mark.asyncio
async def test_stamp_user_turn_envelope_skips_unfenced(monkeypatch: pytest.MonkeyPatch):
    class _Repo:
        def __init__(self, _session: object) -> None:
            raise AssertionError("must not open a repo")

    monkeypatch.setattr("agentcore.db.repositories.MessageRepository", _Repo)
    await stamp_user_turn_envelope(
        conversation_id="c1",
        assistant_message_id="a1",
        envelope="plain user text",
    )


@pytest.mark.asyncio
async def test_persist_stamps_envelope_from_turn_started(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agentcore.observability.session_llm_header import reset_session_headers
    from agentcore.runtime.journal.persist import persist_turn_journal

    reset_session_headers()

    stamped: dict[str, Any] = {}

    async def _stamp(**kwargs: Any) -> None:
        stamped.update(kwargs)

    class _Repo:
        def __init__(self, _s: object) -> None:
            pass

        async def record(self, **_kw: object) -> None:
            raise AssertionError("default persist must merge, not record")

        async def append(self, **_kw: object) -> int:
            return 0

        async def load(self, _message_id: str) -> list:
            return []

        async def max_seq(self, _message_id: str) -> None:
            return None

    class _Session:
        async def rollback(self) -> None:
            return None

    monkeypatch.setattr("agentcore.db.repositories.TurnJournalRepository", _Repo)
    monkeypatch.setattr(
        "agentcore.config.settings.observability_span_export_enabled", False
    )
    monkeypatch.setattr(
        "agentcore.conversation.history.stamp_user_turn_envelope", _stamp
    )

    await persist_turn_journal(
        _Session(),  # type: ignore[arg-type]
        message_id="m1",
        conversation_id="c1",
        trace_id="t",
        entries=[
            {
                "kind": "turn_started",
                "payload": {"turn_envelope": _ENV},
            },
            {"kind": "turn_end", "payload": {"finish_reason": "end_turn"}},
        ],
    )
    assert stamped == {
        "conversation_id": "c1",
        "assistant_message_id": "m1",
        "envelope": _ENV,
        "in_history_system": "",
        "header": None,
    }


@pytest.mark.asyncio
async def test_persist_stamps_header_without_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agentcore.llm.provider.protocol import LLMMessage
    from agentcore.observability.session_llm_header import (
        record_session_header,
        reset_session_headers,
    )
    from agentcore.runtime.journal.persist import persist_turn_journal

    reset_session_headers()
    record_session_header(
        conversation_id="c-header-stamp",
        scenario="chat",
        model="deepseek-v4-pro",
        messages=[LLMMessage(role="system", content="FROZEN CEO")],
        tools=[{"type": "function", "function": {"name": "delegate"}}],
    )
    stamped: dict[str, Any] = {}

    async def _stamp(**kwargs: Any) -> None:
        stamped.update(kwargs)

    class _Repo:
        def __init__(self, _s: object) -> None:
            pass

        async def record(self, **_kw: object) -> None:
            raise AssertionError("default persist must merge, not record")

        async def append(self, **_kw: object) -> int:
            return 0

        async def load(self, _message_id: str) -> list:
            return []

        async def max_seq(self, _message_id: str) -> None:
            return None

    class _Session:
        async def rollback(self) -> None:
            return None

    monkeypatch.setattr("agentcore.db.repositories.TurnJournalRepository", _Repo)
    monkeypatch.setattr(
        "agentcore.config.settings.observability_span_export_enabled", False
    )
    monkeypatch.setattr(
        "agentcore.conversation.history.stamp_user_turn_envelope", _stamp
    )

    await persist_turn_journal(
        _Session(),  # type: ignore[arg-type]
        message_id="m1",
        conversation_id="c-header-stamp",
        trace_id="t",
        entries=[
            {"kind": "turn_started", "payload": {}},
            {"kind": "turn_end", "payload": {"finish_reason": "end_turn"}},
        ],
    )
    reset_session_headers()
    assert stamped["conversation_id"] == "c-header-stamp"
    assert stamped["envelope"] == ""
    assert stamped["in_history_system"] == ""
    assert stamped["header"] is not None
    assert stamped["header"].system == "FROZEN CEO"
    assert stamped["header"].model == "deepseek-v4-pro"
    assert stamped["header"].tools[0]["function"]["name"] == "delegate"


@pytest.mark.asyncio
async def test_persist_stamp_failure_does_not_break_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agentcore.runtime.journal.persist import persist_turn_journal

    class _Repo:
        def __init__(self, _s: object) -> None:
            pass

        async def append(self, **_kw: object) -> int:
            return 0

        async def load(self, _message_id: str) -> list:
            return []

        async def max_seq(self, _message_id: str) -> None:
            return None

    class _Session:
        async def rollback(self) -> None:
            return None

    async def _boom(**_kw: object) -> None:
        raise RuntimeError("stamp failed")

    monkeypatch.setattr("agentcore.db.repositories.TurnJournalRepository", _Repo)
    monkeypatch.setattr(
        "agentcore.config.settings.observability_span_export_enabled", False
    )
    monkeypatch.setattr(
        "agentcore.conversation.history.stamp_user_turn_envelope", _boom
    )

    await persist_turn_journal(
        _Session(),  # type: ignore[arg-type]
        message_id="m1",
        conversation_id="c1",
        trace_id="t",
        entries=[
            {"kind": "turn_started", "payload": {"turn_envelope": _ENV}},
            {"kind": "turn_end", "payload": {"finish_reason": "end_turn"}},
        ],
    )


def _patch_journal_persist(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Repo:
        def __init__(self, _s: object) -> None:
            pass

        async def append(self, **_kw: object) -> int:
            return 0

        async def load(self, _message_id: str) -> list:
            return []

        async def max_seq(self, _message_id: str) -> None:
            return None

    monkeypatch.setattr("agentcore.db.repositories.TurnJournalRepository", _Repo)
    monkeypatch.setattr(
        "agentcore.config.settings.observability_span_export_enabled", False
    )


def _patch_stamp_row(monkeypatch: pytest.MonkeyPatch, user_row: SimpleNamespace) -> None:
    class _Repo:
        def __init__(self, _session: object) -> None:
            pass

        async def user_message_for_assistant(self, **_kw: object) -> SimpleNamespace:
            return user_row

        async def merge_usage(
            self, message_id: str, *, conversation_id: str, usage: dict
        ) -> None:
            del conversation_id
            assert message_id == user_row.id
            merged = dict(user_row.usage or {})
            merged.update(usage)
            user_row.usage = merged

    class _SessionCM:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, *_a: object) -> None:
            return None

    monkeypatch.setattr("agentcore.db.base.async_session_factory", lambda: _SessionCM())
    monkeypatch.setattr("agentcore.db.repositories.MessageRepository", _Repo)


class _PersistSession:
    async def rollback(self) -> None:
        return None


@pytest.mark.asyncio
async def test_t2_persist_fold_drop_opening_matches_probe_keep_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Production T2 window equals the live-probe keep-envelope window.

    persist ``turn_started`` → stamp ``usage.turn_envelope`` → fold → drop
    trailing user (cloud after insert / sidecar before insert) → opening.
    Bubble text stays the original utterance. No upstream call.
    """
    env1 = f"{TURN_ENVELOPE_FENCE}\n<运行时/>"
    env2 = f"{TURN_ENVELOPE_FENCE}\n<表格/>"
    system = "SYS"
    user1 = "q1"
    user2 = "q2"
    reply = "a1"

    turn1 = opening_ceo_messages(
        system_prompt=system,
        history=None,
        turn_envelope=env1,
        user_content=user1,
    )
    probe_t2 = [
        *turn1,
        LLMMessage(role="assistant", content=reply),
        LLMMessage(role="user", content=env2),
        LLMMessage(role="user", content=user2),
    ]

    prompting = _msg("user", user1, id="u1")
    _patch_journal_persist(monkeypatch)
    _patch_stamp_row(monkeypatch, prompting)

    await persist_turn_journal(
        _PersistSession(),  # type: ignore[arg-type]
        message_id="a1",
        conversation_id="c1",
        trace_id="t",
        entries=[
            TurnStartedFact(
                system_prompt=system,
                user_message=user1,
                model_profile="m",
                turn_envelope=env1,
            )
            .to_fact()
            .entry(),
            {"kind": "turn_end", "payload": {"finish_reason": "end_turn"}},
        ],
    )
    assert prompting.content == user1
    assert (prompting.usage or {}).get(TURN_ENVELOPE_USAGE_KEY) == env1

    cloud_rows = [prompting, _msg("assistant", reply), _msg("user", user2)]
    sidecar_rows = [prompting, _msg("assistant", reply)]
    cloud_prior = drop_trailing_user_turn(_fold_history_messages(cloud_rows))
    sidecar_prior = drop_trailing_user_turn(
        _fold_history_messages(sidecar_rows),
        only_if_content=user2,
    )
    assert cloud_prior == sidecar_prior == [
        {"role": "user", "content": env1},
        {"role": "user", "content": user1},
        {"role": "assistant", "content": reply},
    ]

    product_t2 = opening_ceo_messages(
        system_prompt=system,
        history=cloud_prior,
        turn_envelope=env2,
        user_content=user2,
    )
    assert product_t2 == probe_t2
