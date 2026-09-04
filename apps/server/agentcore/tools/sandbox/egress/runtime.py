"""Open/close a packaging egress session (netns + allowlist proxy + cache dir)."""

from __future__ import annotations

import asyncio
import contextlib
import re
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

from agentcore.config import settings
from agentcore.core.errors import SandboxError
from agentcore.core.logging import get_logger
from agentcore.tools.sandbox.egress.netns import PackageNetns, PackageNetnsError
from agentcore.tools.sandbox.egress.ready import (
    EGRESS_UNAVAILABLE_CODE,
    registry_egress_available,
)

logger = get_logger(__name__)

# In-sandbox mount point for package manager caches (OCI bind from DATA_DIR).
PACKAGE_CACHE_MOUNT = "/pkg-cache"

# Unauthenticated / invalid bucket → per-open temp dir under DATA_DIR/pkg-cache/.
_EPHEMERAL_PREFIX = "ephemeral-"

_slot_lock = asyncio.Lock()
_used_slots: set[int] = set()
_MAX_INSTALL_SLOTS = 8

_SAFE_BUCKET_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def _safe_bucket(raw: str | None) -> str | None:
    """Return a validated tenant bucket, or None when missing/invalid (no shared fallback)."""
    text = (raw or "").strip()
    if text and _SAFE_BUCKET_RE.match(text):
        return text
    return None


def _ephemeral_bucket() -> str:
    return f"{_EPHEMERAL_PREFIX}{uuid.uuid4().hex}"


def resolve_cache_bucket(raw: str | None = None) -> str:
    """Prefer a safe caller bucket; otherwise mint a per-open ephemeral name."""
    return _safe_bucket(raw) or _ephemeral_bucket()


def is_ephemeral_bucket(bucket: str) -> bool:
    return bucket.startswith(_EPHEMERAL_PREFIX)


def package_cache_host_dir(bucket: str | None = None) -> Path:
    """Host-side cache root under DATA_DIR (created on demand).

    ``None`` / empty / invalid → a fresh ``ephemeral-*`` directory (not shared ``global``).
    Prefer resolving once via :func:`resolve_cache_bucket` / :func:`open_package_egress`
    so a single install-run keeps one mount path.
    """
    name = resolve_cache_bucket(bucket)
    root = Path(settings.data_dir) / "pkg-cache" / name
    root.mkdir(parents=True, exist_ok=True)
    (root / "npm").mkdir(exist_ok=True)
    (root / "yarn").mkdir(exist_ok=True)
    (root / "pnpm").mkdir(exist_ok=True)
    return root


def install_proxy_env(proxy_url: str) -> dict[str, str]:
    """Env that pins package managers / HTTP clients at the allowlist proxy."""
    return {
        "HTTP_PROXY": proxy_url,
        "HTTPS_PROXY": proxy_url,
        "http_proxy": proxy_url,
        "https_proxy": proxy_url,
        "npm_config_proxy": proxy_url,
        "npm_config_https_proxy": proxy_url,
        "NO_PROXY": "localhost,127.0.0.1",
        "no_proxy": "localhost,127.0.0.1",
    }


async def _alloc_slot() -> int:
    async with _slot_lock:
        for i in range(1, _MAX_INSTALL_SLOTS + 1):
            if i not in _used_slots:
                _used_slots.add(i)
                return i
        raise SandboxError(
            f"云端装包出网位已满（并发上限 {_MAX_INSTALL_SLOTS}），请稍后重试。",
            code=EGRESS_UNAVAILABLE_CODE,
        )


async def _free_slot(slot: int) -> None:
    async with _slot_lock:
        _used_slots.discard(slot)


@dataclass
class PackageEgressSession:
    """Live netns + proxy URL + cache bind for one install-run."""

    slot: int
    netns: PackageNetns
    proxy_url: str
    cache_host_dir: Path
    cache_bucket: str

    @property
    def netns_path(self) -> str:
        return self.netns.netns_path

    @property
    def host_ip(self) -> str:
        return self.netns.host_ip

    @property
    def sbx_ip(self) -> str:
        return self.netns.sbx_ip

    async def close(self) -> None:
        with contextlib.suppress(Exception):
            await self.netns.teardown()
        await _free_slot(self.slot)
        if is_ephemeral_bucket(self.cache_bucket):
            with contextlib.suppress(Exception):
                shutil.rmtree(self.cache_host_dir, ignore_errors=True)


async def open_package_egress(*, cache_bucket: str | None = None) -> PackageEgressSession:
    """Allocate netns + bind the sandboxd-resident allowlist proxy URL.

    The proxy process itself runs inside sandboxd (veth host IP lives there).
    The API must not bind it. Caller must ``await session.close()``.
    """
    if not registry_egress_available():
        raise SandboxError(
            "无法云端装包：主机不具备包装源白名单出网能力（需 Linux gVisor + netns）。"
            "不会在无 chokepoint 时假装装包。",
            code=EGRESS_UNAVAILABLE_CODE,
        )

    slot = await _alloc_slot()
    netns = PackageNetns(slot=slot, subnet_base=settings.package_veth_subnet_base)
    try:
        await netns.setup()
    except PackageNetnsError as exc:
        await _free_slot(slot)
        raise SandboxError(
            "云端装包网络隔离不可用（netns 创建失败），本回合无法装包。",
            code=EGRESS_UNAVAILABLE_CODE,
        ) from exc
    except Exception:
        with contextlib.suppress(Exception):
            await netns.teardown()
        await _free_slot(slot)
        raise

    proxy_url = f"http://{netns.host_ip}:{int(settings.package_egress_proxy_port)}"
    bucket = resolve_cache_bucket(cache_bucket)
    # Pass the resolved name so package_cache_host_dir does not mint another ephemeral.
    cache_dir = package_cache_host_dir(bucket)
    logger.info(
        "package.egress_opened",
        slot=slot,
        proxy_url=proxy_url,
        cache_bucket=bucket,
        cache_dir=str(cache_dir),
        ephemeral=is_ephemeral_bucket(bucket),
    )
    return PackageEgressSession(
        slot=slot,
        netns=netns,
        proxy_url=proxy_url,
        cache_host_dir=cache_dir,
        cache_bucket=bucket,
    )
