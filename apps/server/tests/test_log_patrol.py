"""巡检① 失败榜 / 快照 / 跨窗 diff 的单元测试（scripts/log_patrol.py 的逻辑层）。

钉三件容易静默坏掉的事：
1. 家族表的**身份**（key 不改名、alias 接得回、口径变了 digest 会变）；
2. 失败榜每一行都带得回 trace_id / conversation_id（不带就等于没沉淀）；
3. 跨窗 diff 把「口径变了」和「数量变了」分得开——这正是历史上静默改名断掉序列的地方。
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentcore.observability.query.failure_families import (
    FAILURE_FAMILIES,
    FAMILIES_BY_KEY,
    UNKNOWN_FAMILY,
    FailureFamily,
    clip_diagnostic_value,
    compile_registry,
    family_digests,
    registry_digest,
    resolve_family_key,
)
from agentcore.observability.query.patrol import (
    MUST_REVIEW_FAMILIES,
    SNAPSHOT_SCHEMA_VERSION,
    TimePulseGate,
    diff_snapshots,
    event_text,
    is_tool_failure,
    load_snapshot,
    scan_patrol,
    write_snapshot,
)

# ---------------------------------------------------------------------------
# 家族表：身份与口径指纹
# ---------------------------------------------------------------------------


def test_registry_keys_and_aliases_are_unique():
    # 表是靠人往里加的知识资产 —— key 撞车 / alias 撞 key 会让跨窗序列悄悄合并。
    keys = [f.key for f in FAILURE_FAMILIES]
    assert len(keys) == len(set(keys))
    aliases = [a for f in FAILURE_FAMILIES for a in f.aliases]
    assert len(aliases) == len(set(aliases))
    assert not (set(aliases) & set(keys))
    assert UNKNOWN_FAMILY not in keys  # 残差桶不是家族，不能有口径


def test_every_family_is_decidable_and_compiles():
    for fam in FAILURE_FAMILIES:
        assert fam.events or fam.patterns or fam.detector, f"{fam.key} 没有任何判定依据"
        fam.compiled()  # 正则语法错在这里就红，而不是巡检当场
        assert fam.key.islower()
        assert fam.label


def test_alias_resolves_to_live_key_and_unknown_stays_unknown():
    # 真实事故：temperature_deprecated 被静默改名成 temperature_invalid，跨窗计数断裂。
    assert resolve_family_key("temperature_deprecated") == "temperature_invalid"
    assert resolve_family_key("temperature_invalid") == "temperature_invalid"
    assert resolve_family_key("never_existed") is None


def test_digest_ignores_cosmetics_but_moves_with_semantics():
    fam = FAMILIES_BY_KEY["path_not_dir"]
    base = fam.digest()

    # 改展示名 / 说明 / 起始窗 —— 口径没变，序列必须继续可比。
    assert replace(fam, label="别的名字", note="改了说明", since="1999-01-01").digest() == base

    # 改 pattern / 事件名 / revision / 大小写敏感 —— 口径变了，指纹必须跟着变。
    assert replace(fam, patterns=fam.patterns + (r"扩了一条",)).digest() != base
    assert replace(fam, events=("some.event",)).digest() != base
    assert replace(fam, revision=fam.revision + 1).digest() != base
    assert replace(fam, case_sensitive=not fam.case_sensitive).digest() != base


def test_registry_digest_covers_every_family():
    digests = family_digests()
    assert set(digests) == {f.key for f in FAILURE_FAMILIES}
    assert registry_digest() == registry_digest()
    assert len(set(digests.values())) == len(digests)  # 不同家族不该撞指纹


def test_adding_a_family_is_safe_for_existing_series():
    # 「要能安全新增」：加一族只应改整表指纹，既有各族指纹一个都不许动。
    before = family_digests()
    extended = FAILURE_FAMILIES + (
        FailureFamily(key="brand_new", label="新族", patterns=(r"zzz",), since="2026-09-01"),
    )
    after = {f.key: f.digest() for f in extended}
    assert all(after[k] == v for k, v in before.items())
    assert "brand_new" in after


# ---------------------------------------------------------------------------
# 匹配：只看诊断字段，不看用户内容
# ---------------------------------------------------------------------------


def test_event_text_reads_diagnostics_and_nested_payload():
    text = event_text(
        {
            "event": "tool.execute_end",
            "error": "不是目录：foo",
            "payload": {"reason": "QueuePool limit reached"},
            "timestamp": "2026-08-12T00:00:00Z",
        }
    )
    assert "不是目录：foo" in text
    assert "QueuePool limit reached" in text
    assert "tool.execute_end" in text


def test_event_text_reads_codes_and_tools_arrays_not_content():
    # codes / tools 是结构化错误码与工具名，不是用户内容；args_preview / content 仍必须排除。
    text = event_text(
        {
            "event": "chat.local_turn_tool_failures",
            "codes": ["exec_env_probe_timeout", "schema"],
            "tools": ["code_execute", "file_read"],
            "content": "用户说：预算耗尽",
            "args_preview": '{"summary": "本轮预算耗尽"}',
        }
    )
    assert "exec_env_probe_timeout" in text
    assert "code_execute" in text
    assert "预算耗尽" not in text


def test_clip_diagnostic_value_keeps_header_and_last_line():
    last = "agentcore.workspace.protocol.NotADirectory: foo/bar"
    body = "Traceback (most recent call last):\n" + ("  File x\n" * 400) + last
    assert len(body) > 600
    clipped = clip_diagnostic_value(body)
    assert clipped.startswith("Traceback")
    assert last in clipped


def test_event_text_last_line_lets_path_not_dir_claim_long_traceback():
    last = "agentcore.workspace.protocol.NotADirectory: jinbooks-ui/src/views/x"
    exc = (
        "  + Exception Group Traceback (most recent call last):\n"
        + ("  |   File /app/.venv/lib/x.py\n" * 200)
        + last
    )
    assert len(exc) > 2000
    text = event_text(
        {"event": "http.unhandled_error", "level": "error", "exception": exc}
    )
    assert last in text
    reg = compile_registry()
    assert "path_not_dir" in reg.match(event="http.unhandled_error", text=text)


def test_local_turn_and_exec_env_families_claim_their_events():
    reg = compile_registry()
    assert "local_turn_tool_failures" in reg.match(
        event="chat.local_turn_tool_failures", text=""
    )
    assert "exec_env_probe_dead" in reg.match(
        event="sandbox.exec_env_probe_failed", text=""
    )
    assert "exec_env_probe_dead" in reg.match(
        event="coordination.exec_env_dead_user_notice", text=""
    )
    assert "exec_env_probe_dead" in reg.match(
        event="chat.local_turn_tool_failures", text="exec_env_probe_timeout"
    )


def test_registry_digest_moves_with_extract_policy(monkeypatch):
    from agentcore.observability.query import failure_families as ff

    before = ff.registry_digest()
    monkeypatch.setattr(ff, "TEXT_EXTRACT_REVISION", ff.TEXT_EXTRACT_REVISION + 1)
    assert ff.registry_digest() != before


def test_event_text_ignores_user_and_model_content():
    # 踩过的坑：把 args_preview / content 也扫进来后，队员交接摘要里一句「预算耗尽」
    # 就把该回合误记成外环验证预算耗尽，失败榜 counts 掺进了模型自述。
    obj = {
        "event": "tool.args_salvaged",
        "args_preview": '{"summary": "本轮预算耗尽，未把产物写入工作区"}',
        "content": "用户说：invalid temperature",
        "persona": "契约验收失败工程师",
    }
    text = event_text(obj)
    assert "预算耗尽" not in text
    assert "invalid temperature" not in text

    reg = compile_registry()
    assert reg.match(event="tool.args_salvaged", text=text) == []


def test_family_matching_covers_event_names_patterns_and_tool_detector():
    reg = compile_registry()
    assert "sse_backpressure_drop" in reg.match(
        event="event_sink.backpressure_drop", text="event_sink.backpressure_drop"
    )
    assert "event_loop_lag" in reg.match(event="event_loop.lag", text="event_loop.lag")
    assert "readyz_failed" in reg.match(event="http.readyz_failed", text="http.readyz_failed")
    assert "disk_high_watermark" in reg.match(
        event="disk.high_watermark", text="disk.high_watermark"
    )
    assert "disk_high_watermark" in reg.match(
        event="disk.probe_failed", text="disk.probe_failed"
    )
    assert "rate_limit_fail_open" in reg.match(
        event="rate_limit.redis_fail_open", text="rate_limit.redis_fail_open"
    )
    assert "contract_failed" in reg.match(event="contract.failed", text="contract.failed")
    assert "path_not_dir" in reg.match(event="tool.execute_end", text="不是目录：xhs")
    assert "tool_failed" in reg.match(event="tool.execute_end", text="", tool_failed=True)
    assert reg.match(event="chat.turn_complete", text="chat.turn_complete") == []


def test_families_may_overlap_on_one_event():
    # 一条事件同时属于多族是合法的（各族 count 是独立集合，勿相加当总数）。
    reg = compile_registry()
    hits = reg.match(
        event="contract.write_pass_exhausted",
        text="contract.write_pass_exhausted 未把产物写入工作区",
    )
    assert hits.count("write_pass_exhausted") == 1


def test_tightened_byok_family_no_longer_eats_web_403():
    # rev2 收紧前，网页工具的 HTTP 403 会被记成 BYOK 钥匙问题。
    reg = compile_registry()
    assert "byok_key_balance" not in reg.match(
        event="tool.execute_end", text="网页读取失败：HTTP 403。该站点反爬 / 拒绝访问"
    )
    assert "byok_key_balance" in reg.match(
        event="llm.call_failed", text="当前模型 API Key 无效或无权限，请更新后重试"
    )


def test_local_turn_id_invalid_claims_clipped_uuid_encode_frame():
    # rev2：13k traceback 的 ValueError 行被 clip 切掉，改认 uuid_encode 栈帧。
    frame = (
        '  File "asyncpg/pgproto/codecs/uuid.pyx", line 16, in '
        "asyncpg.pgproto.pgproto.uuid_encode\n"
    )
    dropped = (
        '  File "asyncpg/pgproto/uuid.pyx", line 88, in '
        "asyncpg.pgproto.pgproto.pg_uuid_bytes_from_str\n"
        "ValueError: invalid UUID 'resume-72b2662b-eec1-4954-af03-941d9d04352a': "
        "length must be between 32..36 characters, got 43\n"
    )
    last = "(Background on this error at: https://sqlalche.me/e/20/dbapi)"
    # uuid_encode 留在首 480；ValueError 推到 clip 窗外，复现生产切法。
    filler = "  File /app/x.py, line 1, in f\n" * 40
    exc = "Traceback (most recent call last):\n" + frame + filler + dropped + last
    clipped = clip_diagnostic_value(exc)
    assert "uuid_encode" in clipped
    assert "invalid UUID '" not in clipped
    assert "pg_uuid_bytes_from_str" not in clipped
    assert last in clipped
    text = event_text(
        {"event": "http.unhandled_error", "level": "error", "exception": exc}
    )
    reg = compile_registry()
    assert "local_turn_id_invalid" in reg.match(
        event="http.unhandled_error", text=text
    )
    assert FAMILIES_BY_KEY["local_turn_id_invalid"].revision == 2
    # 同栈帧但不是本机回合 HTTP 500：路径当 UUID 的工具失败不入本族。
    tool_text = event_text(
        {
            "event": "tool.execute_end",
            "level": "error",
            "exception": exc,
            "tool": "read_folder_file",
        }
    )
    assert "local_turn_id_invalid" not in reg.match(
        event="tool.execute_end", text=tool_text
    )


def test_local_turn_id_invalid_still_claims_short_uuid_valueerror():
    # 短 traceback 仍走 Python uuid.UUID 原文，避免只认 clip 栈帧漏掉别的发射点。
    reg = compile_registry()
    text = (
        "invalid UUID 'resume-72b2662b-eec1-4954-af03-941d9d04352a': "
        "length must be between 32..36 characters, got 43"
    )
    assert "local_turn_id_invalid" in reg.match(
        event="http.unhandled_error", text=text
    )


def test_local_turn_id_invalid_does_not_claim_generic_dbapierror():
    # 末行 sqlalche.me/e/20/dbapi 是所有 DBAPIError 的尾巴，单独命中会把普通 SQL 错扫进来。
    last = "(Background on this error at: https://sqlalche.me/e/20/dbapi)"
    exc = (
        "Traceback (most recent call last):\n"
        + ("  File /app/db.py, line 1, in execute\n" * 80)
        + "sqlalchemy.exc.DBAPIError: connection reset\n"
        + last
    )
    text = event_text(
        {"event": "http.unhandled_error", "level": "error", "exception": exc}
    )
    assert last in text
    assert "local_turn_id_invalid" not in compile_registry().match(
        event="http.unhandled_error", text=text
    )


def test_runner_mismatch_ignores_framework_detect_help_text():
    # rev2：`jest.{0,40}vitest` 扫中 test_run 自己的「无法检测测试框架」帮助文案。
    help_text = (
        "无法检测测试框架。请确认工作区包含 pyproject.toml（pytest）、"
        "package.json scripts.test（vitest/jest）、vitest.config.* 或 "
        "jest.config.*，或在 framework 参数中显式指定；"
        "或改用 check=command 并提供 verify 命令。"
    )
    reg = compile_registry()
    assert "runner_mismatch" not in reg.match(
        event="tool.execute_end", text=f"test_run\nerror\n{help_text}"
    )
    assert "runner_mismatch" not in reg.match(
        event="tool.execute_end", text="npx jest --coverage"
    )
    assert FAMILIES_BY_KEY["runner_mismatch"].revision == 2


def test_runner_mismatch_still_claims_vitest_purpose_with_jest_command():
    reg = compile_registry()
    assert "runner_mismatch" in reg.match(
        event="tool.execute_end",
        text="purpose=vitest command=npx jest",
    )
    assert "runner_mismatch" in reg.match(
        event="tool.execute_end",
        text="vitest\nnpx jest",
    )
    assert "runner_mismatch" in reg.match(
        event="tool.execute_end",
        text="framework=vitest\nnpx jest",
    )


@pytest.mark.parametrize(
    ("obj", "expected"),
    [
        ({"event": "tool.execute_end", "ok": False}, True),
        ({"event": "tool.execute_end", "status": "allowlist_deny"}, True),
        ({"event": "tool.execute_end", "status": "brand_new_failure"}, True),
        ({"event": "tool.execute_end", "status": "ok"}, False),
        ({"event": "tool.execute_end"}, False),
        ({"event": "llm.call", "status": "error"}, False),
    ],
)
def test_is_tool_failure(obj, expected):
    # 未知 status 也算失败：新失败态正是 README 说的「新文案必审」，宁可露头别被吞。
    assert is_tool_failure(obj) is expected


# ---------------------------------------------------------------------------
# 扫描：清单 + 失败榜 + 反查
# ---------------------------------------------------------------------------


def _write_jsonl(path: Path, rows: list[dict]) -> Path:
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8"
    )
    return path


CID_A = "aaaaaaaa-1111-2222-3333-444444444444"
CID_B = "bbbbbbbb-1111-2222-3333-444444444444"


def _events() -> list[dict]:
    return [
        {
            "event": "contract.failed",
            "timestamp": "2026-08-12T01:00:00Z",
            "conversation_id": CID_A,
            "trace_id": "trace-a1",
            "level": "error",
            "reason": "acceptance not met",
        },
        {
            "event": "contract.failed",
            "timestamp": "2026-08-12T01:05:00Z",
            "conversation_id": CID_A,
            "trace_id": "trace-a2",
            "level": "error",
            "reason": "acceptance not met",
        },
        {
            "event": "tool.execute_end",
            "timestamp": "2026-08-12T02:00:00Z",
            "conversation_id": CID_B,
            "trace_id": "trace-b1",
            "status": "error",
            "error": "不是目录：xhs_fry_charts",
        },
        {
            "event": "chat.turn_complete",
            "timestamp": "2026-08-12T02:10:00Z",
            "conversation_id": CID_B,
            "trace_id": "trace-b1",
            "finish_reason": "end_turn",
        },
        {
            "event": "http.unhandled_error",
            "timestamp": "2026-08-12T03:00:00Z",
            "conversation_id": CID_B,
            "trace_id": "trace-b2",
            "level": "error",
            "error": "something the table has never seen",
        },
        {
            "event": "contract.failed",
            "timestamp": "2026-08-10T01:00:00Z",  # 窗外
            "conversation_id": CID_A,
            "trace_id": "trace-old",
            "level": "error",
        },
    ]


def _scan(tmp_path: Path, **kwargs):
    log_file = _write_jsonl(tmp_path / "events.jsonl", _events())
    return scan_patrol(
        log_file,
        since=datetime(2026, 8, 12, tzinfo=UTC),
        window_label="test-window",
        **kwargs,
    )


def test_leaderboard_rows_carry_trace_and_conversation_ids(tmp_path):
    # 这是本次沉淀要补的实际缺口：log_stats 的 error_clusters 只有 {签名,次数,样本}，
    # 从「这类错 N 次」到「给我这 N 条 trace」每次都得另写 join。
    snapshot = _scan(tmp_path)
    contract = snapshot.families["contract_failed"].to_json()
    assert contract["events"] == 2
    assert contract["conversations"]["ids"] == [CID_A]
    assert sorted(contract["traces"]["ids"]) == ["trace-a1", "trace-a2"]
    assert contract["traces"]["total"] == 2
    assert contract["traces"]["truncated"] is False

    assert all(
        "conversations" in c.to_json() and "traces" in c.to_json() for c in snapshot.clusters
    )
    hot = next(c for c in snapshot.clusters if c.family == "contract_failed")
    assert sorted(hot.to_json()["traces"]["ids"]) == ["trace-a1", "trace-a2"]


def test_scan_honours_window_and_skips_clean_events(tmp_path):
    snapshot = _scan(tmp_path)
    assert "trace-old" not in snapshot.families["contract_failed"].traces.to_json()["ids"]
    assert snapshot.first_event_at == "2026-08-12T01:00:00Z"
    # chat.turn_complete 既不属任何家族也不是 error → 不进失败榜
    assert snapshot.failure_events == 4


def test_scan_until_clips_the_upper_edge(tmp_path):
    log_file = _write_jsonl(tmp_path / "events.jsonl", _events())
    snapshot = scan_patrol(
        log_file,
        since=datetime(2026, 8, 12, tzinfo=UTC),
        until=datetime(2026, 8, 12, 1, 30, tzinfo=UTC),
        window_label="clipped",
    )
    assert snapshot.families["contract_failed"].events == 2
    assert "path_not_dir" not in snapshot.families


def test_uncovered_error_text_lands_in_the_residual_bucket(tmp_path):
    # README「新文案 → 必审」：表没覆盖到的 error 必须露头，而不是被静默丢掉。
    snapshot = _scan(tmp_path)
    residual = snapshot.families[UNKNOWN_FAMILY]
    assert residual.events == 1
    assert residual.traces.to_json()["ids"] == ["trace-b2"]


def test_must_review_flag_follows_the_readme_signal_list(tmp_path):
    snapshot = _scan(tmp_path)
    rows = {c.conversation_id: c for c in snapshot.conversations}
    assert rows[CID_A].must_review is True  # contract_failed 是必审家族
    assert rows[CID_B].must_review is False  # 工具失败 / 残差不自动升必审
    assert snapshot.conversations[0].conversation_id == CID_A  # 必审排在前面
    assert "rate_limit_fail_open" in MUST_REVIEW_FAMILIES


def test_fail_open_cluster_is_first_hit_then_ten_second_pulse(tmp_path):
    """A Redis outage must be visible without one family hit per fail-opened request."""
    rows = [
        {
            "event": "rate_limit.redis_fail_open",
            "level": "warning",
            "timestamp": f"2026-08-17T10:00:0{i}Z",
            "prefix": "rl:auth",
            "error": "Timeout",
            "count": i + 1,
        }
        for i in range(8)
    ]
    rows.append(
        {
            "event": "rate_limit.redis_fail_open",
            "level": "warning",
            "timestamp": "2026-08-17T10:00:15Z",
            "prefix": "rl:auth",
            "error": "Timeout",
            "count": 9,
        }
    )
    log_file = _write_jsonl(tmp_path / "fail_open.jsonl", rows)
    snapshot = scan_patrol(log_file, include_synthetic=True, window_label="fo")
    fam = snapshot.families["rate_limit_fail_open"]
    assert fam.events == 2  # t=0 first pulse + t=15 second pulse
    assert fam.by_event["rate_limit.redis_fail_open"] == 2


def test_time_pulse_gate_keeps_onset():
    gate = TimePulseGate(interval_s=10.0)
    t0 = datetime(2026, 8, 17, 10, 0, 0, tzinfo=UTC)
    assert gate.accept(t0) is True
    assert gate.accept(datetime(2026, 8, 17, 10, 0, 3, tzinfo=UTC)) is False
    assert gate.accept(datetime(2026, 8, 17, 10, 0, 10, tzinfo=UTC)) is True


def test_conversation_inventory_joins_titles_and_previews(tmp_path):
    export = tmp_path / "export"
    export.mkdir()
    _write_jsonl(export / "events.jsonl", _events())
    _write_jsonl(
        export / "conversations.jsonl",
        [{"id": CID_A, "title": "钱塘江 UI 对齐"}, {"id": CID_B, "title": "独立站"}],
    )
    _write_jsonl(
        export / "messages.jsonl",
        [
            {
                "conversation_id": CID_A,
                "role": "user",
                "content": "帮我对齐 UI 风格",
                "created_at": "2026-08-12T00:59:00Z",
            },
            {
                "conversation_id": CID_A,
                "role": "assistant",
                "content": "好的",
                "created_at": "2026-08-12T01:01:00Z",
            },
            {
                "conversation_id": CID_B,
                "role": "user",
                "content": "窗外的老消息",
                "created_at": "2026-08-01T00:00:00Z",
            },
        ],
    )

    snapshot = scan_patrol(
        export / "events.jsonl",
        since=datetime(2026, 8, 12, tzinfo=UTC),
        window_label="inv",
        export_dir=export,
    )
    rows = {c.conversation_id: c for c in snapshot.conversations}
    assert snapshot.messages_available is True
    assert rows[CID_A].title == "钱塘江 UI 对齐"
    assert rows[CID_A].user_messages == 1 and rows[CID_A].assistant_messages == 1
    assert rows[CID_A].first_user_preview == "帮我对齐 UI 风格"
    assert rows[CID_A].nonempty is True
    # B 窗内只有日志事件、没有消息 —— 仍要在清单里，但不算非空会话
    assert rows[CID_B].nonempty is False
    totals = snapshot.to_json_dict()["totals"]
    assert totals["nonempty_conversations"] == 1
    assert totals["log_only_conversations"] == 1


def test_info_local_turn_tool_failures_reach_the_leaderboard(tmp_path):
    # info 级且未入家族会被 patrol 直接 continue；本族靠事件名认领，必须露头。
    rows = [
        {
            "event": "chat.local_turn_tool_failures",
            "timestamp": "2026-08-12T04:00:00Z",
            "conversation_id": CID_A,
            "trace_id": "trace-lt",
            "user_id": "user-su",
            "level": "info",
            "codes": ["exec_env_probe_timeout"],
            "tools": ["code_execute"],
        }
    ]
    log_file = _write_jsonl(tmp_path / "events.jsonl", rows)
    snapshot = scan_patrol(
        log_file, since=datetime(2026, 8, 12, tzinfo=UTC), window_label="lt"
    )
    assert snapshot.families["local_turn_tool_failures"].events == 1
    assert snapshot.families["exec_env_probe_dead"].events == 1
    assert UNKNOWN_FAMILY not in snapshot.families


def test_repeat_user_view_surfaces_concentrated_hits(tmp_path):
    rows = [
        {
            "event": "workspace.channel_dead",
            "timestamp": "2026-08-12T04:00:00Z",
            "conversation_id": CID_A,
            "trace_id": f"t-{i}",
            "user_id": "user-hot",
            "level": "error",
        }
        for i in range(3)
    ] + [
        {
            "event": "workspace.channel_dead",
            "timestamp": "2026-08-12T04:10:00Z",
            "conversation_id": CID_B,
            "trace_id": "t-once",
            "user_id": "user-once",
            "level": "error",
        }
    ]
    log_file = _write_jsonl(tmp_path / "events.jsonl", rows)
    snapshot = scan_patrol(
        log_file, since=datetime(2026, 8, 12, tzinfo=UTC), window_label="rpt"
    )
    keys = {(r.user_id, r.family) for r in snapshot.repeat_users}
    assert ("user-hot", "channel_dead") in keys
    assert ("user-once", "channel_dead") not in keys
    hot = next(r for r in snapshot.repeat_users if r.user_id == "user-hot")
    assert hot.events == 3


def test_max_ids_truncates_but_keeps_the_true_total(tmp_path):
    rows = [
        {
            "event": "contract.failed",
            "timestamp": "2026-08-12T01:00:00Z",
            "conversation_id": f"cid-{i}",
            "trace_id": f"trace-{i}",
            "level": "error",
        }
        for i in range(10)
    ]
    log_file = _write_jsonl(tmp_path / "events.jsonl", rows)
    snapshot = scan_patrol(
        log_file, since=datetime(2026, 8, 12, tzinfo=UTC), window_label="cap", max_ids=3
    )
    traces = snapshot.families["contract_failed"].traces.to_json()
    assert len(traces["ids"]) == 3
    assert traces["total"] == 10
    assert traces["truncated"] is True


# ---------------------------------------------------------------------------
# 快照与跨窗 diff
# ---------------------------------------------------------------------------


def test_snapshot_round_trips_through_disk(tmp_path):
    snapshot = _scan(tmp_path)
    path = write_snapshot(snapshot, tmp_path / "snapshots" / "w.json")
    loaded = load_snapshot(path)
    assert loaded["schema_version"] == SNAPSHOT_SCHEMA_VERSION
    assert loaded["families"]["contract_failed"]["events"] == 2
    assert loaded["registry"]["digest"] == registry_digest()


def test_load_snapshot_rejects_a_foreign_schema(tmp_path):
    path = tmp_path / "old.json"
    path.write_text(json.dumps({"schema_version": 999}), encoding="utf-8")
    with pytest.raises(ValueError, match="schema_version"):
        load_snapshot(path)


def _baseline(
    families: dict[str, int],
    registry: dict[str, str] | None = None,
    cids: list[str] | None = None,
) -> dict:
    """手搓一份上窗快照——真实场景里它就是一份更早、可能来自更旧家族表的 JSON。"""
    digests = family_digests()
    reg = registry if registry is not None else {k: digests.get(k, "old") for k in families}
    return {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "generated_at": "2026-08-11T23:59:00Z",
        "window": {"label": "上窗", "since": "2026-08-11T00:00:00Z"},
        "registry": {
            "digest": "baselinedig",
            "families": {k: {"digest": v} for k, v in reg.items()},
        },
        "families": {k: {"events": v} for k, v in families.items()},
        "conversations": [{"conversation_id": c} for c in (cids or [])],
    }


def _row(diff: dict, key: str) -> dict:
    return next(r for r in diff["families"] if r["key"] == key)


def test_diff_reports_a_comparable_delta_when_the_definition_held(tmp_path):
    # 纪要里「『不是目录』上窗 108 → 本窗 N」这类核验判定，从此可复算。
    snapshot = _scan(tmp_path)
    diff = diff_snapshots(_baseline({"path_not_dir": 108}), snapshot.to_json_dict())
    row = _row(diff, "path_not_dir")
    assert (row["prev"], row["curr"], row["delta"]) == (108, 1, -107)
    assert row["status"] == "stable" and row["comparable"] is True


def test_diff_tells_a_real_zero_apart_from_a_family_that_did_not_exist(tmp_path):
    # 「上窗是 0」和「上窗还没这个家族」必须分得开，否则新增家族会冒充「暴涨」。
    snapshot = _scan(tmp_path)
    baseline = _baseline({"path_not_dir": 108, "channel_dead": 0})
    diff = diff_snapshots(baseline, snapshot.to_json_dict())

    real_zero = _row(diff, "channel_dead")
    assert real_zero["prev"] == 0 and real_zero["curr"] == 0
    assert real_zero["status"] == "stable" and real_zero["comparable"] is True

    absent_before = _row(diff, "memory_consolidation")
    assert absent_before["prev"] is None
    assert absent_before["status"] == "new" and absent_before["comparable"] is False


def test_diff_follows_a_rename_through_the_alias(tmp_path):
    # 上窗快照用的是退役旧名；序列必须接回来，而不是「一个消失 + 一个新增」。
    snapshot = _scan(tmp_path)
    digests = family_digests()
    baseline = _baseline(
        {"temperature_deprecated": 36},
        registry={"temperature_deprecated": digests["temperature_invalid"]},
    )
    diff = diff_snapshots(baseline, snapshot.to_json_dict())
    row = _row(diff, "temperature_invalid")
    assert row["matched_via"] == "temperature_deprecated"
    assert row["prev"] == 36 and row["status"] == "stable" and row["comparable"] is True
    assert not any(r["key"] == "temperature_deprecated" for r in diff["families"])


def test_diff_refuses_to_compare_counts_across_a_definition_change(tmp_path):
    # 口径变了却照样报「跌了 301」才是真正危险的——必须标 redefined 且判不可比。
    snapshot = _scan(tmp_path)
    baseline = _baseline({"byok_key_balance": 340}, registry={"byok_key_balance": "0ldd1gest"})
    row = _row(diff_snapshots(baseline, snapshot.to_json_dict()), "byok_key_balance")
    assert row["status"] == "redefined"
    assert row["comparable"] is False and row["delta"] is None
    assert row["prev_digest"] == "0ldd1gest"
    assert row["curr_digest"] == family_digests()["byok_key_balance"]


def test_diff_flags_keys_the_table_no_longer_knows(tmp_path):
    snapshot = _scan(tmp_path)
    baseline = _baseline({"some_retired_family": 5}, registry={"some_retired_family": "x"})
    row = _row(diff_snapshots(baseline, snapshot.to_json_dict()), "some_retired_family")
    assert row["status"] == "unknown_key"
    assert row["prev"] == 5 and row["comparable"] is False


def test_diff_never_treats_the_residual_bucket_as_a_series(tmp_path):
    snapshot = _scan(tmp_path)
    baseline = _baseline({UNKNOWN_FAMILY: 550}, registry={})
    rows = [r for r in diff_snapshots(baseline, snapshot.to_json_dict())["families"] if r["key"] == UNKNOWN_FAMILY]
    assert len(rows) == 1  # 不许既算 unknown_key 又算 residual
    assert rows[0]["status"] == "residual"
    assert rows[0]["comparable"] is False and rows[0]["delta"] is None


def test_diff_splits_conversations_into_carried_over_new_and_dropped(tmp_path):
    # 巡检清单要求标出本 sync「续活跃」cid；以前靠临时 _tmp_prior_*_cids.json 手工传递。
    snapshot = _scan(tmp_path)
    baseline = _baseline({}, registry={}, cids=[CID_A, "cccccccc-0000-0000-0000-000000000000"])
    conv = diff_snapshots(baseline, snapshot.to_json_dict())["conversations"]
    assert conv["carried_over"] == [CID_A]
    assert conv["new"] == [CID_B]
    assert conv["dropped"] == ["cccccccc-0000-0000-0000-000000000000"]
    assert (conv["carried_over_n"], conv["new_n"], conv["dropped_n"]) == (1, 1, 1)


def test_diff_rings_when_the_whole_table_moved(tmp_path):
    snapshot = _scan(tmp_path)
    assert diff_snapshots(_baseline({}), snapshot.to_json_dict())["registry_changed"] is True
    same = snapshot.to_json_dict()
    assert diff_snapshots(same, same)["registry_changed"] is False
