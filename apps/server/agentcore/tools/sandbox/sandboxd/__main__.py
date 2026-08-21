"""uid-0 helper: ``python -m agentcore.tools.sandbox.sandboxd``."""

from __future__ import annotations

import asyncio
import contextlib
import signal

from agentcore.core.logging import get_logger, setup_logging
from agentcore.tools.sandbox.sandboxd.server import SandboxdServer


async def _amain() -> None:
    setup_logging()
    logger = get_logger(__name__)
    server = SandboxdServer.from_settings()
    await server.start()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError, RuntimeError, AttributeError):
            loop.add_signal_handler(sig, stop.set)
    logger.info("sandboxd.ready")
    await stop.wait()
    await server.close()


def main() -> None:
    asyncio.run(_amain())


if __name__ == "__main__":
    main()
