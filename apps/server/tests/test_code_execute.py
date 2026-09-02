"""Tests for the short-exec kernel's structured display (工具结果富渲染).

The kernel flattens stdout/stderr/exit_code into the model-facing ``output`` string,
but also carries them STRUCTURED on ``display`` so the desktop renders a terminal
view (stderr in red, exit-code badge) instead of parsing "stdout:\\n…" text. A
non-zero exit must still produce a display (so a failed run surfaces its stderr).
"""

import pytest

from agentcore.core.errors import SandboxError
from agentcore.tools.builtin.run_short import execute_short
from agentcore.tools.protocol import ToolContext
from agentcore.tools.sandbox.protocol import ExecutionRequest, ExecutionResult


class _FakeBackend:
    """A workspace backend stub whose ``execute`` returns a canned result."""

    def __init__(self, result: ExecutionResult) -> None:
        self._result = result
        self.requests: list[ExecutionRequest] = []

    async def execute(self, request: ExecutionRequest) -> ExecutionResult:
        self.requests.append(request)
        return self._result


def _ctx(backend: _FakeBackend, on_phase=None) -> ToolContext:
    return ToolContext.create(
        execution_id="e",
        run_id="s",
        agent_id="a",
        backend=backend,  # type: ignore[arg-type]
        user_id="u",
        on_phase=on_phase,
    )


async def test_code_execute_display_carries_stdout_and_exit():
    backend = _FakeBackend(
        ExecutionResult(success=True, stdout="hello\n", stderr="", exit_code=0, duration_ms=5)
    )
    result = await execute_short(
        {"code": "print('hello')", "language": "python"}, _ctx(backend)
    )

    assert result.success is True
    assert result.display == {
        "stdout": "hello\n",
        "stderr": "",
        "exit_code": 0,
        "language": "python",
    }


async def test_code_execute_emits_executing_phase():
    # 工具执行阶段进度 (联网前端展示优化): short-exec signals 「正在执行」before the (slow,
    # blocking) sandbox run so the waiting row is live instead of a dead spinner.
    backend = _FakeBackend(
        ExecutionResult(success=True, stdout="ok\n", stderr="", exit_code=0, duration_ms=5)
    )
    phases: list[str] = []
    result = await execute_short(
        {"code": "print('ok')", "language": "python"},
        _ctx(backend, on_phase=phases.append),
    )

    assert result.success is True
    assert phases == ["executing"]


async def test_code_execute_surfaces_written_back_files():
    # Bind-to-disk: the model must see exactly which files landed in the
    # workspace, and the display carries them for the client.
    backend = _FakeBackend(
        ExecutionResult(
            success=True,
            stdout="done\n",
            stderr="",
            exit_code=0,
            duration_ms=5,
            written_files=["out/course.pptx", "out/chart.png"],
        )
    )
    result = await execute_short(
        {"code": "make()", "language": "python"}, _ctx(backend), location="server"
    )

    assert result.success is True
    assert "已写回工作区：out/course.pptx、out/chart.png" in result.output
    assert "超出写回限额" not in result.output
    assert result.display is not None
    assert result.display["written_files"] == ["out/course.pptx", "out/chart.png"]


async def test_code_execute_display_unchanged_without_write_back():
    # No written files → no extra display key (old fixtures stay byte-identical).
    backend = _FakeBackend(
        ExecutionResult(success=True, stdout="ok\n", stderr="", exit_code=0, duration_ms=5)
    )
    result = await execute_short(
        {"code": "print('ok')", "language": "python"}, _ctx(backend)
    )
    assert result.display is not None
    assert "written_files" not in result.display
    assert "已写回工作区" not in result.output
    assert result.file_products == []


async def test_code_execute_self_reports_write_back_products():
    # 落盘产物自报 (台账事实口径): the sandbox copy-out paths ride ``ToolResult.file_products``
    # — the same channel as file_write — so the ledger counts them WITHOUT parsing the human
    #「已写回工作区」line, which cannot be split safely when a filename contains「、」.
    from agentcore.llm.provider.protocol import LLMMessage, ToolCall, ToolCallFunction
    from agentcore.runtime.runs.serialize import files_touched_from_transcript
    from agentcore.tools.file_products import with_file_products_marker

    backend = _FakeBackend(
        ExecutionResult(
            success=True,
            stdout="done\n",
            stderr="",
            exit_code=0,
            duration_ms=5,
            written_files=["out/a、b.md", "out/chart.png"],
        )
    )
    result = await execute_short(
        {"code": "make()", "language": "python"}, _ctx(backend), location="server"
    )
    assert [(p.path, p.kind) for p in result.file_products] == [
        ("out/a、b.md", "md"),
        ("out/chart.png", "image"),
    ]
    # 回执文案不带尾注: the marker is stamped by the engine onto the transcript message only.
    assert "agentcore:file_products" not in result.output
    # Through the engine's stamping it round-trips into the run's ledger exactly.
    transcript = [
        LLMMessage(
            role="assistant",
            content=None,
            tool_calls=[
                ToolCall(
                    id="c1",
                    function=ToolCallFunction(name="run", arguments="{}"),
                )
            ],
        ),
        LLMMessage(
            role="tool",
            content=with_file_products_marker(result.output, result.file_products),
            tool_call_id="c1",
        ),
    ]
    assert files_touched_from_transcript(transcript) == ["out/a、b.md", "out/chart.png"]


async def test_code_execute_display_on_failure_keeps_stderr_and_exit():
    backend = _FakeBackend(
        ExecutionResult(
            success=False,
            stdout="",
            stderr="Traceback (most recent call last):\nNameError: name 'boom'",
            exit_code=1,
            duration_ms=5,
        )
    )
    result = await execute_short(
        {"code": "boom", "language": "python"}, _ctx(backend)
    )

    assert result.success is False
    assert result.display is not None
    assert result.display["exit_code"] == 1
    assert "NameError" in result.display["stderr"]
    assert result.display["language"] == "python"


def test_long_running_command_match_catches_dev_servers():
    from agentcore.tools.builtin.run_short import long_running_command_match

    assert long_running_command_match("npm run dev") is not None
    assert long_running_command_match("pnpm dev") is not None
    assert long_running_command_match("npx vite") is not None
    assert long_running_command_match("os.system('npm run start')") is not None
    # Finite / lookalike — must not trip the gate.
    assert long_running_command_match("npm install") is None
    assert long_running_command_match("npm run build") is None
    assert long_running_command_match("npm run development") is None
    assert long_running_command_match("import { defineConfig } from 'vite'") is None


def test_long_running_command_match_ignores_async_http_poll():
    """Finite poll loops exit; do not grow the long_running regex into a scanner."""
    from agentcore.tools.builtin.run_short import long_running_command_match

    assert long_running_command_match("time.sleep(5)") is None
    assert long_running_command_match("requests.get(status_url)") is None
    poll = (
        "import time, requests\n"
        "while True:\n"
        "    r = requests.get('https://api.example/jobs/1')\n"
        "    if r.json().get('done'):\n"
        "        break\n"
        "    time.sleep(2)\n"
    )
    assert long_running_command_match(poll) is None


def test_source_inspect_match_reexport():
    from agentcore.tools.builtin.run_short import source_inspect_match

    dump = source_inspect_match("print(open('apps/server/foo.py').read()[:80])")
    assert dump is not None and dump.kind == "dump"
    grep = source_inspect_match(
        "src = open('apps/server/foo.py').read()\nprint(len(re.findall(r'TODO', src)))"
    )
    assert grep is not None and grep.kind == "grep"


async def test_code_execute_blocks_source_dump_without_sandbox():
    backend = _FakeBackend(
        ExecutionResult(success=True, stdout="should-not-run\n", stderr="", exit_code=0, duration_ms=1)
    )
    result = await execute_short(
        {
            "code": (
                "src = open('apps/server/foo.py', encoding='utf-8').read()\n"
                "print(src[:3000])"
            ),
            "language": "python",
        },
        _ctx(backend),
    )

    assert result.success is False
    assert result.contract_failure is True
    assert result.metadata.get("code") == "source_dump_redirect"
    err = result.error or ""
    assert "file_read" in err
    assert "code_execute" not in err
    assert "run" in err
    assert backend.requests == []


async def test_code_execute_blocks_source_grep_without_sandbox():
    backend = _FakeBackend(
        ExecutionResult(success=True, stdout="should-not-run\n", stderr="", exit_code=0, duration_ms=1)
    )
    result = await execute_short(
        {
            "code": (
                "src = open('apps/server/agentcore/observability/catalog.py', encoding='utf-8').read()\n"
                "print(len(re.findall(r'EventSpec', src)))"
            ),
            "language": "python",
        },
        _ctx(backend),
    )

    assert result.success is False
    assert result.contract_failure is True
    assert result.metadata.get("code") == "source_grep_redirect"
    err = result.error or ""
    assert "grep" in err
    assert "file_read" in err
    assert backend.requests == []


async def test_code_execute_allows_pandas_after_source_inspect_gate():
    backend = _FakeBackend(
        ExecutionResult(success=True, stdout="ok\n", stderr="", exit_code=0, duration_ms=1)
    )
    result = await execute_short(
        {
            "code": "import pandas as pd\ndf = pd.read_csv('a.csv')\nprint(df.head())",
            "language": "python",
        },
        _ctx(backend),
    )

    assert result.success is True
    assert len(backend.requests) == 1


@pytest.mark.parametrize(
    "code",
    [
        "npx tsc --noEmit",
        "npm install",
        "pip install -r requirements.txt",
        "uv sync",
        "uv pip install requests",
        "poetry install",
        "python -m pip install flask",
    ],
)
async def test_short_kernel_does_not_refuse_project_verify_commands(code: str):
    """Classification belongs on unified ``run``; the short kernel just executes."""
    backend = _FakeBackend(
        ExecutionResult(success=True, stdout="ok\n", stderr="", exit_code=0, duration_ms=1)
    )
    result = await execute_short(
        {"code": code, "language": "bash"},
        _ctx(backend),
        location="local",
    )

    assert result.success is True
    assert (result.metadata or {}).get("code") != "project_verify_redirect"
    assert len(backend.requests) == 1


async def test_code_execute_blocks_long_running_without_sandbox():
    backend = _FakeBackend(
        ExecutionResult(success=True, stdout="should-not-run\n", stderr="", exit_code=0, duration_ms=1)
    )
    result = await execute_short(
        {"code": "npm run dev", "language": "bash"},
        _ctx(backend),
        location="local",
    )

    assert result.success is False
    assert result.contract_failure is True
    assert result.metadata.get("code") == "long_running_redirect"
    err = result.error or ""
    assert "background=true" in err
    assert "wait_for" in err
    assert "terminal" not in err
    assert backend.requests == []


async def test_code_execute_long_running_server_message_points_to_run():
    backend = _FakeBackend(
        ExecutionResult(success=True, stdout="", stderr="", exit_code=0, duration_ms=1)
    )
    result = await execute_short(
        {"code": "next dev", "language": "bash"},
        _ctx(backend),
        location="server",
    )

    assert result.success is False
    assert result.contract_failure is True
    err = result.error or ""
    assert "background=true" in err
    assert "wait_for" in err
    assert "terminal" not in err
    assert "subcommand=start" not in err
    assert backend.requests == []


async def test_code_execute_launcher_unavailable_is_contract_failure():
    """Missing/rejected launcher (exit 127) must not burn the circuit breaker."""
    backend = _FakeBackend(
        ExecutionResult(
            success=False,
            stdout="",
            stderr=(
                "代码执行环境启动失败：找不到可用的命令 'bash'。"
                " 本机没有可用的 bash（Windows 上 PATH 的 bash 常是不可用的 WSL 蹦床）。"
                "请改用 language=javascript 或 python 直接跑代码，不要用 bash 外壳包一层。"
            ),
            exit_code=127,
            duration_ms=1,
        )
    )
    result = await execute_short(
        {"code": "print(42)", "language": "bash"},
        _ctx(backend),
        location="local",
    )

    assert result.success is False
    assert result.contract_failure is True
    assert result.metadata.get("code") == "launcher_unavailable"
    assert result.display is not None
    assert result.display["exit_code"] == 127


async def test_code_execute_nonzero_exit_without_launcher_msg_not_contract():
    """Ordinary script failure (e.g. exit 1) must still count toward the breaker."""
    backend = _FakeBackend(
        ExecutionResult(
            success=False,
            stdout="",
            stderr="Error: fail",
            exit_code=1,
            duration_ms=5,
        )
    )
    result = await execute_short(
        {"code": "throw new Error('fail')", "language": "javascript"},
        _ctx(backend),
    )
    assert result.success is False
    assert result.contract_failure is False
    assert result.metadata == {}


async def test_code_execute_sandbox_network_unsupported_is_permanent_retire():
    """gVisor rootless network unsupported → permanent retire of run."""
    backend = _FakeBackend(
        ExecutionResult(
            success=False,
            stdout="",
            stderr="creating sandbox: sandbox network isn't supported with --rootless",
            exit_code=128,
            duration_ms=5,
        )
    )
    result = await execute_short(
        {"code": "print(1)", "language": "python"},
        _ctx(backend),
    )
    assert result.success is False
    assert result.contract_failure is False
    assert result.metadata.get("error_class") == "permanent"
    assert result.metadata.get("code") == "sandbox_network_unsupported"
    assert "retire_tools" not in (result.metadata or {})


async def test_code_execute_forwards_env_to_backend():
    backend = _FakeBackend(
        ExecutionResult(success=True, stdout="ok\n", stderr="", exit_code=0, duration_ms=5)
    )
    result = await execute_short(
        {
            "code": "print('ok')",
            "language": "python",
            "env": {"AGNES_API_KEY": "opaque-secret-value-here"},
        },
        _ctx(backend),
    )
    assert result.success is True
    assert backend.requests[0].env == {"AGNES_API_KEY": "opaque-secret-value-here"}


async def test_code_execute_rejects_path_env():
    backend = _FakeBackend(
        ExecutionResult(success=True, stdout="ok\n", stderr="", exit_code=0, duration_ms=5)
    )
    result = await execute_short(
        {"code": "print(1)", "language": "python", "env": {"PATH": "/evil"}},
        _ctx(backend),
    )
    assert result.success is False
    assert result.contract_failure is True
    assert result.metadata.get("code") == "env_invalid"
    assert backend.requests == []


async def test_code_execute_scrubs_env_from_stdout():
    secret = "opaque-secret-value-here"
    backend = _FakeBackend(
        ExecutionResult(
            success=True,
            stdout=f"token={secret}\n",
            stderr="",
            exit_code=0,
            duration_ms=5,
        )
    )
    result = await execute_short(
        {
            "code": "print(1)",
            "language": "python",
            "env": {"TOKEN": secret},
        },
        _ctx(backend),
    )
    assert secret not in (result.output or "")
    assert secret not in (result.display or {}).get("stdout", "")
    assert "[REDACTED]" in (result.display or {}).get("stdout", "")


async def test_code_execute_cloud_desk_down_is_not_contract_failure():
    from agentcore.tools.sandbox.exec_env import (
        EXEC_ENV_SANDBOX_UNAVAILABLE_CODE,
        EXEC_ENV_SANDBOX_UNAVAILABLE_USER_MESSAGE,
    )

    class _Down(_FakeBackend):
        def __init__(self) -> None:
            super().__init__(
                ExecutionResult(success=True, stdout="", stderr="", exit_code=0, duration_ms=1)
            )
            self.ensure_calls = 0
            self.execute_calls = 0

        async def ensure_workspace_desk(self) -> None:
            self.ensure_calls += 1
            raise SandboxError(
                "代码执行环境启动失败",
                code=EXEC_ENV_SANDBOX_UNAVAILABLE_CODE,
            )

        async def execute(self, request: ExecutionRequest) -> ExecutionResult:
            self.execute_calls += 1
            return await super().execute(request)

    backend = _Down()
    result = await execute_short(
        {"code": "print(1)", "language": "python"},
        _ctx(backend),
        location="server",
    )
    assert result.success is False
    assert result.contract_failure is False
    assert result.metadata.get("code") == EXEC_ENV_SANDBOX_UNAVAILABLE_CODE
    assert "retire_tools" not in (result.metadata or {})
    assert result.error == EXEC_ENV_SANDBOX_UNAVAILABLE_USER_MESSAGE
    assert "本机" not in (result.error or "")
    assert backend.ensure_calls == 1
    assert backend.execute_calls == 0
