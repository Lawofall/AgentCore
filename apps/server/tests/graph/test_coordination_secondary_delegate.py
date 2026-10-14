"""慢工人，给还在跑的协调检查用。旧的二次派工用例已删。"""

from __future__ import annotations

import asyncio

from agentcore.llm.provider.protocol import LLMChunk


class _SlowWorkers:
    """All workers sleep so the CEO can fire a second delegate mid-flight."""

    def __init__(self, texts: list[str], delay: float = 0.35) -> None:
        self._texts = texts
        self.calls = 0
        self.delay = delay

    async def stream(self, request):  # noqa: ANN001
        idx = self.calls
        self.calls += 1
        await asyncio.sleep(self.delay)
        text = self._texts[idx] if idx < len(self._texts) else "done"
        yield LLMChunk(delta_content=text)
