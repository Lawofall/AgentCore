"""Download a pinned 7-Zip extra for sidecar / desktop / API (rar/7z ingest).

Mirrors ``fetch_ripgrep.py``. Guest / API / sidecar all use this extra
(not Debian ``p7zip-full``).

Targets:

- Windows: ``7z.exe`` + ``7z.dll`` from the official x64 installer
- macOS / Linux: ``7zz`` from the ip7z extra tarball

Usage::

    python fetch_7zip.py /out
    python fetch_7zip.py --install-server
    python fetch_7zip.py --install-desktop

Env:

- ``SEVENZ_VERSION`` (default ``25.01``)
- ``SEVENZ_SHA256`` (override pin for the current asset; ``skip`` disables)
"""

from __future__ import annotations

import argparse
import hashlib
import os
import platform
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
from pathlib import Path

_DEFAULT_VERSION = "25.01"
_GITHUB = "https://github.com/ip7z/7zip/releases/download"
_GITHUB_MIRROR_PREFIXES = (
    "https://ghfast.top/",
    "https://ghproxy.net/",
    "https://mirror.ghproxy.com/",
)

# SHA-256 of the 25.01 extras (ip7z GitHub = 7-zip.org bytes). Bump with version.
_PINNED_SHA256 = {
    "7z2501-x64.exe": (
        "78afa2a1c773caf3cf7edf62f857d2a8a5da55fb0fff5da416074c0d28b2b55f"
    ),
    "7zr.exe": "27cbe3d5804ad09e90bbcaa916da0d5c3b0be9462d0e0fb6cb54be5ed9030875",
    "7z2501-linux-x64.tar.xz": (
        "4ca3b7c6f2f67866b92622818b58233dc70367be2f36b498eb0bdeaaa44b53f4"
    ),
    "7z2501-linux-arm64.tar.xz": (
        "39c5140f02ce4436599303c59a149f654cb1bbc47cdc105a942120d747ae040d"
    ),
    "7z2501-mac.tar.xz": (
        "26aa75bc262bb10bf0805617b95569c3035c2c590a99f7db55c7e9607b2685e0"
    ),
}


def _host_kind() -> str:
    system = platform.system().lower()
    machine = platform.machine().lower()
    if system == "windows":
        return "windows-x64"
    if system == "darwin":
        return "mac"
    if system == "linux":
        if machine in ("arm64", "aarch64"):
            return "linux-arm64"
        return "linux-x64"
    raise SystemExit(f"unsupported platform for 7-Zip fetch: {system}/{machine}")


def _exe_name() -> str:
    return "7z.exe" if platform.system().lower() == "windows" else "7zz"


def _candidate_urls(url: str) -> list[str]:
    urls = [url]
    if "github.com/" in url:
        for prefix in _GITHUB_MIRROR_PREFIXES:
            urls.append(f"{prefix}{url}")
    return urls


def _download(url: str, timeout: int = 600) -> bytes:
    last_err: BaseException | None = None
    for candidate in _candidate_urls(url):
        print(f"fetching 7-Zip: {candidate}", flush=True)
        try:
            with urllib.request.urlopen(candidate, timeout=timeout) as resp:
                return resp.read()
        except Exception as urllib_err:
            last_err = urllib_err
            print(f"urllib failed ({urllib_err!r})", flush=True)
            curl = shutil.which("curl") or shutil.which("curl.exe")
            if not curl:
                continue
            with tempfile.NamedTemporaryFile(delete=False) as tmp:
                tmp_path = tmp.name
            try:
                subprocess.check_call(
                    [curl, "-fsSL", "--retry", "3", "-o", tmp_path, candidate],
                    timeout=timeout,
                )
                return Path(tmp_path).read_bytes()
            except Exception as curl_err:
                last_err = curl_err
            finally:
                Path(tmp_path).unlink(missing_ok=True)
    assert last_err is not None
    raise last_err


def _expected_sha256(name: str) -> str | None:
    override = (os.environ.get("SEVENZ_SHA256") or "").strip().lower()
    if override == "skip":
        return None
    if override:
        return override
    pinned = _PINNED_SHA256.get(name)
    if pinned:
        return pinned
    raise SystemExit(
        f"no SHA-256 pin for {name}; set SEVENZ_SHA256 or add a pin for this version"
    )


def _verify(data: bytes, name: str) -> None:
    actual = hashlib.sha256(data).hexdigest()
    print(f"{name} sha256={actual}", flush=True)
    expected = _expected_sha256(name)
    if expected is None:
        return
    if actual != expected:
        raise SystemExit(f"7-Zip sha256 mismatch: {actual} != {expected}")


def _copy_mode(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    dest.chmod(dest.stat().st_mode | 0o755)


def _install_unix(out_dir: Path, version: str, kind: str) -> Path:
    tag = version.replace(".", "")
    name = {
        "linux-x64": f"7z{tag}-linux-x64.tar.xz",
        "linux-arm64": f"7z{tag}-linux-arm64.tar.xz",
        "mac": f"7z{tag}-mac.tar.xz",
    }[kind]
    url = f"{_GITHUB}/{version}/{name}"
    data = _download(url)
    _verify(data, name)
    with tempfile.TemporaryDirectory(prefix="ac-7z-") as td:
        archive = Path(td) / name
        archive.write_bytes(data)
        extract = Path(td) / "out"
        extract.mkdir()
        with tarfile.open(archive, "r:xz") as tf:
            tf.extractall(extract)
        found = next((p for p in extract.rglob("7zz") if p.is_file()), None)
        if found is None:
            raise SystemExit(f"7zz not found in {name}")
        dest = out_dir / "7zz"
        _copy_mode(found, dest)
        return dest


def _install_windows(out_dir: Path, version: str) -> Path:
    tag = version.replace(".", "")
    sfx_name = f"7z{tag}-x64.exe"
    sfx = _download(f"{_GITHUB}/{version}/{sfx_name}")
    _verify(sfx, sfx_name)
    sevenzr = _download(f"{_GITHUB}/{version}/7zr.exe")
    _verify(sevenzr, "7zr.exe")
    with tempfile.TemporaryDirectory(prefix="ac-7z-") as td:
        root = Path(td)
        sfx_path = root / sfx_name
        sfx_path.write_bytes(sfx)
        zr = root / "7zr.exe"
        zr.write_bytes(sevenzr)
        out = root / "out"
        out.mkdir()
        subprocess.check_call(
            [str(zr), "x", f"-o{out}", "-y", str(sfx_path)],
            timeout=120,
        )
        exe = next((p for p in out.rglob("7z.exe") if p.is_file()), None)
        dll = next((p for p in out.rglob("7z.dll") if p.is_file()), None)
        if exe is None or dll is None:
            raise SystemExit("7z.exe / 7z.dll not found in installer extra")
        dest = out_dir / "7z.exe"
        _copy_mode(exe, dest)
        _copy_mode(dll, out_dir / "7z.dll")
        return dest


def fetch_to(out_dir: Path) -> Path:
    version = os.environ.get("SEVENZ_VERSION") or _DEFAULT_VERSION
    kind = os.environ.get("SEVENZ_TARGET") or _host_kind()
    out_dir.mkdir(parents=True, exist_ok=True)
    if kind.startswith("windows"):
        dest = _install_windows(out_dir, version)
    else:
        dest = _install_unix(out_dir, version, kind)
    print(f"7-Zip written: {dest} ({dest.stat().st_size} bytes)", flush=True)
    return dest


def _server_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "bin"


def _desktop_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "desktop" / "resources" / "7z"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "output",
        nargs="?",
        help="destination directory for 7zz/7z.exe (omit with --install-*)",
    )
    parser.add_argument("--install-server", action="store_true")
    parser.add_argument("--install-desktop", action="store_true")
    args = parser.parse_args(argv)
    modes = sum(
        bool(x) for x in (args.output, args.install_server, args.install_desktop)
    )
    if modes != 1:
        parser.error(
            "specify exactly one of: output dir | --install-server | --install-desktop"
        )
    if args.install_server:
        dest = _server_dir()
    elif args.install_desktop:
        dest = _desktop_dir()
    else:
        dest = Path(args.output)
    fetch_to(dest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
