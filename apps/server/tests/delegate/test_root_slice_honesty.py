"""根委派切片诚实软闸单测."""

from __future__ import annotations

from agentcore.runtime.delegate.root_slice_honesty import (
    check_root_slice_honesty,
    root_slice_honesty_soft_message,
)


def _files_task(**deliverable_extra):
    d = {"form": "files", **deliverable_extra}
    return {
        "role": "工程师",
        "task": "从零实现应用 MVP",
        "deliverable": d,
    }


def _workspace_task(**deliverable_extra):
    d = {"form": "workspace", **deliverable_extra}
    return {
        "role": "工程师",
        "task": "从零实现应用 MVP",
        "deliverable": d,
    }


def test_warn_single_handwritten_workspace_no_nail():
    warn = check_root_slice_honesty([_workspace_task()], depth=0, playbook=None)
    assert warn == root_slice_honesty_soft_message()
    assert "嵌套扇出" in warn
    assert "不拒收" in warn


def test_ok_form_files_not_write_engineering():
    """form=files 不算写工程命中。"""
    warn = check_root_slice_honesty([_files_task()], depth=0, playbook=None)
    assert warn is None


def test_warn_requires_files_without_form_not_write_engineering():
    """仅 legacy requires_files、无 form=workspace → 不算显式写工程。"""
    warn = check_root_slice_honesty(
        [
            {
                "role": "工程师",
                "task": "搭脚手架",
                "deliverable": {"requires_files": True},
            }
        ],
        depth=0,
    )
    assert warn is None


def test_ok_form_omitted():
    """form 省略不算写工程命中。"""
    warn = check_root_slice_honesty(
        [{"role": "工程师", "task": "从零实现", "deliverable": {}}],
        depth=0,
    )
    assert warn is None


def test_ok_nested_depth():
    warn = check_root_slice_honesty([_workspace_task()], depth=1)
    assert warn is None


def test_ok_named_playbook():
    warn = check_root_slice_honesty(
        [_workspace_task()],
        depth=0,
        playbook="cite_write_review",
    )
    assert warn is None


def test_ok_two_tasks():
    warn = check_root_slice_honesty(
        [
            _workspace_task(),
            {"role": "QA", "task": "冒烟", "deliverable": {"form": "prose"}},
        ],
        depth=0,
    )
    assert warn is None


def test_ok_artifacts_nail():
    warn = check_root_slice_honesty(
        [_workspace_task(artifacts=["src/main.py"])],
        depth=0,
    )
    assert warn is None


def test_ok_artifact_dir_nail():
    warn = check_root_slice_honesty(
        [_workspace_task(artifact_dir="app/")],
        depth=0,
    )
    assert warn is None


def test_min_length_no_longer_a_slice_nail():
    """已删 min_length 不再豁免切片钉；form=workspace 无白名单钉 → 仍告警。"""
    warn = check_root_slice_honesty(
        [_workspace_task(min_length=500)],
        depth=0,
    )
    assert warn == root_slice_honesty_soft_message()
    assert "min_length" not in warn


def test_ok_required_sections_nail():
    warn = check_root_slice_honesty(
        [_workspace_task(required_sections=["目标", "验收"])],
        depth=0,
    )
    assert warn is None


def test_ok_checkpoint_after_nail():
    task = _workspace_task()
    task["checkpoint_after"] = True
    warn = check_root_slice_honesty([task], depth=0)
    assert warn is None


def test_ok_prose_form():
    warn = check_root_slice_honesty(
        [
            {
                "role": "写手",
                "task": "写长文",
                "deliverable": {"form": "prose"},
            }
        ],
        depth=0,
    )
    assert warn is None
