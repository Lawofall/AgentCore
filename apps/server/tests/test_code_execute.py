"""Tests for the code_execute tool's structured display (工具结果富渲染).

The tool flattens stdout/stderr/exit_code into the model-facing ``output`` string,
but also carries them STRUCTURED on ``display`` so the desktop renders a terminal
view (stderr in red, exit-code badge) instead of parsing "stdout:\\n…" text. A
non-zero exit must still produce a display (so a failed run surfaces its stderr).
"""

import pytest

from agentcore.tools.builtin.code_execute import CodeExecuteTool
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
    result = await CodeExecuteTool().execute(
        {"code": "print('hello')", "language": "python"}, _ctx(backend)
    )

    assert result.success is True
    assert result.display == {
        "stdout": "hello\n",
        "stderr": "",
        "exit_code": 0,
        "language": "python",
    }


def test_code_execute_schema_advertises_env():
    props = CodeExecuteTool().schema.parameters["properties"]
    assert "env" in props
    assert props["env"]["additionalProperties"]["type"] == "string"


async def test_code_execute_emits_executing_phase():
    # 工具执行阶段进度 (联网前端展示优化): code_execute signals 「正在执行」before the (slow,
    # blocking) sandbox run so the waiting row is live instead of a dead spinner.
    backend = _FakeBackend(
        ExecutionResult(success=True, stdout="ok\n", stderr="", exit_code=0, duration_ms=5)
    )
    phases: list[str] = []
    result = await CodeExecuteTool().execute(
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
    result = await CodeExecuteTool(location="server").execute(
        {"code": "make()", "language": "python"}, _ctx(backend)
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
    result = await CodeExecuteTool().execute(
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
    result = await CodeExecuteTool(location="server").execute(
        {"code": "make()", "language": "python"}, _ctx(backend)
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
                    function=ToolCallFunction(name="code_execute", arguments="{}"),
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
    result = await CodeExecuteTool().execute({"code": "boom", "language": "python"}, _ctx(backend))

    assert result.success is False
    assert result.display is not None
    assert result.display["exit_code"] == 1
    assert "NameError" in result.display["stderr"]
    assert result.display["language"] == "python"


def test_long_running_command_match_catches_dev_servers():
    from agentcore.tools.builtin.code_execute import long_running_command_match

    assert long_running_command_match("npm run dev") is not None
    assert long_running_command_match("pnpm dev") is not None
    assert long_running_command_match("npx vite") is not None
    assert long_running_command_match("os.system('npm run start')") is not None
    # Finite / lookalike — must not trip the gate.
    assert long_running_command_match("npm install") is None
    assert long_running_command_match("npm run build") is None
    assert long_running_command_match("npm run development") is None
    assert long_running_command_match("import { defineConfig } from 'vite'") is None


def test_project_verify_command_match_routes_to_test_run():
    from agentcore.tools.builtin.code_execute import project_verify_command_match

    assert project_verify_command_match("npm install") is not None
    assert project_verify_command_match("pnpm install") is not None
    assert project_verify_command_match("pip install -r requirements.txt") is not None
    assert project_verify_command_match("uv sync") is not None
    assert project_verify_command_match("uv pip install requests") is not None
    assert project_verify_command_match("poetry install") is not None
    assert project_verify_command_match("python -m pip install flask") is not None
    assert project_verify_command_match("npx tsc --noEmit") is not None
    assert project_verify_command_match("tsc --noEmit") is not None
    assert project_verify_command_match("npm run build") is not None
    assert project_verify_command_match("npm test") is not None
    assert project_verify_command_match("pytest tests/") is not None
    # Short / lookalike — must not trip.
    assert project_verify_command_match("print(1+1)") is None
    assert project_verify_command_match("import { defineConfig } from 'vite'") is None
    assert project_verify_command_match("from 'vitest'") is None
    assert project_verify_command_match("npm run dev") is None  # long_running owns this
    assert project_verify_command_match("import pip") is None


def test_source_inspect_match_reexport():
    from agentcore.tools.builtin.code_execute import source_inspect_match

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
    result = await CodeExecuteTool().execute(
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
    assert "code_execute" in err
    assert backend.requests == []


async def test_code_execute_blocks_source_grep_without_sandbox():
    backend = _FakeBackend(
        ExecutionResult(success=True, stdout="should-not-run\n", stderr="", exit_code=0, duration_ms=1)
    )
    result = await CodeExecuteTool().execute(
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
    result = await CodeExecuteTool().execute(
        {
            "code": "import pandas as pd\ndf = pd.read_csv('a.csv')\nprint(df.head())",
            "language": "python",
        },
        _ctx(backend),
    )

    assert result.success is True
    assert len(backend.requests) == 1


async def test_code_execute_blocks_project_verify_without_sandbox():
    backend = _FakeBackend(
        ExecutionResult(success=True, stdout="should-not-run\n", stderr="", exit_code=0, duration_ms=1)
    )
    result = await CodeExecuteTool(location="local").execute(
        {"code": "npx tsc --noEmit", "language": "bash"},
        _ctx(backend),
    )

    assert result.success is False
    assert result.contract_failure is True
    assert result.metadata.get("code") == "project_verify_redirect"
    err = result.error or ""
    assert "test_run" in err
    assert "code_execute" in err
    assert backend.requests == []


async def test_code_execute_blocks_npm_install_to_test_run():
    backend = _FakeBackend(
        ExecutionResult(success=True, stdout="should-not-run\n", stderr="", exit_code=0, duration_ms=1)
    )
    result = await CodeExecuteTool(location="server").execute(
        {"code": "npm install", "language": "bash"},
        _ctx(backend),
    )

    assert result.success is False
    assert result.contract_failure is True
    assert result.metadata.get("code") == "project_verify_redirect"
    err = result.error or ""
    assert "test_run" in err
    assert "check=install" in err
    assert backend.requests == []


@pytest.mark.parametrize(
    "code",
    [
        "pip install -r requirements.txt",
        "uv sync",
        "uv pip install requests",
        "poetry install",
        "python -m pip install flask",
    ],
)
async def test_code_execute_blocks_python_install_to_test_run(code: str):
    backend = _FakeBackend(
        ExecutionResult(success=True, stdout="should-not-run\n", stderr="", exit_code=0, duration_ms=1)
    )
    result = await CodeExecuteTool(location="server").execute(
        {"code": code, "language": "bash"},
        _ctx(backend),
    )

    assert result.success is False
    assert result.contract_failure is True
    assert result.metadata.get("code") == "project_verify_redirect"
    err = result.error or ""
    assert "test_run" in err
    assert "check=install" in err
    assert backend.requests == []


async def test_code_execute_blocks_long_running_without_sandbox():
    backend = _FakeBackend(
        ExecutionResult(success=True, stdout="should-not-run\n", stderr="", exit_code=0, duration_ms=1)
    )
    result = await CodeExecuteTool(location="local").execute(
        {"code": "npm run dev", "language": "bash"},
        _ctx(backend),
    )

    assert result.success is False
    assert result.contract_failure is True
    assert result.metadata.get("code") == "long_running_redirect"
    assert "terminal" in (result.error or "")
    assert "wait_for" in (result.error or "")
    assert backend.requests == []


async def test_code_execute_long_running_server_message_points_to_terminal():
    backend = _FakeBackend(
        ExecutionResult(success=True, stdout="", stderr="", exit_code=0, duration_ms=1)
    )
    result = await CodeExecuteTool(location="server").execute(
        {"code": "next dev", "language": "bash"},
        _ctx(backend),
    )

    assert result.success is False
    assert result.contract_failure is True
    err = result.error or ""
    assert "terminal" in err
    assert "subcommand=start" in err
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
    result = await CodeExecuteTool(location="local").execute(
        {"code": "print(42)", "language": "bash"},
        _ctx(backend),
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
    result = await CodeExecuteTool().execute(
        {"code": "throw new Error('fail')", "language": "javascript"},
        _ctx(backend),
    )
    assert result.success is False
    assert result.contract_failure is False
    assert result.metadata == {}


async def test_code_execute_sandbox_network_unsupported_is_permanent_retire():
    """gVisor rootless network unsupported → permanent retire of code_execute."""
    backend = _FakeBackend(
        ExecutionResult(
            success=False,
            stdout="",
            stderr="creating sandbox: sandbox network isn't supported with --rootless",
            exit_code=128,
            duration_ms=5,
        )
    )
    result = await CodeExecuteTool().execute(
        {"code": "print(1)", "language": "python"},
        _ctx(backend),
    )
    assert result.success is False
    assert result.contract_failure is False
    assert result.metadata.get("error_class") == "permanent"
    assert result.metadata.get("code") == "sandbox_network_unsupported"
    assert result.metadata.get("retire_tools") == ["code_execute"]
    assert "沙箱网络" in (result.metadata.get("retire_message") or "")


async def test_code_execute_forwards_env_to_backend():
    backend = _FakeBackend(
        ExecutionResult(success=True, stdout="ok\n", stderr="", exit_code=0, duration_ms=5)
    )
    result = await CodeExecuteTool().execute(
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
    result = await CodeExecuteTool().execute(
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
    result = await CodeExecuteTool().execute(
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
