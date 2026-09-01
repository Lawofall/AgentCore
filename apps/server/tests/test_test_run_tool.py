"""Regression tests for execute_verify — bounded project verification.

Approval posture (GRANTABLE + turn-grantable + cloud withhold) is already pinned in
``test_approvals.py`` / ``test_tools_catalog.py`` — this file covers the execute-path
guards those suites do not: whitelist, framework detection, check modes, and
verify-budget ``contract_failure`` (must not feed the circuit breaker).
"""

from __future__ import annotations

from typing import Any

import pytest

from agentcore.core.errors import SandboxError
from agentcore.runtime.context.workspace_profile import WorkspaceProfile
from agentcore.tools.builtin.package_install import is_install_shaped_argv
from agentcore.tools.builtin.run_verify import (
    _ALLOWED_PREFIXES,
    _VERIFY_BUDGET_HEAVY_SECONDS,
    _VERIFY_BUDGET_SECONDS,
    _VERIFY_BUDGET_STANDARD_SECONDS,
    _base_command,
    _command_looks_like_test,
    _detect_framework,
    _format_test_output,
    _is_allowed_command,
    _is_allowed_verify_argv,
    _profile_test_argv,
    _python_argv_runner,
    _resolve_test_argv,
    _shell_command_runner,
    _test_not_passed_error,
    execute_verify,
    resolve_verify_budget_seconds,
    resolve_verify_timeouts,
    verify_coalesce_fingerprint,
)
from agentcore.tools.protocol import ToolContext
from agentcore.tools.sandbox.exec_env import (
    EXEC_DISASTER_TIMEOUT_S,
    EXEC_ENV_SANDBOX_UNAVAILABLE_CODE,
    EXEC_ENV_SANDBOX_UNAVAILABLE_USER_MESSAGE,
    EXEC_IDLE_TIMEOUT_DEFAULT_S,
    EXEC_IDLE_TIMEOUT_INSTALL_S,
)
from agentcore.tools.sandbox.protocol import ExecutionRequest, ExecutionResult
from agentcore.workspace.protocol import PathNotFound


class _FakeBackend:
    """Minimal workspace stub: ``exists`` / ``files`` control ``read``."""

    def __init__(
        self,
        exists: set[str] | None = None,
        *,
        files: dict[str, str] | None = None,
        result: ExecutionResult | None = None,
        location: str = "server",
    ) -> None:
        self.location = location
        self._exists = exists or set()
        self._files = files or {}
        self.requests: list[ExecutionRequest] = []
        self._result = result or ExecutionResult(
            success=True, stdout="1 passed\n", stderr="", exit_code=0, duration_ms=1
        )

    async def read(self, path: str) -> bytes:
        norm = path.replace("\\", "/")
        if norm in self._files:
            return self._files[norm].encode("utf-8")
        if path in self._files:
            return self._files[path].encode("utf-8")
        if norm in self._exists or path in self._exists:
            return b""
        raise PathNotFound(path)

    async def execute(self, request: ExecutionRequest) -> ExecutionResult:
        self.requests.append(request)
        return self._result

    async def index_files(self, *, cap: int = 50, order: str = "recent"):
        return [], 0


def _ctx(backend: _FakeBackend) -> ToolContext:
    return ToolContext.create(
        execution_id="e",
        run_id="s",
        agent_id="a",
        backend=backend,  # type: ignore[arg-type]
        user_id="u",
    )


def _make_profile(**kwargs: Any) -> WorkspaceProfile:
    defaults: dict[str, Any] = {
        "languages": [],
        "frameworks": [],
        "package_managers": [],
        "test_commands": [],
    }
    defaults.update(kwargs)
    return WorkspaceProfile(**defaults)


# --- approval posture lives on ``run`` (test_approvals / test_tools_catalog) ---


def test_verify_timeouts_idle_and_disaster():
    """活性为主、灾难顶为辅；废弃 300/600「验证预算」分档."""
    disaster, idle = resolve_verify_timeouts("test")
    assert disaster == EXEC_DISASTER_TIMEOUT_S == 1200
    assert idle == EXEC_IDLE_TIMEOUT_DEFAULT_S == 60
    disaster_i, idle_i = resolve_verify_timeouts("install")
    assert disaster_i == EXEC_DISASTER_TIMEOUT_S
    assert idle_i == EXEC_IDLE_TIMEOUT_INSTALL_S == 120
    # Deprecated alias still returns disaster ceiling for all checks.
    assert resolve_verify_budget_seconds("typecheck") == EXEC_DISASTER_TIMEOUT_S
    assert resolve_verify_budget_seconds("build") == EXEC_DISASTER_TIMEOUT_S
    assert resolve_verify_budget_seconds("install") == EXEC_DISASTER_TIMEOUT_S
    assert resolve_verify_budget_seconds("test") == EXEC_DISASTER_TIMEOUT_S
    assert (
        resolve_verify_budget_seconds("command", ["npx", "tsc", "--noEmit"])
        == EXEC_DISASTER_TIMEOUT_S
    )
    assert _VERIFY_BUDGET_SECONDS == _VERIFY_BUDGET_STANDARD_SECONDS == EXEC_DISASTER_TIMEOUT_S
    assert _VERIFY_BUDGET_HEAVY_SECONDS == EXEC_DISASTER_TIMEOUT_S


def test_command_install_shaped_idle_is_120():
    """``check=command`` + install-shaped argv uses install idle; ``pnpm test`` stays 60."""
    _, idle_install = resolve_verify_timeouts("command", ["pnpm", "install"])
    assert idle_install == EXEC_IDLE_TIMEOUT_INSTALL_S == 120
    _, idle_add = resolve_verify_timeouts("command", ["pnpm", "add", "lodash"])
    assert idle_add == EXEC_IDLE_TIMEOUT_INSTALL_S
    _, idle_uv = resolve_verify_timeouts("command", ["uv", "sync"])
    assert idle_uv == EXEC_IDLE_TIMEOUT_INSTALL_S
    _, idle_test = resolve_verify_timeouts("command", ["pnpm", "test"])
    assert idle_test == EXEC_IDLE_TIMEOUT_DEFAULT_S == 60
    _, idle_bare = resolve_verify_timeouts("command")
    assert idle_bare == EXEC_IDLE_TIMEOUT_DEFAULT_S


# --- command whitelist ---


def test_allowed_prefixes_cover_supported_runners():
    prefixes = set(_ALLOWED_PREFIXES)
    assert ("pytest",) in prefixes
    assert ("python", "-m", "pytest") in prefixes
    assert ("uv", "run", "pytest") in prefixes
    assert ("npx", "vitest") in prefixes
    assert ("npx", "jest") in prefixes
    assert ("pnpm", "test") in prefixes
    assert ("npm", "test") in prefixes
    assert ("vitest",) in prefixes
    assert ("jest",) in prefixes
    assert ("npx", "tsc") in prefixes
    assert ("npm", "run", "build") in prefixes
    assert ("pnpm", "run", "typecheck") in prefixes
    assert ("npm", "install") in prefixes
    assert ("pnpm", "ci") in prefixes
    assert ("yarn", "install") in prefixes


@pytest.mark.parametrize(
    "argv",
    [
        ["pytest", "--tb=short", "-q"],
        ["python", "-m", "pytest", "-q"],
        ["uv", "run", "pytest", "--tb=short", "-q"],
        ["npx", "vitest", "run"],
        ["npx", "jest"],
        ["pnpm", "test"],
        ["npm", "test", "--", "foo"],
        ["vitest", "run"],
        ["jest", "--coverage"],
        ["npx", "tsc", "--noEmit"],
        ["npm", "run", "build"],
        ["pnpm", "run", "typecheck"],
        ["cargo", "check"],
        ["npm", "install"],
        ["npm", "ci"],
        ["pnpm", "install"],
        ["pnpm", "ci"],
        ["yarn", "install"],
        ["npm", "--prefix", "apps/web", "install"],
        ["pnpm", "--dir", "packages/ui", "install"],
        ["yarn", "--cwd", "frontend", "install"],
        ["node", "--test"],
        ["node", "--test", "test/foo.test.js"],
        ["npm", "--prefix", "_verify/repo", "test"],
        ["npm", "--prefix", "_verify/repo", "run", "test"],
    ],
)
def test_is_allowed_command_accepts_whitelisted_prefixes(argv: list[str]):
    assert _is_allowed_command(argv) is True
    assert _is_allowed_verify_argv(argv) is True


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["bash", "-c", "rm -rf /"],
        ["curl", "https://evil.example"],
        ["python", "-c", "import os; os.system('id')"],
        ["python", "script.py"],  # not ``python -m pytest``
        ["npx", "eslint"],  # npx alone is not enough — must be vitest/jest/tsc
        ["node", "-e", "1"],
        ["sh", "-c", "pytest"],
        ["sudo", "pytest"],
        ["npm", "run", "dev"],  # long-running — not verify
        ["npm", "install", "--registry", "https://evil.example/"],
        ["npm", "--prefix", "../escape", "install"],
        ["npm", "--prefix", "/etc", "install"],
        ["npm", "--prefix", "_verify/repo", "run", "start"],  # arbitrary script
        ["node", "--test", "/tmp/x.test.js"],  # absolute path
        ["node", "--test", "../escape.test.js"],
        ["npm", "--prefix", "/etc", "test"],
        ["git", "apply", "p.patch"],
    ],
)
def test_is_allowed_command_rejects_non_whitelisted(argv: list[str]):
    assert _is_allowed_command(argv) is False


def test_npm_prefix_test_is_not_install_shaped():
    """``--prefix`` + test must not be classified as the install channel."""
    argv = ["npm", "--prefix", "_verify/repo", "test"]
    assert is_install_shaped_argv(argv) is False
    assert _is_allowed_command(argv) is True
    assert _is_allowed_verify_argv(argv) is True


@pytest.mark.parametrize(
    "argv",
    [
        ["npm", "--prefix", "_verify/repo", "run", "start"],
        ["node", "--test", "/tmp/x.test.js"],
        ["git", "apply", "p.patch"],
    ],
)
def test_node_and_prefix_test_still_reject_unsafe(argv: list[str]):
    assert _is_allowed_command(argv) is False
    assert _is_allowed_verify_argv(argv) is False


def test_base_command_always_produces_allowed_argv():
    for framework in ("pytest", "vitest", "jest"):
        for pm in ([], ["uv"], ["npm"]):
            argv = _base_command(framework, _make_profile(package_managers=pm))  # type: ignore[arg-type]
            assert _is_allowed_command(argv), (framework, pm, argv)


def test_python_argv_runner_embeds_argv_without_bash():
    code = _python_argv_runner(["npx", "tsc", "--noEmit"])
    assert "bash" not in code
    assert "npx" in code
    assert "subprocess.run" in code


def test_python_argv_runner_does_not_shell_whole_argv():
    code = _python_argv_runner(["pnpm", "add", "vitest"])
    assert "shell=True" not in code
    assert "list2cmdline(argv)" not in code
    assert "list2cmdline" not in code
    assert "ComSpec" in code
    assert "/c" in code


def test_shell_command_runner_is_bash_or_powershell_not_cmd():
    code = _shell_command_runner("pnpm test")
    assert "-lc" in code
    assert "-NoProfile" in code
    assert "-NonInteractive" in code
    assert "-Command" in code
    assert "shell=True" not in code
    assert "list2cmdline" not in code
    assert "cmd.exe" not in code
    assert "/c" not in code
    assert "system32" in code
    assert "Git" in code


async def test_execute_rejects_when_command_leaves_whitelist(
    monkeypatch: pytest.MonkeyPatch,
):
    backend = _FakeBackend(exists={"pyproject.toml"})

    async def _fake_profile(_backend):
        # Empty test_commands so resolve falls back to mocked _base_command.
        return _make_profile(languages=["python"], test_commands=[])

    async def _framework(_backend, _prof, _arg):
        return "pytest"

    monkeypatch.setattr(
        "agentcore.tools.builtin.run_verify.detect_workspace_profile",
        _fake_profile,
    )
    monkeypatch.setattr(
        "agentcore.tools.builtin.run_verify._detect_framework",
        _framework,
    )
    monkeypatch.setattr(
        "agentcore.tools.builtin.run_verify._base_command",
        lambda *_a, **_k: ["bash", "-c", "evil"],
    )

    result = await execute_verify({"scope": "all"}, _ctx(backend))
    assert result.success is False
    assert "白名单" in (result.error or "")
    assert backend.requests == []  # never reached the sandbox
    assert result.contract_failure is True


# --- framework detection ---


async def test_detect_framework_honors_explicit_arg():
    backend = _FakeBackend()
    assert await _detect_framework(backend, _make_profile(), "pytest") == "pytest"
    assert await _detect_framework(backend, _make_profile(), "vitest") == "vitest"
    assert await _detect_framework(backend, _make_profile(), "jest") == "jest"


async def test_detect_framework_from_profile_test_commands():
    backend = _FakeBackend()
    assert (
        await _detect_framework(
            backend, _make_profile(test_commands=["uv run pytest -q"]), "auto"
        )
        == "pytest"
    )
    assert (
        await _detect_framework(
            backend, _make_profile(test_commands=["npx vitest run"]), "auto"
        )
        == "vitest"
    )
    assert (
        await _detect_framework(
            backend, _make_profile(test_commands=["npx jest"]), "auto"
        )
        == "jest"
    )
    # Bare npm/pnpm test must NOT imply jest — scripts.test body decides.
    assert (
        await _detect_framework(
            backend, _make_profile(test_commands=["npm test"]), "auto"
        )
        is None
    )


async def test_detect_framework_from_package_scripts_test_body():
    vitest_pkg = '{"scripts":{"test":"vitest run"}}'
    jest_pkg = '{"scripts":{"test":"jest --coverage"}}'
    assert (
        await _detect_framework(
            _FakeBackend(files={"package.json": vitest_pkg}),
            _make_profile(test_commands=["npm test"]),
            "auto",
        )
        == "vitest"
    )
    assert (
        await _detect_framework(
            _FakeBackend(files={"package.json": jest_pkg}),
            _make_profile(test_commands=["pnpm test"]),
            "auto",
        )
        == "jest"
    )


async def test_detect_framework_from_config_files():
    assert (
        await _detect_framework(
            _FakeBackend(exists={"vitest.config.ts"}), _make_profile(), "auto"
        )
        == "vitest"
    )
    assert (
        await _detect_framework(
            _FakeBackend(exists={"jest.config.js"}), _make_profile(), "auto"
        )
        == "jest"
    )
    assert (
        await _detect_framework(
            _FakeBackend(exists={"pyproject.toml"}), _make_profile(), "auto"
        )
        == "pytest"
    )
    # Bare package.json must NOT default to jest.
    assert (
        await _detect_framework(
            _FakeBackend(exists={"package.json"}), _make_profile(), "auto"
        )
        is None
    )


async def test_detect_framework_returns_none_when_unknown():
    assert await _detect_framework(_FakeBackend(), _make_profile(), "auto") is None


async def test_profile_test_argv_prefers_whitelist_script():
    assert _profile_test_argv(_make_profile(test_commands=["pnpm test"])) == [
        "pnpm",
        "test",
    ]
    assert _profile_test_argv(_make_profile(test_commands=["npx vitest run"])) == [
        "npx",
        "vitest",
        "run",
    ]
    assert _profile_test_argv(_make_profile(test_commands=["bash -c evil"])) is None


async def test_resolve_test_argv_prefers_profile_over_base_command():
    """Vitest repo with scripts.test must run ``pnpm test``, not ``npx jest``."""
    backend = _FakeBackend(
        files={"package.json": '{"scripts":{"test":"vitest run"}}'},
    )
    profile = _make_profile(
        package_managers=["pnpm"],
        test_commands=["pnpm test"],
    )
    argv, framework, err = await _resolve_test_argv(
        backend=backend,
        profile=profile,
        arguments={"scope": "all"},
    )
    assert err is None
    assert framework == "vitest"
    assert argv == ["pnpm", "test"]
    assert "jest" not in argv


async def test_resolve_test_argv_falls_back_to_base_command_without_profile():
    backend = _FakeBackend(exists={"vitest.config.ts"})
    argv, framework, err = await _resolve_test_argv(
        backend=backend,
        profile=_make_profile(),
        arguments={"scope": "all"},
    )
    assert err is None
    assert framework == "vitest"
    assert argv == ["npx", "vitest", "run"]


async def test_execute_fails_cleanly_when_framework_undetectable(
    monkeypatch: pytest.MonkeyPatch,
):
    backend = _FakeBackend()

    async def _empty_profile(_backend):
        return _make_profile()

    monkeypatch.setattr(
        "agentcore.tools.builtin.run_verify.detect_workspace_profile",
        _empty_profile,
    )
    result = await execute_verify({"scope": "all"}, _ctx(backend))
    assert result.success is False
    assert "无法检测" in (result.error or "")
    assert backend.requests == []
    assert result.contract_failure is True


# --- bounded verify modes ---


async def test_check_command_runs_via_shell_runner(
    monkeypatch: pytest.MonkeyPatch,
):
    backend = _FakeBackend(
        result=ExecutionResult(
            success=True, stdout="", stderr="", exit_code=0, duration_ms=12
        )
    )

    async def _fake_profile(_backend):
        return _make_profile()

    monkeypatch.setattr(
        "agentcore.tools.builtin.run_verify.detect_workspace_profile",
        _fake_profile,
    )
    result = await execute_verify(
        {"check": "command", "command": "npx tsc --noEmit"},
        _ctx(backend),
    )
    assert result.success is True
    assert len(backend.requests) == 1
    req = backend.requests[0]
    assert req.language == "python"
    assert req.timeout_seconds == _VERIFY_BUDGET_HEAVY_SECONDS
    assert "npx" in req.code and "tsc" in req.code
    assert "-lc" in req.code
    assert "-NoProfile" in req.code
    assert "shell=True" not in req.code
    assert "list2cmdline" not in req.code
    assert "cmd.exe" not in req.code
    assert "## 验证结果：通过" in result.output
    assert "退出码 0" in result.output
    assert "### 摘要" not in result.output
    assert "验证预算" not in result.output
    assert result.contract_failure is False


async def test_check_build_uses_profile_build_command(
    monkeypatch: pytest.MonkeyPatch,
):
    backend = _FakeBackend(
        exists={"package.json"},
        result=ExecutionResult(
            success=True, stdout="built\n", stderr="", exit_code=0, duration_ms=20
        ),
    )

    async def _fake_profile(_backend):
        return _make_profile(
            package_managers=["npm"],
            build_commands=["npm run build"],
        )

    monkeypatch.setattr(
        "agentcore.tools.builtin.run_verify.detect_workspace_profile",
        _fake_profile,
    )
    result = await execute_verify({"check": "build"}, _ctx(backend))
    assert result.success is True
    assert "npm" in backend.requests[0].code
    assert "build" in backend.requests[0].code
    assert backend.requests[0].timeout_seconds == _VERIFY_BUDGET_HEAVY_SECONDS
    assert result.metadata is not None
    assert result.metadata.get("check") == "build"


async def test_idle_timeout_is_exec_env_not_contract_failure(
    monkeypatch: pytest.MonkeyPatch,
):
    """静默挂起 → exec_timeout + exec_env_timeout；不进 contract_failure."""
    backend = _FakeBackend(
        result=ExecutionResult(
            success=False,
            stdout="",
            stderr="Timeout: no output for 60s (execution stalled)",
            exit_code=-1,
            duration_ms=60_000,
        )
    )

    async def _fake_profile(_backend):
        return _make_profile()

    monkeypatch.setattr(
        "agentcore.tools.builtin.run_verify.detect_workspace_profile",
        _fake_profile,
    )
    result = await execute_verify(
        {"check": "command", "command": "npm run build"},
        _ctx(backend),
    )
    assert result.success is False
    assert result.contract_failure is False
    assert result.metadata is not None
    assert result.metadata.get("code") == "exec_timeout"
    assert result.metadata.get("exec_env_timeout") is True
    assert result.metadata.get("timeout_kind") == "idle"
    assert "无输出" in (result.error or "") or "无响应" in (result.output or "")
    assert "验证结果：未完成（执行无响应）" in result.output
    assert "验证预算" not in (result.output or "")
    assert "缩小范围" not in (result.output or "")
    assert backend.requests[0].idle_timeout_seconds == EXEC_IDLE_TIMEOUT_DEFAULT_S
    assert backend.requests[0].timeout_seconds == EXEC_DISASTER_TIMEOUT_S


async def test_disaster_timeout_is_contract_failure_not_tool_breakage(
    monkeypatch: pytest.MonkeyPatch,
):
    """灾难顶强制中止 = 验证未完成 → contract_failure；不得当作「工具坏了」进熔断."""
    backend = _FakeBackend(
        result=ExecutionResult(
            success=False,
            stdout="",
            stderr=f"Timeout: forced stop after {_VERIFY_BUDGET_SECONDS}s (forced stop)",
            exit_code=-1,
            duration_ms=_VERIFY_BUDGET_SECONDS * 1000,
        )
    )

    async def _fake_profile(_backend):
        return _make_profile()

    monkeypatch.setattr(
        "agentcore.tools.builtin.run_verify.detect_workspace_profile",
        _fake_profile,
    )
    result = await execute_verify(
        {"check": "command", "command": "npm run build"},
        _ctx(backend),
    )
    assert result.success is False
    assert result.contract_failure is True
    assert result.metadata is not None
    assert result.metadata.get("code") == "exec_forced_stop"
    assert result.metadata.get("exec_env_timeout") is not True
    assert result.metadata.get("timeout_kind") == "disaster"
    assert "灾难顶" in (result.error or "") or "强制中止" in (result.error or "")
    assert "验证结果：未完成（强制中止）" in result.output
    assert "验证预算" not in (result.output or "")
    assert "缩小范围" not in (result.output or "")


async def test_check_command_missing_is_contract_failure():
    result = await execute_verify({"check": "command"}, _ctx(_FakeBackend()))
    assert result.success is False
    assert result.contract_failure is True
    assert "command" in (result.error or "")


async def test_default_check_test_still_parses_pytest(
    monkeypatch: pytest.MonkeyPatch,
):
    backend = _FakeBackend(
        exists={"pyproject.toml"},
        result=ExecutionResult(
            success=True,
            stdout="2 passed\n",
            stderr="",
            exit_code=0,
            duration_ms=5,
        ),
    )

    async def _fake_profile(_backend):
        return _make_profile(languages=["python"], test_commands=["pytest"])

    monkeypatch.setattr(
        "agentcore.tools.builtin.run_verify.detect_workspace_profile",
        _fake_profile,
    )
    result = await execute_verify({"scope": "all"}, _ctx(backend))
    assert result.success is True
    assert backend.requests[0].language == "python"
    assert "通过" in result.output


def _auto_permission_ctx(backend: _FakeBackend) -> ToolContext:
    return ToolContext.create(
        execution_id="e",
        run_id="s",
        agent_id="a",
        backend=backend,  # type: ignore[arg-type]
        user_id="u",
        permission_axes='{"file_write":"session","command":"auto","team_kickoff":"rules","host":"session"}',
    )


async def test_check_install_runs_with_restricted_network_and_registry_pin(
    monkeypatch: pytest.MonkeyPatch,
):
    backend = _FakeBackend(
        exists={"package.json"},
        result=ExecutionResult(
            success=True, stdout="added 1\n", stderr="", exit_code=0, duration_ms=50
        ),
    )

    async def _fake_profile(_backend):
        return _make_profile(package_managers=["npm"])

    monkeypatch.setattr(
        "agentcore.tools.builtin.run_verify.detect_workspace_profile",
        _fake_profile,
    )
    result = await execute_verify({"check": "install"}, _auto_permission_ctx(backend))
    assert result.success is True
    assert len(backend.requests) == 1
    req = backend.requests[0]
    assert req.network_mode == "restricted"
    assert req.cache_bucket == "u"
    assert req.timeout_seconds == _VERIFY_BUDGET_SECONDS
    assert req.idle_timeout_seconds == EXEC_IDLE_TIMEOUT_INSTALL_S
    assert req.env is not None
    assert "registry.npmjs.org" in (req.env.get("NPM_CONFIG_REGISTRY") or "")
    assert req.env.get("NPM_CONFIG_CACHE", "").startswith("/pkg-cache")
    assert "npm" in req.code and "install" in req.code
    assert "-lc" not in req.code


async def test_check_install_pure_python_resolves_uv_not_npm(
    monkeypatch: pytest.MonkeyPatch,
):
    backend = _FakeBackend(
        exists={"pyproject.toml"},
        result=ExecutionResult(
            success=True, stdout="Resolved\n", stderr="", exit_code=0, duration_ms=50
        ),
    )

    async def _fake_profile(_backend):
        return _make_profile(package_managers=["uv"], languages=["python"])

    monkeypatch.setattr(
        "agentcore.tools.builtin.run_verify.detect_workspace_profile",
        _fake_profile,
    )
    result = await execute_verify({"check": "install"}, _auto_permission_ctx(backend))
    assert result.success is True
    req = backend.requests[0]
    assert "uv" in req.code and "sync" in req.code
    assert "npm" not in req.code
    assert req.env is not None
    assert "pypi.org" in (req.env.get("PIP_INDEX_URL") or "")
    assert req.env.get("UV_CACHE_DIR", "").startswith("/pkg-cache")


async def test_check_install_omits_cache_bucket_without_user_id(
    monkeypatch: pytest.MonkeyPatch,
):
    backend = _FakeBackend(
        exists={"package.json"},
        result=ExecutionResult(
            success=True, stdout="added 1\n", stderr="", exit_code=0, duration_ms=50
        ),
    )

    async def _fake_profile(_backend):
        return _make_profile(package_managers=["npm"])

    monkeypatch.setattr(
        "agentcore.tools.builtin.run_verify.detect_workspace_profile",
        _fake_profile,
    )
    ctx = ToolContext.create(
        execution_id="e",
        run_id="s",
        agent_id="a",
        backend=backend,  # type: ignore[arg-type]
        user_id="",
        permission_axes='{"file_write":"session","command":"auto","team_kickoff":"rules","host":"session"}',
    )
    result = await execute_verify({"check": "install"}, ctx)
    assert result.success is True
    assert backend.requests[0].cache_bucket is None


async def test_check_install_rejects_without_restricted_network(
    monkeypatch: pytest.MonkeyPatch,
):
    backend = _FakeBackend(exists={"package.json"})

    async def _fake_profile(_backend):
        return _make_profile(package_managers=["npm"])

    monkeypatch.setattr(
        "agentcore.tools.builtin.run_verify.detect_workspace_profile",
        _fake_profile,
    )
    # Default ctx: no permission_axes → network_mode would be none
    result = await execute_verify({"check": "install"}, _ctx(backend))
    assert result.success is False
    assert result.contract_failure is True
    assert result.metadata is not None
    assert result.metadata.get("code") == "install_network_unavailable"
    assert "无法装包" in (result.error or "")
    assert "test_run" not in (result.error or "")
    assert backend.requests == []


async def test_check_install_local_skips_host_egress_gate(
    monkeypatch: pytest.MonkeyPatch,
):
    """Local backend must not require API-host gVisor egress availability."""
    backend = _FakeBackend(
        exists={"package.json"},
        location="local",
        result=ExecutionResult(
            success=True, stdout="added 1\n", stderr="", exit_code=0, duration_ms=50
        ),
    )

    async def _fake_profile(_backend):
        return _make_profile(package_managers=["npm"])

    monkeypatch.setattr(
        "agentcore.tools.builtin.run_verify.detect_workspace_profile",
        _fake_profile,
    )
    result = await execute_verify({"check": "install"}, _auto_permission_ctx(backend))
    assert result.success is True
    req = backend.requests[0]
    assert req.cache_bucket is None
    assert req.env is not None
    assert "registry.npmjs.org" in (req.env.get("NPM_CONFIG_REGISTRY") or "")
    assert "NPM_CONFIG_CACHE" not in req.env


async def test_check_install_local_still_requires_permission(
    monkeypatch: pytest.MonkeyPatch,
):
    backend = _FakeBackend(exists={"package.json"}, location="local")

    async def _fake_profile(_backend):
        return _make_profile(package_managers=["npm"])

    monkeypatch.setattr(
        "agentcore.tools.builtin.run_verify.detect_workspace_profile",
        _fake_profile,
    )
    result = await execute_verify({"check": "install"}, _ctx(backend))
    assert result.success is False
    assert result.contract_failure is True
    assert result.metadata is not None
    assert result.metadata.get("code") == "install_network_unavailable"
    assert backend.requests == []


async def test_command_install_allows_cd_and_runs_shell(
    monkeypatch: pytest.MonkeyPatch,
):
    backend = _FakeBackend(
        result=ExecutionResult(
            success=True, stdout="", stderr="", exit_code=0, duration_ms=20
        )
    )

    async def _fake_profile(_backend):
        return _make_profile()

    monkeypatch.setattr(
        "agentcore.tools.builtin.run_verify.detect_workspace_profile",
        _fake_profile,
    )
    result = await execute_verify(
        {"check": "command", "command": "cd apps/web && npm install"},
        _auto_permission_ctx(backend),
    )
    assert result.success is True
    assert backend.requests
    code = backend.requests[0].code
    assert "cd apps/web && npm install" in code
    assert "-lc" in code
    assert "input_bytes=first.stdout" not in code


async def test_command_install_keeps_tail_and_stderr_merge(
    monkeypatch: pytest.MonkeyPatch,
):
    backend = _FakeBackend(
        result=ExecutionResult(
            success=True, stdout="", stderr="", exit_code=0, duration_ms=20
        )
    )

    async def _fake_profile(_backend):
        return _make_profile()

    monkeypatch.setattr(
        "agentcore.tools.builtin.run_verify.detect_workspace_profile",
        _fake_profile,
    )
    result = await execute_verify(
        {"check": "command", "command": "pnpm add vitest 2>&1 | tail -20"},
        _auto_permission_ctx(backend),
    )
    assert result.success is True
    assert backend.requests
    assert "tail" in backend.requests[0].code
    assert "2>&1" in backend.requests[0].code
    assert "pnpm add vitest 2>&1 | tail -20" in backend.requests[0].code


async def test_command_test_grep_executes_as_shell_not_stripped(
    monkeypatch: pytest.MonkeyPatch,
):
    backend = _FakeBackend(
        result=ExecutionResult(
            success=True, stdout="ok\n", stderr="", exit_code=0, duration_ms=20
        )
    )

    async def _fake_profile(_backend):
        return _make_profile()

    monkeypatch.setattr(
        "agentcore.tools.builtin.run_verify.detect_workspace_profile",
        _fake_profile,
    )
    result = await execute_verify(
        {"check": "command", "command": "pnpm test | grep FAIL"},
        _ctx(backend),
    )
    assert result.success is True
    code = backend.requests[0].code
    assert "pnpm test | grep FAIL" in code
    assert "grep" in code
    assert "FAIL" in code
    assert "input_bytes=first.stdout" not in code
    assert "-lc" in code


async def test_command_install_allows_real_pipe_and_redirect(
    monkeypatch: pytest.MonkeyPatch,
):
    backend = _FakeBackend(
        result=ExecutionResult(
            success=True, stdout="", stderr="", exit_code=0, duration_ms=10
        )
    )

    async def _fake_profile(_backend):
        return _make_profile()

    monkeypatch.setattr(
        "agentcore.tools.builtin.run_verify.detect_workspace_profile",
        _fake_profile,
    )
    for command in ("pnpm add vitest | grep x", "pnpm install > log.txt"):
        backend.requests.clear()
        result = await execute_verify(
            {"check": "command", "command": command},
            _auto_permission_ctx(backend),
        )
        assert result.success is True
        assert backend.requests
        assert command in backend.requests[0].code


async def test_command_cd_dotdot_from_root_refused(
    monkeypatch: pytest.MonkeyPatch,
):
    backend = _FakeBackend()

    async def _fake_profile(_backend):
        return _make_profile()

    monkeypatch.setattr(
        "agentcore.tools.builtin.run_verify.detect_workspace_profile",
        _fake_profile,
    )
    result = await execute_verify(
        {"check": "command", "command": "cd .. && pnpm test"},
        _ctx(backend),
    )
    assert result.success is False
    assert result.contract_failure is True
    assert "工作区" in (result.error or "")
    assert backend.requests == []


@pytest.mark.parametrize(
    ("command", "idle"),
    [
        ("pnpm install", EXEC_IDLE_TIMEOUT_INSTALL_S),
        ("pnpm add lodash", EXEC_IDLE_TIMEOUT_INSTALL_S),
        ("uv sync", EXEC_IDLE_TIMEOUT_INSTALL_S),
        ("pnpm test", EXEC_IDLE_TIMEOUT_DEFAULT_S),
    ],
)
async def test_check_command_idle_follows_install_argv(
    monkeypatch: pytest.MonkeyPatch,
    command: str,
    idle: int,
):
    backend = _FakeBackend(
        result=ExecutionResult(
            success=True, stdout="", stderr="", exit_code=0, duration_ms=10
        )
    )

    async def _fake_profile(_backend):
        return _make_profile()

    monkeypatch.setattr(
        "agentcore.tools.builtin.run_verify.detect_workspace_profile",
        _fake_profile,
    )
    ctx = (
        _auto_permission_ctx(backend)
        if idle == EXEC_IDLE_TIMEOUT_INSTALL_S
        else _ctx(backend)
    )
    result = await execute_verify(
        {"check": "command", "command": command},
        ctx,
    )
    assert result.success is True
    assert backend.requests[0].idle_timeout_seconds == idle


def test_command_looks_like_test_skips_install_shaped():
    assert _command_looks_like_test(["pnpm", "add", "-Dw", "vitest"]) is False
    assert _command_looks_like_test(["uv", "add", "pytest"]) is False
    assert _command_looks_like_test(["pnpm", "test"]) is True


def test_test_not_passed_error_leads_with_red_counts():
    assert _test_not_passed_error(failed=1, errors=0, exit_code=0) == "测试未通过（失败 1）"
    assert _test_not_passed_error(failed=4, errors=1, exit_code=1) == "测试未通过（失败 4，错误 1）"
    assert _test_not_passed_error(failed=0, errors=0, exit_code=1) == "测试未通过（退出码 1）"


def test_format_test_output_notes_exit_zero_when_parsed_red():
    from agentcore.tools.builtin.test_parsers import TestRunResult

    body = _format_test_output(
        TestRunResult(
            framework="vitest",
            passed=0,
            failed=1,
            errors=0,
            skipped=0,
            duration_seconds=0.2,
        ),
        ["npx", "vitest", "run"],
        0.2,
        exit_code=0,
    )
    assert "退出码：0" in body
    assert "命令返回成功码，但解析到失败用例" in body


async def test_vitest_red_with_exit_zero_titles_failed_count(
    monkeypatch: pytest.MonkeyPatch,
):
    """解析到失败时标题说失败条数，不把退出码 0 写进 error。"""
    backend = _FakeBackend(
        result=ExecutionResult(
            success=True,
            stdout=(
                "FAIL packages/core/src/render/Canvas2DRenderer.test.ts > "
                "group 递归绘制 children\n"
                "AssertionError: expected array to include fillRect\n"
                "Tests 0 passed | 1 failed\n"
            ),
            stderr="",
            exit_code=0,
            duration_ms=80,
        )
    )

    async def _fake_profile(_backend):
        return _make_profile()

    monkeypatch.setattr(
        "agentcore.tools.builtin.run_verify.detect_workspace_profile",
        _fake_profile,
    )
    result = await execute_verify(
        {"check": "command", "command": "npx vitest run"},
        _ctx(backend),
    )
    assert result.success is False
    assert result.error == "测试未通过（失败 1）"
    assert "退出码 0" not in (result.error or "")
    assert "退出码：0" in (result.output or "")
    assert "解析到失败用例" in (result.output or "")


async def test_pnpm_add_vitest_does_not_parse_as_test(
    monkeypatch: pytest.MonkeyPatch,
):
    backend = _FakeBackend(
        result=ExecutionResult(
            success=False,
            stdout="",
            stderr="ERR_PNPM_UNEXPECTED_PKG\n",
            exit_code=1,
            duration_ms=20,
        )
    )

    async def _fake_profile(_backend):
        return _make_profile()

    monkeypatch.setattr(
        "agentcore.tools.builtin.run_verify.detect_workspace_profile",
        _fake_profile,
    )
    result = await execute_verify(
        {"check": "command", "command": "pnpm add -Dw vitest"},
        _auto_permission_ctx(backend),
    )
    assert result.success is False
    assert "测试结果" not in (result.output or "")
    assert "0 passed" not in (result.output or "")
    assert "验证结果" in (result.output or "")
    assert "ERR_PNPM_UNEXPECTED_PKG" in (result.output or "")
    assert "### 摘要" not in (result.output or "")
    assert "passed" not in (result.display or {})


async def test_command_install_rejects_registry_override(monkeypatch: pytest.MonkeyPatch):
    backend = _FakeBackend()

    async def _fake_profile(_backend):
        return _make_profile()

    monkeypatch.setattr(
        "agentcore.tools.builtin.run_verify.detect_workspace_profile",
        _fake_profile,
    )
    result = await execute_verify(
        {
            "check": "command",
            "command": "npm install --registry https://evil.example/",
        },
        _auto_permission_ctx(backend),
    )
    assert result.success is False
    assert "包装源" in (result.error or "") or "registry" in (result.error or "").lower()
    assert backend.requests == []


async def test_command_npm_prefix_install_allowed(monkeypatch: pytest.MonkeyPatch):
    backend = _FakeBackend(
        result=ExecutionResult(
            success=True, stdout="", stderr="", exit_code=0, duration_ms=10
        )
    )

    async def _fake_profile(_backend):
        return _make_profile()

    monkeypatch.setattr(
        "agentcore.tools.builtin.run_verify.detect_workspace_profile",
        _fake_profile,
    )
    result = await execute_verify(
        {"check": "command", "command": "npm --prefix apps/web install"},
        _auto_permission_ctx(backend),
    )
    assert result.success is True
    assert backend.requests[0].network_mode == "restricted"
    assert "--prefix" in backend.requests[0].code


async def test_working_directory_injects_npm_prefix(monkeypatch: pytest.MonkeyPatch):
    backend = _FakeBackend(
        result=ExecutionResult(
            success=True, stdout="", stderr="", exit_code=0, duration_ms=10
        )
    )

    async def _fake_profile(_backend):
        return _make_profile(package_managers=["npm"])

    monkeypatch.setattr(
        "agentcore.tools.builtin.run_verify.detect_workspace_profile",
        _fake_profile,
    )
    result = await execute_verify(
        {"check": "install", "working_directory": "apps/web"},
        _auto_permission_ctx(backend),
    )
    assert result.success is True
    assert "--prefix" in backend.requests[0].code
    assert "apps/web" in backend.requests[0].code
    assert "-lc" not in backend.requests[0].code


async def test_verify_policy_inner_refuses_typecheck(monkeypatch: pytest.MonkeyPatch):
    """Investigate/review posture must not burn minute-level full-repo tsc."""
    backend = _FakeBackend(exists={"tsconfig.json"})

    async def _fake_profile(_backend):
        return _make_profile(
            languages=["typescript"],
            typecheck_commands=["npx tsc --noEmit"],
        )

    monkeypatch.setattr(
        "agentcore.tools.builtin.run_verify.detect_workspace_profile",
        _fake_profile,
    )
    ctx = ToolContext.create(
        execution_id="e",
        run_id="s",
        agent_id="a",
        backend=backend,  # type: ignore[arg-type]
        user_id="u",
        verify_policy="inner",
    )
    result = await execute_verify({"check": "typecheck"}, ctx)
    assert result.success is False
    assert result.contract_failure is True
    assert (result.metadata or {}).get("code") == "verify_policy_inner"
    assert "code_diagnostics" in (result.error or "")
    assert "执行 run" in (result.error or "")
    assert "test_run" not in (result.error or "")
    assert backend.requests == []


def test_apply_verify_policies_does_not_guess_from_role_names():
    from agentcore.runtime.runs.types import RunSpec
    from agentcore.runtime.runs.worker_budget import apply_verify_policies_to_specs

    review = RunSpec(run_id="r1", role="渲染链路审查员", task="查 blank page")
    accept = RunSpec(run_id="r2", role="验收员", task="外环 typecheck")
    explicit = RunSpec(
        run_id="r3", role="审查员", task="x", verify_policy="outer"
    )
    apply_verify_policies_to_specs([review, accept, explicit])
    assert review.verify_policy == ""
    assert accept.verify_policy == ""
    assert explicit.verify_policy == "outer"


def test_project_verify_redirect_respects_inner_policy():
    from agentcore.tools.builtin.project_verify import project_verify_redirect_message

    outer = project_verify_redirect_message("npx tsc")
    assert "请用 run" in outer
    assert "test_run" not in outer
    assert "check=install" not in outer
    inner = project_verify_redirect_message("npx tsc", verify_policy="inner")
    assert "code_diagnostics" in inner
    assert "verify_policy=inner" in inner
    assert "test_run" not in inner
    assert "check=install" not in inner


def test_verify_inner_discipline_prompt_is_gone():
    import agentcore.runtime.runs.worker_budget as wb

    assert not hasattr(wb, "VERIFY_INNER_DISCIPLINE")


async def test_build_whitelist_unaffected_by_install_rules(
    monkeypatch: pytest.MonkeyPatch,
):
    """既有 build/typecheck 白名单不回归：无网也可跑（不强制 restricted）。"""
    backend = _FakeBackend(
        exists={"package.json"},
        result=ExecutionResult(
            success=True, stdout="built\n", stderr="", exit_code=0, duration_ms=20
        ),
    )

    async def _fake_profile(_backend):
        return _make_profile(
            package_managers=["npm"],
            build_commands=["npm run build"],
        )

    monkeypatch.setattr(
        "agentcore.tools.builtin.run_verify.detect_workspace_profile",
        _fake_profile,
    )
    result = await execute_verify({"check": "build"}, _ctx(backend))
    assert result.success is True
    assert backend.requests[0].network_mode == "none"


def test_verify_coalesce_fingerprint_stable_on_resolved_argv():
    a = verify_coalesce_fingerprint("typecheck", ["npx", "tsc", "--noEmit"], None)
    b = verify_coalesce_fingerprint("typecheck", ["npx", "tsc", "--noEmit"], "")
    c = verify_coalesce_fingerprint("typecheck", ["npx", "tsc", "--noEmit"], "apps/web")
    assert a == b
    assert a != c


async def test_sibling_verify_inflight_coalesce(monkeypatch: pytest.MonkeyPatch):
    """Two workers sharing one execution join one sandbox execute (no double burn)."""
    import asyncio

    from agentcore.runtime.coordination.session import (
        CoordinationSession,
        clear_active_coordination,
        set_active_coordination,
    )

    class _GateBackend(_FakeBackend):
        def __init__(self) -> None:
            super().__init__(
                exists={"tsconfig.json"},
                result=ExecutionResult(
                    success=False,
                    stdout="",
                    stderr="Timeout: execution exceeded 300s",
                    exit_code=-1,
                    duration_ms=300_000,
                ),
            )
            self.calls = 0
            self.entered = asyncio.Event()
            self.release = asyncio.Event()

        async def execute(self, request: ExecutionRequest) -> ExecutionResult:
            self.calls += 1
            self.requests.append(request)
            self.entered.set()
            await self.release.wait()
            return self._result

    backend = _GateBackend()

    async def _fake_profile(_backend):
        return _make_profile(
            languages=["typescript"],
            typecheck_commands=["npx tsc --noEmit"],
        )

    monkeypatch.setattr(
        "agentcore.tools.builtin.run_verify.detect_workspace_profile",
        _fake_profile,
    )

    clear_active_coordination()
    session = CoordinationSession(execution_id="e-coalesce", total_workers=2)
    session._running_workers["w1"] = "渲染"
    session._running_workers["w2"] = "存储"
    set_active_coordination(session)
    try:
        t1 = asyncio.create_task(
            execute_verify(
                {"check": "typecheck"},
                ToolContext.create(
                    execution_id="e-coalesce",
                    run_id="w1",
                    agent_id="w1",
                    backend=backend,  # type: ignore[arg-type]
                    user_id="u",
                ),
            )
        )
        await asyncio.wait_for(backend.entered.wait(), timeout=2.0)
        t2 = asyncio.create_task(
            execute_verify(
                {"check": "typecheck"},
                ToolContext.create(
                    execution_id="e-coalesce",
                    run_id="w2",
                    agent_id="w2",
                    backend=backend,  # type: ignore[arg-type]
                    user_id="u",
                ),
            )
        )
        # Second caller should be waiting on inflight before we release.
        await asyncio.sleep(0.05)
        assert backend.calls == 1
        assert session.has_verify_busy() is True
        assert session.has_inflight_work() is False
        backend.release.set()
        r1, r2 = await asyncio.gather(t1, t2)
        assert backend.calls == 1
        assert r1.contract_failure is True
        assert r2.contract_failure is True
        assert (r2.metadata or {}).get("verify_shared") == "inflight"
        assert "团队共享验证" in (r2.output or "")
        assert "烧预算" not in (r2.output or "")
        # Cache hit on a third call (no new sandbox execute).
        r3 = await execute_verify(
            {"check": "typecheck"},
            ToolContext.create(
                execution_id="e-coalesce",
                run_id="w1",
                agent_id="w1",
                backend=backend,  # type: ignore[arg-type]
                user_id="u",
            ),
        )
        assert backend.calls == 1
        assert (r3.metadata or {}).get("verify_shared") == "cache"
        # Successful land must invalidate cache so a later verify re-runs.
        from agentcore.tools.builtin.file_ops import _mark_landed_files

        _mark_landed_files(
            ToolContext.create(
                execution_id="e-coalesce",
                run_id="w1",
                agent_id="w1",
                backend=backend,  # type: ignore[arg-type]
                user_id="u",
            ),
            "src/app.ts",
            kind="skeleton",
        )
        assert session._verify_cache == {}
        r4 = await execute_verify(
            {"check": "typecheck"},
            ToolContext.create(
                execution_id="e-coalesce",
                run_id="w2",
                agent_id="w2",
                backend=backend,  # type: ignore[arg-type]
                user_id="u",
            ),
        )
        assert backend.calls == 2
        assert (r4.metadata or {}).get("verify_shared") is None
    finally:
        clear_active_coordination("e-coalesce")


async def test_test_run_cloud_desk_down_is_not_contract_failure(
    monkeypatch: pytest.MonkeyPatch,
):
    class _Down(_FakeBackend):
        async def execute(self, request: ExecutionRequest) -> ExecutionResult:
            self.requests.append(request)
            raise SandboxError(
                "代码执行环境启动失败",
                code=EXEC_ENV_SANDBOX_UNAVAILABLE_CODE,
            )

    async def _fake_profile(_backend):
        return _make_profile()

    monkeypatch.setattr(
        "agentcore.tools.builtin.run_verify.detect_workspace_profile",
        _fake_profile,
    )
    result = await execute_verify(
        {"check": "command", "command": "npx tsc --noEmit"},
        _ctx(_Down()),
    )
    assert result.success is False
    assert result.contract_failure is False
    assert result.metadata.get("code") == EXEC_ENV_SANDBOX_UNAVAILABLE_CODE
    assert "retire_tools" not in (result.metadata or {})
    assert result.error == EXEC_ENV_SANDBOX_UNAVAILABLE_USER_MESSAGE
    assert "本机" not in (result.error or "")
