"""Declared cloud-desk userland — not sandboxd/API live ``/usr``.

The guest OCI root is a packed tree (image path
``/opt/agentcore/guest-rootfs``). sandboxd points each bundle ``rootfs`` at
that tree. Adding packages to the API process must not change what ``run``
sees; adding a compiler later goes only into the guest stage.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

DEFAULT_GUEST_ROOTFS = "/opt/agentcore/guest-rootfs"
MARKER_NAME = ".agentcore-guest-rootfs"
HOST_USERLAND_PATHS = frozenset({"/usr", "/lib", "/lib64", "/bin", "/etc"})


class GuestRootfsError(Exception):
    """Packed guest userland missing, malformed, or cannot be attached."""


def guest_rootfs_path() -> Path:
    try:
        from agentcore.config import settings

        raw = str(getattr(settings, "gvisor_guest_rootfs", "") or "").strip()
    except Exception:  # noqa: BLE001 — settings must not break sandboxd boot
        raw = ""
    if not raw:
        raw = os.environ.get("GVISOR_GUEST_ROOTFS", DEFAULT_GUEST_ROOTFS)
    return Path(raw)


def looks_like_guest_rootfs(path: Path) -> bool:
    if not path.is_dir():
        return False
    if (path / MARKER_NAME).is_file():
        return True
    return (path / "bin" / "true").exists() or (path / "usr" / "bin" / "true").exists()


def require_guest_rootfs(path: Path | None = None) -> Path:
    root = path if path is not None else guest_rootfs_path()
    if not looks_like_guest_rootfs(root):
        raise GuestRootfsError(f"云桌 guest rootfs 不存在或不是声明的用户态: {root}")
    return root.resolve()


def prepare_bundle_rootfs(
    bundle_dir: str | Path, *, guest_rootfs: Path | None = None
) -> Path:
    """Point ``bundle/rootfs`` at the shared guest tree.

    Linux: symlink (``rmtree`` on the bundle must not walk into the shared
    tree). Elsewhere: copy the tree so unit tests can run without symlink
    privilege. Production is Linux.
    """
    bundle = Path(bundle_dir)
    guest = require_guest_rootfs(guest_rootfs)
    dest = bundle / "rootfs"
    if dest.is_symlink() or dest.is_file():
        dest.unlink()
    elif dest.is_dir():
        try:
            dest.rmdir()
        except OSError as exc:
            raise GuestRootfsError(f"bundle rootfs 不是空目录，拒绝覆盖: {dest}") from exc
    if sys.platform == "linux":
        dest.symlink_to(guest, target_is_directory=True)
    else:
        shutil.copytree(guest, dest, symlinks=True)
    return dest


def is_host_userland_bind(mount: dict) -> bool:
    dest = str(mount.get("destination") or "")
    src = str(mount.get("source") or "")
    return dest in HOST_USERLAND_PATHS or src in HOST_USERLAND_PATHS
