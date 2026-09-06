"""Sidecar import-boundary guard — desktop local engine depends on a clean import graph."""

from __future__ import annotations

import importlib
import subprocess
import sys
import textwrap


def test_sidecar_package_imports_without_cycle() -> None:
    """Desktop spawns ``python -m agentcore.sidecar`` — import graph must stay acyclic."""
    importlib.import_module("agentcore.sidecar.server_pkg")
    importlib.import_module("agentcore.sidecar.server")


def _assert_subprocess(script: str) -> None:
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(script)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr


def test_git_capability_probe_does_not_load_password_or_jwt_stack() -> None:
    """Packed sidecar omits pwdlib/jose; prepare's git probe must not import them."""
    _assert_subprocess(
        """
        import sys
        from agentcore.tools.builtin import git_execution_enabled_for

        class _B:
            location = "local"
            root = "."

        git_execution_enabled_for(_B(), desktop_online=True)
        blocked = (
            "pwdlib",
            "jose",
            "agentcore.security.passwords",
            "agentcore.security.tokens",
            "agentcore.workspace.git_credentials",
        )
        hit = [name for name in blocked if name in sys.modules]
        assert not hit, hit
        """
    )


def test_security_keys_import_does_not_load_passwords_or_tokens() -> None:
    _assert_subprocess(
        """
        import sys
        from agentcore.security.keys import KeyEncryptor

        assert KeyEncryptor is not None
        blocked = (
            "pwdlib",
            "jose",
            "agentcore.security.passwords",
            "agentcore.security.tokens",
        )
        hit = [name for name in blocked if name in sys.modules]
        assert not hit, hit
        """
    )


def test_workspace_git_url_helpers_do_not_load_git_credentials() -> None:
    _assert_subprocess(
        """
        import sys
        from agentcore.workspace.git import _validate_url

        _validate_url("https://github.com/acme/demo.git")
        assert "agentcore.workspace.git_credentials" not in sys.modules
        assert "pwdlib" not in sys.modules
        """
    )


def test_git_tool_import_does_not_load_password_or_jwt_stack() -> None:
    """Worker registry may import GitTool; still must not pull pwdlib/jose."""
    _assert_subprocess(
        """
        import sys
        from agentcore.tools.builtin.git_ops import GitTool

        assert GitTool is not None
        blocked = (
            "pwdlib",
            "jose",
            "agentcore.security.passwords",
            "agentcore.security.tokens",
        )
        hit = [name for name in blocked if name in sys.modules]
        assert not hit, hit
        """
    )
