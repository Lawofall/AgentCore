"""云端 + gVisor 的整条能力链自洽（默认开；紧急关 / 探测失败钉住）。

链条（单一真相源 ``code_execution_enabled_for`` 贯穿）：
``GVISOR_ENABLED``（代码默认 true）→ 工具类注册 → ``<工作区>`` 能力
自述「已装配」→ 委派能力闸 → 沙箱→审批姿态 ``AUTO_PASS``。
显式关或健康探测失败时整条链反向成立（不注册 / 未装配 / 硬拒 / ``UNAVAILABLE``）。
单测 conftest 强制 ``gvisor_enabled=False``，故「关」路径用本模块显式断言。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentcore.config import settings
from agentcore.runtime.context.workspace_context import build_workspace_context
from agentcore.runtime.delegate.completion import (
    execution_capability_warning,
)
from agentcore.runtime.runs import build_run_plan
from agentcore.runtime.sandbox_approval import (
    ExecutionApprovalPosture,
    execution_approval_posture,
    execution_tool_auto_passes,
)
from agentcore.tools.builtin import build_worker_registry, code_execution_enabled_for
from agentcore.tools.builtin.run import RunTool
from agentcore.tools.sandbox.cloud_python import format_cloud_python_libs
from agentcore.tools.sandbox.subprocess import SubprocessSandbox


def _gaps(ctx: str) -> set[str]:
    for line in ctx.splitlines():
        if line.startswith("缺口："):
            return {p.strip() for p in line.removeprefix("缺口：").split("、") if p.strip()}
    return set()
from agentcore.workspace.server import ServerWorkspace


def _cloud_backend(tmp_path: Path) -> ServerWorkspace:
    return ServerWorkspace(root=tmp_path, sandbox=SubprocessSandbox())


def _pptx_plan():
    plan, errors = build_run_plan(
        [{"role": "课件工程师", "task": "用 python-pptx 生成可直接播放的 course.pptx"}],
        valid_tools=set(),
        id_prefix="chain",
        parent_run_id="CEO",
        depth=1,
    )
    assert not errors
    return plan


def test_cloud_when_gvisor_off_chain_stays_withheld(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """紧急关闭 / conftest：gvisor 关时云执行类整链扣留。"""
    monkeypatch.setattr(settings, "gvisor_enabled", False)
    monkeypatch.setattr(settings, "code_execute_cloud_enabled", False)
    backend = _cloud_backend(tmp_path)

    assert code_execution_enabled_for(backend) is False
    names = build_worker_registry(backend=backend).names
    assert "run" not in names
    ctx = build_workspace_context(backend, desktop_online=True)
    assert "run" in _gaps(ctx)
    warn = execution_capability_warning(_pptx_plan(), backend)
    assert warn is not None
    assert execution_approval_posture(backend) is ExecutionApprovalPosture.UNAVAILABLE
    assert execution_tool_auto_passes(backend, "run") is False


def test_settings_gvisor_enabled_defaults_true():
    """生产/内测默认开：字段默认值 true（conftest 不覆盖本断言的模型字段）。"""
    from agentcore.config.workspace import WorkspaceSettings

    assert WorkspaceSettings.model_fields["gvisor_enabled"].default is True


def test_cloud_escape_hatch_registers_execution_chain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Dev 逃生口（CODE_EXECUTE_CLOUD_ENABLED，安全权限与治理 §5.4）：无 gVisor 时同样
    翻开注册闸 / 能力自述 / 委派闸——但审批姿态保持 UNAVAILABLE（姿态表只喂 auto-pass，
    云端 worker 本就无 per-call 闸，逃生口不改姿态表）。"""
    monkeypatch.setattr(settings, "code_execute_cloud_enabled", True)
    backend = _cloud_backend(tmp_path)

    assert code_execution_enabled_for(backend) is True
    names = build_worker_registry(backend=backend).names
    assert "run" in names
    ctx = build_workspace_context(backend, desktop_online=True)
    assert "run" not in _gaps(ctx)
    plan = _pptx_plan()
    assert execution_capability_warning(plan, backend) is None
    assert execution_approval_posture(backend) is ExecutionApprovalPosture.UNAVAILABLE


def test_cloud_gvisor_on_chain_flips_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "gvisor_enabled", True)
    backend = _cloud_backend(tmp_path)

    # ① 注册闸：执行类工具进 worker 全集。
    assert code_execution_enabled_for(backend) is True
    names = build_worker_registry(backend=backend).names
    assert "run" in names

    # ② 能力自述：workspace_context 能力行翻「已装配」。
    # 装包另位：无 netns egress 时 package_install 保持未装配（能跑 ≠ 能装）。
    ctx = build_workspace_context(backend, desktop_online=True)
    assert "run" not in _gaps(ctx)
    assert "package_install=" not in ctx


    # ③ 委派能力闸：S3 后无 code_verified kind 硬放行；二进制产物启发不再软警告。
    plan = _pptx_plan()
    assert execution_capability_warning(plan, backend) is None

    # ④ 审批姿态：云端 gVisor 真隔离 → 整类 execution_class 自动放行。
    assert execution_approval_posture(backend) is ExecutionApprovalPosture.AUTO_PASS
    assert execution_tool_auto_passes(backend, "run") is True
    assert execution_tool_auto_passes(backend, "browser") is True
    # desktop_notify 不吃 gVisor AUTO_PASS（仅 command=auto）。
    assert execution_tool_auto_passes(backend, "desktop_notify") is False
    assert execution_tool_auto_passes(backend, "host") is False


def test_cloud_probe_failed_withholds_execution_chain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Boot probe unhealthy → config 开着也不装配（谓词 / registry / context 一致）。"""
    from agentcore.tools.sandbox.cloud_health import set_cloud_sandbox_health_for_tests

    monkeypatch.setattr(settings, "gvisor_enabled", True)
    set_cloud_sandbox_health_for_tests(False)
    backend = _cloud_backend(tmp_path)

    assert code_execution_enabled_for(backend) is False
    names = build_worker_registry(backend=backend).names
    assert "run" not in names
    ctx = build_workspace_context(backend, desktop_online=True)
    assert "run" in _gaps(ctx)


def test_cloud_probe_ok_keeps_execution_chain(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Boot probe healthy → 与配置开启后的现状一致。"""
    from agentcore.tools.sandbox.cloud_health import set_cloud_sandbox_health_for_tests

    monkeypatch.setattr(settings, "gvisor_enabled", True)
    set_cloud_sandbox_health_for_tests(True)
    backend = _cloud_backend(tmp_path)

    assert code_execution_enabled_for(backend) is True
    names = build_worker_registry(backend=backend).names
    assert "run" in names
    ctx = build_workspace_context(backend, desktop_online=True)
    assert "run" not in _gaps(ctx)


def test_cloud_unprobed_keeps_config_only_semantics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """从未探测（单测默认 / lifespan 未跑）→ 行为与改动前完全一致。"""
    from agentcore.tools.sandbox.cloud_health import cloud_sandbox_health

    monkeypatch.setattr(settings, "gvisor_enabled", True)
    backend = _cloud_backend(tmp_path)

    assert cloud_sandbox_health() is None
    assert code_execution_enabled_for(backend) is True
    names = build_worker_registry(backend=backend).names
    assert "run" in names
    ctx = build_workspace_context(backend, desktop_online=True)
    assert "run" not in _gaps(ctx)


def test_server_tool_description_declares_libs_and_write_back():
    desc = RunTool(location="server").schema.description
    assert "云桌" in desc  # 与 test_tools_catalog 的措辞契约保持一致
    assert "HOW→consult(run)" in desc
    libs = format_cloud_python_libs()
    assert "python-pptx" in libs
    assert libs not in desc


def test_gvisor_desk_oci_workspace_rw(tmp_path: Path):
    from agentcore.tools.sandbox.gvisor import GVisorSandbox

    sandbox = GVisorSandbox(runtime_root=str(tmp_path / "rt"))
    cache = tmp_path / "cache"
    cache.mkdir()
    staged = sandbox._build_desk_oci(  # noqa: SLF001
        workspace=str(tmp_path / "ws"),
        scratch_dir=str(tmp_path / "sc"),
        netns_path="/var/run/netns/acpkg1",
        cache_host_dir=str(cache),
        proxy_url="http://10.0.0.1:8898",
        memory_limit_mb=512,
    )
    ws_mount = next(m for m in staged["mounts"] if m["destination"] == "/workspace")
    assert "rw" in ws_mount["options"]
    assert ws_mount["type"] == "bind"
    assert staged["linux"]["resources"]["memory"]["limit"] == 512 * 1024 * 1024
    assert staged["process"]["user"]["uid"] != 65534


def test_gvisor_timeout_clamped_by_guardrail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from agentcore.tools.sandbox.gvisor import GVisorSandbox
    from agentcore.tools.sandbox.protocol import ExecutionRequest

    monkeypatch.setattr(settings, "gvisor_timeout_max_seconds", 45)
    sandbox = GVisorSandbox(runtime_root=str(tmp_path / "rt"))
    assert sandbox._effective_timeout(  # noqa: SLF001
        ExecutionRequest(code="x", language="python", timeout_seconds=60)
    ) == 45
    assert sandbox._effective_timeout(  # noqa: SLF001
        ExecutionRequest(code="x", language="python", timeout_seconds=10)
    ) == 10
