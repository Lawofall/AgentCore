"""Declared cloud-desk userland — not sandboxd/API live ``/usr``.

The guest OCI root is a packed tree (image path
``/opt/agentcore/guest-rootfs``). sandboxd points each bundle ``rootfs`` at
that tree. Adding packages to the API process must not change what ``run``
sees; adding a compiler later goes only into the guest stage.
"""

from __future__ import annotations

import contextlib
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


def _linux_root_can_bind_mount() -> bool:
    """sandboxd is uid 0 + SYS_ADMIN. Tests and the API process are not."""
    geteuid = getattr(os, "geteuid", None)
    mount = getattr(os, "mount", None)
    return sys.platform == "linux" and geteuid is not None and geteuid() == 0 and mount is not None


def _mount_bind_ro(source: Path, dest: Path) -> None:
    """Bind the packed guest onto ``dest`` (not a symlink into that tree).

    runsc gofer safe-mount rejects ``bundle/rootfs`` as a symlink: extra OCI
    binds (``/workspace``) resolve into the shared packed tree
    (``expected …/rootfs/workspace, but found /opt/agentcore/guest-rootfs/workspace``)
    and the sentry dies with an empty mounts JSON / client-sync EOF.
    """
    mount = getattr(os, "mount", None)
    if mount is None:
        raise GuestRootfsError("os.mount 不可用")
    flags = int(getattr(os, "MS_BIND", 4096)) | int(getattr(os, "MS_REC", 16384))
    mount(str(source), str(dest), "none", flags)
    remount = flags | int(getattr(os, "MS_RDONLY", 1)) | int(getattr(os, "MS_REMOUNT", 32))
    mount(str(source), str(dest), "none", remount)


def unmount_bundle_rootfs(bundle_dir: str | Path) -> None:
    """Drop a bind-mounted bundle rootfs. No-op when it was a symlink/copy."""
    umount = getattr(os, "umount", None)
    if umount is None:
        return
    dest = Path(bundle_dir) / "rootfs"
    with contextlib.suppress(OSError):
        umount(str(dest))


def prepare_bundle_rootfs(
    bundle_dir: str | Path, *, guest_rootfs: Path | None = None
) -> Path:
    """Point ``bundle/rootfs`` at the shared guest tree.

    Linux sandboxd (uid 0): bind-mount so OCI extra binds land on this path.
    Linux tests (non-root): symlink so ``rmtree`` cannot walk the shared tree.
    Elsewhere: copy the tree so unit tests can run without symlink privilege.
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
    if _linux_root_can_bind_mount():
        dest.mkdir()
        try:
            _mount_bind_ro(guest, dest)
        except OSError as exc:
            raise GuestRootfsError(f"无法把 guest rootfs bind 到 bundle: {exc}") from exc
    elif sys.platform == "linux":
        dest.symlink_to(guest, target_is_directory=True)
    else:
        shutil.copytree(guest, dest, symlinks=True)
    return dest


def is_host_userland_bind(mount: dict) -> bool:
    dest = str(mount.get("destination") or "")
    src = str(mount.get("source") or "")
    return dest in HOST_USERLAND_PATHS or src in HOST_USERLAND_PATHS
