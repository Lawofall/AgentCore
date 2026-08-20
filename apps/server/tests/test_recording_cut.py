"""录制 → conformance 巡检向量裁切管道（conformance/recording_cut.py）单测。

覆盖步②第二波验收点：durable-face 过滤（EVENT_DISPOSITION 主轴 + EPHEMERAL 白名单）、
时间戳稳定化（同输入两次裁切字节相同）、投影生成 projected、端到端「合成录制 → 裁切 →
产物向量过回合巡检判定（fold ≡ projected 后端 parity）」、两套 fixtures 来源互不误删。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentcore.conformance.projection import project_turn
from agentcore.conformance.recording_cut import (
    CUT_KEEP_EPHEMERAL,
    RECORDED_FIXTURE_PREFIX,
    cut_recording_to_fixture,
    durable_face,
    serialize_fixture,
    stitch_recording_events,
    write_fixture,
)
from agentcore.conformance.timestamps import (
    format_stable_timestamp,
    wall_clock_ms_sequence,
)
from agentcore.runtime.events.disposition import EVENT_DISPOSITION, Disposition

# ---------------------------------------------------------------------------
# 合成录制素材（开发期无真实录制入库；形状对齐 recorder.py 的 v2 文档）
# ---------------------------------------------------------------------------

_USAGE = {"input_tokens": 1200, "output_tokens": 300}
_COST = {"input": 240_000, "output": 120_000, "total": 360_000, "currency": "USD"}


def _e(etype: str, payload: dict | None = None, t_ms: int = 0) -> dict:
    return {"type": etype, "payload": payload or {}, "timestamp": None, "t_ms": t_ms}


def _team_preview_payload() -> dict:
    return {
        "checkpoint_id": "cp-1",
        "form": "delegate",
        "sides": [],
        "workers": [{"id": "w1", "role": "研究员"}],
        "tools": [],
        "primitive": "delegate",
        "motion": "",
        "max_rounds": 1,
        "thorough": True,
    }


def _run_plan_payload() -> dict:
    return {
        "execution_id": "exec1",
        "plan_type": "delegate",
        "runs": [{"id": "r1", "agent_id": "w1", "task": "调研", "depends_on": []}],
        "agents": [
            {
                "id": "w1",
                "role": "研究员",
                "thinking": True,
            }
        ],
    }


def _synthetic_recording() -> dict:
    """两段（send 挂起 → resume 收口）的完整回合录制，混入 EPHEMERAL 直播噪音。"""
    send_leg = [
        _e("message_start", {"message_id": "m1", "conversation_id": "conv1"}, 0),
        _e("reasoning_delta", {"delta": "先想。"}, 10),
        # 直播噪音：应被裁掉（oracle no-op）。
        _e("tool_progress", {"tool_name": "web_search", "chars": 12}, 15),
        _e("tool_use_start", {"tool_call_id": "t1", "tool_name": "web_search",
                              "arguments": {"query": "q"}}, 20),
        _e("tool_use_progress", {"tool_call_id": "t1", "phase": "running"}, 25),
        _e("tool_use_end", {"tool_call_id": "t1", "tool_name": "web_search",
                            "status": "success", "result": "ok"}, 30),
        # 流内纠正：白名单 EPHEMERAL——丢弃会把违规版+修正版拼在一起。
        _e("content_delta", {"delta": "第一版草稿。"}, 40),
        _e("content_reset", {"reason": "retry"}, 45),
        _e("content_delta", {"delta": "这是"}, 50),
        _e("content_delta", {"delta": "定稿正文。"}, 55),
        _e("workspace_op_required", {"op_id": "op1", "op": "read"}, 60),
        _e("team_preview_required", _team_preview_payload(), 70),
        _e("message_end", {"finish_reason": "paused", "usage": _USAGE, "cost": _COST}, 80),
        _e("turn_saved", {}, 85),
    ]
    resume_leg = [
        _e("message_start", {"message_id": "m1", "conversation_id": "conv1"}, 0),
        _e("team_preview_resolved", {"checkpoint_id": "cp-1", "decision": "continue"}, 5),
        _e("run_plan", _run_plan_payload(), 10),
        _e("run_started", {"run_id": "r1", "agent_id": "w1", "kind": "agent"}, 20),
        _e("run_reasoning_delta", {"run_id": "r1", "agent_id": "w1", "delta": "查。"}, 25),
        # 白名单 EPHEMERAL：oracle 写 agent.toolProgress。
        _e("run_tool_progress", {"run_id": "r1", "agent_id": "w1",
                                 "tool_name": "code_execute", "chars": 64}, 30),
        _e("run_output_delta", {"run_id": "r1", "agent_id": "w1", "delta": "worker 草稿"}, 35),
        _e("run_output_reset", {"run_id": "r1", "agent_id": "w1", "reason": "retry"}, 40),
        _e("run_output_delta", {"run_id": "r1", "agent_id": "w1", "delta": "worker 结论。"}, 45),
        _e("run_completed", {"run_id": "r1", "agent_id": "w1",
                             "output_summary": "结论", "duration_ms": 900}, 50),
        _e("content_delta", {"delta": "汇总完毕。"}, 60),
        _e("message_end", {"finish_reason": "end_turn", "usage": _USAGE, "cost": _COST}, 70),
        _e("turn_saved", {}, 75),
    ]
    return {
        "version": 2,
        "kind": "demo_tape_recording",
        "meta": {"conversation_id": "conv1", "message_id": "m1", "recorded_at": "x"},
        "segments": [
            {"wall_t0_ms": 1000, "events": send_leg},
            {"wall_t0_ms": 60_000, "events": resume_leg},
        ],
    }


# ---------------------------------------------------------------------------
# 白名单自洽 + durable-face 过滤
# ---------------------------------------------------------------------------


def test_whitelist_entries_are_ephemeral():
    """入表原则自洁：白名单只允许收 EPHEMERAL（DURABLE/DERIVED 本来就保留，进表=冗余）。"""
    for event in CUT_KEEP_EPHEMERAL:
        assert EVENT_DISPOSITION[event][0] is Disposition.EPHEMERAL, (
            f"{event.value} 已不是 EPHEMERAL，应从 CUT_KEEP_EPHEMERAL 移除"
        )


def test_durable_face_filters_by_disposition():
    events = stitch_recording_events(_synthetic_recording())
    face_types = [ev["type"] for ev in durable_face(events)]
    # DURABLE / DERIVED 保留。
    for kept in ("tool_use_start", "tool_use_end", "content_delta", "reasoning_delta",
                 "run_plan", "run_completed", "message_end", "team_preview_required"):
        assert kept in face_types
    # 白名单 EPHEMERAL 保留。
    for kept in ("message_start", "content_reset", "run_output_reset", "run_tool_progress"):
        assert kept in face_types
    # 其余 EPHEMERAL（直播噪音 / 控制帧 / 客户端工具请求）裁掉。
    for cut in ("tool_progress", "tool_use_progress", "turn_saved", "workspace_op_required"):
        assert cut not in face_types


def test_unknown_event_type_raises():
    with pytest.raises(ValueError, match="not a known EventType"):
        durable_face([{"type": "made_up_event", "payload": {}}])


def test_retired_sim_show_event_types_are_dropped():
    face = durable_face(
        [
            {"type": "sim.show.heart_pick", "payload": {"run_id": "r", "tick": 0}},
            {"type": "message_start", "payload": {"message_id": "m"}},
        ]
    )
    assert [ev["type"] for ev in face] == ["message_start"]


def test_retired_question_posted_event_types_are_dropped():
    from agentcore.runtime.events.types import RETIRED_EVENT_TYPE_VALUES

    face = durable_face(
        [
            {"type": "question_posted", "payload": {"ask_id": "a"}},
            {"type": "question_resolved", "payload": {"ask_id": "a", "status": "answered"}},
            {
                "type": "delegation_authorization_required",
                "payload": {"authorization_id": "d"},
            },
            {
                "type": "delegation_authorization_resolved",
                "payload": {"authorization_id": "d", "status": "granted"},
            },
            {"type": "message_start", "payload": {"message_id": "m"}},
        ]
    )
    assert [ev["type"] for ev in face] == ["message_start"]
    assert frozenset(
        {
            "question_posted",
            "question_resolved",
            "delegation_authorization_required",
            "delegation_authorization_resolved",
        }
    ) == RETIRED_EVENT_TYPE_VALUES


# ---------------------------------------------------------------------------
# 时间戳稳定化 + 字节幂等
# ---------------------------------------------------------------------------


def test_cut_is_byte_stable_across_reruns():
    fx1 = cut_recording_to_fixture(_synthetic_recording(), name="case", description="d")
    fx2 = cut_recording_to_fixture(_synthetic_recording(), name="case", description="d")
    assert serialize_fixture(fx1) == serialize_fixture(fx2)
    # 时间戳与录制墙钟无关：按 duration_ms 墙钟方案确定性铸造（对齐 conformance.export）。
    pairs = [(ev["type"], ev["payload"]) for ev in fx1["events"]]
    expected = [
        format_stable_timestamp(ms) for ms in wall_clock_ms_sequence(pairs)
    ]
    stamps = [ev["timestamp"] for ev in fx1["events"]]
    assert stamps == expected
    # run_completed.duration_ms=900 → 末帧相对首帧跨度 ≥ 900ms（卡片「用时」可读）。
    first_ms = wall_clock_ms_sequence(pairs)[0]
    last_ms = wall_clock_ms_sequence(pairs)[-1]
    assert last_ms - first_ms >= 900


def test_cut_output_shape_matches_fixture_contract():
    fx = cut_recording_to_fixture(_synthetic_recording(), name="case", description="说明")
    assert set(fx.keys()) == {"name", "description", "events", "projected"}
    assert fx["name"] == f"{RECORDED_FIXTURE_PREFIX}case"
    assert fx["description"] == "说明"
    for ev in fx["events"]:
        # 与既有回合向量元素形状完全一致：pacing 超集字段（t_ms）不进巡检向量。
        assert set(ev.keys()) == {"type", "payload", "timestamp"}
    # projected 即后端 oracle 对产物事件的投影（录制永不带 projected，裁切时生成）。
    assert fx["projected"] == project_turn(fx["events"])


# ---------------------------------------------------------------------------
# 裁判态不变量 + 端到端
# ---------------------------------------------------------------------------


def test_durable_face_preserves_judge_state():
    """核心不变量：durable face 的投影 == 原片全量流的投影。

    这正是白名单入表原则的判定面——被裁事件必须全是 oracle no-op；有语义的
    EPHEMERAL（content_reset / run_output_reset / run_tool_progress …）必须保留。
    """
    raw = stitch_recording_events(_synthetic_recording())
    raw_wire = [{"type": ev["type"], "payload": ev.get("payload") or {}} for ev in raw]
    face = durable_face(raw)
    face_wire = [{"type": ev["type"], "payload": ev.get("payload") or {}} for ev in face]
    assert project_turn(face_wire) == project_turn(raw_wire)


def test_run_tool_progress_whitelisted_for_mid_tool_pause():
    """白名单补充项实证（拍板预留情形）：录制停在 worker 工具中途时，丢 run_tool_progress
    会让 projected 丢 agent.toolProgress——按「oracle 有语义处理」原则必须保留。"""
    recording = {
        "version": 2,
        "kind": "demo_tape_recording",
        "meta": {"conversation_id": "c", "message_id": "m"},
        "segments": [
            {
                "wall_t0_ms": 0,
                "events": [
                    _e("message_start", {"message_id": "m"}, 0),
                    _e("run_plan", _run_plan_payload(), 5),
                    _e("run_started", {"run_id": "r1", "agent_id": "w1", "kind": "agent"}, 10),
                    _e("run_tool_progress", {"run_id": "r1", "agent_id": "w1",
                                             "tool_name": "code_execute", "chars": 32}, 15),
                    _e("checkpoint_required", {"checkpoint_id": "cp-mid", "title": "确认",
                                               "description": "", "risk": "medium"}, 20),
                    _e("message_end", {"finish_reason": "paused", "usage": _USAGE,
                                       "cost": _COST}, 25),
                ],
            }
        ],
    }
    fx = cut_recording_to_fixture(recording, name="mid_tool_pause")
    assert fx["projected"]["status"] == "paused"
    agent = next(a for a in fx["projected"]["agents"] if a["id"] == "w1")
    assert agent["toolProgress"] == {"toolName": "code_execute", "chars": 32}
    # 反证：若把它从 durable face 里去掉，裁判态即漂移（证明其入表必要性）。
    without = [ev for ev in fx["events"] if ev["type"] != "run_tool_progress"]
    drifted = next(a for a in project_turn(without)["agents"] if a["id"] == "w1")
    assert drifted["toolProgress"] is None


def test_e2e_synthetic_recording_cut_passes_turn_conformance(tmp_path: Path):
    """端到端：合成录制 → 裁切 → 落盘产物过回合巡检判定（isTurnFixture 等价条件 +
    fold ≡ projected 后端投影 parity），且重跑裁切落盘字节一致。"""
    fx = cut_recording_to_fixture(
        _synthetic_recording(), name="delegate_pause_resume", description="e2e"
    )
    path = write_fixture(fx, out_dir=tmp_path)
    assert path.name == "recorded_delegate_pause_resume.json"

    on_disk = json.loads(path.read_text(encoding="utf-8"))
    # isTurnFixture 等价判定：name + events[] + projected.status。
    assert isinstance(on_disk["name"], str)
    assert isinstance(on_disk["events"], list) and on_disk["events"]
    assert "status" in on_disk["projected"]
    assert on_disk["projected"]["status"] == "completed"
    # 回合巡检判定核心：对产物事件跑 fold（后端 oracle 即 parity 裁判）== 内嵌 golden。
    assert project_turn(on_disk["events"]) == on_disk["projected"]
    # 挂起→恢复的交互全生命周期折出 resolved（不是悬空 pending）。
    tp = next(i for i in on_disk["projected"]["interactions"] if i["kind"] == "team_preview")
    assert tp["status"] == "resolved"
    # 幂等：重裁重写字节一致（golden 可复现）。
    again = cut_recording_to_fixture(
        _synthetic_recording(), name="delegate_pause_resume", description="e2e"
    )
    assert serialize_fixture(again) == path.read_text(encoding="utf-8")


def test_legacy_v1_recording_cuts_via_read_alias(tmp_path: Path):
    """旧 v1 录制（kind/ts 方言）经 load_recording 读时别名 → 裁切产物为契约字段。"""
    from agentcore.demo_tape.recorder import load_recording

    v1 = {
        "version": 1,
        "kind": "demo_tape_recording",
        "meta": {"conversation_id": "c", "message_id": "m"},
        "segments": [
            {
                "wall_t0_ms": 0,
                "events": [
                    {"kind": "message_start", "payload": {"message_id": "m"}, "ts": "t0"},
                    {"kind": "content_delta", "payload": {"delta": "老格式正文"}, "ts": "t1"},
                    {"kind": "message_end",
                     "payload": {"finish_reason": "end_turn", "usage": _USAGE, "cost": _COST},
                     "ts": "t2"},
                ],
            }
        ],
    }
    p = tmp_path / "legacy.json"
    p.write_text(json.dumps(v1, ensure_ascii=False), encoding="utf-8")
    fx = cut_recording_to_fixture(load_recording(p), name="legacy")
    assert [ev["type"] for ev in fx["events"]] == ["message_start", "content_delta", "message_end"]
    assert fx["projected"]["status"] == "completed"
    assert fx["projected"]["content"] == "老格式正文"


# ---------------------------------------------------------------------------
# 两套来源并存：export 清盘不误删裁切产物；裁切不占用手写向量命名面
# ---------------------------------------------------------------------------


def test_export_sweep_preserves_recorded_fixtures(tmp_path: Path, monkeypatch):
    from agentcore.conformance import export as export_module

    monkeypatch.setattr(export_module, "_FIXTURES_DIR", tmp_path)
    recorded = write_fixture(
        cut_recording_to_fixture(_synthetic_recording(), name="keep_me"), out_dir=tmp_path
    )
    stale = tmp_path / "stale_hand_vector.json"
    stale.write_text("{}", encoding="utf-8")

    export_module.main()

    assert recorded.exists(), "export 清盘误删了录制裁切向量"
    assert not stale.exists(), "export 清盘应删掉它拥有的过期手写向量"
    assert (tmp_path / "single_agent_text.json").exists()
    assert (tmp_path / "simulation-region-positions.json").exists()


def test_hand_vectors_never_use_recorded_prefix():
    """命名面所有权：recorded_ 前缀专属裁切管道，手写 VECTORS 不得占用（防清盘边界失效）。"""
    from agentcore.conformance.vectors import VECTORS

    offenders = [n for n in VECTORS if n.startswith(RECORDED_FIXTURE_PREFIX)]
    assert offenders == []


# ---------------------------------------------------------------------------
# 入库脱敏（与 tape export 共用 sanitize.py）
# ---------------------------------------------------------------------------


def test_cut_sanitizes_run_context_memory_and_passes_scan():
    from agentcore.demo_tape.sanitize import (
        DEMO_MEMORY_PLACEHOLDER,
        assert_ingest_clean,
    )

    memory = (
        "<rules>\n"
        "以下条目请一并遵循；与本回合用户直接指令冲突时，以本回合指令为准。\n"
        "硬约束：题材/领域偏好与历史任务不得改变本回合路由"
        "（直答/委派/调研/辩论以用户当前话为准）。\n\n"
        "## 沟通偏好\n"
        "- 真偏好 <!-- ts:2026-07-13 -->\n"
        "</rules>"
    )
    recording = {
        "version": 2,
        "meta": {"conversation_id": "c", "message_id": "m"},
        "segments": [
            {
                "events": [
                    _e("message_start", {"message_id": "m", "conversation_id": "c"}, 0),
                    _e(
                        "run_context",
                        {
                            "run_id": "r1",
                            "blocks": [{"channel": "system", "body": memory}],
                        },
                        5,
                    ),
                    _e("content_delta", {"delta": "正文"}, 10),
                    _e(
                        "message_end",
                        {"finish_reason": "end_turn", "usage": _USAGE, "cost": _COST},
                        20,
                    ),
                ]
            }
        ],
    }
    fx = cut_recording_to_fixture(recording, name="sanitized_ctx")
    body = fx["events"][1]["payload"]["blocks"][0]["body"]
    assert DEMO_MEMORY_PLACEHOLDER in body
    assert "真偏好" not in body
    assert_ingest_clean(fx["events"])
