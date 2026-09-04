"""uid-0 helper: ``python -m agentcore.tools.sandbox.sandboxd``."""

from __future__ import annotations

import asyncio
import contextlib
import signal

from agentcore.core.logging import get_logger, setup_logging
from agentcore.tools.sandbox.egress.proxy import (
    ensure_package_egress_proxy,
    shutdown_package_egress_proxy,
)
from agentcore.tools.sandbox.sandboxd.preview_http import (
    ensure_preview_http,
    shutdown_preview_http,
)
from agentcore.tools.sandbox.sandboxd.server import SandboxdServer


async def _amain() -> None:
    setup_logging()
    logger = get_logger(__name__)
    server = SandboxdServer.from_settings()
    await server.start()
    try:
        await ensure_package_egress_proxy()
    except Exception as exc:  # noqa: BLE001 — proxy is for install; desk exec must still listen
        logger.warning("sandboxd.package_proxy_failed", error=str(exc)[:200])
    try:
        await ensure_preview_http()
    except Exception as exc:  # noqa: BLE001 — preview bind is optional; desk exec must still listen
        logger.warning("sandboxd.preview_proxy_failed", error=str(exc)[:200])
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError, RuntimeError, AttributeError):
            loop.add_signal_handler(sig, stop.set)
    logger.info("sandboxd.ready")
    await stop.wait()
    await server.close()
    await shutdown_package_egress_proxy()
    await shutdown_preview_http()


def main() -> None:
    asyncio.run(_amain())


if __name__ == "__main__":
    main()
