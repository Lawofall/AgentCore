"""Test helper: a tiny packed guest tree + settings pointer."""

from __future__ import annotations

from pathlib import Path

from agentcore.config import settings
from agentcore.tools.sandbox.guest_rootfs import MARKER_NAME


def write_fake_guest_rootfs(root: Path) -> Path:
    """Minimal tree that ``looks_like_guest_rootfs`` accepts."""
    (root / "usr" / "bin").mkdir(parents=True, exist_ok=True)
    (root / "bin").mkdir(parents=True, exist_ok=True)
    (root / MARKER_NAME).write_text("test\n", encoding="utf-8")
    (root / "usr" / "bin" / "true").write_text("", encoding="utf-8")
    (root / "bin" / "true").write_text("", encoding="utf-8")
    return root


def install_fake_guest_rootfs(tmp_path: Path, monkeypatch: object) -> Path:
    root = write_fake_guest_rootfs(tmp_path / "guest-rootfs")
    monkeypatch.setattr(settings, "gvisor_guest_rootfs", str(root))
    return root
