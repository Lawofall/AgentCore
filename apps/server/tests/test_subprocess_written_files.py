"""产物写回 · 本地腿：SubprocessSandbox 如实报告本次执行写进工作区的文件。

本地 / sidecar 沙箱以工作区为 cwd 让脚本直接写盘（没有 gVisor 的 copy-out 可枚举），
``written_files`` 曾恒为 ``None`` —— 于是桌面（产品主力形态）用户跑脚本产出的文件
全部不进交付物台账：不出现在产物卡、不出现在用户面路径、CEO 清单也看不见。

这里锁住两件事：报得到（真产物在列），报得干净（旁路区 ``AgentCore/{index,trash,
baselines}`` 与系统噪音不得混进交付物清单，执行前就存在且未变动的文件也不得混进）。
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from agentcore.tools.sandbox.protocol import ExecutionRequest
from agentcore.tools.sandbox.subprocess import SubprocessSandbox, probe_available_languages
from agentcore.tools.sandbox.written_scan import scan_written_files, written_scan_cutoff_ns

pytestmark = pytest.mark.skipif(
    "python" not in probe_available_languages(),
    reason="no python launcher on PATH for the real subprocess sandbox",
)

_WRITE_SCRIPT = """
import pathlib

pathlib.Path("report.md").write_text("# 报告", encoding="utf-8")
pathlib.Path("artifacts").mkdir(exist_ok=True)
pathlib.Path("artifacts/chart.png").write_bytes(b"PNG")
pathlib.Path("out").mkdir(exist_ok=True)
pathlib.Path("out/build.txt").write_text("x", encoding="utf-8")
for zone in ("index", "trash", "baselines"):
    d = pathlib.Path("AgentCore") / zone
    d.mkdir(parents=True, exist_ok=True)
    (d / "noise.json").write_text("{}", encoding="utf-8")
pathlib.Path("node_modules").mkdir(exist_ok=True)
pathlib.Path("node_modules/dep.js").write_text("x", encoding="utf-8")
pathlib.Path("stale.pyc").write_bytes(b"\\x00")
print("done")
"""


def _age(path: Path, seconds: float = 60.0) -> None:
    """Backdate ``path`` so it reads as 'existed before this execution'."""
    old = time.time() - seconds
    os.utime(path, (old, old))


async def test_written_files_reports_artifacts_and_skips_bypass_zones(tmp_path: Path):
    root = tmp_path / "ws"
    root.mkdir()
    seed = root / "seed.txt"
    seed.write_text("untouched", encoding="utf-8")
    _age(seed)

    result = await SubprocessSandbox().execute(
        ExecutionRequest(
            code=_WRITE_SCRIPT,
            language="python",
            cwd=str(root),
            timeout_seconds=30,
        )
    )

    assert result.success is True, result.stderr
    # 真产物在列（含 .png —— AI 噪音后缀在别处对 AI 隐藏，但生成的图表正是交付物）。
    assert result.written_files == ["artifacts/chart.png", "report.md"]
    # 旁路区 / 系统噪音目录 / 系统噪音后缀 / 未变动的旧文件都不得混进交付物清单。
    assert (root / "AgentCore" / "index" / "noise.json").is_file()  # 真写了，只是不报
    assert (root / "node_modules" / "dep.js").is_file()
    assert (root / "out" / "build.txt").is_file()


async def test_written_files_empty_when_execution_writes_nothing(tmp_path: Path):
    root = tmp_path / "ws"
    root.mkdir()
    seed = root / "seed.txt"
    seed.write_text("untouched", encoding="utf-8")
    _age(seed)

    result = await SubprocessSandbox().execute(
        ExecutionRequest(
            code="print(open('seed.txt', encoding='utf-8').read())",
            language="python",
            cwd=str(root),
            timeout_seconds=30,
        )
    )

    assert result.success is True, result.stderr
    # 只读的执行必须报空：读文件不改 mtime，纯计算不该凭空产出交付物。
    assert result.written_files == []


async def test_execute_closes_stdin_when_script_has_no_input(tmp_path: Path, monkeypatch):
    """Sidecar stdin is the JSON-RPC pipe; inheriting it stalls ``print('ok')``."""
    import subprocess as sp

    root = tmp_path / "ws"
    root.mkdir()
    seen: list[object] = []
    real_popen = sp.Popen

    def _capture(*args: object, **kwargs: object):
        seen.append(kwargs.get("stdin"))
        return real_popen(*args, **kwargs)

    monkeypatch.setattr(sp, "Popen", _capture)
    result = await SubprocessSandbox().execute(
        ExecutionRequest(
            code="print('ok')",
            language="python",
            cwd=str(root),
            timeout_seconds=30,
        )
    )
    assert result.success is True, result.stderr
    assert seen
    assert all(value is sp.DEVNULL for value in seen)


async def test_written_files_none_without_workspace_cwd():
    """无工作区 cwd（一次性 temp dir）→ 无从报起，保持 ``None`` 而不是假装空。"""
    result = await SubprocessSandbox().execute(
        ExecutionRequest(code="print('ok')", language="python", timeout_seconds=30)
    )
    assert result.success is True, result.stderr
    assert result.written_files is None


def test_scan_stops_at_budget_and_flags_truncation(tmp_path: Path):
    """预算是硬约束：撞上目录上限就收手，并把「没看全」如实标出来。"""
    root = tmp_path / "ws"
    for i in range(12):
        (root / f"d{i:02d}").mkdir(parents=True)
        (root / f"d{i:02d}" / "a.txt").write_text("x", encoding="utf-8")

    cutoff = written_scan_cutoff_ns() - 60 * 1_000_000_000  # 全都算「本次写的」
    full = scan_written_files(root, cutoff_ns=cutoff)
    assert len(full.files) == 12
    assert full.truncated is False

    clipped = scan_written_files(root, cutoff_ns=cutoff, max_dirs=4)
    assert clipped.truncated is True
    # BFS：先看根与最浅的目录，被砍掉的永远是更深的尾巴。
    assert len(clipped.files) < 12
    assert set(clipped.files) <= set(full.files)


def test_scan_ignores_symlinks(tmp_path: Path):
    """符号链接既不下潜也不上报。"""
    root = tmp_path / "ws"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("x", encoding="utf-8")
    try:
        (root / "link").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable on this host")

    cutoff = written_scan_cutoff_ns() - 60 * 1_000_000_000
    assert scan_written_files(root, cutoff_ns=cutoff).files == []
