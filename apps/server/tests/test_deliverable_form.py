"""Unit tests for deliverable.form (prose | files) on the delegate contract path."""

from __future__ import annotations

from agentcore.runtime.runs.builder import build_run_plan
from agentcore.runtime.runs.contract import describe_deliverable
from agentcore.runtime.runs.executor.identities import build_worker_identity
from agentcore.runtime.runs.types import Deliverable
from agentcore.tools.builtin.delegate.schema import (
    DELEGATE_DESCRIPTION,
    DELEGATE_PARAMETERS,
    TASK_DELIVERABLE_SCHEMA,
)
from agentcore.workspace.stage_dirs import DRAFTS_DIR


def test_form_parsed_onto_deliverable():
    plan, errs = build_run_plan(
        [{"role": "A", "task": "打招呼", "deliverable": {"form": "prose"}}],
        id_prefix="t",
    )
    assert errs == []
    d = plan.nodes[0].deliverable
    assert d is not None
    assert d.form == "prose"


def test_form_files_is_write_disk():
    plan, errs = build_run_plan(
        [{"role": "A", "task": "建站", "deliverable": {"form": "files"}}],
        id_prefix="t",
    )
    assert errs == []
    d = plan.nodes[0].deliverable
    assert d is not None
    assert d.form == "files"


def test_form_workspace_parsed():
    plan, errs = build_run_plan(
        [{"role": "A", "task": "改登录", "deliverable": {"form": "workspace"}}],
        id_prefix="t",
    )
    assert errs == []
    d = plan.nodes[0].deliverable
    assert d is not None
    assert d.form == "workspace"
    assert d.workspace_native is True
    assert d.artifact_dir == ""


def test_form_alone_is_enough_content():
    plan, errs = build_run_plan(
        [{"role": "A", "task": "a", "deliverable": {"form": "prose"}}],
        id_prefix="t",
    )
    assert errs == []
    assert plan.nodes[0].deliverable is not None

def test_form_prose_rejects_artifacts():
    """D1: raw form=prose ∩ non-empty artifacts must hard-reject (gate before clear)."""
    plan, errs = build_run_plan(
        [
            {
                "role": "A",
                "task": "a",
                "deliverable": {
                    "form": "prose",
                    "artifacts": ["hello.md"],
                },
            }
        ],
        id_prefix="t",
    )
    assert errs
    assert any("form=prose" in e and "artifacts" in e for e in errs)
    assert plan.nodes == [] or not plan.nodes


def test_form_prose_ignores_legacy_requires_files_key():
    """Unknown requires_files is not consumed; prose alone still builds."""
    plan, errs = build_run_plan(
        [
            {
                "role": "A",
                "task": "a",
                "deliverable": {
                    "form": "prose",
                    "requires_files": True,
                },
            }
        ],
        id_prefix="t",
    )
    assert errs == []
    d = plan.nodes[0].deliverable
    assert d is not None
    assert d.form == "prose"
    assert d.artifacts == []
def test_form_prose_alone_still_builds():
    plan, errs = build_run_plan(
        [
            {
                "role": "A",
                "task": "a",
                "deliverable": {"form": "prose"},
            }
        ],
        id_prefix="t",
    )
    assert errs == []
    d = plan.nodes[0].deliverable
    assert d is not None
    assert d.form == "prose"
    assert d.artifacts == []

def test_invalid_form_defaults_to_files():
    plan, _ = build_run_plan(
        [{"role": "A", "task": "a", "deliverable": {"form": "slides", "name": "x"}}],
        id_prefix="t",
    )
    d = plan.nodes[0].deliverable
    assert d is not None
    assert d.form == "files"


def test_identity_form_prose_has_no_file_write_guidance():
    prose = build_worker_identity(has_dependents=False, form="prose")
    files = build_worker_identity(has_dependents=False, form="files")
    omitted = build_worker_identity(has_dependents=False, form=None)
    workspace = build_worker_identity(has_dependents=False, form="workspace")

    assert "form=prose" in prose
    assert "file_write" not in prose
    assert "纯文字" in prose

    assert "form=files" in files
    assert "file_write" in files
    assert "必须" in files
    # 压缩后的落盘纪律（identity，不只 CEO skill）。
    assert "artifact manifest" in files
    assert "禁止" in files and "file_read" in files
    assert "Artifact-first" not in files
    assert "落盘与修订" not in files
    assert "consult(long_form_landing)" in files
    assert "consult(long_form_landing)" in omitted
    assert "consult(long_form_landing)" not in prose
    assert "consult(data_file_landing)" in files
    assert "consult(data_file_landing)" in omitted
    assert "consult(data_file_landing)" not in prose
    assert "consult(verify_and_fix)" in files
    assert "consult(verify_and_fix)" in omitted
    assert "consult(verify_and_fix)" not in prose

    # omit = files（无双向自判）
    assert "form=files" in omitted
    assert "可独立阅读的文字" not in omitted
    assert "file_write" in omitted
    assert "artifact manifest" in omitted
    assert "禁止" in omitted and "file_read" in omitted
    assert "Artifact-first" not in omitted
    assert "落盘与修订" not in omitted

    assert "form=workspace" in workspace
    assert "改工程" in workspace
    assert "AgentCore/文档" in workspace
    assert "file_write" in workspace
    assert "consult(long_form_landing)" in workspace
    assert "consult(verify_and_fix)" in workspace

def test_artifacts_inject_files_form_identity_block():
    """非空 artifacts 且 form 省略 ⇒ 强制 files 形态提示，非 legacy。"""
    by_artifacts = build_worker_identity(
        has_dependents=False, artifacts=["report.md"]
    )
    assert "form=files" in by_artifacts
    assert "落盘文件" in by_artifacts

    # Omit form + empty artifacts → files（漏填默认）。
    by_omit = build_worker_identity(has_dependents=False)
    assert "form=files" in by_omit

    # Explicit prose still wins.
    prose_wins = build_worker_identity(
        has_dependents=False, form="prose", artifacts=["x.md"]
    )
    assert "form=prose" in prose_wins
    assert "form=files" not in prose_wins


def test_identity_prose_body_floor_copy_nonempty():
    up = build_worker_identity(has_dependents=True, form="prose")
    assert "非空即可" in up
    assert "min_length" not in up

def test_identity_handoff_topology_preserved_with_form():
    up = build_worker_identity(has_dependents=True, form="prose")
    leaf = build_worker_identity(has_dependents=False, form="prose")
    assert "必须调用 handoff" in up
    assert "不必为交而交" in leaf
    assert "必须调用 handoff" not in leaf
    # 交付真相项4：有下游 prose — summary 不算 body；leaf 不抬此提示
    assert "不算正文" in up
    assert "不算正文" not in leaf

def test_describe_deliverable_form_split():
    prose = describe_deliverable(Deliverable(form="prose"))
    assert "纯文字" in prose
    assert "file_write" not in prose

    files = describe_deliverable(Deliverable(form="files"))
    assert "落盘" in files
    assert "file_write" in files

    workspace = describe_deliverable(Deliverable(form="workspace"))
    assert "改工程" in workspace
    assert "AgentCore/文档" in workspace
    assert DRAFTS_DIR not in workspace

def test_schema_exposes_form_enum():
    props = TASK_DELIVERABLE_SCHEMA["properties"]
    assert "form" in props
    assert props["form"]["enum"] == ["prose", "files", "workspace"]
    assert "workspace" in props["form"]["description"]
    assert "prose" in props["form"]["description"]
    assert "【看】" in props["form"]["description"]
    assert "【看】" not in DELEGATE_DESCRIPTION
    assert "【存文档】" not in DELEGATE_DESCRIPTION
    assert "【改工程】" not in DELEGATE_DESCRIPTION
    assert "才用本工具" not in DELEGATE_DESCRIPTION
    # 何时用写在 description（行业：when-to-use 在工具面）；编制闭集不进按钮。
    assert "改产物" in DELEGATE_DESCRIPTION
    assert "成规模查证" in DELEGATE_DESCRIPTION
    assert "闲聊" in DELEGATE_DESCRIPTION
    assert "用：" not in DELEGATE_DESCRIPTION
    assert "不用：" not in DELEGATE_DESCRIPTION
    assert "跨模块" not in DELEGATE_DESCRIPTION
    assert "点名对比" not in DELEGATE_DESCRIPTION
    assert "编制自选" not in DELEGATE_DESCRIPTION
    assert "结局分层" not in DELEGATE_DESCRIPTION
    assert "playbook_args.app" not in DELEGATE_DESCRIPTION
    assert "build_app" not in DELEGATE_DESCRIPTION
    assert "建站→build_website" not in DELEGATE_DESCRIPTION
    assert "建站→build_website" not in DELEGATE_PARAMETERS["properties"]["playbook"]["description"]
    assert "二选一" in DELEGATE_DESCRIPTION
    assert "既填 code_audit 又传 tasks" not in DELEGATE_DESCRIPTION
    assert "HOW→consult(team_orchestration_advanced)" in DELEGATE_DESCRIPTION
    # 协调 / 一张图 / 续派 HOW 的唯一所有者是 skill，不在工具 description。
    from agentcore.runtime.skills import build_system_skill_registry

    orch = build_system_skill_registry().get("team_orchestration_advanced")
    assert orch is not None
    orch_body = orch.body
    assert "立即返回" in orch_body
    assert "立即返回" not in DELEGATE_DESCRIPTION
    assert "一回合一张协作图" in orch_body or "同一张图" in orch_body
    assert "一张图" not in DELEGATE_DESCRIPTION
    # 弱模型空失败可抄：顶层非空 tasks 三件套骨架（与 empty 拒收同源）只留参数面。
    from agentcore.runtime.delegate.playbook_declaration import HANDWRITTEN_TASKS_SKELETON

    assert HANDWRITTEN_TASKS_SKELETON not in DELEGATE_DESCRIPTION
    assert "默认" in DELEGATE_DESCRIPTION
    assert "手写顶层 tasks" in DELEGATE_DESCRIPTION or "默认手写" in DELEGATE_DESCRIPTION
    assert "快捷进阶" in DELEGATE_DESCRIPTION or "固化流水线" in DELEGATE_DESCRIPTION
    tasks_desc = DELEGATE_PARAMETERS["properties"]["tasks"]["description"]
    assert HANDWRITTEN_TASKS_SKELETON in tasks_desc
    assert "摸底抄骨架" in tasks_desc
    assert "摸底抄骨架" not in DELEGATE_DESCRIPTION
    assert "默认主路" in tasks_desc
    assert "手写" in tasks_desc and "互斥" in tasks_desc
    playbook_desc = DELEGATE_PARAMETERS["properties"]["playbook"]["description"]
    assert "不要传 tasks" in playbook_desc
    assert "非默认" in playbook_desc or "进阶" in playbook_desc or "快捷" in playbook_desc
    assert "build_app" not in playbook_desc
    assert "playbook_id" not in DELEGATE_PARAMETERS["properties"]
    assert "parallelism" not in DELEGATE_PARAMETERS["properties"]
    pa = DELEGATE_PARAMETERS["properties"]["playbook_args"]["description"]
    assert "build_app" not in pa
    assert "绿场必填 app" not in pa
    assert "build_app→app" not in pa
    assert "建站→build_website" not in pa
    assert "快捷" in pa or "手写" in pa
    # code_audit.modules 必须出现在 CEO 工具面（扇出靠填槽，不从 scope 推断）
    assert "code_audit" in pa and "modules" in pa
    assert "不从 scope 自动拆" in pa
    deps = DELEGATE_PARAMETERS["properties"]["tasks"]["items"]["properties"]["depends_on"][
        "description"
    ]
    assert "本批 id" in deps or "同回合" in deps
    assert ("角色名" in deps or "role" in deps) and "del_*" in deps
    props_task = DELEGATE_PARAMETERS["properties"]["tasks"]["items"]["properties"]
    assert "require_upstream" not in props_task
    assert "retrieval_budget" not in props_task  # CEO 不可配置；额度走结构化默认
    # 已确认约束钉在 task / deliverable；team_brief 只填共享口径，可省略。
    assert "已确认约束" in props_task["task"]["description"]
    assert "已确认约束" in str(TASK_DELIVERABLE_SCHEMA.get("description") or "")
    brief = DELEGATE_PARAMETERS["properties"]["team_brief"]["description"]
    assert "共享口径" in brief
    assert "省略" in brief
    assert "便签墙" not in brief
    assert "换行" not in brief

    assert "coordinate" not in DELEGATE_PARAMETERS["properties"]
    assert "coordination" not in DELEGATE_PARAMETERS["properties"]
    assert "complexity_hint" not in DELEGATE_PARAMETERS["properties"]
    assert "checkpoint_after" not in props_task
    assert "bind_after_deps" not in props_task
    assert "result_handling" not in props_task
    cf = props_task["continue_from_run_id"]["description"]
    assert "同人" in cf or "续派" in cf
    assert "调查" in cf or "改稿" in cf
    # 真纯丙：CEO schema 不再提供 tools 白名单开关。
    assert "tools" not in props_task
    # 假辩论通道关死：CEO 不可经 delegate.tasks 写 stance/group/round。
    assert "stance" not in props_task
    assert "group" not in props_task
    assert "round" not in props_task
    # 已删 A+B+C 字段：schema 不再暴露。
    assert "must_contain" not in props
    assert "min_length" not in props
    assert "requires_files" not in props
    assert "name" not in props
    assert "objective" not in props_task
    assert "playbook_none_reason" not in DELEGATE_PARAMETERS["properties"]
    for banned in (
        "required_sections",
        "output_format",
        "strict",
        "citation_mode",
        "workspace_native",
        "artifact_dir",
        "web_quality_scan",
        "code_audit_gate",
    ):
        assert banned not in props

def test_schema_depends_on_teaches_when_to_declare_dependency():
    # 工具面瘦身：【何时填】长引导（生产者→消费者 + 正反例）在 skill；
    # 参数只留「怎么填」短指针，工具 description 不再抄 HOW。
    deps = DELEGATE_PARAMETERS["properties"]["tasks"]["items"]["properties"]["depends_on"][
        "description"
    ]
    assert "本批 id" in deps and "del_*" in deps
    assert "角色名" in deps or "role" in deps
    assert "生产者→消费者" in deps  # 短指针，细节在 skill
    assert "新开一队" in deps
    assert "append_to_execution_id" not in deps
    from agentcore.runtime.skills import build_system_skill_registry

    orch = build_system_skill_registry().get("team_orchestration_advanced")
    assert orch is not None
    orch_body = orch.body
    assert "生产者→消费者" in orch_body
    assert "平铺并行" in orch_body
    assert "新开一队、接续上一张图" in orch_body
    assert "生产者→消费者" not in DELEGATE_DESCRIPTION
    assert "平铺并行" not in DELEGATE_DESCRIPTION
    assert "新开一队、接续上一张图" not in DELEGATE_DESCRIPTION
    append = DELEGATE_PARAMETERS["properties"]["append_to_execution_id"]["description"]
    assert "latest" in append and "一张图" in append

async def test_prose_worker_still_offered_write_tools():
    """真纯丙·H2：form=prose 仍装配写盘工具；identity 仍提示正文交付。"""
    from agentcore.runtime.events import EventSink
    from agentcore.runtime.runs.executor import build_agent_executor
    from agentcore.runtime.runs.types import RunPhase
    from agentcore.runtime.runs.wave import WaveScheduler
    from agentcore.tools.registry import ToolRegistry
    from tests.runs_executor.conftest import (
        _ContentProvider,
        _ctx,
        _GrantableTool,
        _OfferRecorder,
    )

    tasks = [{"role": "A", "task": "打招呼", "deliverable": {"form": "prose"}}]
    plan, _ = build_run_plan(tasks, id_prefix="t")
    reg = ToolRegistry()
    for name in ("file_write", "file_append", "str_replace", "file_read", "code_execute"):
        reg.register(_GrantableTool(name))
    provider = _OfferRecorder()
    executor = build_agent_executor(
        plan=plan,
        llm=provider,
        tools=reg,
        sink=EventSink(),
        base_tool_context=_ctx(),
        system_prompt="SYS",
        user_message="让每个 AI 打招呼",
        execution_id="e",
        approval_gate=None,
    )
    res = await WaveScheduler().run(plan, executor)
    assert res["t_1"].phase is RunPhase.COMPLETED
    offered = set(provider.offered[0])
    assert "file_write" in offered
    assert "file_append" in offered
    assert "str_replace" in offered
    assert "file_read" in offered
    assert "code_execute" in offered

    plan2, _ = build_run_plan(tasks, id_prefix="u")
    id_provider = _ContentProvider(["HI"])
    id_exec = build_agent_executor(
        plan=plan2,
        llm=id_provider,
        tools=reg,
        sink=EventSink(),
        base_tool_context=_ctx(),
        system_prompt="SYS",
        user_message="让每个 AI 打招呼",
        execution_id="e2",
        approval_gate=None,
    )
    await WaveScheduler().run(plan2, id_exec)
    assert "form=prose" in id_provider.system_messages[0]
    assert "file_write" not in id_provider.system_messages[0]

async def test_files_worker_keeps_write_tools_and_identity():
    from agentcore.llm.provider.protocol import LLMChunk, ToolCallDelta
    from agentcore.runtime.events import EventSink
    from agentcore.runtime.runs.executor import build_agent_executor
    from agentcore.runtime.runs.types import RunPhase
    from agentcore.runtime.runs.wave import WaveScheduler
    from agentcore.tools.registry import ToolRegistry
    from tests.runs_executor.conftest import (
        _ctx,
        _FileWriteTool,
        _ScriptedRounds,
    )

    plan, _ = build_run_plan(
        [{"role": "A", "task": "建页面", "deliverable": {"form": "files"}}],
        id_prefix="t",
    )
    reg = ToolRegistry()
    reg.register(_FileWriteTool())
    # form=files：须真实落盘才能 COMPLETED（交付真相）。
    rounds = [
        [
            LLMChunk(
                delta_tool_calls=[
                    ToolCallDelta(
                        index=0,
                        id="c1",
                        function_name="file_write",
                        arguments_delta='{"path": "index.html", "content": "<html></html>"}',
                    )
                ]
            )
        ],
        [LLMChunk(delta_content="已写入")],
    ]
    provider = _ScriptedRounds(rounds)
    executor = build_agent_executor(
        plan=plan,
        llm=provider,
        tools=reg,
        sink=EventSink(),
        base_tool_context=_ctx(),
        system_prompt="SYS",
        user_message="做一个网页",
        execution_id="e",
        approval_gate=None,
    )
    res = await WaveScheduler().run(plan, executor)
    assert res["t_1"].phase is RunPhase.COMPLETED
    assert "form=files" in provider.system_messages[0]
    assert "file_write" in provider.system_messages[0]

async def test_cold_start_pending_allows_single_worker_delegate():
    """pending ∧ 1 worker：不再因节点数拒（组队靠提示词）。"""
    from tests.delegate.conftest import Provider, ctx, tool

    t = tool(Provider(["结构笔记"]))
    t._base_tool_context.cold_start_explore_pending = True
    result = await t.execute(
        {
            "tasks": [
                {
                    "role": "调研",
                    "task": "摸清项目结构",
                    "deliverable": {"form": "prose"},
                }
            ],
            "coordinate": False,
        },
        ctx(),
    )
    assert result.success is True
    assert result.contract_failure is not True
    err = result.error or ""
    assert "≥2" not in err
    assert "包办" not in err
    assert "至少两" not in err

def test_cold_start_allows_artifacts():
    """裸 artifacts 文件名仍迁入默认落点（与节点数闸无关）。"""
    plan, errs = build_run_plan(
        [
            {
                "role": "调研",
                "task": "摸清项目",
                "deliverable": {"artifacts": ["brief.md"]},
            },
            {"role": "B", "task": "读 README", "deliverable": {"form": "prose"}},
        ],
        id_prefix="t",
    )
    assert errs == []
    assert plan.nodes[0].deliverable is not None
    # 裸文件名迁入默认落点（无显式路径 → 工作稿/）。
    assert plan.nodes[0].deliverable.artifacts == ["AgentCore/文档/工作稿/brief.md"]

