"""Cloud gVisor sandbox preinstalled Python packages (single source of truth).

The sibling ``cloud_python.txt`` is requirements-style and unpinned.
Dockerfile.guest ``pip install -r`` and ``code_execute`` descriptions both
read it — do not hand-copy the names into comments, docs, or skill bodies.
"""

from __future__ import annotations

from pathlib import Path

LIST_FILE = Path(__file__).with_name("cloud_python.txt")
# Path relative to the Docker build context (``apps/server``).
DOCKER_CONTEXT_PATH = "agentcore/tools/sandbox/cloud_python.txt"


def load_cloud_python_packages(text: str | None = None) -> tuple[str, ...]:
    """Return requirement names from the list file (comments / blanks skipped)."""
    raw = LIST_FILE.read_text(encoding="utf-8") if text is None else text
    names: list[str] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        spec = stripped.split("#", 1)[0].strip()
        if spec:
            names.append(spec)
    return tuple(names)


def format_cloud_python_libs(packages: tuple[str, ...] | None = None) -> str:
    """顿号-joined names for the cloud ``code_execute`` tool description."""
    pkgs = load_cloud_python_packages() if packages is None else packages
    return "、".join(pkgs)
