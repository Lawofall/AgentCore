"""Landing predicate: pinned artifacts / artifact_dir only. No form enum."""

from __future__ import annotations

from agentcore.runtime.runs.builder import build_run_plan
from agentcore.runtime.runs.contract import describe_deliverable, is_file_deliverable
from agentcore.runtime.runs.types import (
    Deliverable,
    deliverable_expects_landing,
    raw_deliverable_expects_landing,
)
from agentcore.tools.builtin.delegate.schema import (
    DELEGATE_DESCRIPTION,
    TASK_DELIVERABLE_SCHEMA,
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
    assert d.artifacts == ["AgentCore/文档/工作稿/note.md"]
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


def test_describe_deliverable_renders_sections():
    desc = describe_deliverable(Deliverable(required_sections=["结论"]))
    assert "结论" in desc
    assert "form=" not in desc


def test_ceo_schema_deliverable_is_artifacts_only():
    props = TASK_DELIVERABLE_SCHEMA["properties"]
    assert set(props) == {"artifacts"}
    assert "form" not in props
    assert "【看】" not in DELEGATE_DESCRIPTION
    assert "【存文档】" not in DELEGATE_DESCRIPTION
    assert "【改工程】" not in DELEGATE_DESCRIPTION
    assert "form=prose" not in (TASK_DELIVERABLE_SCHEMA.get("description") or "")
    assert "摸底抄骨架" not in DELEGATE_DESCRIPTION
