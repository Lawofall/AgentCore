"""The launcher ``run`` uses for a human command string.

One dialect per machine. Distinct from ``code_execute`` language inventory
(``python`` / ``javascript`` / ``bash``): Git Bash on PATH is not a reason to
tell the model the workspace shell is bash if ``run`` actually starts
PowerShell — and the reverse.

Windows: Git Bash (``bash -lc``) if a real Git Bash exists; else Windows
PowerShell 5.1 or ``pwsh`` 7. POSIX: first PATH bash (``bash -lc``).
Must stay aligned with desktop ``execCodec.resolveRunShell``.
"""

from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass

_OS_LABEL = {"win32": "Windows", "darwin": "macOS", "linux": "Linux"}
_OS_ALLOWED = frozenset(_OS_LABEL.values())


@dataclass(frozen=True)
class RunShellFact:
    """User-machine OS + the ``run`` command dialect (not host, not PTY)."""

    os_label: str
    shell_label: str


def bash_shell_label(path: str) -> str:
    """Git's bash vs any other usable bash binary."""
    norm = path.replace("/", "\\").lower()
    if "\\git\\bin\\bash" in norm:
        return "Git Bash"
    return "bash"


def powershell_shell_label(path: str) -> str:
    """Windows PowerShell 5.1 vs PowerShell 7 (``pwsh``)."""
    norm = path.replace("/", "\\").lower()
    if "windowspowershell" in norm or norm.endswith("\\powershell.exe"):
        return "PowerShell 5.1"
    if norm.endswith("\\pwsh.exe") or norm.endswith("\\pwsh"):
        return "PowerShell 7"
    return "PowerShell"


def probe_host_run_shell() -> RunShellFact | None:
    """Resolve ``run``'s launcher on this process (sidecar / tests)."""
    os_label = _OS_LABEL.get(sys.platform)
    if os_label is None:
        return None
    from agentcore.tools.sandbox.subprocess import resolve_bash_launcher

    bash = resolve_bash_launcher()
    if bash:
        return RunShellFact(os_label, bash_shell_label(bash))
    ps = shutil.which("powershell") or shutil.which("pwsh")
    if ps:
        return RunShellFact(os_label, powershell_shell_label(ps))
    return None


def parse_run_shell_fact(payload: object) -> RunShellFact | None:
    """Desktop ``probe_exec`` ``os`` / ``shell`` keys. Missing or unknown → None."""
    if not isinstance(payload, dict):
        return None
    os_label = payload.get("os")
    shell = payload.get("shell")
    if os_label not in _OS_ALLOWED:
        return None
    if not isinstance(shell, str):
        return None
    label = shell.strip()
    if not label:
        return None
    return RunShellFact(os_label, label)
