"""Schema + mapping + logging for ``RecordTurnRequest.tool_failures``."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from agentcore.api.schemas.messages import (
    LocalTurnToolFailure,
    RecordTurnRequest,
    normalize_local_turn_tool_failure_code,
    truncate_tool_failure_message,
)
from agentcore.conversation import local_turn as local_turn_mod
from agentcore.conversation.service import record_local_turn
from agentcore.conversation.store.outbox import (
    to_record_turn_body,
    tool_failures_from_journal,
)

pytestmark = pytest.mark.anyio

_TRACE = "0123456789abcdef0123456789abcdef"


@pytest.mark.parametrize(
    ("message", "code", "expected"),
    [
        ("searxng healthz failed", None, "searxng_unreachable"),
        ("搜索服务 searxng.local 最近连续多次请求失败（超时或连接失败）", None, "searxng_unreachable"),
        ("搜索失败：无法建立连接（出网受限或站点不可达）", None, "egress_connect"),
        ("ConnectError: connection refused", None, "egress_connect"),
        ("连接超时（无法连上该站点）", None, "egress_connect"),
        ("缺少必填参数：query", None, "schema"),
        (
            "delegate 缺 tasks/playbook：请在 payload 顶层直接放非空 `tasks`",
            None,
            "declaration_empty",
        ),
        ("delegate 须传手写 `tasks`，其余…", None, "declaration_empty"),
        (
            "playbook 与 tasks 二选一，不可同时传。手写 tasks：去掉具名…",
            None,
            "declaration_xor",
        ),
        ("未知 playbook『x』；可用：a, b。", None, "declaration_unknown"),
        ("anything", "declaration_empty", "declaration_empty"),
        ("anything", "searxng_unreachable", "searxng_unreachable"),
        ("searxng down", "egress_connect", "egress_connect"),
        ("unknown", "weird", "other"),
        ("Timeout: execution exceeded 30s", None, "exec_timeout"),
        ("Timeout: no output for 60s (execution stalled)", None, "exec_timeout"),
        (
            "Timeout: forced stop after 1200s (forced stop)",
            None,
            "exec_forced_stop",
        ),
        ("验证未在 300s 预算内完成（验证未完成，非工具故障）", None, "exec_timeout"),
        ("ExecEnvProbeFailed: 本机执行环境自检未通过", None, "exec_timeout"),
        ("anything", "verify_budget", "exec_timeout"),
        ("anything", "exec_timeout", "exec_timeout"),
        ("anything", "exec_forced_stop", "exec_forced_stop"),
        ("git timed out", "git_timeout", "git_timeout"),
        ("git timed out", "timeout", "git_timeout"),
        ("no git repo", "no_repo", "no_repo"),
        ("schema reject", "schema", "schema"),
        ("dirty", "dirty_skip", "dirty_skip"),
        ("auth", "unauthenticated", "unauthenticated"),
        ("x", "not_a_web_url", "not_a_web_url"),
        ("x", "url_not_workspace_path", "url_not_workspace_path"),
        ("文件不存在：docs/ghost.md", None, "not_found"),
        ("不是目录：apps/server/src", "schema", "not_found"),
        ("路径不存在：apps/server/src", "other", "not_found"),
        ("anything", "not_found", "not_found"),
        (
            "禁止用 code_execute 跑项目级慢验证（检测到：pytest）。本工具约 60s 硬顶",
            None,
            "project_verify_redirect",
        ),
        ("anything", "project_verify_redirect", "project_verify_redirect"),
        (
            "禁止用 code_execute 打开源码再正则扫描（检测到：re.findall(）。",
            None,
            "source_grep_redirect",
        ),
        ("anything", "source_grep_redirect", "source_grep_redirect"),
        ("缺少参数", "schema", "schema"),
        ("这份文件太大", "too_large", "too_large"),
    ],
)
def test_normalize_local_turn_tool_failure_code(message, code, expected):
    assert normalize_local_turn_tool_failure_code(message, code=code) == expected


def test_tool_failures_from_journal_omits_channel_redirect():
    failures = tool_failures_from_journal(
        [
            {
                "kind": "tool_call",
                "payload": {
                    "name": "code_execute",
                    "success": False,
                    "result": "禁止用 code_execute 打开源码再正则扫描（检测到：re.findall(）。",
                    "code": "source_grep_redirect",
                },
            }
        ]
    )
    assert failures == []


def test_tool_failures_from_journal_passes_payload_code():
    failures = tool_failures_from_journal(
        [
            {
                "kind": "tool_call",
                "payload": {
                    "name": "git",
                    "success": False,
                    "result": "git timed out after 30s",
                    "code": "git_timeout",
                },
            }
        ]
    )
    assert len(failures) == 1
    assert failures[0]["tool"] == "git"
    assert failures[0]["code"] == "git_timeout"


def test_tool_failures_from_journal_schema_from_missing_arg_message():
    failures = tool_failures_from_journal(
        [
            {
                "kind": "tool_call",
                "payload": {
                    "name": "code_search",
                    "success": False,
                    "result": "缺少必填参数：query",
                },
            }
        ]
    )
    assert failures[0]["code"] == "schema"


def test_tool_failures_from_journal_git_no_repo_code():
    failures = tool_failures_from_journal(
        [
            {
                "kind": "tool_call",
                "payload": {
                    "name": "git",
                    "success": False,
                    "result": "工作区无 git 仓库",
                    "code": "no_repo",
                },
            }
        ]
    )
    assert failures[0]["code"] == "no_repo"


def test_tool_failures_from_journal_declaration_empty():
    """Local delegate declaration empty → code is declaration_empty, not other."""
    from agentcore.runtime.delegate.playbook_declaration import _EMPTY_DELEGATE_MSG

    failures = tool_failures_from_journal(
        [
            {
                "kind": "tool_call",
                "payload": {
                    "name": "delegate",
                    "success": False,
                    "result": _EMPTY_DELEGATE_MSG,
                },
            }
        ]
    )
    assert len(failures) == 1
    assert failures[0]["tool"] == "delegate"
    assert failures[0]["code"] == "declaration_empty"


def test_truncate_tool_failure_message_caps_at_200():
    long = "x" * 250
    assert len(truncate_tool_failure_message(long)) == 200
    assert truncate_tool_failure_message(None) == ""


def test_local_turn_tool_failure_schema_normalizes_and_truncates():
    long = "无法建立连接：" + ("y" * 250)
    row = LocalTurnToolFailure(tool="web_search", code="weird", message=long)
    assert row.code == "egress_connect"
    assert len(row.message) == 200


def test_record_turn_request_accepts_empty_user_message():
    """Process-only salvage may omit real um (ffafc42b)."""
    body = RecordTurnRequest(
        user_message="",
        user_message_id="u1",
        trace_id=_TRACE,
    )
    assert body.user_message == ""


def test_record_turn_request_tool_failures_optional_default_empty():
    body = RecordTurnRequest(
        user_message="hi",
        user_message_id="u1",
        trace_id=_TRACE,
    )
    assert body.tool_failures == []


def test_record_turn_request_accepts_tool_failures():
    body = RecordTurnRequest(
        user_message="hi",
        user_message_id="u1",
        trace_id=_TRACE,
        tool_failures=[
            {
                "tool": "web_search",
                "code": "searxng_unreachable",
                "message": "searxng unreachable",
            }
        ],
    )
    assert len(body.tool_failures) == 1
    assert body.tool_failures[0].tool == "web_search"
    assert body.tool_failures[0].code == "searxng_unreachable"


def test_record_turn_request_rejects_empty_tool_name():
    with pytest.raises(ValidationError):
        RecordTurnRequest(
            user_message="hi",
            user_message_id="u1",
            trace_id=_TRACE,
            tool_failures=[{"tool": "", "code": "other", "message": "x"}],
        )


def test_tool_failures_from_journal_prefers_tool_call_facts():
    entries = [
        {
            "kind": "tool_call",
            "payload": {
                "name": "web_search",
                "success": False,
                "result": "搜索失败：无法建立连接（出网受限或站点不可达）",
            },
        },
        {
            "kind": "tool_use_end",
            "payload": {
                "tool_name": "web_search",
                "status": "error",
                "result": "duplicate display end",
            },
        },
        {
            "kind": "tool_call",
            "payload": {"name": "read_url", "success": True, "result": "ok"},
        },
    ]
    failures = tool_failures_from_journal(entries)
    assert len(failures) == 1
    assert failures[0]["tool"] == "web_search"
    assert failures[0]["code"] == "egress_connect"


def test_tool_failures_from_journal_falls_back_to_tool_use_end():
    entries = [
        {
            "kind": "tool_use_end",
            "payload": {
                "tool_name": "web_search",
                "status": "error",
                "result": "searxng unreachable",
            },
        }
    ]
    failures = tool_failures_from_journal(entries)
    assert failures == [
        {
            "tool": "web_search",
            "code": "searxng_unreachable",
            "message": "searxng unreachable",
        }
    ]


def test_to_record_turn_body_includes_tool_failures_from_journal():
    body = to_record_turn_body(
        {
            "user_message_id": "u1",
            "user_message": "hi",
            "trace_id": _TRACE,
            "journal": {
                "1": {
                    "kind": "tool_call",
                    "payload": {
                        "name": "web_search",
                        "success": False,
                        "result": "搜索服务 down",
                    },
                    "ts": "t1",
                }
            },
        }
    )
    assert body["tool_failures"] == [
        {
            "tool": "web_search",
            "code": "searxng_unreachable",
            "message": "搜索服务 down",
        }
    ]


def test_to_record_turn_body_resume_after_seq_filters_only_tool_failures():
    journal = {
        "0": {
            "kind": "tool_call",
            "payload": {
                "name": "file_read",
                "success": False,
                "result": "pause-turn fail",
                "code": "too_large",
            },
            "ts": "t0",
        },
        "1": {
            "kind": "tool_call",
            "payload": {
                "name": "web_search",
                "success": False,
                "result": "搜索服务 down",
            },
            "ts": "t1",
        },
    }
    body = to_record_turn_body(
        {
            "user_message_id": "u1",
            "user_message": "hi",
            "trace_id": _TRACE,
            "resume_after_seq": 0,
            "journal": journal,
        }
    )
    assert len(body["journal"]) == 2
    assert body["tool_failures"] == [
        {
            "tool": "web_search",
            "code": "searxng_unreachable",
            "message": "搜索服务 down",
        }
    ]


def test_tool_call_fact_code_schema_is_parse_only_not_all_contract_failure():
    from agentcore.runtime.engine.tool_call_fact_code import tool_call_fact_code
    from agentcore.runtime.loop_controller import ToolAttempt

    too_large = ToolAttempt(
        fingerprint="a",
        tool_name="file_read",
        success=False,
        contract_failure=True,
        meta={"code": "too_large"},
    )
    assert tool_call_fact_code(too_large) == "too_large"

    parse = ToolAttempt(
        fingerprint="b",
        tool_name="file_read",
        success=False,
        parse_failure=True,
    )
    assert tool_call_fact_code(parse) == "schema"

    contract_only = ToolAttempt(
        fingerprint="c",
        tool_name="file_read",
        success=False,
        contract_failure=True,
    )
    assert tool_call_fact_code(contract_only) == ""


def test_to_record_turn_body_omits_tool_failures_when_none():
    body = to_record_turn_body(
        {
            "user_message_id": "u1",
            "user_message": "hi",
            "trace_id": _TRACE,
            "journal": {
                "0": {
                    "kind": "tool_call",
                    "payload": {"name": "web_search", "success": True, "result": "ok"},
                }
            },
        }
    )
    assert "tool_failures" not in body


def test_to_record_turn_body_includes_agent_mentions():
    mentions = [{"agent_id": "w1", "role": "研究员"}]
    body = to_record_turn_body(
        {
            "user_message_id": "u1",
            "user_message": "hi",
            "trace_id": _TRACE,
            "agent_mentions": mentions,
        }
    )
    assert body["agent_mentions"] == mentions
    omitted = to_record_turn_body(
        {
            "user_message_id": "u2",
            "user_message": "hi",
            "trace_id": _TRACE,
        }
    )
    assert "agent_mentions" not in omitted


async def test_record_local_turn_logs_tool_failures(monkeypatch):
    logged: list[tuple] = []

    class _Logger:
        def info(self, event, **kwargs):
            logged.append((event, kwargs))

    monkeypatch.setattr(local_turn_mod, "logger", _Logger())
    finalize = AsyncMock(
        return_value={
            "user_message_id": "u1",
            "assistant_message_id": "a1",
            "title": None,
            "followups": None,
            "noop": False,
        }
    )
    monkeypatch.setattr(
        local_turn_mod,
        "get_cloud_store",
        lambda: type("S", (), {"finalize": finalize})(),
    )

    await record_local_turn(
        conversation_id="c1",
        user_id="u1",
        user_message="hi",
        assistant_content="ok",
        user_message_id="u1",
        message_id="m1",
        trace_id=_TRACE,
        tool_failures=[
            {"tool": "web_search", "code": "searxng_unreachable", "message": "down"},
            {"tool": "read_url", "code": "other", "message": "HTTP 403 from example.com"},
            {
                "tool": "file_read",
                "code": "other",
                "message": "Timeout: execution exceeded 30s",
            },
        ],
    )

    assert logged == [
        (
            "chat.local_turn_tool_failures",
            {
                "conversation_id": "c1",
                "message_id": "m1",
                "count": 3,
                "codes": ["searxng_unreachable", "other", "other"],
                "tools": ["web_search", "read_url", "file_read"],
                "messages": [
                    "down",
                    "HTTP 403 from example.com",
                    "Timeout: execution exceeded 30s",
                ],
            },
        )
    ]
    finalize.assert_awaited_once()
    assert "tool_failures" not in finalize.await_args.kwargs


async def test_record_local_turn_caps_logged_failure_messages(monkeypatch):
    logged: list[tuple] = []

    class _Logger:
        def info(self, event, **kwargs):
            logged.append((event, kwargs))

    monkeypatch.setattr(local_turn_mod, "logger", _Logger())
    finalize = AsyncMock(
        return_value={
            "user_message_id": "u1",
            "assistant_message_id": "a1",
            "title": None,
            "followups": None,
            "noop": False,
        }
    )
    monkeypatch.setattr(
        local_turn_mod,
        "get_cloud_store",
        lambda: type("S", (), {"finalize": finalize})(),
    )

    await record_local_turn(
        conversation_id="c1",
        user_id="u1",
        user_message="hi",
        assistant_content="ok",
        user_message_id="u1",
        message_id="m1",
        trace_id=_TRACE,
        tool_failures=[{"tool": "read_url", "code": "other", "message": "x" * 250}],
    )

    assert logged[0][1]["messages"] == ["x" * 200]


async def test_record_local_turn_skips_log_when_no_failures(monkeypatch):
    logged: list[tuple] = []

    class _Logger:
        def info(self, event, **kwargs):
            logged.append((event, kwargs))

    monkeypatch.setattr(local_turn_mod, "logger", _Logger())
    finalize = AsyncMock(
        return_value={
            "user_message_id": "u1",
            "assistant_message_id": "a1",
            "title": None,
            "followups": None,
            "noop": False,
        }
    )
    monkeypatch.setattr(
        local_turn_mod,
        "get_cloud_store",
        lambda: type("S", (), {"finalize": finalize})(),
    )

    await record_local_turn(
        conversation_id="c1",
        user_id="u1",
        user_message="hi",
        assistant_content="ok",
        user_message_id="u1",
        message_id="m1",
        trace_id=_TRACE,
    )

    assert logged == []
