"""Pin sandboxd allowlisted runsc argv (desk net only)."""

import pytest

from agentcore.tools.sandbox.sandboxd.argv import (
    EXEC_BINS,
    build_runsc_cmd,
    build_runsc_exec_cmd,
)


def test_runsc_cmd_is_fixed_systrap_sandbox_ignore_cgroups():
    cmd = build_runsc_cmd(
        runsc_path="runsc",
        runtime_root="/data/sandbox",
        bundle_dir="/data/sandbox/b",
        container_id="agentcore-br",
    )
    run_idx = cmd.index("run")
    assert "--rootless" not in cmd[:run_idx]
    assert cmd[:4] == [
        "runsc",
        "--platform=systrap",
        "--network=sandbox",
        "--ignore-cgroups",
    ]
    assert "-d" not in cmd
    assert "-detach" not in cmd


def test_runsc_cmd_detach_inserts_dash_detach():
    cmd = build_runsc_cmd(
        runsc_path="runsc",
        runtime_root="/data/sandbox",
        bundle_dir="/data/sandbox/b",
        container_id="agentcore-desk",
        detach=True,
    )
    run_idx = cmd.index("run")
    assert cmd[run_idx + 1] == "-detach"
    assert "-d" not in cmd
    assert cmd[run_idx + 2] == "--bundle=/data/sandbox/b"
    assert cmd[run_idx + 3] == "agentcore-desk"
    assert "--rootless" not in cmd


def test_exec_cmd_allowlists_interpreter_and_scratch():
    cmd = build_runsc_exec_cmd(
        runsc_path="runsc",
        runtime_root="/data/sandbox",
        container_id="agentcore-desk",
        argv=["python3", "-u", "/scratch/x.py"],
        env=["FOO=bar"],
    )
    assert cmd == [
        "runsc",
        "--root=/data/sandbox",
        "exec",
        "--cwd=/workspace",
        "-env",
        "FOO=bar",
        "agentcore-desk",
        "python3",
        "-u",
        "/scratch/x.py",
    ]


def test_exec_bins_unchanged():
    assert frozenset({"python3", "node", "bash"}) == EXEC_BINS


def test_exec_cmd_rejects_non_scratch_path():
    with pytest.raises(ValueError, match="scratch"):
        build_runsc_exec_cmd(
            runsc_path="runsc",
            runtime_root="/data/sandbox",
            container_id="agentcore-desk",
            argv=["python3", "/workspace/x.py"],
        )
