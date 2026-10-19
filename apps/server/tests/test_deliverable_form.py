"""Landing predicate: pinned artifacts / artifact_dir only. No form enum."""

from __future__ import annotations

from agentcore.runtime.delegate.empty_tasks import (
    EMPTY_DELEGATE_MSG,
    HANDWRITTEN_TASKS_SKELETON,
)
from agentcore.runtime.runs.builder import build_run_plan
from agentcore.runtime.runs.contract import describe_deliverable, is_file_deliverable
from agentcore.runtime.runs.types import (
    Deliverable,
    deliverable_expects_landing,
    raw_deliverable_expects_landing,
    task_raw_expects_landing,
)
from agentcore.tools.builtin.delegate.schema import (
    DELEGATE_DESCRIPTION,
    DELEGATE_PARAMETERS,
    TASK_ARTIFACTS_SCHEMA,
)


def test_deliverable_default_does_not_expect_landing():
    d = Deliverable()
    assert deliverable_expects_landing(d) is False
    assert deliverable_expects_landing(None) is False
    assert is_file_deliverable(d) is False


def test_nonempty_artifacts_expect_landing():
    d = Deliverable(artifacts=["report.md"])
    assert deliverable_expects_landing(d) is True
    assert is_file_deliverable(d) is True


def test_nonempty_artifact_dir_expects_landing():
    d = Deliverable(artifact_dir="AgentCore/文档/research")
    assert deliverable_expects_landing(d) is True


def test_raw_omitted_empty_does_not_expect_landing():
    assert raw_deliverable_expects_landing(None) is False
    assert raw_deliverable_expects_landing({}) is False
    assert raw_deliverable_expects_landing("x") is False
    assert raw_deliverable_expects_landing({"form": "files"}) is False
    assert raw_deliverable_expects_landing({"form": "prose"}) is False
    assert raw_deliverable_expects_landing({"form": "workspace"}) is False
    assert raw_deliverable_expects_landing({"workspace_native": True}) is False
    assert raw_deliverable_expects_landing({"artifacts": ["a.md"]}) is True
    assert raw_deliverable_expects_landing({"artifact_dir": "docs"}) is True
    assert raw_deliverable_expects_landing({"artifacts": ["  "]}) is False


def test_task_top_level_artifacts_expect_landing():
    assert task_raw_expects_landing({"artifacts": ["a.md"]}) is True
    assert task_raw_expects_landing({"artifacts": []}) is False
    assert task_raw_expects_landing({"deliverable": {"artifacts": ["a.md"]}}) is True
    plan, errs = build_run_plan(
        [{"role": "A", "task": "写报告", "artifacts": ["note.md"]}],
    )
    assert errs == []
    d = plan.nodes[0].deliverable
    assert d.artifacts == ["note.md"]
    assert deliverable_expects_landing(d) is True


def test_leftover_form_key_is_discarded_not_translated():
    plan, errs = build_run_plan(
        [{"role": "A", "task": "打招呼", "deliverable": {"form": "prose"}}],
    )
    assert errs == []
    d = plan.nodes[0].deliverable
    assert d is not None
    assert not hasattr(d, "form") or not getattr(d, "form", None)
    assert deliverable_expects_landing(d) is False


def test_leftover_form_files_without_artifacts_does_not_expect_landing():
    plan, errs = build_run_plan(
        [{"role": "A", "task": "建站", "deliverable": {"form": "files"}}],
    )
    assert errs == []
    d = plan.nodes[0].deliverable
    assert d is not None
    assert deliverable_expects_landing(d) is False


def test_artifacts_survive_leftover_form_key():
    plan, errs = build_run_plan(
        [
            {
                "role": "A",
                "task": "写报告",
                "deliverable": {"form": "prose", "artifacts": ["note.md"]},
            }
        ],
    )
    assert errs == []
    d = plan.nodes[0].deliverable
    assert d is not None
    assert d.artifacts == ["note.md"]
    assert deliverable_expects_landing(d) is True


def test_unknown_json_keys_do_not_fail_build():
    plan, errs = build_run_plan(
        [
            {
                "role": "A",
                "task": "a",
                "deliverable": {
                    "form": "slides",
                    "name": "x",
                    "workspace_native": True,
                },
            }
        ],
    )
    assert errs == []
    d = plan.nodes[0].deliverable
    assert d is not None
    assert deliverable_expects_landing(d) is False


def test_describe_deliverable_empty_without_instance_facts():
    assert describe_deliverable(None) == ""
    assert describe_deliverable(Deliverable()) == ""
    assert "form=" not in describe_deliverable(Deliverable(artifacts=["a.md"]))
    assert "【" not in describe_deliverable(Deliverable())
    desc = describe_deliverable(Deliverable(artifacts=["report.md"]))
    assert "report.md" in desc
    assert "交付路径" in desc


def test_ceo_schema_advertises_task_artifacts():
    task_props = DELEGATE_PARAMETERS["properties"]["tasks"]["items"]["properties"]
    assert set(task_props) == {
        "role",
        "task",
        "artifacts",
        "id",
        "depends_on",
        "replaces_run_id",
        "continue_from_run_id",
        "target_folder_id",
        "model",
    }
    assert TASK_ARTIFACTS_SCHEMA["type"] == "array"
    assert "form=prose" not in (TASK_ARTIFACTS_SCHEMA.get("description") or "")
    arts = TASK_ARTIFACTS_SCHEMA.get("description") or ""
    assert "流水线写死" not in arts
    assert "不催写盘" in arts
    tasks_desc = DELEGATE_PARAMETERS["properties"]["tasks"]["description"]
    assert HANDWRITTEN_TASKS_SKELETON not in tasks_desc
    assert "可抄" not in tasks_desc
    assert HANDWRITTEN_TASKS_SKELETON in EMPTY_DELEGATE_MSG
    assert "省略即可" not in DELEGATE_PARAMETERS["properties"]["team_brief"][
        "description"
    ]
    assert "【看】" not in DELEGATE_DESCRIPTION
    assert "【存文档】" not in DELEGATE_DESCRIPTION
    assert "【改工程】" not in DELEGATE_DESCRIPTION
    assert "摸底抄骨架" not in DELEGATE_DESCRIPTION
    from agentcore.tools.builtin.delegate.schema import DELEGATE_DESCRIPTION_PREFER

    assert "【看】" not in DELEGATE_DESCRIPTION_PREFER
    assert "摸底抄骨架" not in DELEGATE_DESCRIPTION_PREFER
