"""Pin sandboxd allowlisted runsc argv (shape A vs B)."""

from agentcore.tools.sandbox.sandboxd.argv import build_runsc_cmd


def test_shape_code_is_rootless_none():
    cmd = build_runsc_cmd(
        runsc_path="runsc",
        runtime_root="/data/sandbox",
        bundle_dir="/data/sandbox/b",
        container_id="agentcore-x",
        shape="code",
        network_mode="none",
    )
    run_idx = cmd.index("run")
    assert "--rootless" in cmd[:run_idx]
    assert "--network=none" in cmd[:run_idx]
    assert "--network=sandbox" not in cmd[:run_idx]
    assert "--ignore-cgroups" not in cmd[:run_idx]


def test_shape_code_restricted_is_rootless_host():
    cmd = build_runsc_cmd(
        runsc_path="runsc",
        runtime_root="/data/sandbox",
        bundle_dir="/data/sandbox/b",
        container_id="agentcore-x",
        shape="code",
        network_mode="host",
    )
    run_idx = cmd.index("run")
    assert "--rootless" in cmd[:run_idx]
    assert "--network=host" in cmd[:run_idx]


def test_shape_net_is_fixed_systrap_sandbox_ignore_cgroups():
    cmd = build_runsc_cmd(
        runsc_path="runsc",
        runtime_root="/data/sandbox",
        bundle_dir="/data/sandbox/b",
        container_id="agentcore-br",
        shape="net",
    )
    run_idx = cmd.index("run")
    assert "--rootless" not in cmd[:run_idx]
    assert cmd[:4] == [
        "runsc",
        "--platform=systrap",
        "--network=sandbox",
        "--ignore-cgroups",
    ]
