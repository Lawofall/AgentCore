"""Allowlisted ``runsc`` argv — the only flags sandboxd will exec.

API cannot pass extra flags. Shape ``net`` always includes ``--ignore-cgroups``
(Docker overlay: session OCI cgroup is not a supported surface).

``run -d`` starts a long-lived guest (cloud desk). ``exec`` runs a command inside
an already-running guest.
"""

from __future__ import annotations

# Interpreters the API may ``runsc exec`` into a desk guest. Paths are the
# in-guest names from host binds; no shell, no extra binaries.
EXEC_BINS = frozenset({"python3", "node", "bash"})


def build_runsc_cmd(
    *,
    runsc_path: str,
    runtime_root: str,
    bundle_dir: str,
    container_id: str,
    detach: bool = False,
) -> list[str]:
    cmd = [
        runsc_path,
        "--platform=systrap",
        "--network=sandbox",
        "--ignore-cgroups",
        f"--root={runtime_root}",
        "run",
    ]
    if detach:
        cmd.append("-d")
    cmd.extend([f"--bundle={bundle_dir}", container_id])
    return cmd


def build_runsc_exec_cmd(
    *,
    runsc_path: str,
    runtime_root: str,
    container_id: str,
    argv: list[str],
    cwd: str = "/workspace",
    env: list[str] | None = None,
) -> list[str]:
    """Allowlisted ``runsc exec`` into a running guest.

    ``argv[0]`` must be an interpreter in :data:`EXEC_BINS`. Extra path args must
    stay under ``/scratch`` (script + stdin files the API dropped into the
    guest-private scratch bind). ``cwd`` is always ``/workspace``.
    """
    if cwd != "/workspace":
        raise ValueError("exec cwd must be /workspace")
    if not argv or argv[0] not in EXEC_BINS:
        raise ValueError("exec argv[0] is not an allowlisted interpreter")
    for part in argv[1:]:
        if part.startswith("-"):
            continue
        if part.startswith("/") and not part.startswith("/scratch/"):
            raise ValueError("exec path args must be under /scratch")
    cmd = [
        runsc_path,
        f"--root={runtime_root}",
        "exec",
        f"--cwd={cwd}",
    ]
    for item in env or []:
        cmd.extend(["-env", item])
    cmd.append(container_id)
    cmd.extend(argv)
    return cmd
