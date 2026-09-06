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
    shutil.rmtree(bundle)
    assert marker.is_file()
    assert looks_like_guest_rootfs(guest)


def test_is_host_userland_bind():
    for path in HOST_USERLAND_PATHS:
        assert is_host_userland_bind({"destination": path, "source": path})
    assert not is_host_userland_bind(
        {"destination": "/workspace", "source": "/data/ws"}
    )
