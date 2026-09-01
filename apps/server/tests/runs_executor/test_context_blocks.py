from dataclasses import replace

from agentcore.runtime.runs.builder import build_run_plan
from agentcore.runtime.runs.executor.context import (
    _CONTEXT_BLOCK_BODY_CAP,
    _build_captain_context_blocks,
    _build_context_blocks,
    _build_messages,
    _context_block_payloads,
)
from agentcore.runtime.runs.types import ContextBlock, Deliverable, RunPhase, RunSpec, RunState
from tests.runs_executor.conftest import _plan


def test_sibling_block_warns_about_file_path_collisions():
    spec = RunSpec(run_id="x", agent_id="x", role="A", task="t", sibling_summary="- B：做B")
    msgs = _build_messages(_plan(spec), spec, {}, "SYS", "原始请求")
    user = msgs[1].content or ""
    assert "避免互相覆盖" in user  # the soft path-ownership nudge
    assert "做B" in user  # still carries the sibling intent summary


def test_team_position_block_four_dag_shapes():
    # D（统一团队位置块）: a worker's user prompt now carries its DAG TOPOLOGY — who runs
    # beside it (siblings) and, crucially, where its output GOES — symmetric to the
    # upstream PRODUCT injection (_dep_context_blocks). Four shapes → four framings; this
    # pins each so the「上游越权写最终交付物」fix (an upstream link learns it hands off,
    # not authors the final artifact) and the terminal-ownership boost (a writer learns
    # it IS the final author) can't silently regress. Also pins A1 (递指针 affordance):
    # the upstream branch — and ONLY it — grants intermediate persist guidance:
    # task-book artifacts (strict) or DRAFTS_DIR + descriptive name (free teams);
    # never workspace-root findings-<role>.md. Terminal / parallel / solo must NOT get A1.
    from agentcore.workspace.stage_dirs import DRAFTS_DIR

    plan, errs = build_run_plan(
        [
            {"id": "r1", "role": "调研员A", "task": "查A"},
            {"id": "r2", "role": "调研员B", "task": "查B"},
            {"id": "w", "role": "写手", "task": "写报告", "depends_on": ["r1", "r2"]},
        ],
        id_prefix="t",
    )
    assert errs == []
    r1, w = plan.by_id("t_r1"), plan.by_id("t_w")

    # (1) UPSTREAM link (has dependents): told it feeds the downstream 写手 and must NOT
    #     produce the final artifact itself — the over-reach fix.
    up = _build_messages(plan, r1, {}, "SYS", "原始请求")[1].content or ""
    assert "检索纪律" not in up
    assert "你在团队中的位置" in up
    assert "上游一环" in up and "写手" in up
    assert "不要自己产出整个最终交付物" in up
    assert "调研员B" in up  # parallel-peer awareness still present
    assert "不一定全是你的活" in up  # request reframed as a team goal, not a mandate
    # A1 free-team path: DRAFTS_DIR + descriptive name; still names the anti-pattern.
    # 落点是工作稿而非 research/：大中间产物就是过程材料，research/ 不再当杂物入口。
    assert DRAFTS_DIR in up and "自起描述性文件名" in up and "切勿用空路径" in up
    assert "findings-" in up
    assert "工作区根" in up

    # (2) TERMINAL synthesizer (has upstream, no dependents): told it IS the final author
    #     — reinforces structure ownership (the worker-side L3 lever).
    term = _build_messages(plan, w, {}, "SYS", "原始请求")[1].content or ""
    assert "终端环" in term and "最终交付物" in term
    assert "先 file_read" in term and "全仓" in term
    assert "不要自己产出整个最终交付物" not in term  # not an upstream link
    assert "自起描述性文件名" not in term  # A1 is upstream-only
    assert w.sibling_summary == ""  # lone fan-in → no parallel-peer line

    # (3) PARALLEL batch (siblings only, no up/down): peer coordination, no flow framing.
    par_plan, _ = build_run_plan(
        [{"role": "A", "task": "做A"}, {"role": "B", "task": "做B"}], id_prefix="p"
    )
    par = _build_messages(par_plan, par_plan.by_id("p_1"), {}, "SYS", "原始请求")[1].content or ""
    assert "并行队友" in par
    assert "上游一环" not in par and "终端环" not in par
    assert "自起描述性文件名" not in par  # no hand-off → no A1 intermediate-persist hint

    # (4) SOLO single worker (no team): no position block, plain request header.
    solo_plan, _ = build_run_plan([{"role": "A", "task": "做A"}], id_prefix="s")
    solo = (
        _build_messages(solo_plan, solo_plan.by_id("s_1"), {}, "SYS", "原始请求")[1].content or ""
    )
    assert "你在团队中的位置" not in solo
    assert "不一定全是你的活" not in solo  # a solo worker IS the whole job


def test_team_position_a1_respects_pinned_artifacts():
    """A1 with task-book artifacts: strict path, not RESEARCH_DIR free naming."""
    from agentcore.workspace.stage_dirs import RESEARCH_DIR

    plan, errs = build_run_plan(
        [
            {
                "id": "r1",
                "role": "调研员",
                "task": "查A",
                "deliverable": {
                    "form": "files",
                    "artifacts": [f"{RESEARCH_DIR}/选型调研报告.md"],
                },
            },
            {"id": "w", "role": "写手", "task": "写报告", "depends_on": ["r1"]},
        ],
        id_prefix="pin",
    )
    assert errs == []
    up = _build_messages(plan, plan.by_id("pin_r1"), {}, "SYS", "原始请求")[1].content or ""
    assert "严格按任务书路径" in up
    assert f"{RESEARCH_DIR}/选型调研报告.md" in up
    assert "自起描述性文件名" not in up
    assert "findings-" not in up


async def test_context_blocks_channel_sequence_and_single_source():
    # A solo worker (no team_position) with deliverable + gate_notes + steer exercises
    # the optional channels and pins their order; the rendered opening user message must
    # be EXACTLY the same ContextBlock list joined (用户看到的 == LLM 吃到的, 双投影零漂移).
    plan, _ = build_run_plan([{"role": "A", "task": "做A"}], id_prefix="t")
    spec = replace(
        plan.by_id("t_1"),
        deliverable=Deliverable(required_sections=["结论"]),
        gate_notes="把关要点文",
        steer="按新方向调整",
    )
    blocks = _build_context_blocks(plan, spec, {}, "原始请求", None)
    assert [b.channel for b in blocks] == [
        "request",
        "task",
        "deliverable",
        "gate_notes",
        "steer",
    ]
    rendered = _build_messages(plan, spec, {}, "SYS", "原始请求")[1].content
    assert rendered == "\n\n".join(f"## {b.heading}\n{b.body}" for b in blocks)
    # steer rides last + highest priority, carried verbatim.
    assert blocks[-1].channel == "steer"
    assert blocks[-1].body == "按新方向调整"
    assert blocks[-2].channel == "gate_notes"
    assert blocks[-2].body == "把关要点文"
    deliverable = next(b for b in blocks if b.channel == "deliverable")
    assert "结论" in deliverable.body
    assert "建议正文骨架" not in deliverable.body
    assert "检索预算" not in deliverable.body
    assert "交付形态" not in deliverable.body


async def test_context_blocks_omit_deliverable_when_no_instance_facts():
    plan, _ = build_run_plan([{"role": "A", "task": "做A"}], id_prefix="t")
    spec = plan.by_id("t_1")
    blocks = _build_context_blocks(plan, spec, {}, "原始请求", None)
    assert [b.channel for b in blocks] == ["request", "task"]
    assert not any(b.channel == "deliverable" for b in blocks)


async def test_context_blocks_dependency_carries_provenance():
    # 通道③: each upstream dep becomes a `dependency` block carrying its provenance
    # (source_role / source_run_id / fidelity / files) so the UI shows HOW a teammate's
    # product was handed down — a prose dep → pass_through, a file-writing dep → pointer.
    plan, _ = build_run_plan(
        [
            {"id": "a", "role": "研究员", "task": "调研"},
            {"id": "b", "role": "工程师", "task": "落盘"},
            {"id": "c", "role": "写手", "task": "撰写", "depends_on": ["a", "b"]},
        ],
        id_prefix="t",
    )
    completed = {
        "t_a": RunState(phase=RunPhase.COMPLETED, content="关键事实 X"),
        "t_b": RunState(
            phase=RunPhase.COMPLETED, content="改了配置", files_touched=["out/config.json"]
        ),
    }
    blocks = _build_context_blocks(plan, plan.by_id("t_c"), completed, "原始请求", None)
    deps = {b.source_role: b for b in blocks if b.channel == "dependency"}
    assert set(deps) == {"研究员", "工程师"}
    assert deps["研究员"].source_run_id == "t_a"
    assert deps["研究员"].fidelity == "pass_through"
    assert deps["研究员"].files == []
    assert deps["工程师"].source_run_id == "t_b"
    assert deps["工程师"].fidelity == "pointer"
    assert deps["工程师"].files == ["out/config.json"]


def test_team_brief_block_injected_before_task():
    plan, errs = build_run_plan(
        [{"id": "w", "role": "写手", "task": "写稿"}],
        id_prefix="t",
    )
    assert errs == []
    spec = plan.nodes[0]
    blocks = _build_context_blocks(
        plan, spec, {}, "原始请求", None, team_brief="受众：初学者；篇幅约 1500 字"
    )
    brief = next(b for b in blocks if b.channel == "team_brief")
    assert "团队共识" in brief.heading
    assert "初学者" in brief.body
    msgs = _build_messages(
        plan, spec, {}, "SYS", "原始请求", team_brief="跨波共识"
    )
    user = msgs[1].content or ""
    assert "跨波共识" in user

    # 决策④: the prompt feeds the LLM the FULL block, but the run_context/journal copy is
    # head+tail capped (flagged via `truncated`, ORIGINAL size kept in `chars`) so a huge
    # pasted request can't bloat the journal.
    long_body = "甲" * (_CONTEXT_BLOCK_BODY_CAP + 5000)
    blocks = [
        ContextBlock(channel="task", heading="你的任务", body="短"),
        ContextBlock(channel="request", heading="原始用户请求", body=long_body),
    ]
    payloads = _context_block_payloads(blocks)
    # a within-budget block passes through untouched.
    assert payloads[0]["truncated"] is False
    assert payloads[0]["body"] == "短"
    assert payloads[0]["chars"] == 1
    # the over-budget block is capped + flagged, but reports its ORIGINAL size.
    capped = payloads[1]
    assert capped["truncated"] is True
    assert capped["chars"] == len(long_body)
    assert len(capped["body"]) <= _CONTEXT_BLOCK_BODY_CAP
    assert "系统视图截断" in capped["body"]  # transport elision (not delivery-omission)
    # the wire shape carries every field the frontend / oracle fold reads.
    assert set(capped) == {
        "channel",
        "heading",
        "body",
        "chars",
        "truncated",
        "source_role",
        "source_run_id",
        "fidelity",
        "files",
    }


def test_context_block_payloads_exempts_system_block_from_cap():
    # The captain `system` block (verbatim CEO system prompt) is EXEMPT from the 决策④ cap:
    # the desktop「收到的上下文」dialog shows it in full (having folded in the old「提示词」
    # button), and it's bounded internal content — not the unbounded user/dep body the cap
    # guards. A non-system block of the same size is still capped, proving the carve-out is
    # channel-scoped.
    long_prompt = "甲" * (_CONTEXT_BLOCK_BODY_CAP + 5000)
    blocks = [
        ContextBlock(channel="system", heading="CEO 系统提示", body=long_prompt),
        ContextBlock(channel="request", heading="原始用户请求", body=long_prompt),
    ]
    payloads = _context_block_payloads(blocks)
    # system: full body, never flagged, even far past the cap.
    assert payloads[0]["truncated"] is False
    assert payloads[0]["body"] == long_prompt
    assert payloads[0]["chars"] == len(long_prompt)
    # request of identical size: still capped + flagged.
    assert payloads[1]["truncated"] is True
    assert len(payloads[1]["body"]) <= _CONTEXT_BLOCK_BODY_CAP


def test_captain_context_blocks_channels_order_and_single_source():
    # A continued chat: system + a prior turn + this request → the three CEO-side channels
    # in order, each body verbatim what the captain's `messages` array feeds the LLM.
    history = [
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "你好，有什么可以帮你？"},
        {"role": "user", "content": ""},  # blank turns are dropped, not rendered
    ]
    blocks = _build_captain_context_blocks("你是 CEO。", history, "帮我润色这段话。")
    assert [b.channel for b in blocks] == ["system", "history", "request"]
    # system block carries the verbatim chat system prompt (决策②: visibility is a FRONTEND
    # concern — the desktop「收到的上下文」dialog shows it to all, mobile hides it; the
    # projection/block always carries it verbatim, and is exempt from the 决策④ body cap).
    assert blocks[0].body == "你是 CEO。"
    # history renders the prior turns as 用户/CEO prose, blank turns dropped.
    assert blocks[1].body == "用户：你好\n\nCEO：你好，有什么可以帮你？"
    # the request is this turn's user message verbatim.
    assert blocks[-1].channel == "request"
    assert blocks[-1].body == "帮我润色这段话。"


def test_captain_context_blocks_first_turn_omits_history():
    # A fresh conversation (no prior turns) → only system + request, no empty history block.
    blocks = _build_captain_context_blocks("你是 CEO。", [], "第一条消息")
    assert [b.channel for b in blocks] == ["system", "request"]


def test_worker_turn_observe_covers_identity(monkeypatch):
    captured: list[dict] = []

    class _Spy:
        def info(self, event: str, **kwargs: object) -> None:
            captured.append({"event": event, **kwargs})

    monkeypatch.setattr("agentcore.runtime.context.assembler.logger", _Spy())
    spec = RunSpec(run_id="x", agent_id="x", role="汇报员", task="t")
    msgs = _build_messages(_plan(spec), spec, {}, "SYS", "原始请求")
    system = msgs[0].content or ""
    assert system.startswith("<身份>")
    assert "SYS" in system
    assert "你的角色：汇报员" in system
    rows = [r for r in captured if r.get("event") == "cost.prompt_assembled"]
    assert len(rows) == 1
    row = rows[0]
    assert row["scope"] == "worker_turn"
    assert row["sections"]["worker_base"] == 3
    assert row["sections"]["identity"] > 0
    assert row["sections"]["role"] == len("你的角色：汇报员")


def test_build_messages_appends_working_set_on_system_tail():
    spec = RunSpec(run_id="x", agent_id="x", role="调研员", task="t")
    block = "<工作集>\n正文以磁盘为准；需要细节时用 file_read。\n- read src/a.py\n</工作集>"
    msgs = _build_messages(
        _plan(spec), spec, {}, "SYS", "原始请求", working_set=block
    )
    system = msgs[0].content or ""
    assert system.endswith(block)
    assert system.index("SYS") < system.index("<工作集>")
