"""约定文档 ``artifact_dir``：裸文件名 join 工作稿；空 artifacts 不钉目录；收口认盘。"""

from __future__ import annotations

from agentcore.runtime.runs.artifact_dir import (
    apply_artifact_dir_defaults,
    resolve_artifact_dir,
)
from agentcore.runtime.runs.builder import build_run_plan
from agentcore.runtime.runs.contract import check_contract, describe_deliverable
from agentcore.runtime.runs.types import Deliverable
from agentcore.workspace.stage_dirs import DRAFTS_DIR, RESEARCH_DIR, REVIEWS_DIR


def test_resolve_empty_artifacts_does_not_pin_drafts():
    d = Deliverable(form="files")
    assert resolve_artifact_dir(d) == ""


def test_resolve_bare_filename_pins_drafts():
    d = Deliverable(form="files", artifacts=["GDD.md"])
    assert resolve_artifact_dir(d) == DRAFTS_DIR


def test_resolve_workspace_native_has_no_dossier_landing():
    """盖上「工作区原生」→ 无落点；不盖的空 artifacts 也不钉工作稿。"""
    native = Deliverable(form="files", workspace_native=True)
    assert resolve_artifact_dir(native) == ""
    assert resolve_artifact_dir(Deliverable(form="files")) == ""


def test_workspace_native_outranks_declared_dossier_paths():
    """优先级最高：artifacts / artifact_dir 里的工作间路径也压不过它。"""
    d = Deliverable(
        form="files",
        artifact_dir=RESEARCH_DIR,
        artifacts=[f"{DRAFTS_DIR}/patch.py"],
        workspace_native=True,
    )
    assert resolve_artifact_dir(d) == ""


def test_apply_workspace_native_leaves_paths_untouched():
    """无落点 → 不填 artifact_dir、不把裸文件名搬进工作稿（worker 自己定位）。"""
    d = Deliverable(form="files", artifacts=["app.py"], workspace_native=True)
    apply_artifact_dir_defaults(d)
    assert d.artifact_dir == ""
    assert d.artifacts == ["app.py"]


def test_apply_workspace_native_clears_leftover_dir_when_paths_already_located():
    """已定位路径 + leftover 目录：native 赢，清 leftover，路径不动。"""
    located = [f"{DRAFTS_DIR}/patch.py"]
    d = Deliverable(
        form="files",
        artifact_dir=RESEARCH_DIR,
        artifacts=located,
        workspace_native=True,
    )
    apply_artifact_dir_defaults(d)
    assert d.workspace_native is True
    assert d.artifact_dir == ""
    assert d.artifacts == located


def test_apply_workspace_native_empty_artifacts_clears_leftover_dir():
    """写码节点误带 artifact_dir、无文件名合同 → leftover 清掉，不套默认工作稿。"""
    d = Deliverable(form="files", artifact_dir=RESEARCH_DIR, workspace_native=True)
    apply_artifact_dir_defaults(d)
    assert d.workspace_native is True
    assert d.artifact_dir == ""
    assert d.artifacts == []


def test_apply_workspace_does_not_join_leftover_artifact_dir():
    """workspace / native leftover 目录不得把裸名拧进约定文档路径。"""
    name = "前端刷新审计-对话页面.md"
    d = Deliverable(
        form="files",
        artifacts=[name],
        artifact_dir=REVIEWS_DIR,
        workspace_native=True,
    )
    apply_artifact_dir_defaults(d)
    assert d.form == "workspace"
    assert d.workspace_native is True
    assert d.artifact_dir == ""
    assert d.artifacts == [name]
    desc = describe_deliverable(d)
    assert name in desc
    assert "不要落进 `AgentCore/文档/`" not in desc
    assert f"建议约定文档落盘目录：`{REVIEWS_DIR}/`" not in desc
    assert f"落点目录：`{REVIEWS_DIR}/`" not in desc


def test_describe_workspace_native_omits_drafts_hint():
    """写码节点的任务书不得再出现「建议落工作稿/」这类误导。"""
    d = Deliverable(form="files", workspace_native=True)
    apply_artifact_dir_defaults(d)
    desc = describe_deliverable(d)
    assert DRAFTS_DIR not in desc
    assert "form=workspace" in desc


def test_resolve_skips_business_artifacts():
    d = Deliverable(form="files", artifacts=["site/index.html"])
    assert resolve_artifact_dir(d) == ""


def test_resolve_derives_from_existing_stage_artifact():
    d = Deliverable(
        form="files",
        artifacts=[f"{RESEARCH_DIR}/法律透镜报告.md"],
    )
    assert resolve_artifact_dir(d) == RESEARCH_DIR


def test_resolve_honors_explicit_artifact_dir():
    d = Deliverable(form="files", artifact_dir=RESEARCH_DIR)
    assert resolve_artifact_dir(d) == RESEARCH_DIR


def test_apply_fills_dir_prefix_and_relocates_bare_filename():
    d = Deliverable(form="files", artifacts=["miro-research.md"])
    apply_artifact_dir_defaults(d)
    assert d.artifact_dir == DRAFTS_DIR
    assert d.artifacts == [f"{DRAFTS_DIR}/miro-research.md"]


def test_apply_flattens_nested_drafts_artifact_name():
    d = Deliverable(form="files", artifacts=[f"{DRAFTS_DIR}/主题/01.md"])
    apply_artifact_dir_defaults(d)
    assert d.artifacts == [f"{DRAFTS_DIR}/主题_01.md"]


def test_apply_empty_artifacts_keeps_shared_dir_without_fake_artifact():
    """空 artifacts 不钉目录、不注入 artifacts 冒充归属键。"""
    d = Deliverable(form="files")
    apply_artifact_dir_defaults(d)
    assert d.artifact_dir == ""
    assert d.artifacts == []


def test_describe_mentions_artifact_dir_filename_only():
    d = Deliverable(form="files", artifact_dir=RESEARCH_DIR, artifacts=[])
    desc = describe_deliverable(d)
    assert f"落点目录：`{RESEARCH_DIR}/`" in desc
    assert "只定文件名" not in desc
    assert "勿写到工作区根" not in desc


def test_contract_landed_outside_artifact_dir_is_silent():
    """有落盘即过：仅 artifact_dir 未命中不发约定目录软提醒、不催搬。"""
    d = Deliverable(form="files", artifact_dir=RESEARCH_DIR, artifacts=[])
    root = check_contract(
        "已写",
        d,
        files_written=1,
        workspace_paths=["miro-research.md"],
    )
    assert root.ok
    assert not any("约定文档目录" in w for w in root.warnings)
    assert not any("勿写到工作区根" in w for w in root.warnings)

    ok = check_contract(
        "已写",
        d,
        files_written=1,
        workspace_paths=[f"{RESEARCH_DIR}/miro-research.md"],
    )
    assert ok.ok
    assert not any("约定文档目录" in w for w in ok.warnings)


def test_artifact_dir_mismatch_is_delivered_without_todo():
    """仅 artifact_dir 未命中且已落盘：认实际路径，不发 path_hint 待办，不挡 delivered。"""
    from agentcore.runtime.delegate.delivery_status import build_delivery_status
    from agentcore.runtime.runs.executor.shared import _delivery_gaps_from_warnings
    from agentcore.runtime.runs.file_acceptance import (
        REASON_PATH_MISMATCH,
        build_file_acceptance,
    )
    from agentcore.runtime.runs.plan import RunPlan
    from agentcore.runtime.runs.types import RunPhase, RunSpec, RunState

    d = Deliverable(form="files", artifact_dir=RESEARCH_DIR, artifacts=[])
    verdict = check_contract(
        "已写",
        d,
        files_written=1,
        workspace_paths=["miro-research.md"],
    )
    assert verdict.ok
    assert verdict.warnings == []
    gaps = _delivery_gaps_from_warnings(list(verdict.warnings), None)
    assert not any(g.get("reason") == "path_hint" for g in gaps)

    plan = RunPlan(
        nodes=[
            RunSpec(
                run_id="w1",
                task="调研 Miro",
                role="竞品分析师",
                deliverable=d,
            )
        ]
    )
    results = {
        "w1": RunState(
            phase=RunPhase.COMPLETED,
            content="ok",
            files_touched=["miro-research.md"],
            file_acceptance=build_file_acceptance(
                ["miro-research.md"], phase=RunPhase.COMPLETED
            ),
            warnings=list(verdict.warnings),
            delivery_gaps=gaps,
        )
    }
    payload = build_delivery_status(plan, results, execution_id="e-adir-pipe")
    assert payload is not None
    assert payload["state"] == "delivered"
    assert "miro-research.md" in payload["delivered_files"]
    assert not any(g.get("reason") == REASON_PATH_MISMATCH for g in payload["gaps"])


def test_build_run_plan_empty_files_does_not_inject_drafts_dir():
    """无显式路径的 files 交付 → 不钉工作稿，也不按 role·task 猜 research。"""
    plan, errors = build_run_plan(
        [
            {
                "role": "竞品分析师",
                "task": "调研 Excalidraw 竞品并落盘笔记",
                "deliverable": {"form": "files"},
            }
        ]
    )
    assert errors == []
    d = plan.nodes[0].deliverable
    assert d is not None
    assert d.artifact_dir == ""
    assert d.artifacts == []
    desc = describe_deliverable(d)
    assert "form=files" in desc
    assert DRAFTS_DIR not in desc


def test_build_run_plan_workspace_native_skips_default_drafts_dir():
    """派单两态对照：盖上 → 无落点；同一批不盖的空 artifacts 也不钉工作稿。"""
    plan, errors = build_run_plan(
        [
            {
                "role": "后端工程师",
                "task": "实现登录接口",
                "deliverable": {"form": "files", "workspace_native": True},
            },
            {
                "role": "竞品分析师",
                "task": "调研并落盘笔记",
                "deliverable": {"form": "files"},
            },
        ]
    )
    assert errors == []
    coder, researcher = (n.deliverable for n in plan.nodes)
    assert coder is not None and researcher is not None
    assert coder.form == "workspace"
    assert coder.workspace_native is True
    assert coder.artifact_dir == ""
    assert researcher.artifact_dir == ""


def test_build_run_plan_omit_form_is_files_without_drafts_dir():
    plan, errors = build_run_plan([{"role": "写手", "task": "写笔记"}])
    assert errors == []
    d = plan.nodes[0].deliverable
    assert d is not None
    assert d.form == "files"
    assert d.artifact_dir == ""


def test_build_run_plan_workspace_form_skips_default_drafts_dir():
    plan, errors = build_run_plan(
        [
            {
                "role": "后端工程师",
                "task": "实现登录接口",
                "deliverable": {"form": "workspace"},
            }
        ]
    )
    assert errors == []
    d = plan.nodes[0].deliverable
    assert d is not None
    assert d.form == "workspace"
    assert d.artifact_dir == ""
    assert d.workspace_native is True


def test_build_run_plan_workspace_does_not_join_leftover_review_dir():
    """生产组合经 builder：裸名 + leftover reviews 目录 + native → 目录清掉，裸名保留。"""
    name = "前端刷新审计-对话页面.md"
    plan, errors = build_run_plan(
        [
            {
                "role": "审查官",
                "task": "审对话页刷新",
                "deliverable": {
                    "form": "files",
                    "artifacts": [name],
                    "artifact_dir": REVIEWS_DIR,
                    "workspace_native": True,
                },
            }
        ]
    )
    assert errors == []
    d = plan.nodes[0].deliverable
    assert d is not None
    assert d.form == "workspace"
    assert d.workspace_native is True
    assert d.artifact_dir == ""
    assert d.artifacts == [name]


def test_ceo_schema_exposes_three_tier_form_only():
    """CEO 填参面只有三档 form + artifacts；内部旋钮不进 schema。"""
    from agentcore.tools.builtin.delegate.schema import TASK_DELIVERABLE_SCHEMA

    props = TASK_DELIVERABLE_SCHEMA["properties"]
    assert props["form"]["enum"] == ["prose", "files", "workspace"]
    assert set(props) == {"form", "artifacts"}
    for banned in (
        "required_sections",
        "output_format",
        "strict",
        "citation_mode",
        "workspace_native",
        "artifact_dir",
    ):
        assert banned not in props


def test_shared_artifact_dir_not_sibling_cross():
    """同批只共享约定文档目录、无文件级 artifacts → 不触发 sibling 交叉。"""
    from agentcore.runtime.coordination.append_guard import find_sibling_artifact_crosses

    plan, errors = build_run_plan(
        [
            {
                "role": "成本模型研究员",
                "task": "调研 API 定价",
                "deliverable": {"form": "files", "artifact_dir": RESEARCH_DIR},
            },
            {
                "role": "系统架构研究员",
                "task": "调研调度优化",
                "deliverable": {"form": "files", "artifact_dir": RESEARCH_DIR},
            },
        ]
    )
    assert errors == []
    assert all(n.deliverable and n.deliverable.artifacts == [] for n in plan.nodes)
    assert find_sibling_artifact_crosses(plan) == []


def test_same_file_artifact_still_sibling_cross():
    from agentcore.runtime.coordination.append_guard import find_sibling_artifact_crosses

    plan, errors = build_run_plan(
        [
            {
                "role": "前端",
                "task": "写 App",
                "deliverable": {"form": "files", "artifacts": ["src/App.tsx"]},
            },
            {
                "role": "整合",
                "task": "也写 App",
                "deliverable": {"form": "files", "artifacts": ["src/App.tsx"]},
            },
        ]
    )
    assert errors == []
    hits = find_sibling_artifact_crosses(plan)
    assert len(hits) == 1
    assert hits[0].reason == "sibling_artifact"


def test_build_run_plan_leaves_website_artifacts_alone():
    plan, errors = build_run_plan(
        [
            {
                "role": "前端工程师",
                "task": "实现首页",
                "deliverable": {
                    "form": "files",
                    "artifacts": ["site/index.html"],
                },
            }
        ]
    )
    assert errors == []
    d = plan.nodes[0].deliverable
    assert d is not None
    assert d.artifact_dir == ""
    assert d.artifacts == ["site/index.html"]


def _plan_artifact_dir(task: str, role: str = "竞品分析师") -> str:
    plan, errors = build_run_plan(
        [{"role": role, "task": task, "deliverable": {"form": "files"}}]
    )
    assert errors == []
    d = plan.nodes[0].deliverable
    assert d is not None
    return d.artifact_dir


def test_landing_never_reads_role_or_task_free_text():
    """研究/审查字样只在 role·task 里出现 → 不得把产物钉进 research / reviews。

    落点推断整链已净删除；``resolve_artifact_dir`` 连读 role·task 的入口都没有。
    """
    for task in (
        "调研 Miro 并落盘笔记",
        "审查后端方案并写审查报告",
        f"阅读 `{RESEARCH_DIR}/旧笔记.md` 后继续调研竞品并落盘",
    ):
        assert _plan_artifact_dir(task) == ""
    assert _plan_artifact_dir("审查后端方案", role="审查官") == ""


def test_resolve_keeps_explicit_dossier_artifacts():
    d = Deliverable(
        form="files",
        artifacts=[f"{RESEARCH_DIR}/调研笔记.md"],
    )
    assert resolve_artifact_dir(d) == RESEARCH_DIR


def test_build_run_plan_coding_brief_with_research_path_no_artifact_dir():
    plan, errors = build_run_plan(
        [
            {
                "role": "UX 系统工程师",
                "task": (
                    "根据 AgentCore/文档/research/法庭迷局/UX系统设计.md "
                    "实现 src/ui 交互系统"
                ),
                "deliverable": {
                    "form": "files",
                    "artifacts": ["src/ui/nav_system.ts"],
                },
            }
        ],
    )
    assert errors == []
    d = plan.nodes[0].deliverable
    assert d is not None
    assert d.artifact_dir == ""
    assert d.artifacts == ["src/ui/nav_system.ts"]


_AI_DEV_DIR = "AgentCore/文档/AI开发"


def test_resolve_derives_custom_docs_subtree_from_artifacts():
    """写手声明 AI开发/ 产物时，核对目录跟 artifacts，不钉 research。"""
    artifacts = [
        f"{_AI_DEV_DIR}/00-导航与任务路由.md",
        f"{_AI_DEV_DIR}/01-仓库地图.md",
        f"{_AI_DEV_DIR}/04-开发约定与禁忌.md",
    ]
    d = Deliverable(form="files", artifacts=artifacts)
    assert resolve_artifact_dir(d) == _AI_DEV_DIR


def test_apply_overrides_mismatched_research_artifact_dir():
    """显式/默认 artifact_dir=research 但 artifacts 在 AI开发/ → 纠正对齐。"""
    artifacts = [
        f"{_AI_DEV_DIR}/00-导航与任务路由.md",
        f"{_AI_DEV_DIR}/03-架构与数据流.md",
    ]
    d = Deliverable(
        form="files",
        artifact_dir=RESEARCH_DIR,
        artifacts=artifacts,
    )
    apply_artifact_dir_defaults(d)
    assert d.artifact_dir == _AI_DEV_DIR
    assert d.artifacts == artifacts


def test_writer_ai_dev_no_false_path_hint_while_notes_stay_research():
    """回归：写手落 AI开发/ + 调研笔记落 research/ —— 不因写手误钉 research 冒假缺口。"""
    from agentcore.runtime.runs.executor.shared import _delivery_gaps_from_warnings

    writer_artifacts = [
        f"{_AI_DEV_DIR}/00-导航与任务路由.md",
        f"{_AI_DEV_DIR}/01-仓库地图.md",
    ]
    writer = Deliverable(
        form="files",
        artifact_dir=RESEARCH_DIR,  # 复现钉错
        artifacts=writer_artifacts,
    )
    apply_artifact_dir_defaults(writer)
    assert writer.artifact_dir == _AI_DEV_DIR

    writer_verdict = check_contract(
        "已写",
        writer,
        files_written=len(writer_artifacts),
        workspace_paths=list(writer_artifacts),
    )
    assert writer_verdict.ok
    assert not any("约定文档目录" in w for w in writer_verdict.warnings)
    assert not any(
        g.get("reason") == "path_hint"
        for g in _delivery_gaps_from_warnings(list(writer_verdict.warnings), None)
    )

    note = Deliverable(
        form="files",
        artifacts=[f"{RESEARCH_DIR}/ai-dev-docs-文档侧笔记.md"],
    )
    apply_artifact_dir_defaults(note)
    assert note.artifact_dir == RESEARCH_DIR
    note_verdict = check_contract(
        "已写",
        note,
        files_written=1,
        workspace_paths=[f"{RESEARCH_DIR}/ai-dev-docs-文档侧笔记.md"],
    )
    assert note_verdict.ok
    assert not any("约定文档目录" in w for w in note_verdict.warnings)


def test_build_run_plan_writer_custom_docs_dir_aligns_artifact_dir():
    plan, errors = build_run_plan(
        [
            {
                "role": "文档写手",
                "task": "根据调研笔记撰写便于 AI 开发的文档",
                "deliverable": {
                    "form": "files",
                    "artifact_dir": RESEARCH_DIR,
                    "artifacts": [
                        f"{_AI_DEV_DIR}/00-导航与任务路由.md",
                        f"{_AI_DEV_DIR}/01-仓库地图.md",
                    ],
                },
            }
        ]
    )
    assert errors == []
    d = plan.nodes[0].deliverable
    assert d is not None
    assert d.artifact_dir == _AI_DEV_DIR
    assert all(a.startswith(f"{_AI_DEV_DIR}/") for a in d.artifacts)
