"""Tests for the intra-batch write-conflict guard (并行写隔离·硬约束).

Two layers: the pure :class:`WriteCoordinator` ownership rules, and the
``FileWriteTool`` end-to-end behaviour when a coordinator is wired onto the context
(concurrent sibling refused; dependency overwrite allowed; no-coordinator path inert).
"""

import json
from pathlib import Path

from agentcore.llm.provider.protocol import ToolCall, ToolCallFunction
from agentcore.runtime.engine.tool_exec import execute_tools
from agentcore.runtime.events import EventSink
from agentcore.runtime.loop_controller import DEFAULT_TOOL_FAILURE_DISABLE, LoopController
from agentcore.tools.builtin.file_ops import FileAppendTool, FileWriteTool
from agentcore.tools.protocol import ToolContext
from agentcore.tools.registry import ToolRegistry
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace.server import ServerWorkspace
from agentcore.workspace.write_claims import WriteCoordinator


def _ctx(
    workspace: Path,
    *,
    run_id: str = "s",
    coordinator: WriteCoordinator | None = None,
    ancestors: frozenset[str] = frozenset(),
) -> ToolContext:
    return ToolContext.create(
        execution_id="e",
        run_id=run_id,
        agent_id="a",
        backend=ServerWorkspace(root=workspace, sandbox=SubprocessSandbox()),
        user_id="u",
        write_coordinator=coordinator,
        write_ancestors=ancestors,
    )


# --- WriteCoordinator unit rules ---


def test_first_claim_granted():
    c = WriteCoordinator()
    assert c.claim("report.md", "a", frozenset()) is None


def test_concurrent_sibling_conflicts():
    c = WriteCoordinator()
    assert c.claim("report.md", "a", frozenset()) is None
    # b has no dependency on a → blocked, told who owns it.
    assert c.claim("report.md", "b", frozenset()) == "a"


def test_same_run_may_rewrite_its_own_file():
    c = WriteCoordinator()
    assert c.claim("report.md", "a", frozenset()) is None
    # A contract retry re-writes the same path under the same run → allowed.
    assert c.claim("report.md", "a", frozenset()) is None


def test_descendant_may_overwrite_ancestor_file():
    c = WriteCoordinator()
    assert c.claim("report.md", "upstream", frozenset()) is None
    # d depends on upstream → consolidating its product is intended, not a clobber.
    assert c.claim("report.md", "d", frozenset({"upstream"})) is None
    # ownership transferred to d: a fresh unrelated sibling now conflicts with d.
    assert c.claim("report.md", "e", frozenset()) == "d"


def test_paths_normalized_to_one_owner():
    c = WriteCoordinator()
    assert c.claim("out/report.md", "a", frozenset()) is None
    # ./out/report.md and out//report.md are the same file → same conflict.
    assert c.claim("./out/report.md", "b", frozenset()) == "a"
    assert c.claim("out//report.md", "b", frozenset()) == "a"


def test_release_frees_a_failed_write():
    c = WriteCoordinator()
    assert c.claim("report.md", "a", frozenset()) is None
    c.release("report.md", "a")
    # a never really wrote it (write failed) → b is free to take the name.
    assert c.claim("report.md", "b", frozenset()) is None


def test_release_only_affects_the_owner():
    c = WriteCoordinator()
    assert c.claim("report.md", "a", frozenset()) is None
    # b doesn't own it; its release is a no-op (can't free a's claim).
    c.release("report.md", "b")
    assert c.claim("report.md", "b", frozenset()) == "a"


# --- FileWriteTool end-to-end ---


async def test_concurrent_sibling_write_is_refused_and_does_not_clobber(tmp_path: Path):
    coordinator = WriteCoordinator()
    a = await FileWriteTool().execute(
        {"path": "report.md", "content": "from-A"},
        _ctx(tmp_path, run_id="a", coordinator=coordinator),
    )
    assert a.success is True

    b = await FileWriteTool().execute(
        {"path": "report.md", "content": "from-B"},
        _ctx(tmp_path, run_id="b", coordinator=coordinator),
    )
    assert b.success is False
    assert "写入冲突" in b.error
    assert "`a`" in b.error or "owner" in b.error.lower() or "负责" in b.error
    # A's deliverable survives — B never overwrote it.
    assert (tmp_path / "report.md").read_text(encoding="utf-8") == "from-A"


async def test_dependency_overwrite_is_allowed(tmp_path: Path):
    coordinator = WriteCoordinator()
    await FileWriteTool().execute(
        {"path": "report.md", "content": "draft"},
        _ctx(tmp_path, run_id="up", coordinator=coordinator),
    )
    # downstream depends on "up" → may consolidate (overwrite) its file.
    d = await FileWriteTool().execute(
        {"path": "report.md", "content": "final"},
        _ctx(tmp_path, run_id="down", coordinator=coordinator, ancestors=frozenset({"up"})),
    )
    assert d.success is True
    assert (tmp_path / "report.md").read_text(encoding="utf-8") == "final"


async def test_write_conflict_result_is_contract_failure(tmp_path: Path):
    """并发写冲突回执标 contract_failure（自我纠正型参数打回）——file_write 与 file_append 对称。"""
    coordinator = WriteCoordinator()
    await FileWriteTool().execute(
        {"path": "report.md", "content": "from-A"},
        _ctx(tmp_path, run_id="a", coordinator=coordinator),
    )
    # A concurrent sibling's file_write onto A's claimed path collides.
    w = await FileWriteTool().execute(
        {"path": "report.md", "content": "from-B"},
        _ctx(tmp_path, run_id="b", coordinator=coordinator),
    )
    assert w.success is False
    assert w.contract_failure is True

    # file_append onto the same claimed path collides identically.
    ap = await FileAppendTool().execute(
        {"path": "report.md", "content": "more"},
        _ctx(tmp_path, run_id="c", coordinator=coordinator),
    )
    assert ap.success is False
    assert ap.contract_failure is True


async def test_write_conflict_does_not_trip_run_circuit_breaker(tmp_path: Path):
    """写冲突经 execute_tools→LoopController 不计入 run 级熔断：同批连撞不烧穿禁用阈值。"""
    coordinator = WriteCoordinator()
    a = await FileWriteTool().execute(
        {"path": "report.md", "content": "from-A"},
        _ctx(tmp_path, run_id="a", coordinator=coordinator),
    )
    assert a.success is True

    reg = ToolRegistry()
    reg.register(FileWriteTool())
    controller = LoopController(tool_failure_warn=2, tool_failure_disable=3)
    # A concurrent sibling collides more times than the disable threshold.
    for _ in range(DEFAULT_TOOL_FAILURE_DISABLE + 1):
        tc = ToolCall(
            id="c",
            function=ToolCallFunction(
                name="file_write",
                arguments=json.dumps({"path": "report.md", "content": "from-B"}),
            ),
        )
        _msgs, _terminal, attempts = await execute_tools(
            [tc],
            reg,
            _ctx(tmp_path, run_id="b", coordinator=coordinator),
            EventSink(),
            # 云端沙箱上的 worker 写文件：按 sandbox_approval 免逐次卡。
            approval_gate=None,
            role="worker",
        )
        assert attempts[0].success is False
        assert attempts[0].contract_failure is True  # forwarded from the ToolResult
        controller.record(attempts)

    # The run-scoped breaker never tallied the collisions → tool stays enabled.
    # Same-fingerprint validation may path-stop (steer) without disabling the pen.
    assert controller.tool_failure_count("file_write") == 0
    cb = controller.tool_circuit_breaker()
    assert cb.disabled == ()
    assert cb.warned == ()
    assert cb.force_segmented == frozenset()
    # A's deliverable survived every collision (B never overwrote it).
    assert (tmp_path / "report.md").read_text(encoding="utf-8") == "from-A"


async def test_no_coordinator_means_no_guard(tmp_path: Path):
    # The CEO / tests path: without a coordinator, file_write is unguarded (two writes
    # to the same path just overwrite, last-writer-wins — the pre-existing behaviour).
    first = await FileWriteTool().execute(
        {"path": "report.md", "content": "one"}, _ctx(tmp_path, run_id="a")
    )
    second = await FileWriteTool().execute(
        {"path": "report.md", "content": "two"}, _ctx(tmp_path, run_id="b")
    )
    assert first.success is True
    assert second.success is True
    assert (tmp_path / "report.md").read_text(encoding="utf-8") == "two"


# --- C3: str_replace / declare / transfer ---


async def test_str_replace_respects_ownership(tmp_path: Path):
    from agentcore.tools.builtin.file_ops import StrReplaceTool

    coordinator = WriteCoordinator()
    # 新建（非覆盖非空代码）以建立 integration 归属。
    await FileWriteTool().execute(
        {"path": "App.tsx", "content": "from-integration"},
        _ctx(tmp_path, run_id="integration", coordinator=coordinator),
    )
    r = await StrReplaceTool().execute(
        {"path": "App.tsx", "old_string": "from-integration", "new_string": "hijack"},
        _ctx(tmp_path, run_id="frontend", coordinator=coordinator),
    )
    assert r.success is False
    assert "integration" in (r.error or "")
    assert "负责" in (r.error or "")
    assert (tmp_path / "App.tsx").read_text(encoding="utf-8") == "from-integration"


def test_declare_does_not_steal_from_ancestor():
    """交接式：下游派发声明同 path 不抢祖先锁；真写时再交接。"""
    c = WriteCoordinator()
    assert c.declare("site/index.html", "skeleton", frozenset()) is None
    assert (
        c.declare("site/index.html", "assemble", frozenset({"skeleton"})) is None
    )
    assert c.owner_of("site/index.html") == "skeleton"
    assert c.claim("site/index.html", "assemble", frozenset({"skeleton"})) is None
    assert c.owner_of("site/index.html") == "assemble"


def test_declare_ancestor_handoff_opt_in():
    c = WriteCoordinator()
    c.declare("a.ts", "lead", frozenset())
    assert (
        c.declare(
            "a.ts",
            "child",
            frozenset({"lead"}),
            allow_ancestor_handoff=True,
        )
        is None
    )
    assert c.owner_of("a.ts") == "child"


def test_declare_and_completed_owner_still_blocks():
    c = WriteCoordinator()
    assert c.declare("site/index.html", "skeleton", frozenset()) is None
    # Completed owner still holds — unrelated sibling cannot declare or claim.
    assert c.declare("site/index.html", "frontend", frozenset()) == "skeleton"
    assert c.claim("site/index.html", "frontend", frozenset()) == "skeleton"


def test_replaces_transfers_ownership():
    c = WriteCoordinator()
    c.declare("App.tsx", "old_fe", frozenset())
    moved = c.transfer_all_from("old_fe", "new_fe")
    assert "App.tsx" in moved
    assert c.owner_of("App.tsx") == "new_fe"
    assert c.claim("App.tsx", "new_fe", frozenset()) is None


def test_force_claim_transfers():
    c = WriteCoordinator()
    c.claim("x.md", "a", frozenset())
    assert c.claim("x.md", "b", frozenset(), force=True) is None
    assert c.owner_of("x.md") == "b"


def test_ownership_snapshot_roundtrip():
    c = WriteCoordinator()
    c.declare("a/b.md", "w1", frozenset())
    c.mark_written("a/b.md")
    payload = c.to_dict()
    assert payload.get("_v") == 3
    restored = WriteCoordinator.from_dict(payload)
    assert restored.owner_of("a/b.md") == "w1"
    assert restored.is_written("a/b.md")
    assert restored.claim("a/b.md", "w2", frozenset()) == "w1"


def test_legacy_flat_snapshot_still_loads():
    restored = WriteCoordinator.from_dict({"a/b.md": "w1"})
    assert restored.owner_of("a/b.md") == "w1"
    assert not restored.is_written("a/b.md")


def test_v2_snapshot_migrates_with_run_desks():
    """v2 bare path → desk×path；优先节点 target，否则 birth，不明用哨兵。"""
    from agentcore.workspace.write_claims import (
        LEGACY_DESK_SENTINEL,
        OWNERSHIP_KEY_SEP,
        WriteCoordinator,
        make_ownership_key,
    )

    v2 = {
        "_v": 2,
        "owners": {"App.tsx": "fe_a", "other.md": "fe_b"},
        "written": ["App.tsx"],
    }
    migrated = WriteCoordinator.from_dict(
        v2,
        birth_desk_id="birth-desk",
        run_target_folder_ids={"fe_a": "desk-a", "fe_b": None},
    )
    assert migrated.owner_of("App.tsx", desk_id="desk-a") == "fe_a"
    assert migrated.owner_of("other.md", desk_id="birth-desk") == "fe_b"
    # Cross-desk same path does not collide after migrate.
    assert migrated.claim("App.tsx", "fe_x", frozenset(), desk_id="desk-b") is None
    assert migrated.owner_of("App.tsx", desk_id="desk-a") == "fe_a"

    unknown = WriteCoordinator.from_dict({"_v": 2, "owners": {"x.md": "w1"}})
    key = make_ownership_key(LEGACY_DESK_SENTINEL, "x.md")
    assert OWNERSHIP_KEY_SEP in key
    assert unknown.owner_of("x.md") == "w1"
    assert unknown.claim("x.md", "w2", frozenset(), desk_id="other-desk") is None


def test_cross_desk_claim_and_declare_independent():
    c = WriteCoordinator()
    assert c.declare("App.tsx", "a", frozenset(), desk_id="desk-1") is None
    assert c.declare("App.tsx", "b", frozenset(), desk_id="desk-2") is None
    assert c.claim("App.tsx", "a", frozenset(), desk_id="desk-1") is None
    assert c.claim("App.tsx", "b", frozenset(), desk_id="desk-2") is None
    assert c.claim("App.tsx", "c", frozenset(), desk_id="desk-1") == "a"


def test_conflict_message_distinguishes_declared_vs_written():
    from agentcore.workspace.write_claims import ownership_conflict_message

    declared = ownership_conflict_message(
        "src/x.ts",
        "backend-fix",
        ownership_kind="declared",
        owner_status="running",
    )
    assert "仅派发占位" in declared
    assert "不是上一 run 残留锁" in declared
    assert "进行中" in declared
    assert "transfer_ownership" in declared

    written = ownership_conflict_message(
        "src/x.ts",
        "backend-fix",
        owner_role="后端补齐",
        ownership_kind="written",
        owner_status="completed",
    )
    assert "已成功写入" in written
    assert "已完成" in written
    assert "后端补齐" in written
    assert "auto-replaces" in written or "同座位" in written
    assert "移交写权" in written
    assert "不要 escalate" in written
    assert "transfer_ownership" not in written


def test_claim_denial_feeds_ownership_hints_without_error_verbatim():
    """Paraphrased escalate questions still get ownership_paths from claim denials."""
    from agentcore.workspace.write_claims import (
        ownership_escalation_hints,
        parse_ownership_conflict_paths,
    )

    c = WriteCoordinator()
    assert c.claim("src/ui/ReasoningGraph.tsx", "del_old", frozenset()) is None
    assert c.claim("src/game/GameScene.ts", "del_old", frozenset()) is None
    assert c.claim("src/ui/ReasoningGraph.tsx", "del_new", frozenset()) == "del_old"
    assert c.claim("src/game/GameScene.ts", "del_new", frozenset()) == "del_old"
    assert c.denied_paths_for("del_new") == [
        "src/ui/ReasoningGraph.tsx",
        "src/game/GameScene.ts",
    ]

    paraphrased = (
        "子任务续跑时产出路径被上一 run 占位锁占用："
        "src/ui/ReasoningGraph.tsx、src/game/GameScene.ts 均报已归队友负责"
    )
    assert parse_ownership_conflict_paths(paraphrased) == []

    hints = ownership_escalation_hints(
        escalator_run_id="del_new",
        question=paraphrased,
        write_coordinator=c,
    )
    assert hints["ownership_paths"] == [
        "src/ui/ReasoningGraph.tsx",
        "src/game/GameScene.ts",
    ]
    assert hints["lock_owner_run_id"] == "del_old"

    c.transfer("src/ui/ReasoningGraph.tsx", "del_new")
    c.transfer("src/game/GameScene.ts", "del_new")
    after = ownership_escalation_hints(
        escalator_run_id="del_new",
        question=paraphrased,
        write_coordinator=c,
    )
    assert after == {}


def test_ownership_hints_still_parse_verbatim_conflict_error():
    from agentcore.workspace.write_claims import ownership_escalation_hints

    c = WriteCoordinator()
    assert c.claim("site/index.html", "owner", frozenset()) is None
    question = "写入冲突：`site/index.html` 已归队友负责（仅派发占位）"
    hints = ownership_escalation_hints(
        escalator_run_id="other",
        question=question,
        write_coordinator=c,
    )
    assert hints["ownership_paths"] == ["site/index.html"]
    assert hints["lock_owner_run_id"] == "owner"


def test_ancestors_include_nested_parent_run_id():
    from agentcore.runtime.runs.executor.context import _ancestors_by_id
    from agentcore.runtime.runs.plan import RunPlan
    from agentcore.runtime.runs.types import RunSpec

    plan = RunPlan(
        nodes=[
            RunSpec(
                run_id="storage",
                role="存储",
                task="写 storage",
                parent_run_id="backend-fix",
                depth=1,
            ),
            RunSpec(
                run_id="tools",
                role="工具",
                task="写 tools",
                parent_run_id="backend-fix",
                depth=1,
            ),
        ]
    )
    anc = _ancestors_by_id(plan)
    assert "backend-fix" in anc["storage"]
    assert "backend-fix" in anc["tools"]
    # Siblings do not count each other as ancestors.
    assert "tools" not in anc["storage"]
    assert "storage" not in anc["tools"]


def test_nested_child_may_claim_parent_declared_path():
    c = WriteCoordinator()
    assert c.declare("src/storage/db.ts", "backend-fix", frozenset()) is None
    # Child write_ancestors includes nested parent → claim succeeds and transfers.
    assert (
        c.claim("src/storage/db.ts", "storage", frozenset({"backend-fix"})) is None
    )
    assert c.owner_of("src/storage/db.ts") == "storage"
    # Unrelated peer still blocked by the new owner.
    assert c.claim("src/storage/db.ts", "other", frozenset()) == "storage"


def test_nested_siblings_still_mutex_under_shared_parent():
    c = WriteCoordinator()
    c.declare("src/tools/base-tool.ts", "backend-fix", frozenset())
    assert (
        c.claim("src/tools/base-tool.ts", "tools_a", frozenset({"backend-fix"}))
        is None
    )
    # Sibling only has parent in ancestors, not tools_a → conflict.
    assert (
        c.claim("src/tools/base-tool.ts", "tools_b", frozenset({"backend-fix"}))
        == "tools_a"
    )

def test_handoff_owned_paths_on_complete_unique_dependent():
    from agentcore.runtime.coordination.append_guard import (
        declare_plan_artifacts,
        handoff_owned_paths_on_complete,
    )
    from agentcore.runtime.runs.plan import RunPlan
    from agentcore.runtime.runs.types import Deliverable, RunSpec
    from agentcore.workspace.write_claims import WriteCoordinator

    plan = RunPlan()
    for node in (
        RunSpec(
            run_id="skeleton",
            role="骨架",
            task="壳",
            deliverable=Deliverable(
                artifacts=["site/index.html", "site/styles.css", "site/CONTRACT.md"]
            ),
        ),
        RunSpec(
            run_id="section_0",
            role="分区",
            task="块",
            depends_on=["skeleton"],
            deliverable=Deliverable(artifacts=["site/sections/s0.html"]),
        ),
        RunSpec(
            run_id="assemble",
            role="组装",
            task="合",
            depends_on=["section_0"],
            deliverable=Deliverable(
                artifacts=["site/index.html", "site/styles.css", "site/main.js"]
            ),
        ),
    ):
        plan.add(node)
    ownership = WriteCoordinator()
    declare_plan_artifacts(plan, ownership)
    assert ownership.owner_of("site/index.html") == "skeleton"
    assert ownership.owner_of("site/styles.css") == "skeleton"
    # assemble declared intent only — did not steal
    moved = handoff_owned_paths_on_complete(
        plan, ownership, "skeleton", completed_run_ids={"skeleton"}
    )
    assert ("site/index.html", "assemble") in moved
    assert ("site/styles.css", "assemble") in moved
    assert ownership.owner_of("site/CONTRACT.md") == "skeleton"
    assert ownership.owner_of("site/index.html") == "assemble"


def test_declare_steals_from_completed_owner_across_waves():
    """跨波次修订：新节点声明同 artifact、原主已完成 → 派发即移交。"""
    from agentcore.runtime.coordination.append_guard import declare_plan_artifacts
    from agentcore.runtime.runs.plan import RunPlan
    from agentcore.runtime.runs.types import Deliverable, RunSpec
    from agentcore.workspace.write_claims import WriteCoordinator

    writer = RunSpec(
        run_id="writer",
        role="结题写手",
        task="写总览",
        deliverable=Deliverable(artifacts=["docs/overview.md"]),
    )
    reviser = RunSpec(
        run_id="reviser",
        role="修订员",
        task="落实审校修订",
        deliverable=Deliverable(artifacts=["docs/overview.md"]),
    )
    wave1 = RunPlan()
    wave1.add(writer)
    ownership = WriteCoordinator()
    declare_plan_artifacts(wave1, ownership)
    assert ownership.owner_of("docs/overview.md") == "writer"
    ownership.mark_written("docs/overview.md")

    # 第二波：写手已完成；修订员声明同路径 → 自动接手（无需用户点卡）。
    live = RunPlan()
    live.add(writer)
    live.add(reviser)
    conflicts = declare_plan_artifacts(
        live,
        ownership,
        only_run_ids={"reviser"},
        completed_run_ids={"writer"},
    )
    assert conflicts == []
    assert ownership.owner_of("docs/overview.md") == "reviser"
    assert ownership.claim("docs/overview.md", "reviser", frozenset()) is None


def test_declare_still_blocks_running_owner():
    """锁主仍在跑时，无关新节点声明同路径仍冲突（不自动抢）。"""
    from agentcore.runtime.coordination.append_guard import declare_plan_artifacts
    from agentcore.runtime.runs.plan import RunPlan
    from agentcore.runtime.runs.types import Deliverable, RunSpec
    from agentcore.workspace.write_claims import WriteCoordinator

    a = RunSpec(
        run_id="a",
        role="A",
        task="写",
        deliverable=Deliverable(artifacts=["x.md"]),
    )
    b = RunSpec(
        run_id="b",
        role="B",
        task="也写",
        deliverable=Deliverable(artifacts=["x.md"]),
    )
    plan = RunPlan()
    plan.add(a)
    ownership = WriteCoordinator()
    declare_plan_artifacts(plan, ownership)
    live = RunPlan()
    live.add(a)
    live.add(b)
    conflicts = declare_plan_artifacts(
        live,
        ownership,
        only_run_ids={"b"},
        completed_run_ids=set(),  # a still running
    )
    assert conflicts == [("b", "x.md", "a")]
    assert ownership.owner_of("x.md") == "a"


def test_nested_lookup_owner_status_falls_back_to_parent_session():
    """嵌套 eid 无会话时，lookup 与 resolve_write_coordinator 同款父回退。"""
    from agentcore.runtime.coordination.session import (
        CoordinationSession,
        clear_active_coordination,
        current_execution_id,
        set_active_coordination,
    )
    from agentcore.workspace.write_claims import lookup_owner_status

    clear_active_coordination()
    parent = CoordinationSession(execution_id="parent-exec", total_workers=2)
    set_active_coordination(parent)
    parent._running_workers["author-v1"] = "作者"
    parent.ensure_file_ownership().declare("docs/plan.md", "author-v1", frozenset())
    token = current_execution_id.set("parent-exec")
    try:
        role, status = lookup_owner_status(
            "author-v1", execution_id="nested-child-exec"
        )
        assert status == "running"
        assert role == "作者"
    finally:
        current_execution_id.reset(token)
        clear_active_coordination("parent-exec")
        clear_active_coordination()


def test_ended_owner_declare_and_claim_handoff_without_completed_run_ids():
    """旁路 ended_owners：不进进度 completed_run_ids 也能 declare/claim 接手。"""
    from agentcore.runtime.coordination.append_guard import declare_plan_artifacts
    from agentcore.runtime.runs.plan import RunPlan
    from agentcore.runtime.runs.types import Deliverable, RunSpec
    from agentcore.workspace.write_claims import WriteCoordinator

    ownership = WriteCoordinator()
    ownership.declare("docs/overview.md", "author-v1", frozenset())
    ownership.mark_written("docs/overview.md")
    ownership.mark_ended("author-v1")
    assert ownership.is_ended("author-v1")
    assert ownership.claim("docs/overview.md", "merger", frozenset()) is None
    assert ownership.owner_of("docs/overview.md") == "merger"

    # Fresh ledger: declare_plan_artifacts path with ended (not completed_run_ids).
    ownership2 = WriteCoordinator()
    wave1 = RunPlan()
    wave1.add(
        RunSpec(
            run_id="author-v1",
            role="作者",
            task="写",
            deliverable=Deliverable(artifacts=["docs/overview.md"]),
        )
    )
    declare_plan_artifacts(wave1, ownership2)
    ownership2.mark_ended("author-v1")
    reviser = RunSpec(
        run_id="reviser",
        role="修订",
        task="改",
        deliverable=Deliverable(artifacts=["docs/overview.md"]),
    )
    live = RunPlan()
    live.add(wave1.nodes[0])
    live.add(reviser)
    conflicts = declare_plan_artifacts(
        live,
        ownership2,
        only_run_ids={"reviser"},
        completed_run_ids=set(),  # 故意不塞进度 completed
    )
    assert conflicts == []
    assert ownership2.owner_of("docs/overview.md") == "reviser"

    # Snapshot roundtrip keeps ended.
    restored = WriteCoordinator.from_dict(ownership2.to_dict())
    assert restored.is_ended("author-v1")


def test_conflict_message_unknown_and_ended_ban_user_transfer():
    from agentcore.workspace.write_claims import ownership_conflict_message

    ended = ownership_conflict_message(
        "docs/x.md",
        "author",
        ownership_kind="written",
        owner_status="ended",
    )
    assert "已结束" in ended
    assert "不要 escalate" in ended
    assert "用户卡可点" not in ended
    assert "transfer_ownership" not in ended

    unknown = ownership_conflict_message(
        "docs/x.md",
        "author",
        owner_status="unknown",
    )
    assert "未知" in unknown
    assert "不要 escalate" in unknown
    assert "用户卡可点" not in unknown
    assert "transfer_ownership" not in unknown
    # Running still offers structured user card.
    running = ownership_conflict_message(
        "docs/x.md",
        "author",
        owner_status="running",
    )
    assert "用户卡可点「移交写权」" in running
