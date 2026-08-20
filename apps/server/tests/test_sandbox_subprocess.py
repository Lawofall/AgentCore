"""SubprocessSandbox execution under any event loop (incl. Windows SelectorEventLoop).

uvicorn ``--reload`` on Windows installs a ``SelectorEventLoop``, which cannot create
asyncio subprocess transports (``NotImplementedError``). SubprocessSandbox must therefore
use blocking ``subprocess`` in a worker thread — these tests pin that contract.
"""

from __future__ import annotations

import asyncio
import sys

import pytest

from agentcore.core.errors import SandboxError
from agentcore.tools.sandbox.exec_env import (
    EXEC_ENV_PROBE_FAIL_MARKER,
    EXEC_ENV_SPAWN_DENIED_CODE,
    exec_env_probe_failure_code,
)
from agentcore.tools.sandbox.protocol import ExecutionRequest
from agentcore.tools.sandbox.subprocess import SubprocessSandbox


def _make_selector_loop() -> asyncio.AbstractEventLoop:
    """Build a SelectorEventLoop the way Windows reload / policy would."""
    if sys.platform == "win32":
        # Match uvicorn --reload: WindowsSelectorEventLoopPolicy → SelectorEventLoop.
        return asyncio.SelectorEventLoop()
    return asyncio.SelectorEventLoop()


def test_execute_succeeds_under_selector_event_loop():
    """Real command must succeed on SelectorEventLoop (no NotImplementedError)."""

    async def _run() -> None:
        sandbox = SubprocessSandbox()
        result = await sandbox.execute(
            ExecutionRequest(
                code="print('selector-ok')",
                language="python",
                timeout_seconds=10,
            )
        )
        assert result.success is True
        assert "selector-ok" in result.stdout
        assert result.exit_code == 0

    loop = _make_selector_loop()
    try:
        loop.run_until_complete(_run())
    finally:
        loop.close()


def test_timeout_returns_graceful_result_under_selector_event_loop():
    """Disaster wall: success=False, exit_code=-1, stderr uses forced-stop marker."""

    async def _run() -> None:
        sandbox = SubprocessSandbox()
        result = await sandbox.execute(
            ExecutionRequest(
                code="import time; time.sleep(5)",
                language="python",
                timeout_seconds=1,
            )
        )
        assert result.success is False
        assert result.exit_code == -1
        assert "Timeout: forced stop after" in result.stderr

    loop = _make_selector_loop()
    try:
        loop.run_until_complete(_run())
    finally:
        loop.close()


def test_idle_timeout_kills_silent_process_under_selector_event_loop():
    """Idle silence is the primary hang kill (before disaster wall)."""

    async def _run() -> None:
        sandbox = SubprocessSandbox()
        result = await sandbox.execute(
            ExecutionRequest(
                code="import time; time.sleep(10)",
                language="python",
                timeout_seconds=30,
                idle_timeout_seconds=1,
            )
        )
        assert result.success is False
        assert result.exit_code == -1
        assert "Timeout: no output for" in result.stderr

    loop = _make_selector_loop()
    try:
        loop.run_until_complete(_run())
    finally:
        loop.close()


def test_bash_missing_returns_actionable_stderr(monkeypatch):
    """Windows-common: no usable bash → fail fast, steer to python/javascript."""
    import agentcore.tools.sandbox.subprocess as sp

    monkeypatch.setattr(sp, "resolve_bash_launcher", lambda: None)

    async def _run() -> None:
        sandbox = SubprocessSandbox()
        result = await sandbox.execute(
            ExecutionRequest(code="echo hi", language="bash", timeout_seconds=5)
        )
        assert result.success is False
        assert result.exit_code == 127
        assert "bash" in result.stderr
        assert "python" in result.stderr.lower() or "javascript" in result.stderr.lower()
        # Must be instant — not a hung WSL trampoline timeout.
        assert result.duration_ms < 2000

    asyncio.run(_run())


def test_probe_available_languages_omits_bash_without_launcher(monkeypatch):
    import agentcore.tools.sandbox.subprocess as sp

    monkeypatch.setattr(sp, "resolve_bash_launcher", lambda: None)
    monkeypatch.setattr(
        sp.shutil,
        "which",
        lambda name: "/bin/python" if name == "python" else (
            "/bin/node" if name == "node" else None
        ),
    )
    assert "bash" not in sp.probe_available_languages()
    assert "python" in sp.probe_available_languages()


def test_decode_pipe_bytes_utf16le_nul_dense():
    from agentcore.tools.sandbox.subprocess import _decode_pipe_bytes

    # UTF-16LE ``wsl`` → must not become ``w\0s\0l\0`` via UTF-8.
    raw = "wsl: 局域网".encode("utf-16-le")
    text = _decode_pipe_bytes(raw)
    assert "\0" not in text
    assert "wsl" in text
    assert "局域网" in text


def test_decode_pipe_bytes_utf8_unchanged():
    from agentcore.tools.sandbox.subprocess import _decode_pipe_bytes

    raw = b"hello stdout\n"
    assert _decode_pipe_bytes(raw) == "hello stdout\n"


def test_is_wsl_bash_trampoline():
    from agentcore.tools.sandbox.subprocess import _is_wsl_bash_trampoline

    assert _is_wsl_bash_trampoline(r"C:\Windows\System32\bash.exe")
    assert _is_wsl_bash_trampoline(r"C:\Windows\SysWOW64\bash.exe")
    assert not _is_wsl_bash_trampoline(r"C:\Program Files\Git\bin\bash.exe")
    assert not _is_wsl_bash_trampoline("/usr/bin/bash")


def test_resolve_bash_skips_wsl_trampoline(monkeypatch, tmp_path):
    """When PATH only has System32 bash, refuse rather than spawn the trampoline."""
    import agentcore.tools.sandbox.subprocess as sp

    trampoline = tmp_path / "System32" / "bash.exe"
    trampoline.parent.mkdir(parents=True)
    trampoline.write_bytes(b"MZ")

    monkeypatch.setattr(sp, "_IS_WINDOWS", True)
    monkeypatch.setattr(sp, "_GIT_BASH_CANDIDATES", ())
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setenv("PATH", str(trampoline.parent))
    monkeypatch.setenv("PATHEXT", ".EXE")

    assert sp.resolve_bash_launcher() is None


def test_resolve_bash_prefers_git_bash(monkeypatch, tmp_path):
    git_bash = tmp_path / "Git" / "bin" / "bash.exe"
    git_bash.parent.mkdir(parents=True)
    git_bash.write_bytes(b"MZ")
    trampoline = tmp_path / "System32" / "bash.exe"
    trampoline.parent.mkdir(parents=True)
    trampoline.write_bytes(b"MZ")

    import agentcore.tools.sandbox.subprocess as sp

    monkeypatch.setattr(sp, "_IS_WINDOWS", True)
    monkeypatch.setattr(sp, "_GIT_BASH_CANDIDATES", (str(git_bash),))
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    # PATH prefers trampoline first — Git candidate must still win.
    monkeypatch.setenv("PATH", str(trampoline.parent))
    monkeypatch.setenv("PATHEXT", ".EXE")

    assert sp.resolve_bash_launcher() == str(git_bash)


def test_popen_permissionerror_returns_spawn_denied_tag(monkeypatch):
    """Popen EACCES/EPERM is declared at spawn site — not raised as SandboxError."""
    import agentcore.tools.sandbox.subprocess as sp

    def boom(*_args, **_kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(sp.subprocess, "Popen", boom)

    async def _run() -> None:
        result = await SubprocessSandbox().execute(
            ExecutionRequest(code="print(1)", language="python", timeout_seconds=5)
        )
        assert result.success is False
        assert result.exit_code == -1
        assert EXEC_ENV_PROBE_FAIL_MARKER in result.stderr
        assert exec_env_probe_failure_code(result.stderr) == EXEC_ENV_SPAWN_DENIED_CODE
        assert "Permission denied" in result.stderr

    asyncio.run(_run())


def test_popen_filenotfound_still_raises_sandbox_error(monkeypatch):
    """Spawn-time ENOENT stays on the existing SandboxError path (not spawn-denied)."""
    import agentcore.tools.sandbox.subprocess as sp

    def boom(*_args, **_kwargs):
        raise FileNotFoundError(2, "No such file or directory")

    monkeypatch.setattr(sp.subprocess, "Popen", boom)

    async def _run() -> None:
        with pytest.raises(SandboxError, match="代码执行环境启动失败"):
            await SubprocessSandbox().execute(
                ExecutionRequest(code="print(1)", language="python", timeout_seconds=5)
            )

    asyncio.run(_run())
