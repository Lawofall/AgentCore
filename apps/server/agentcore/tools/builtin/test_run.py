"""Bounded project verification — test / typecheck / build / install with idle + disaster caps.

Expands the historical ``test_run`` surface into the first-class verify tool of the
short-exec / bounded-verify / long-running triad:

- ``code_execute`` — short, self-exiting scripts only
- **this tool** — project checks (install / test / typecheck / build / explicit command)
- ``terminal`` — long-running processes

Runs through the same sandbox chain and GRANTABLE posture as ``code_execute``.
Idle hang → ``exec_timeout`` (exec-env family); disaster wall → ``exec_forced_stop`` /
``contract_failure`` (verify incomplete), not circuit-breaker fuel. Local runs launch
via a Python argv runner so Windows never defaults through a WSL bash trampoline.

Install path: registry pin / argv deny — see ``package_install``. Cloud
install rides the same desk guest as ``code_execute``. Local runs skip the
host gVisor chokepoint (desktop / sidecar policy) but still need the
permission axis; else honest 甲 degrade.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import time
from dataclasses import replace
from typing import Any, Literal

from agentcore.core.errors import SandboxError
from agentcore.core.types import ToolApproval, ToolCategory
from agentcore.runtime.context.workspace_profile import WorkspaceProfile, detect_workspace_profile
from agentcore.tools.builtin.code_execute import _permission_allows_restricted_network
from agentcore.tools.builtin.package_install import (
    apply_working_directory,
    install_cache_env,
    install_prefix_allowed,
    is_install_shaped_argv,
    is_safe_relpath,
    network_unavailable_code,
    network_unavailable_message,
    registry_pin_env,
    reject_shell_chain_command,
    resolve_install_argv,
    validate_install_argv,
)
from agentcore.tools.builtin.test_parsers import (
    TestRunResult,
    parse_generic_output,
    parse_jest_output,
    parse_pytest_output,
    parse_vitest_output,
)
from agentcore.tools.protocol import ToolContext, ToolResult, ToolSchema
from agentcore.tools.registration import (
    AUDIENCE_WORKER_ONLY,
    FileProductsContract,
    ToolRegistration,
    ToolSurface,
)
from agentcore.tools.sandbox.protocol import ExecutionRequest, ExecutionResult
from agentcore.workspace.protocol import PathNotFound, WorkspaceBackend

Framework = Literal["pytest", "vitest", "jest"]
Scope = Literal["all", "affected", "file"]
CheckKind = Literal["test", "typecheck", "build", "install", "command"]

# Outer-loop verify timeouts (定案：活性为主，灾难顶为辅；废弃「验证预算」合同):
# - idle 60s (install 120s): no stdout/stderr → hang
# - disaster 1200s: absolute safety net only
# Engine schema timeout uses disaster + slack so sandbox returns Timeout first.
# Aligned with ``settings.gvisor_timeout_max_seconds`` / desktop ``EXEC_TIMEOUT_CAP_S``.
from agentcore.tools.sandbox.exec_env import (
    _ENGINE_TIMEOUT_SLACK_SECONDS,
    EXEC_DISASTER_TIMEOUT_S,
    EXEC_FORCED_STOP_CODE,
    EXEC_IDLE_TIMEOUT_DEFAULT_S,
    EXEC_IDLE_TIMEOUT_INSTALL_S,
    EXEC_TIMEOUT_CODE,
    TIMEOUT_LEGACY_MARKER,
    is_disaster_timeout_text,
    is_idle_timeout_text,
)

_VERIFY_DISASTER_SECONDS = EXEC_DISASTER_TIMEOUT_S
# Back-compat aliases for tests importing old names (map to disaster ceiling).
_VERIFY_BUDGET_STANDARD_SECONDS = _VERIFY_DISASTER_SECONDS
_VERIFY_BUDGET_HEAVY_SECONDS = _VERIFY_DISASTER_SECONDS
_VERIFY_BUDGET_SECONDS = _VERIFY_DISASTER_SECONDS
_DEFAULT_TIMEOUT = _VERIFY_DISASTER_SECONDS

# Heavy outer-loop shape (typecheck / build) — lengthens command= budget only.
_HEAVY_VERIFY_RE = re.compile(
    r"\b(?:"
    r"tsc\b|vue-tsc\b|typecheck\b|type-check\b|"
    r"(?:npm|pnpm|yarn)\s+run\s+(?:typecheck|type-check|build)\b|"
    r"(?:npm|pnpm|yarn)\s+(?:typecheck|build)\b|"
    r"cargo\s+(?:check|build)\b|go\s+build\b"
    r")",
    re.IGNORECASE,
)

TEST_RUN_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "check": {
            "type": "string",
            "enum": ["test", "typecheck", "build", "install", "command"],
            "default": "test",
            "description": (
                "验证种类：install=云端受控装包（npm/pnpm/yarn 或 uv/pip/poetry，需受限出网）；"
                "test=测试套件；typecheck=类型检查（tsc 等）；build=项目构建；"
                "command=显式跑 command（用于 playbook_args.verify / task 验收命令）。"
                "绿场验包优先 check=install 再 build/typecheck。慢 build / 全量 tsc / "
                "项目测试 / 装包请用本工具，勿用 code_execute。全量 typecheck/build/"
                "`tsc -b` 仅验收员（外环）；修码自检用内环 code_diagnostics，"
                "勿三路并行全仓 tsc。"
            ),
        },
        "command": {
            "type": "string",
            "description": (
                "check=command 时必填：要跑的验证命令（如 pnpm test、npx tsc --noEmit、"
                "npm run build、npm install、uv sync、pip install）。须为项目检查/装包形，"
                "禁止长驻进程与 cd&& shell 链；子目录用 --prefix/--dir/--directory 或 "
                "working_directory。"
            ),
        },
        "working_directory": {
            "type": "string",
            "description": (
                "可选：工作区相对子目录（禁止绝对路径 / ..）。装包时注入 "
                "npm --prefix / pnpm --dir / yarn --cwd / uv|poetry --directory；"
                "亦可直接在 command 里写这些旗标。"
            ),
        },
        "scope": {
            "type": "string",
            "enum": ["all", "affected", "file"],
            "default": "affected",
            "description": (
                "仅 check=test：all=全量；affected=受影响测试；file=单个测试文件。"
            ),
        },
        "test_file": {
            "type": "string",
            "description": "scope=file 时必填，测试文件的工作区相对路径。",
        },
        "framework": {
            "type": "string",
            "enum": ["pytest", "vitest", "jest", "auto"],
            "default": "auto",
            "description": "仅 check=test：测试框架。auto 时从 WorkspaceProfile 自动检测。",
        },
        "filter": {
            "type": "string",
            "description": "仅 check=test：可选测试名过滤（如 pytest -k）。",
        },
        "purpose": {
            "type": "string",
            "description": (
                "一句话中文说明这次验证要确认什么；会展示给用户作为审批说明，执行时忽略"
            ),
        },
    },
    "required": [],
}

_ALLOWED_PREFIXES: tuple[tuple[str, ...], ...] = (
    # package install / ci (bounded; registry pinned via package_install env)
    ("npm", "install"),
    ("npm", "ci"),
    ("npm", "i"),
    ("pnpm", "install"),
    ("pnpm", "ci"),
    ("pnpm", "i"),
    ("pnpm", "add"),
    ("yarn", "install"),
    ("yarn", "ci"),
    # npm/pnpm/yarn with safe --prefix/--dir/--cwd before verb (matched via helper)
    ("npm", "--prefix"),
    ("pnpm", "--dir"),
    ("pnpm", "-C"),
    ("yarn", "--cwd"),
    # Python package install (uv / pip / poetry)
    ("pip", "install"),
    ("python", "-m", "pip", "install"),
    ("python3", "-m", "pip", "install"),
    ("uv", "sync"),
    ("uv", "add"),
    ("uv", "pip", "install"),
    ("uv", "--directory"),
    ("poetry", "install"),
    ("poetry", "add"),
    ("poetry", "--directory"),
    # tests
    ("pytest",),
    ("python", "-m", "pytest"),
    ("npx", "vitest"),
    ("npx", "jest"),
    ("pnpm", "test"),
    ("npm", "test"),
    ("yarn", "test"),
    ("uv", "run", "pytest"),
    ("vitest",),
    ("jest",),
    # typecheck / build
    ("tsc",),
    ("npx", "tsc"),
    ("vue-tsc",),
    ("npx", "vue-tsc"),
    ("npm", "run", "typecheck"),
    ("npm", "run", "type-check"),
    ("npm", "run", "build"),
    ("npm", "run", "lint"),
    ("pnpm", "run", "typecheck"),
    ("pnpm", "run", "type-check"),
    ("pnpm", "run", "build"),
    ("pnpm", "run", "lint"),
    ("pnpm", "typecheck"),
    ("pnpm", "build"),
    ("yarn", "typecheck"),
    ("yarn", "build"),
    ("yarn", "run", "typecheck"),
    ("yarn", "run", "build"),
    ("cargo", "test"),
    ("cargo", "check"),
    ("cargo", "build"),
    ("go", "test"),
    ("go", "build"),
    ("python", "-m", "mypy"),
    ("mypy",),
    ("uv", "run", "mypy"),
)

# Mirrors completion._VERIFY_COMMAND_RE — keep allow surface honest for explicit command=.
_VERIFY_SHAPED_RE = re.compile(
    r"\b(?:"
    r"tsc\b|vue-tsc\b|typecheck\b|"
    r"(?:npm|pnpm|yarn)\s+(?:ci|install|i|add)\b|"
    r"(?:pip3?|poetry)\s+(?:install|add)\b|"
    r"uv\s+(?:sync|add)\b|"
    r"uv\s+pip\s+install\b|"
    r"(?:python3?|py)\s+-m\s+pip\s+install\b|"
    r"(?:npm|pnpm|yarn)\s+run\s+(?:test|typecheck|type-check|build|lint)\b|"
    r"(?:npm|pnpm|yarn)\s+test\b|"
    r"pytest\b|vitest\b|\bjest\b|mypy\b|"
    r"cargo\s+(?:test|check|build)\b|go\s+(?:test|build)\b|"
    r"(?:mvn|gradlew?)\s+test\b"
    r")",
    re.IGNORECASE,
)

_VITEST_CONFIG_NAMES = (
    "vitest.config.ts",
    "vitest.config.js",
    "vitest.config.mts",
    "vitest.config.mjs",
)
_JEST_CONFIG_NAMES = (
    "jest.config.js",
    "jest.config.ts",
    "jest.config.mjs",
    "jest.config.cjs",
)

_SOURCE_EXTENSIONS = frozenset({".py", ".ts", ".tsx", ".js", ".jsx"})
_MAX_AFFECTED_SOURCES = 10

_TIMEOUT_MARKER = TIMEOUT_LEGACY_MARKER  # legacy journals still match


def resolve_verify_timeouts(check: CheckKind) -> tuple[int, int]:
    """Return ``(disaster_wall_seconds, idle_silence_seconds)`` for outer verify."""
    idle = (
        EXEC_IDLE_TIMEOUT_INSTALL_S
        if check == "install"
        else EXEC_IDLE_TIMEOUT_DEFAULT_S
    )
    return EXEC_DISASTER_TIMEOUT_S, idle


def resolve_verify_budget_seconds(
    check: CheckKind, argv: list[str] | None = None
) -> int:
    """Deprecated alias — returns disaster ceiling only (idle is separate)."""
    _ = argv
    return resolve_verify_timeouts(check)[0]


def _make_output_callback(context: ToolContext):
    # Only backends that run the sandbox in-process stream: subprocess / gVisor read
    # ``ExecutionRequest.on_output``. The desktop channel cannot carry a callback —
    # ``workspace/local.py::_channel_execute`` drops it and hands back the whole output
    # once the command has exited.
    on_progress = context.on_progress
    if not on_progress:
        return None

    def callback(stream: str, chunk: str) -> None:
        on_progress("output", {"stream": stream, "chunk": chunk})

    return callback


def _is_allowed_command(argv: list[str]) -> bool:
    if not argv:
        return False
    # Install with ``--prefix`` / ``--dir`` / ``--cwd`` before the verb: prefix table
    # alone is insufficient (variable path token); validate via package_install.
    if install_prefix_allowed(argv):
        return validate_install_argv(argv) is None
    for prefix in _ALLOWED_PREFIXES:
        if len(argv) >= len(prefix) and tuple(argv[: len(prefix)]) == prefix:
            # Bare ``npm --prefix`` without a following install verb is not enough.
            dir_prefixes = (
                ("npm", "--prefix"),
                ("pnpm", "--dir"),
                ("pnpm", "-C"),
                ("yarn", "--cwd"),
                ("uv", "--directory"),
                ("poetry", "--directory"),
            )
            if prefix in dir_prefixes:
                return install_prefix_allowed(argv) and validate_install_argv(argv) is None
            return True
    return False


def _is_allowed_verify_argv(argv: list[str]) -> bool:
    if _is_allowed_command(argv):
        return True
    if is_install_shaped_argv(argv):
        return validate_install_argv(argv) is None
    return bool(_VERIFY_SHAPED_RE.search(_argv_to_shell(argv)))


def _is_heavy_verify_argv(argv: list[str]) -> bool:
    """True when argv looks like typecheck/build (legacy helper; unused for timeouts)."""
    return bool(_HEAVY_VERIFY_RE.search(_argv_to_shell(argv)))


def verify_coalesce_fingerprint(
    check: CheckKind,
    argv: list[str],
    working_directory: str | None,
) -> str:
    """Stable key for sibling verify coalesce (resolved argv, not raw args)."""
    payload = {
        "check": check,
        "argv": list(argv),
        "wd": (working_directory or "").strip(),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


_VERIFY_SHARED_PREFIX = (
    "【团队共享验证】同 execution 内队友已跑过相同验证，复用结果避免重复烧预算"
    "（来源：{source}）。\n\n"
)


def _annotate_shared_verify(result: ToolResult, source: str) -> ToolResult:
    meta = dict(result.metadata or {})
    meta["verify_shared"] = source
    body = result.output or ""
    prefix = _VERIFY_SHARED_PREFIX.format(source=source)
    output = body if body.startswith("【团队共享验证】") else prefix + body
    return replace(result, output=output, metadata=meta)


def _argv_to_shell(argv: list[str]) -> str:
    return " ".join(shlex.quote(arg) for arg in argv)


def _parse_command(command: str) -> list[str] | None:
    text = command.strip()
    if not text:
        return None
    try:
        argv = shlex.split(text, posix=True)
    except ValueError:
        return None
    return argv or None


def _python_argv_runner(argv: list[str], *, chdir: str | None = None) -> str:
    """Run ``argv`` under Python so local Windows never needs a bash trampoline."""
    chdir_block = ""
    if chdir:
        chdir_block = (
            "import os\n"
            f"os.chdir({chdir!r})\n"
        )
    return (
        "import shutil\n"
        "import subprocess\n"
        "import sys\n"
        f"{chdir_block}"
        f"argv = {list(argv)!r}\n"
        "resolved = shutil.which(argv[0])\n"
        "if resolved:\n"
        "    argv = [resolved, *argv[1:]]\n"
        "if sys.platform == 'win32' and argv[0].lower().endswith(('.cmd', '.bat')):\n"
        "    completed = subprocess.run(subprocess.list2cmdline(argv), shell=True)\n"
        "else:\n"
        "    completed = subprocess.run(argv)\n"
        "raise SystemExit(completed.returncode)\n"
    )


def _is_test_file(path: str) -> bool:
    norm = path.replace("\\", "/")
    base = os.path.basename(norm)
    if base.startswith("test_") or base.endswith("_test.py"):
        return True
    if ".test." in base or ".spec." in base:
        return True
    if "/tests/" in norm or norm.startswith("tests/"):
        return True
    return "/__tests__/" in norm


def _is_source_file(path: str) -> bool:
    _, ext = os.path.splitext(path)
    return ext.lower() in _SOURCE_EXTENSIONS


async def _file_exists(backend: WorkspaceBackend, path: str) -> bool:
    try:
        await backend.read(path)
        return True
    except (PathNotFound, Exception):
        return False


def _framework_from_command_text(text: str) -> Framework | None:
    """Infer framework from a command / scripts.test body (not bare ``npm test``)."""
    lowered = (text or "").lower()
    if not lowered.strip():
        return None
    if "pytest" in lowered:
        return "pytest"
    if "vitest" in lowered:
        return "vitest"
    if "jest" in lowered:
        return "jest"
    return None


def _decode_backend_text(raw: Any) -> str:
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace")
    return str(raw or "")


async def _framework_from_package_scripts(backend: WorkspaceBackend) -> Framework | None:
    """Parse ``package.json`` ``scripts.test`` body for vitest/jest/pytest."""
    try:
        raw = await backend.read("package.json")
    except (PathNotFound, Exception):
        return None
    text = _decode_backend_text(raw)
    if not text.strip():
        return None
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    scripts = data.get("scripts")
    if not isinstance(scripts, dict):
        return None
    return _framework_from_command_text(str(scripts.get("test") or ""))


async def _detect_framework(
    backend: WorkspaceBackend,
    profile: WorkspaceProfile,
    framework_arg: str,
) -> Framework | None:
    if framework_arg in ("pytest", "vitest", "jest"):
        return framework_arg  # type: ignore[return-value]

    for cmd in profile.test_commands:
        # Bare ``npm|pnpm|yarn test`` is not enough — scripts.test body decides.
        hit = _framework_from_command_text(cmd)
        if hit is not None:
            return hit

    from_scripts = await _framework_from_package_scripts(backend)
    if from_scripts is not None:
        return from_scripts

    for name in _VITEST_CONFIG_NAMES:
        if await _file_exists(backend, name):
            return "vitest"

    for name in _JEST_CONFIG_NAMES:
        if await _file_exists(backend, name):
            return "jest"

    if await _file_exists(backend, "pyproject.toml"):
        return "pytest"

    # Forbidden: bare package.json → default jest (vitest repos were mis-routed).
    return None


def _base_command(framework: Framework, profile: WorkspaceProfile) -> list[str]:
    if framework == "pytest":
        if "uv" in profile.package_managers:
            return ["uv", "run", "pytest", "--tb=short", "-q"]
        return ["pytest", "--tb=short", "-q"]
    if framework == "vitest":
        return ["npx", "vitest", "run"]
    return ["npx", "jest"]


def _profile_test_argv(profile: WorkspaceProfile) -> list[str] | None:
    """First whitelist-ok argv from ``profile.test_commands`` (repo script preference)."""
    for cmd in profile.test_commands:
        argv = _parse_command(cmd)
        if argv and _is_allowed_verify_argv(argv):
            return argv
    return None


def _extend_test_targets(argv: list[str], targets: list[str]) -> list[str]:
    """Append test file targets; insert ``--`` for npm/pnpm/yarn script runners."""
    if not targets:
        return argv
    if (
        len(argv) >= 2
        and argv[0] in ("npm", "pnpm", "yarn")
        and argv[1] in ("test", "run")
    ):
        return [*argv, "--", *targets]
    return [*argv, *targets]


def _infer_test_candidates(source_path: str) -> list[str]:
    norm = source_path.replace("\\", "/")
    base = os.path.basename(norm)
    stem, ext = os.path.splitext(base)
    dir_part = os.path.dirname(norm)

    if ext.lower() == ".py":
        candidates = [
            f"test_{stem}.py",
            f"tests/test_{stem}.py",
            f"{stem}_test.py",
        ]
        if dir_part:
            candidates.insert(0, f"{dir_part}/test_{stem}.py")
        return candidates

    if ext.lower() in (".ts", ".tsx", ".js", ".jsx"):
        suffix = ext
        in_dir = [
            f"{stem}.test{suffix}",
            f"{stem}.spec{suffix}",
        ]
        if dir_part:
            in_dir = [f"{dir_part}/{name}" for name in in_dir]
        return in_dir + [
            f"__tests__/{stem}.test{suffix}",
            f"tests/{stem}.test{suffix}",
        ]

    return []


async def _resolve_affected_paths(backend: WorkspaceBackend) -> list[str]:
    index = getattr(backend, "index_files", None)
    if index is None:
        return []

    try:
        paths, _ = await index(cap=50, order="recent")
    except Exception:
        return []

    sources = [p for p in paths if _is_source_file(p) and not _is_test_file(p)]
    test_paths: list[str] = []
    for src in sources[:_MAX_AFFECTED_SOURCES]:
        for candidate in _infer_test_candidates(src):
            if await _file_exists(backend, candidate):
                test_paths.append(candidate)
                break
    return list(dict.fromkeys(test_paths))


def _append_filter(argv: list[str], framework: Framework, filter_expr: str) -> list[str]:
    if not filter_expr.strip():
        return argv
    if framework == "pytest":
        return [*argv, "-k", filter_expr]
    return [*argv, "--testNamePattern", filter_expr]


def _parse_output(
    framework: Framework,
    stdout: str,
    stderr: str,
    exit_code: int,
) -> TestRunResult:
    if framework == "pytest":
        result = parse_pytest_output(stdout, stderr)
    elif framework == "vitest":
        result = parse_vitest_output(stdout, stderr)
    else:
        result = parse_jest_output(stdout, stderr)

    if (
        result.passed == 0
        and result.failed == 0
        and result.errors == 0
        and (exit_code != 0 or not result.failures)
    ):
        return parse_generic_output(stdout, stderr, exit_code)
    return result


def _format_test_output(
    result: TestRunResult,
    command_argv: list[str],
    duration_seconds: float,
) -> str:
    parts: list[str] = []
    header_counts = [f"{result.passed} passed"]
    if result.failed:
        header_counts.append(f"{result.failed} failed")
    if result.errors:
        header_counts.append(f"{result.errors} error")
    parts.append(f"## 测试结果：{', '.join(header_counts)}")

    if result.failures:
        parts.append("\n### 失败用例\n")
        for failure in result.failures:
            loc = failure.test_name
            if failure.file_path:
                loc = failure.file_path
                if failure.line is not None:
                    loc = f"{failure.file_path}:{failure.line}"
                loc = f"{failure.test_name} ({loc})"
            line = f"❌ {loc}"
            if failure.message:
                line += f"\n   {failure.message}"
            if failure.snippet:
                line += f"\n   > {failure.snippet}"
            parts.append(line)

    parts.append("\n### 摘要")
    parts.append(f"- 框架：{result.framework}")
    parts.append(f"- 命令：{_argv_to_shell(command_argv)}")
    if result.duration_seconds is not None:
        parts.append(f"- 耗时：{result.duration_seconds:.1f}s")
    elif duration_seconds > 0:
        parts.append(f"- 耗时：{duration_seconds:.1f}s")
    parts.append(
        f"- 通过：{result.passed} / 失败：{result.failed} / 错误：{result.errors}"
    )
    if result.skipped:
        parts.append(f"- 跳过：{result.skipped}")

    if result.failed or result.errors:
        parts.append("\n（用 file_read 查看失败测试的完整上下文）")
    elif result.framework == "unknown" and result.raw_output:
        parts.append("\n### 原始输出\n")
        parts.append(result.raw_output)

    return "\n".join(parts)


def _format_check_output(
    *,
    check: CheckKind,
    command_argv: list[str],
    exec_result: ExecutionResult,
    duration_seconds: float,
    budget_exceeded: bool,
    budget_seconds: int,
    timeout_kind: str | None = None,
) -> str:
    if budget_exceeded:
        if timeout_kind == "idle":
            status = "未完成（执行无响应）"
        elif timeout_kind == "disaster":
            status = "未完成（强制中止）"
        else:
            status = "未完成（已中止）"
    elif exec_result.exit_code == 0:
        status = "通过"
    else:
        status = "未通过"
    parts = [
        f"## 验证结果：{status}",
        "",
        "### 摘要",
        f"- 种类：{check}",
        f"- 命令：{_argv_to_shell(command_argv)}",
        f"- 退出码：{exec_result.exit_code}",
        f"- 耗时：{duration_seconds:.1f}s",
        f"- 灾难顶：{budget_seconds}s",
    ]
    if budget_exceeded and timeout_kind == "idle":
        parts.append(
            "- 说明：执行长时间无输出，已按挂起中止；"
            "这是执行环境/进程无响应，不是「验证预算」合同。"
            "请缩小范围或检查本机环境后重试。"
        )
    elif budget_exceeded and timeout_kind == "disaster":
        parts.append(
            f"- 说明：已跑满灾难顶 {budget_seconds}s，强制中止；"
            "未取得完整验证结果。可缩小范围、拆命令后重试。"
        )
    elif budget_exceeded:
        parts.append(
            "- 说明：验证未取得完整结果（已中止）。可缩小范围或拆命令后重试。"
        )
    raw = (exec_result.stdout or "").strip()
    err = (exec_result.stderr or "").strip()
    if raw:
        parts.extend(["", "### stdout", raw])
    if err:
        parts.extend(["", "### stderr", err])
    return "\n".join(parts)


def _is_budget_timeout(exec_result: ExecutionResult) -> bool:
    """True when sandbox killed the process (idle hang or disaster wall)."""
    err = exec_result.stderr or ""
    if exec_result.exit_code == -1 and (
        is_idle_timeout_text(err)
        or is_disaster_timeout_text(err)
        or TIMEOUT_LEGACY_MARKER in err
    ):
        return True
    return (
        is_idle_timeout_text(err)
        or is_disaster_timeout_text(err)
        or TIMEOUT_LEGACY_MARKER in err
    )


def _timeout_kind(exec_result: ExecutionResult) -> str | None:
    err = exec_result.stderr or ""
    if is_idle_timeout_text(err):
        return "idle"
    if is_disaster_timeout_text(err) or TIMEOUT_LEGACY_MARKER in err:
        return "disaster"
    return None


def _note_install_network_unavailable() -> None:
    """Stamp cloud-web verify honesty latch (案 B：结构化装包拒)."""
    try:
        from agentcore.runtime.closing_posture import note_cloud_web_verify_gap

        note_cloud_web_verify_gap()
    except Exception:  # noqa: BLE001 — side channel must never break verify
        pass


def _note_verify_budget_exhausted() -> None:
    """Stamp structured verify-budget latch（禁『仍在跑』收口；不扫自由文）."""
    try:
        from agentcore.runtime.closing_posture import note_verify_budget_exhausted

        note_verify_budget_exhausted()
    except Exception:  # noqa: BLE001 — side channel must never break verify
        pass


def _js_pm_run(profile: WorkspaceProfile, script: str) -> list[str]:
    pm = "npm"
    for candidate in ("pnpm", "yarn", "npm"):
        if candidate in profile.package_managers:
            pm = candidate
            break
    if pm == "yarn":
        return ["yarn", script]
    if pm == "pnpm":
        # Prefer bare script when common; fall back to run for custom names.
        if script in ("test", "build", "typecheck"):
            return ["pnpm", script] if script != "test" else ["pnpm", "test"]
        return ["pnpm", "run", script]
    return ["npm", "run", script] if script != "test" else ["npm", "test"]


async def _resolve_typecheck_argv(
    backend: WorkspaceBackend,
    profile: WorkspaceProfile,
) -> list[str] | None:
    for cmd in getattr(profile, "typecheck_commands", None) or []:
        argv = _parse_command(cmd)
        if argv and _is_allowed_verify_argv(argv):
            return argv
    if await _file_exists(backend, "tsconfig.json"):
        return ["npx", "tsc", "--noEmit"]
    return None


async def _resolve_build_argv(
    backend: WorkspaceBackend,
    profile: WorkspaceProfile,
) -> list[str] | None:
    for cmd in profile.build_commands:
        argv = _parse_command(cmd)
        if argv and _is_allowed_verify_argv(argv):
            return argv
    if profile.package_managers and await _file_exists(backend, "package.json"):
        return _js_pm_run(profile, "build")
    return None


async def _resolve_test_argv(
    *,
    backend: WorkspaceBackend,
    profile: WorkspaceProfile,
    arguments: dict[str, Any],
) -> tuple[list[str] | None, Framework | None, str | None]:
    scope: Scope = arguments.get("scope", "affected")  # type: ignore[assignment]
    test_file = (arguments.get("test_file") or "").strip()
    framework_arg = arguments.get("framework", "auto")
    filter_expr = (arguments.get("filter") or "").strip()

    if scope == "file" and not test_file:
        return None, None, "scope=file 时必须提供 test_file 参数"

    framework = await _detect_framework(backend, profile, framework_arg)
    # Prefer repo-declared test script (profile / package.json scripts.test) over
    # hardcoded npx jest/vitest — mirrors typecheck/build profile preference.
    profile_argv = _profile_test_argv(profile)
    if profile_argv is None and framework is None:
        return (
            None,
            None,
            (
                "无法检测测试框架。请确认工作区包含 pyproject.toml（pytest）、"
                "package.json scripts.test（vitest/jest）、vitest.config.* 或 "
                "jest.config.*，或在 framework 参数中显式指定；"
                "或改用 check=command 并提供 verify 命令。"
            ),
        )

    if profile_argv is not None:
        argv = list(profile_argv)
    else:
        assert framework is not None
        argv = _base_command(framework, profile)

    if framework is not None:
        argv = _append_filter(argv, framework, filter_expr)

    if scope == "file":
        argv = _extend_test_targets(argv, [test_file])
    elif scope == "affected":
        affected = await _resolve_affected_paths(backend)
        if affected:
            argv = _extend_test_targets(argv, affected)
        elif framework == "pytest":
            argv = _extend_test_targets(argv, ["tests/"])
    return argv, framework, None


class TestRunTool:
    """Bounded project verification (install / test / typecheck / build / command)."""

    registration = ToolRegistration(
        surface=ToolSurface.BUILTIN,
        audience=AUDIENCE_WORKER_ONLY,
        execution_class=True,
        # 跑验证命令：构建 / 缓存副产物枚举不出，也不是本回合交付物。
        file_products=FileProductsContract.NO_PRODUCT,
    )

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="test_run",
            description=(
                "有界项目验证：跑工作区声明的检查（受控装包 / 测试 / typecheck / build / "
                "显式 verify 命令）。适合外环验绿与慢 build、"
                "全量 tsc、项目测试、npm/pnpm/yarn 或 uv/pip/poetry 装包——"
                "【不要】用 code_execute 跑这些。云端装包用 check=install（或 "
                "check=command + npm install / uv sync），需受限出网；无网时诚实降级，勿空转。"
                "长时间无输出会按挂起中止；灾难顶约 20 分钟强制中止。"
                "长驻进程请用 terminal。"
                "【范围】修码自检用内环 code_diagnostics；"
                "全量 typecheck / build / `tsc -b` 仅验收员外环执行——"
                "禁止多名 fix worker 三路并行全仓 tsc。"
                "同 execution 内队友已跑过的相同验证会自动复用结果，勿重复硬烧。"
            ),
            parameters=TEST_RUN_PARAMETERS,
            category=ToolCategory.EXECUTION,
            # Same sandbox chain + GRANTABLE posture as code_execute (P0): a project
            # check executes arbitrary project code, so governance must stay aligned.
            approval=ToolApproval.GRANTABLE,
            # Engine wall covers disaster ceiling so sandbox Timeout wins first.
            timeout_seconds=_VERIFY_DISASTER_SECONDS + _ENGINE_TIMEOUT_SLACK_SECONDS,
        )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        start = time.monotonic()
        check: CheckKind = arguments.get("check", "test")  # type: ignore[assignment]
        if check not in ("test", "typecheck", "build", "install", "command"):
            check = "test"

        working_directory = (arguments.get("working_directory") or "").strip() or None
        if working_directory is not None and not is_safe_relpath(working_directory):
            return ToolResult(
                tool_call_id="",
                success=False,
                output="",
                error=(
                    f"working_directory 必须是工作区相对安全路径（禁止绝对路径 / ..）："
                    f"{working_directory}"
                ),
                duration_ms=0,
                contract_failure=True,
                metadata={"code": "verify_contract"},
            )

        profile = await detect_workspace_profile(context.backend)
        framework: Framework | None = None
        err: str | None = None
        argv: list[str] | None = None

        if check == "command":
            raw_cmd = (arguments.get("command") or "").strip()
            if not raw_cmd:
                return ToolResult(
                    tool_call_id="",
                    success=False,
                    output="",
                    error="check=command 时必须提供 command 参数",
                    duration_ms=0,
                    contract_failure=True,
                    metadata={"code": "verify_contract"},
                )
            chain_err = reject_shell_chain_command(raw_cmd)
            if chain_err:
                return ToolResult(
                    tool_call_id="",
                    success=False,
                    output="",
                    error=chain_err,
                    duration_ms=0,
                    contract_failure=True,
                    metadata={"code": "verify_contract"},
                )
            argv = _parse_command(raw_cmd)
            if argv is None:
                return ToolResult(
                    tool_call_id="",
                    success=False,
                    output="",
                    error=f"无法解析 command：{raw_cmd}",
                    duration_ms=0,
                    contract_failure=True,
                    metadata={"code": "verify_contract"},
                )
            argv = apply_working_directory(argv, working_directory)
        elif check == "install":
            argv = resolve_install_argv(
                package_managers=list(profile.package_managers or []),
                working_directory=working_directory,
            )
        elif check == "typecheck":
            argv = await _resolve_typecheck_argv(context.backend, profile)
            if argv is None:
                err = (
                    "无法推断 typecheck 命令。请用 check=command 并提供命令"
                    "（如 npx tsc --noEmit），或确认存在 tsconfig.json / typecheck 脚本。"
                )
        elif check == "build":
            argv = await _resolve_build_argv(context.backend, profile)
            if argv is None:
                err = (
                    "无法推断 build 命令。请用 check=command 并提供命令"
                    "（如 npm run build），或确认 package.json 含 build 脚本。"
                )
        else:
            argv, framework, err = await _resolve_test_argv(
                backend=context.backend,
                profile=profile,
                arguments=arguments,
            )

        if err:
            return ToolResult(
                tool_call_id="",
                success=False,
                output="",
                error=err,
                duration_ms=int((time.monotonic() - start) * 1000),
                contract_failure=True,
                metadata={"code": "verify_contract"},
            )
        assert argv is not None

        # Investigate/review posture: refuse outer-loop typecheck/build burns.
        policy = (getattr(context, "verify_policy", None) or "").strip().lower()
        if policy == "inner":
            heavy = check in ("typecheck", "build") or (
                check == "command" and _is_heavy_verify_argv(argv)
            )
            if heavy:
                msg = (
                    "当前队员为调查/审查姿态（verify_policy=inner）："
                    "禁止全仓 typecheck / build / 同形慢命令。"
                    "修码自检请用 code_diagnostics；运行时问题优先 browser / 读入口；"
                    "外环验绿请 escalate 或交验收员（verify_policy=outer）执行 test_run。"
                )
                return ToolResult(
                    tool_call_id="",
                    success=False,
                    output=msg,
                    error=msg,
                    duration_ms=int((time.monotonic() - start) * 1000),
                    contract_failure=True,
                    metadata={
                        "code": "verify_policy_inner",
                        "check": check,
                        "verify_policy": "inner",
                    },
                )

        if is_install_shaped_argv(argv) or check == "install":
            install_err = validate_install_argv(argv)
            if install_err:
                return ToolResult(
                    tool_call_id="",
                    success=False,
                    output="",
                    error=install_err,
                    duration_ms=int((time.monotonic() - start) * 1000),
                    contract_failure=True,
                    metadata={"code": "verify_contract"},
                )

        if not _is_allowed_verify_argv(argv):
            return ToolResult(
                tool_call_id="",
                success=False,
                output="",
                error=f"命令不在验证白名单内：{_argv_to_shell(argv)}",
                duration_ms=int((time.monotonic() - start) * 1000),
                contract_failure=True,
                metadata={"code": "verify_contract"},
            )

        needs_install_net = is_install_shaped_argv(argv) or check == "install"
        allows_restricted = _permission_allows_restricted_network(
            context.permission_axes
        )
        is_local_backend = getattr(context.backend, "location", "server") == "local"
        if needs_install_net and not allows_restricted:
            msg = network_unavailable_message()
            _note_install_network_unavailable()
            return ToolResult(
                tool_call_id="",
                success=False,
                output=msg,
                error=msg,
                duration_ms=int((time.monotonic() - start) * 1000),
                contract_failure=True,
                metadata={"code": network_unavailable_code(), "check": check},
            )
        command_shell = _argv_to_shell(argv)
        budget_seconds, idle_seconds = resolve_verify_timeouts(check)
        env: dict[str, str] | None = None
        cache_bucket: str | None = None
        if needs_install_net:
            if is_local_backend:
                # Local: pin registry only. Package managers use the user's own
                # caches — do not require /pkg-cache or cloud desk netns.
                env = {**registry_pin_env()}
            else:
                env = {**registry_pin_env(), **install_cache_env()}
                # Prefer user_id; conversation_id as secondary. Missing → leave None so
                # open_package_egress mints a per-run ephemeral-* bucket (no shared global).
                cache_bucket = (context.user_id or "").strip() or (
                    (context.conversation_id or "").strip() or None
                )
        request = ExecutionRequest(
            code=_python_argv_runner(argv),
            language="python",
            timeout_seconds=budget_seconds,
            idle_timeout_seconds=idle_seconds,
            on_output=_make_output_callback(context),
            network_mode="restricted" if allows_restricted else "none",
            cache_bucket=cache_bucket,
            env=env,
        )

        if context.on_phase:
            context.on_phase("executing")

        fingerprint = verify_coalesce_fingerprint(check, argv, working_directory)

        async def _run_verify() -> ToolResult:
            # Downgrade loop's busy("tool") → verify so idle patrol can wake CEO
            # during minute-level budgets (wall+0 no longer parks forever).
            if context.run_id:
                from agentcore.runtime.coordination.session import note_coord_worker_busy

                note_coord_worker_busy(context.run_id, "verify")
            try:
                exec_result = await context.backend.execute(request)
            except SandboxError as e:
                duration_ms = int((time.monotonic() - start) * 1000)
                msg = e.message or str(e)
                details = getattr(e, "details", None) or {}
                egress_code = details.get("code") if isinstance(details, dict) else None
                if needs_install_net and egress_code == "egress_unavailable":
                    degrade = network_unavailable_message()
                    _note_install_network_unavailable()
                    return ToolResult(
                        tool_call_id="",
                        success=False,
                        output=degrade,
                        error=degrade,
                        duration_ms=duration_ms,
                        contract_failure=True,
                        metadata={"code": network_unavailable_code(), "check": check},
                    )
                return ToolResult(
                    tool_call_id="",
                    success=False,
                    output=msg,
                    error=msg,
                    duration_ms=duration_ms,
                )
            duration_ms = int((time.monotonic() - start) * 1000)
            duration_s = duration_ms / 1000.0
            from agentcore.tools.sandbox.exec_env import (
                exec_env_probe_failure_code,
                exec_env_probe_failure_language,
                is_exec_env_probe_failure,
                probe_failure_retire_steer,
                probe_failure_retire_tools,
                should_retire_exec_env,
            )

            if is_exec_env_probe_failure(exec_result.stderr) or is_exec_env_probe_failure(
                exec_result.stdout
            ):
                msg = (exec_result.stderr or exec_result.stdout or "").strip()
                probe_code = exec_env_probe_failure_code(msg)
                # Every check runs as a python script (see ``_python_argv_runner``),
                # so a dead python retires this tool — but it no longer drags
                # ``code_execute`` down with it. Timeout never retires.
                probe_language = exec_env_probe_failure_language(msg)
                meta: dict[str, object] = {
                    "check": check,
                    "code": probe_code,
                    "exec_env_timeout": True,
                }
                if should_retire_exec_env(probe_code, language=probe_language):
                    meta["error_class"] = "permanent"
                    meta["retire_tools"] = list(
                        probe_failure_retire_tools(probe_language)
                    )
                    meta["retire_message"] = probe_failure_retire_steer(
                        probe_code, language=probe_language
                    )
                return ToolResult(
                    tool_call_id="",
                    success=False,
                    output=msg,
                    error=msg,
                    duration_ms=duration_ms,
                    metadata=meta,
                    display={
                        "check": check,
                        "command": command_shell,
                        "exit_code": exec_result.exit_code,
                        "stdout": exec_result.stdout,
                        "stderr": exec_result.stderr,
                    },
                )
            budget_exceeded = _is_budget_timeout(exec_result)
            kind = _timeout_kind(exec_result) if budget_exceeded else None
            if budget_exceeded:
                # Keep hollow-rework latch (禁「仍在跑」); semantics no longer「验证预算」.
                _note_verify_budget_exhausted()

            if check == "test" and framework is not None and not budget_exceeded:
                parsed = _parse_output(
                    framework,
                    exec_result.stdout,
                    exec_result.stderr,
                    exec_result.exit_code,
                )
                if parsed.duration_seconds is None and exec_result.duration_ms:
                    parsed.duration_seconds = exec_result.duration_ms / 1000.0
                output = _format_test_output(parsed, argv, duration_s)
                tests_passed = (
                    parsed.failed == 0
                    and parsed.errors == 0
                    and exec_result.exit_code == 0
                )
                display = {
                    "check": check,
                    "framework": parsed.framework,
                    "command": command_shell,
                    "passed": parsed.passed,
                    "failed": parsed.failed,
                    "errors": parsed.errors,
                    "skipped": parsed.skipped,
                    "exit_code": exec_result.exit_code,
                    "stdout": exec_result.stdout,
                    "stderr": exec_result.stderr,
                    "failures": [
                        {
                            "test_name": f.test_name,
                            "file_path": f.file_path,
                            "line": f.line,
                            "message": f.message,
                            "snippet": f.snippet,
                        }
                        for f in parsed.failures
                    ],
                }
                return ToolResult(
                    tool_call_id="",
                    success=tests_passed,
                    output=output,
                    error=(
                        None
                        if tests_passed
                        else f"测试未通过（退出码 {exec_result.exit_code}）"
                    ),
                    duration_ms=duration_ms,
                    metadata={
                        "check": check,
                        "framework": parsed.framework,
                        "passed": parsed.passed,
                        "failed": parsed.failed,
                        "errors": parsed.errors,
                    },
                    display=display,
                )

            output = _format_check_output(
                check=check,
                command_argv=argv,
                exec_result=exec_result,
                duration_seconds=duration_s,
                budget_exceeded=budget_exceeded,
                budget_seconds=budget_seconds,
                timeout_kind=kind,
            )
            ok = (not budget_exceeded) and exec_result.exit_code == 0
            error: str | None
            if budget_exceeded and kind == "idle":
                error = (
                    f"执行超过 {idle_seconds}s 无输出，已按挂起中止"
                    "（未取得验证结果）"
                )
            elif budget_exceeded:
                error = (
                    f"已跑满灾难顶 {budget_seconds}s，强制中止"
                    "（未取得完整验证结果）"
                )
            else:
                error = None if ok else f"验证未通过（退出码 {exec_result.exit_code}）"

            meta_code = (
                EXEC_TIMEOUT_CODE
                if kind == "idle"
                else (EXEC_FORCED_STOP_CODE if budget_exceeded else "verify_result")
            )
            return ToolResult(
                tool_call_id="",
                success=ok,
                output=output,
                error=error,
                duration_ms=duration_ms,
                metadata={
                    "check": check,
                    "code": meta_code,
                    "timeout_seconds": budget_seconds,
                    "idle_timeout_seconds": idle_seconds,
                    "exit_code": exec_result.exit_code,
                    **(
                        {"exec_env_timeout": True, "timeout_kind": kind}
                        if kind == "idle"
                        else ({"timeout_kind": kind} if kind else {})
                    ),
                },
                display={
                    "check": check,
                    "command": command_shell,
                    "exit_code": exec_result.exit_code,
                    "stdout": exec_result.stdout,
                    "stderr": exec_result.stderr,
                    "budget_exceeded": budget_exceeded,
                    "timeout_kind": kind,
                },
                # Idle hang feeds exec-env retire via meta; disaster is contract-ish
                # incomplete result (not a tool fuse / not「验证预算」).
                contract_failure=bool(budget_exceeded and kind != "idle"),
            )

        session = None
        eid = (context.execution_id or "").strip()
        if eid:
            from agentcore.runtime.coordination.session import active_coordination

            session = active_coordination(eid)

        if session is not None and session.active:
            # Mark verify while waiting on a sibling inflight too — keeps progress
            # honest without blocking idle patrol (verify ∉ has_inflight_work).
            if context.run_id:
                from agentcore.runtime.coordination.session import note_coord_worker_busy

                note_coord_worker_busy(context.run_id, "verify")
            result, source = await session.coalesce_verify(fingerprint, _run_verify)
            if source != "run":
                from agentcore.core.logging import get_logger

                get_logger(__name__).info(
                    "test_run.verify_shared",
                    execution_id=session.execution_id,
                    run_id=context.run_id,
                    source=source,
                    check=check,
                    fingerprint=fingerprint[:12],
                    command=command_shell[:120],
                )
                result = _annotate_shared_verify(result, source)
                # Caller-facing duration = wait/join wall, not producer wall.
                result = replace(
                    result,
                    duration_ms=int((time.monotonic() - start) * 1000),
                )
            return result

        return await _run_verify()
