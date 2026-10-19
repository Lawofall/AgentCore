"""``run`` command-shell fact: labels + probe payload parse."""

from agentcore.tools.sandbox.run_shell import (
    bash_shell_label,
    parse_run_shell_fact,
    powershell_shell_label,
)


def test_bash_shell_label_git_vs_other():
    assert (
        bash_shell_label(r"C:\Program Files\Git\bin\bash.exe") == "Git Bash"
    )
    assert bash_shell_label("/usr/bin/bash") == "bash"


def test_powershell_shell_label_edition():
    assert (
        powershell_shell_label(
            r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
        )
        == "PowerShell 5.1"
    )
    assert powershell_shell_label(r"C:\Program Files\PowerShell\7\pwsh.exe") == (
        "PowerShell 7"
    )


def test_parse_run_shell_fact_accepts_desktop_payload():
    fact = parse_run_shell_fact(
        {"languages": ["python"], "os": "Windows", "shell": "Git Bash"}
    )
    assert fact is not None
    assert fact.os_label == "Windows"
    assert fact.shell_label == "Git Bash"


def test_parse_run_shell_fact_rejects_unknown_or_missing():
    assert parse_run_shell_fact({"languages": ["bash"]}) is None
    assert parse_run_shell_fact({"os": "win32", "shell": "Git Bash"}) is None
    assert parse_run_shell_fact({"os": "Windows", "shell": "  "}) is None
    assert parse_run_shell_fact(None) is None
