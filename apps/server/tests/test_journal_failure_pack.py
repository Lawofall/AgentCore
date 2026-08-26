"""Journal-only failure pack: redacted files, skip non-failures, never raise."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from agentcore.runtime.journal.failure_pack import (
    FAILURE_PACK_SCHEMA,
    write_journal_failure_pack,
)
from agentcore.runtime.journal.persist import persist_turn_journal

_SECRET_USER = "please write my diary SECRET_USER"
_SECRET_PROMPT = "You are the CEO. User said SECRET_PROMPT"
_SECRET_BODY = "here is the full assistant reply"

_ERROR_ENTRIES = [
    {
        "kind": "turn_started",
        "payload": {
            "system_prompt": _SECRET_PROMPT,
            "user_message": _SECRET_USER,
            "model_profile": "chat",
            "history_len": 2,
        },
        "ts": None,
    },
    {
        "kind": "llm_call",
        "payload": {
            "run_id": "cap",
            "content": _SECRET_BODY,
            "finish_reason": "stop",
            "usage": {"input": 3, "output": 4},
        },
        "ts": None,
    },
    {"kind": "turn_end", "payload": {"finish_reason": "error"}, "ts": None},
]


def _assert_no_secrets(text: str) -> None:
    assert _SECRET_USER not in text
    assert _SECRET_PROMPT not in text
    assert _SECRET_BODY not in text
    assert "diary" not in text
    assert "full assistant reply" not in text


def _fake_persist_session(monkeypatch):
    class Repo:
        def __init__(self, _s):
            pass

        async def record(self, **_kw) -> None:
            return None

        async def append(self, *, turn_id, seq, conversation_id, trace_id, entry) -> int:
            del turn_id, conversation_id, trace_id, entry
            return seq if seq is not None else 0

    class Session:
        async def rollback(self):
            pass

    monkeypatch.setattr("agentcore.db.repositories.TurnJournalRepository", Repo)
    monkeypatch.setattr("agentcore.config.settings.observability_span_export_enabled", False)
    return Session()


def test_error_writes_meta_and_redacted_without_user_text(tmp_path: Path) -> None:
    root = tmp_path / "packs"
    tid = "a" * 32
    write_journal_failure_pack(
        _ERROR_ENTRIES,
        message_id="m1",
        conversation_id="c1",
        trace_id=tid,
        packs_root=root,
    )
    out = root / tid
    meta_path = out / "meta.json"
    journal_path = out / "journal.redacted.jsonl"
    assert meta_path.is_file()
    assert journal_path.is_file()
    assert not (out / "decision_spine.json").exists()
    assert not (out / "timeline.jsonl").exists()

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["schema_version"] == FAILURE_PACK_SCHEMA
    assert meta["kind"] == "journal_only"
    assert meta["finish_reason"] == "error"
    assert meta["trace_id"] == tid
    assert meta["journal"]["mode"] == "redacted"
    assert set(meta["files"]) == {"journal.redacted.jsonl", "meta.json"}

    dumped = meta_path.read_text(encoding="utf-8") + journal_path.read_text(encoding="utf-8")
    _assert_no_secrets(dumped)
    rows = [
        json.loads(line)
        for line in journal_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(rows) == 3
    started = next(r for r in rows if r["kind"] == "turn_started")
    assert started["payload"]["model_profile"] == "chat"
    assert "user_message" not in started["payload"]
    llm = next(r for r in rows if r["kind"] == "llm_call")
    assert llm["payload"]["run_id"] == "cap"
    assert "content" not in llm["payload"]


def test_end_turn_does_not_write(tmp_path: Path) -> None:
    root = tmp_path / "packs"
    write_journal_failure_pack(
        [
            {"kind": "run_plan", "payload": {}},
            {"kind": "turn_end", "payload": {"finish_reason": "end_turn"}},
        ],
        message_id="m1",
        conversation_id="c1",
        trace_id="b" * 32,
        packs_root=root,
    )
    assert not root.exists() or not any(root.iterdir())


def test_write_error_does_not_raise(tmp_path: Path, monkeypatch) -> None:
    def _boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(
        "agentcore.runtime.journal.failure_pack._write_jsonl",
        _boom,
    )
    write_journal_failure_pack(
        _ERROR_ENTRIES,
        message_id="m1",
        conversation_id="c1",
        trace_id="c" * 32,
        packs_root=tmp_path / "packs",
    )


def test_missing_trace_skips(tmp_path: Path) -> None:
    root = tmp_path / "packs"
    write_journal_failure_pack(
        _ERROR_ENTRIES,
        message_id="m1",
        conversation_id="c1",
        trace_id=None,
        packs_root=root,
    )
    assert not root.exists() or not any(root.iterdir())


def test_gc_drops_packs_older_than_ttl(tmp_path: Path, monkeypatch) -> None:
    seen: list[tuple[str, dict]] = []

    def _info(event, **kw):
        seen.append((event, kw))

    monkeypatch.setattr(
        "agentcore.runtime.journal.failure_pack.logger.info",
        _info,
    )
    root = tmp_path / "packs"
    stale = root / "oldpack"
    stale.mkdir(parents=True)
    (stale / "meta.json").write_text("{}\n", encoding="utf-8")
    old = time.time() - 31 * 86400
    os.utime(stale, (old, old))
    tid = "d" * 32
    write_journal_failure_pack(
        _ERROR_ENTRIES,
        message_id="m1",
        conversation_id="c1",
        trace_id=tid,
        packs_root=root,
    )
    assert not stale.exists()
    assert (root / tid / "meta.json").is_file()
    expired = [kw for ev, kw in seen if ev == "journal.failure_pack_gc_expired"]
    assert expired
    assert expired[0]["trace_id"] == "oldpack"


def test_locate_and_format_auto_pack_line(tmp_path: Path) -> None:
    from agentcore.runtime.journal.failure_pack import (
        failure_pack_pointer,
        format_auto_pack_line,
        locate_failure_pack,
    )

    tid = "e" * 32
    root = tmp_path / "packs"
    assert locate_failure_pack(tid, packs_root=root) is None
    assert format_auto_pack_line(tid, packs_root=root) is None
    assert failure_pack_pointer(tid, packs_root=root) is None
    write_journal_failure_pack(
        _ERROR_ENTRIES,
        message_id="m1",
        conversation_id="c1",
        trace_id=tid,
        packs_root=root,
    )
    found = locate_failure_pack(tid, packs_root=root)
    assert found == root / tid
    line = format_auto_pack_line(tid, packs_root=root)
    assert line is not None
    assert "logs/packs/" + tid in line.replace("\\", "/")
    assert "无原文" in line
    pointer = failure_pack_pointer(tid, packs_root=root)
    assert pointer == {"kind": "journal_only", "path": f"logs/packs/{tid}"}
    assert locate_failure_pack("not-a-trace", packs_root=root) is None


async def test_persist_error_closer_calls_write_pack(monkeypatch) -> None:
    session = _fake_persist_session(monkeypatch)
    calls: list[dict] = []

    def _capture(entries, *, message_id, conversation_id, trace_id, packs_root=None):
        del packs_root
        calls.append(
            {
                "finish": (entries[-1].get("payload") or {}).get("finish_reason"),
                "message_id": message_id,
                "conversation_id": conversation_id,
                "trace_id": trace_id,
            }
        )

    monkeypatch.setattr(
        "agentcore.runtime.journal.failure_pack.write_journal_failure_pack",
        _capture,
    )
    await persist_turn_journal(
        session,  # type: ignore[arg-type]
        message_id="m1",
        conversation_id="c1",
        trace_id="e" * 32,
        entries=_ERROR_ENTRIES,
    )
    assert calls == [
        {
            "finish": "error",
            "message_id": "m1",
            "conversation_id": "c1",
            "trace_id": "e" * 32,
        }
    ]


async def test_persist_end_turn_does_not_call_write_pack(monkeypatch) -> None:
    session = _fake_persist_session(monkeypatch)
    calls: list[object] = []
    monkeypatch.setattr(
        "agentcore.runtime.journal.failure_pack.write_journal_failure_pack",
        lambda *a, **k: calls.append((a, k)),
    )
    await persist_turn_journal(
        session,  # type: ignore[arg-type]
        message_id="m1",
        conversation_id="c1",
        trace_id="f" * 32,
        entries=[
            {"kind": "run_plan", "payload": {}},
            {"kind": "turn_end", "payload": {"finish_reason": "end_turn"}},
        ],
    )
    assert calls == []


async def test_persist_pack_write_error_does_not_raise(monkeypatch) -> None:
    session = _fake_persist_session(monkeypatch)

    def _boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(
        "agentcore.runtime.journal.failure_pack.write_journal_failure_pack",
        _boom,
    )
    await persist_turn_journal(
        session,  # type: ignore[arg-type]
        message_id="m1",
        conversation_id="c1",
        trace_id="g" * 32,
        entries=_ERROR_ENTRIES,
    )
