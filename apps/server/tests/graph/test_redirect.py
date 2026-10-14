"""Drive-level run-redirect (热优先 · 冷诚实回落): user redirect cancels ONE worker;
salvageable transcript → continue_run revision; empty → cold ``_redir`` + replaces_run_id.
Parallel teammates keep running.
"""

import asyncio

from agentcore.llm.provider.protocol import LLMChunk
from agentcore.runtime.events import EventSink
from agentcore.runtime.events.types import EventType
from agentcore.runtime.runs.redirect_queue import enqueue_redirect, take_redirects
from tests.delegate.conftest import Provider, ctx, tool

_STEER = "改成B方向重做"


class _RedirectProvider:
    """ORIGINAL workers sleep (so redirect can cancel mid-flight). COLD re-run sees
    ``steer`` in the user prompt; HOT continue_run sees the revision instruction."""

    def __init__(self) -> None:
        self.steered_calls = 0
        self.hot_calls = 0

    async def stream(self, request):  # noqa: ANN001
        user = " ".join(m.content or "" for m in request.messages if m.role == "user")
        if (
            "续干指令" in user
            or "修改要求" in user
            or "立即改此人" in user
            or "改方向" in user
        ):
            self.hot_calls += 1
            yield LLMChunk(delta_content="HOT_DONE")
            return
        if _STEER in user:
            self.steered_calls += 1
            yield LLMChunk(delta_content="STEERED_DONE")
            return
        await asyncio.sleep(0.5)
        yield LLMChunk(delta_content="ORIG_DONE")


class _RedirectOnStartSink(EventSink):
    """Enqueue ONE redirect when the first worker starts."""

    def __init__(self, feedback: str) -> None:
        super().__init__()
        self._feedback = feedback
        self._sent = False
        self.redirected_run_id = ""

    def emit(self, event) -> None:  # noqa: ANN001
        if not self._sent and event.type is EventType.RUN_STARTED:
            run_id = str(event.payload.get("run_id") or "")
            # Skip revision / redir children — only redirect the original worker.
            if run_id and "_rev" not in run_id and not run_id.endswith("_redir"):
                self.redirected_run_id = run_id
                enqueue_redirect(
                    execution_id="e",
                    run_id=run_id,
                    feedback=self._feedback,
                    conversation_id="c",
                )
                self._sent = True
        super().emit(event)


class _HotRedirectProvider:
    """First call for a worker yields a draft then sleeps (so cancel can salvage);
    subsequent continue_run sees the revision instruction."""

    def __init__(self) -> None:
        self.calls = 0
        self.hot_calls = 0

    async def stream(self, request):  # noqa: ANN001
        self.calls += 1
        user = " ".join(m.content or "" for m in request.messages if m.role == "user")
        if "续干指令" in user or "修改要求" in user:
            self.hot_calls += 1
            yield LLMChunk(delta_content="HOT_DONE")
            return
        # Emit a draft assistant turn worth of content, then hang so redirect cancels.
        yield LLMChunk(delta_content="半成品草稿")
        await asyncio.sleep(0.5)
        yield LLMChunk(delta_content="…续")


class _HotRedirectSink(EventSink):
    """Redirect after the first run_output_delta (partial draft is on the wire)."""

    def __init__(self, feedback: str) -> None:
        super().__init__()
        self._feedback = feedback
        self._sent = False
        self.redirected_run_id = ""
        self.cancelled_reasons: list[str] = []
        self.continues_roots: list[str] = []
        self.replaces: list[str] = []

    def emit(self, event) -> None:  # noqa: ANN001
        if event.type is EventType.RUN_CANCELLED:
            self.cancelled_reasons.append(str(event.payload.get("reason") or ""))
        if event.type is EventType.RUN_STARTED:
            continues = event.payload.get("continues_run_id")
            if continues:
                self.continues_roots.append(str(continues))
            replaces = event.payload.get("replaces_run_id")
            if replaces:
                self.replaces.append(str(replaces))
        if (
            not self._sent
            and event.type is EventType.RUN_OUTPUT_DELTA
            and event.payload.get("delta")
        ):
            run_id = str(event.payload.get("run_id") or "")
            if run_id:
                self.redirected_run_id = run_id
                enqueue_redirect(
                    execution_id="e",
                    run_id=run_id,
                    feedback=self._feedback,
                    conversation_id="c",
                )
                self._sent = True
        super().emit(event)
