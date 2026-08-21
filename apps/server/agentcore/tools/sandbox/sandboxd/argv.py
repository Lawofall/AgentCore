"""Allowlisted ``runsc`` argv — the only flags sandboxd will exec.

API cannot pass extra flags. Shape ``net`` always includes ``--ignore-cgroups``
(Docker overlay: session OCI cgroup is not a supported surface).
"""

from __future__ import annotations

from agentcore.tools.sandbox.sandboxd.protocol import CodeNetwork, Shape


def build_runsc_cmd(
    *,
    runsc_path: str,
    runtime_root: str,
    bundle_dir: str,
    container_id: str,
    shape: Shape,
    network_mode: CodeNetwork = "none",
) -> list[str]:
    if shape == "net":
        return [
            runsc_path,
            "--platform=systrap",
            "--network=sandbox",
            "--ignore-cgroups",
            f"--root={runtime_root}",
            "run",
            f"--bundle={bundle_dir}",
            container_id,
        ]
    network = "host" if network_mode == "host" else "none"
    return [
        runsc_path,
        "--rootless",
        f"--network={network}",
        f"--root={runtime_root}",
        "run",
        f"--bundle={bundle_dir}",
        container_id,
    ]
