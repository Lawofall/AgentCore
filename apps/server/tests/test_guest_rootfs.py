"""Packed guest rootfs: declared userland, not live /usr binds."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from agentcore.config import settings
from agentcore.tools.sandbox.guest_rootfs import (
    HOST_USERLAND_PATHS,
    GuestRootfsError,
    is_host_userland_bind,
    looks_like_guest_rootfs,
    prepare_bundle_rootfs,
    require_guest_rootfs,
    unmount_bundle_rootfs,
)
from tests.guest_rootfs_testutil import install_fake_guest_rootfs, write_fake_guest_rootfs


def test_looks_like_guest_rootfs_needs_marker_or_true(tmp_path: Path):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert looks_like_guest_rootfs(empty) is False
    root = write_fake_guest_rootfs(tmp_path / "guest")
    assert looks_like_guest_rootfs(root) is True


def test_require_guest_rootfs_rejects_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(settings, "gvisor_guest_rootfs", str(tmp_path / "nope"))
    with pytest.raises(GuestRootfsError, match="guest rootfs"):
        require_guest_rootfs()


def test_prepare_bundle_rootfs_does_not_delete_shared_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    guest = install_fake_guest_rootfs(tmp_path, monkeypatch)
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "rootfs").mkdir()
    prepare_bundle_rootfs(bundle)
    marker = guest / ".agentcore-guest-rootfs"
    assert marker.is_file()
    unmount_bundle_rootfs(bundle)
    shutil.rmtree(bundle)
    assert marker.is_file()
    assert looks_like_guest_rootfs(guest)


def _linux_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("agentcore.tools.sandbox.guest_rootfs.sys.platform", "linux")
    monkeypatch.setattr(
        "agentcore.tools.sandbox.guest_rootfs.os.geteuid", lambda: 0, raising=False
    )


def test_prepare_bundle_rootfs_bind_mounts_when_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    mounts: list[tuple[str, str]] = []

    def _mount(source: Path, dest: Path) -> None:
        mounts.append((str(source), str(dest)))

    _linux_root(monkeypatch)
    monkeypatch.delattr("os.mount", raising=False)
    monkeypatch.setattr(
        "agentcore.tools.sandbox.guest_rootfs._sys_mount_bind", _mount
    )
    guest = install_fake_guest_rootfs(tmp_path, monkeypatch)
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    dest = prepare_bundle_rootfs(bundle)
    assert dest.is_dir()
    assert not dest.is_symlink()
    assert mounts == [(str(guest), str(dest))]


def test_prepare_bundle_rootfs_root_does_not_symlink_when_mount_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _linux_root(monkeypatch)
    monkeypatch.delattr("os.mount", raising=False)

    def _boom(_source: Path, _dest: Path) -> None:
        raise OSError(1, "mount denied")

    monkeypatch.setattr(
        "agentcore.tools.sandbox.guest_rootfs._sys_mount_bind", _boom
    )
    install_fake_guest_rootfs(tmp_path, monkeypatch)
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    with pytest.raises(GuestRootfsError, match="bind"):
        prepare_bundle_rootfs(bundle)
    dest = bundle / "rootfs"
    assert dest.is_dir()
    assert not dest.is_symlink()


def test_unmount_bundle_rootfs_swallows_missing(tmp_path: Path):
    unmount_bundle_rootfs(tmp_path / "no-such-bundle")


def test_is_host_userland_bind():
    for path in HOST_USERLAND_PATHS:
        assert is_host_userland_bind({"destination": path, "source": path})
    assert not is_host_userland_bind(
        {"destination": "/workspace", "source": "/data/ws"}
    )
