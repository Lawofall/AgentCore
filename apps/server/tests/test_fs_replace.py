"""Atomic temp+replace retry and in-place fallback (Windows lock / WinError 5)."""

from __future__ import annotations

import errno
from pathlib import Path

import pytest

import agentcore.workspace.fs_replace as fs_replace_mod
from agentcore.workspace.fs_replace import atomic_write_bytes, replace_with_retry


def _winerror_5() -> OSError:
    err = OSError(5, "Access is denied")
    err.winerror = 5  # type: ignore[attr-defined]
    return err


def test_replace_with_retry_recovers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src = tmp_path / "tmp.bin"
    dest = tmp_path / "dest.bin"
    src.write_bytes(b"new")
    dest.write_bytes(b"old")
    calls = {"n": 0}
    real = fs_replace_mod.os.replace

    def flaky(a: object, b: object) -> None:
        calls["n"] += 1
        if calls["n"] < 3:
            raise _winerror_5()
        real(a, b)

    monkeypatch.setattr(fs_replace_mod.os, "replace", flaky)
    replace_with_retry(src, dest, delays=(0.0, 0.0, 0.0, 0.0, 0.0))
    assert dest.read_bytes() == b"new"
    assert calls["n"] == 3


def test_atomic_write_bytes_falls_back_inplace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dest = tmp_path / "dest.bin"
    dest.write_bytes(b"old")

    def always_locked(a: object, b: object) -> None:
        del a, b
        raise _winerror_5()

    monkeypatch.setattr(fs_replace_mod.os, "replace", always_locked)
    monkeypatch.setattr(fs_replace_mod, "REPLACE_RETRY_DELAYS_S", (0.0, 0.0))
    atomic_write_bytes(dest, b"new")
    assert dest.read_bytes() == b"new"
    leftovers = list(tmp_path.glob(".tmp_str_replace_*"))
    assert leftovers == []


def test_atomic_write_bytes_does_not_fallback_on_enospc(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dest = tmp_path / "dest.bin"
    dest.write_bytes(b"old")

    def no_space(a: object, b: object) -> None:
        del a, b
        err = OSError(errno.ENOSPC, "No space left on device")
        err.errno = errno.ENOSPC
        raise err

    monkeypatch.setattr(fs_replace_mod.os, "replace", no_space)
    with pytest.raises(OSError) as ei:
        atomic_write_bytes(dest, b"new")
    assert ei.value.errno == errno.ENOSPC
    assert dest.read_bytes() == b"old"
