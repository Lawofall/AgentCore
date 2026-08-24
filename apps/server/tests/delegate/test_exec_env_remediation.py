"""Honest exec-env remediation: already-on-cloud ≠ re-import-to-cloud."""

from __future__ import annotations

import pytest

from agentcore.runtime.delegate.completion import execution_capability_warning
from agentcore.runtime.delegate.exec_env_remediation import (
    cloud_exec_unavailable_delivery_action,
    exec_env_remediation_zh,
)
from agentcore.runtime.runs.plan import RunPlan
from agentcore.runtime.runs.types import RunSpec
from agentcore.tools.sandbox.cloud_health import (
    reset_cloud_sandbox_health_for_tests,
    set_cloud_sandbox_health_for_tests,
)
from tests.delegate.conftest import LocalBackend


class _CloudBackend:
    location = "server"


def _plan(task: str) -> RunPlan:
    return RunPlan(nodes=[RunSpec(run_id="a", role="dev", task=task)])


def setup_function() -> None:
    reset_cloud_sandbox_health_for_tests()


def teardown_function() -> None:
    reset_cloud_sandbox_health_for_tests()


def test_cloud_delivery_action_forbids_reimport():
    set_cloud_sandbox_health_for_tests(
        False, failure=("not_linux", "platform=win32")
    )
    action = cloud_exec_unavailable_delivery_action(_CloudBackend())
    assert action["kind"] == "bind_local_folder"
    desc = action["description"]
    assert "不要" in desc or "勿" in desc
    assert "导入到云" in desc
    assert "not_linux" in desc
    assert "**推荐**" not in desc or "推荐**引导 Composer「导入到云" not in desc


def test_local_delivery_action_may_suggest_import():
    desc = exec_env_remediation_zh(backend=LocalBackend(), kind="delivery_action")
    assert "导入到云" in desc or "连接 Git" in desc


def test_cloud_capability_warning_forbids_reimport():
    set_cloud_sandbox_health_for_tests(False, failure=("runsc_failed", "x"))
    warn = execution_capability_warning(_plan("运行脚本生成 course.pptx"), _CloudBackend())
    assert warn is not None
    assert "禁止" in warn or "不要" in warn
    assert "导入到云" in warn
    assert "runsc_failed" in warn


def test_capability_office_copy_carves_out_deterministic_word_pdf():
    """两个分支都须点名 md_to_docx / md_to_pdf，且缺口只落在 pptx/xlsx。"""
    for backend in (LocalBackend(), _CloudBackend()):
        copy = exec_env_remediation_zh(backend=backend, kind="capability_office")
        assert "md_to_docx" in copy and "md_to_pdf" in copy
        assert ".pptx/.xlsx" in copy
        assert ".docx/.pptx/.xlsx" not in copy


def test_cloud_runtime_ready_copy_when_execution_on(
    monkeypatch: pytest.MonkeyPatch,
):
    """Cloud + execution on + runtime smell → terminal gap, never 「再导入到云」."""
    from agentcore.config import settings
    from agentcore.tools.builtin import execution_class_enabled_for

    monkeypatch.setattr(settings, "gvisor_enabled", True)
    monkeypatch.setattr(settings, "code_execute_cloud_enabled", True)
    set_cloud_sandbox_health_for_tests(True)
    backend = _CloudBackend()
    assert execution_class_enabled_for(backend) is True
    warn = exec_env_remediation_zh(
        backend=backend, kind="capability_runtime_ready"
    )
    assert "terminal" in warn
    assert "不要" in warn or "勿" in warn
    assert "导入到云" in warn


def test_sidecar_cloud_desk_delivery_does_not_wait_for_gvisor(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        "agentcore.sidecar.server_pkg.core.is_sidecar_process", lambda: True
    )
    desc = exec_env_remediation_zh(backend=_CloudBackend(), kind="delivery_action")
    assert "不要" in desc or "勿" in desc
    assert "导入到云" in desc
    assert "gVisor" not in desc
    assert "假装起云沙箱" in desc
    run = exec_env_remediation_zh(backend=_CloudBackend(), kind="capability_run")
    assert "新开云协作对话" in run
    assert "稍后重试" not in run
