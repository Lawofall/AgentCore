"""Declared cloud-desk userland — not sandboxd/API live ``/usr``.

The guest OCI root is a packed tree (image path
``/opt/agentcore/guest-rootfs``). sandboxd points each bundle ``rootfs`` at
that tree. Adding packages to the API process must not change what ``run``
sees; adding a compiler later goes only into the guest stage.
"""

from __future__ import annotations

import contextlib
import ctypes
import os
import shutil
import sys
from pathlib import Path

DEFAULT_GUEST_ROOTFS = "/opt/agentcore/guest-rootfs"
MARKER_NAME = ".agentcore-guest-rootfs"
HOST_USERLAND_PATHS = frozenset({"/usr", "/lib", "/lib64", "/bin", "/etc"})

# mount(2) / umount2(2) — Linux uapi. Not taken from ``os.MS_*``: this
# image's CPython does not export ``os.mount``.
_MS_BIND = 4096
_MS_REC = 16384
_MNT_DETACH = 2


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


def _sandboxd_bind_path() -> bool:
    """True only for Linux uid 0 (sandboxd). API and tests are not this."""
    geteuid = getattr(os, "geteuid", None)
    return sys.platform == "linux" and geteuid is not None and geteuid() == 0


def _libc_mount_umount2() -> tuple[object, object]:
    libc = ctypes.CDLL(None, use_errno=True)
    mount = libc.mount
    mount.argtypes = [
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.c_ulong,
        ctypes.c_void_p,
    ]
    mount.restype = ctypes.c_int
    umount2 = libc.umount2
    umount2.argtypes = [ctypes.c_char_p, ctypes.c_int]
    umount2.restype = ctypes.c_int
    return mount, umount2


def _sys_mount_bind(source: Path, dest: Path) -> None:
    """mount(2) MS_BIND|MS_REC. Kernel call; not ``os.mount``."""
    try:
        mount, _umount2 = _libc_mount_umount2()
    except AttributeError as exc:
        raise OSError(0, "libc mount 不可用") from exc
    flags = _MS_BIND | _MS_REC
    rc = mount(os.fsencode(str(source)), os.fsencode(str(dest)), b"none", flags, None)
    if rc != 0:
        err = ctypes.get_errno()
        raise OSError(err, os.strerror(err), str(dest))


def _sys_umount(dest: Path) -> None:
    _mount, umount2 = _libc_mount_umount2()
    rc = umount2(os.fsencode(str(dest)), _MNT_DETACH)
    if rc != 0:
        err = ctypes.get_errno()
        raise OSError(err, os.strerror(err), str(dest))


def unmount_bundle_rootfs(bundle_dir: str | Path) -> None:
    """Drop a bind-mounted bundle rootfs. No-op when it was a symlink/copy."""
    if sys.platform != "linux":
        return
    dest = Path(bundle_dir) / "rootfs"
    with contextlib.suppress(OSError, AttributeError):
        _sys_umount(dest)


def prepare_bundle_rootfs(
    bundle_dir: str | Path, *, guest_rootfs: Path | None = None
) -> Path:
    """Point ``bundle/rootfs`` at the shared guest tree.

    Linux sandboxd (uid 0): bind-mount so OCI extra binds land on this path.
    Failure raises — it does not fall back to a symlink (runsc gofer rejects
    that and the desk dies with client-sync EOF).
    Linux tests (non-root): symlink so ``rmtree`` cannot walk the shared tree.
    Elsewhere: copy the tree so unit tests can run without mount privilege.
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
    if _sandboxd_bind_path():
        dest.mkdir()
        try:
            _sys_mount_bind(guest, dest)
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
