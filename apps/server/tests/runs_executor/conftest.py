"""Shared fixtures for runs executor tests."""

import tempfile
from pathlib import Path

from agentcore.core.types import ToolApproval, ToolCategory
from agentcore.llm.profiles import PLATFORM_MODEL_FLASH, TurnProfiles
from agentcore.llm.provider.protocol import LLMChunk, TokenUsage, ToolCallDelta
from agentcore.runtime.approvals import ApprovalGate
from agentcore.runtime.events import EventSink
from agentcore.runtime.interaction import InteractionRegistry
from agentcore.runtime.runs.executor import build_agent_executor
from agentcore.runtime.runs.plan import RunPlan
from agentcore.runtime.runs.types import RunPhase, RunSpec, RunState
from agentcore.tools.file_products import file_product
from agentcore.tools.protocol import ToolContext, ToolResult, ToolSchema
from agentcore.tools.registry import ToolRegistry
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace.server import ServerWorkspace


class _ContentProvider:
    """Fake LLM: yields one scripted content chunk per call (no tool calls) and
    records each request's user message so dep-injection can be asserted.

    ``requests`` keeps the FULL (role, content) list per call so a continuation /
    auto-rework test can prove the worker sees its own prior draft + the appended
    instruction (统一「续写」原语)."""

    base_url = "http://test.invalid/v1"

    def __init__(self, contents: list[str]) -> None:
        self._contents = contents
        self.calls = 0
        self.user_messages: list[str] = []
        self.system_messages: list[str] = []
        self.requests: list[list[tuple[str, str]]] = []

    async def stream(self, request):
        self.requests.append([(m.role, m.content or "") for m in request.messages])
        user = next((m.content for m in request.messages if m.role == "user"), "")
        self.user_messages.append(user or "")
        system = next((m.content for m in request.messages if m.role == "system"), "")
        self.system_messages.append(system or "")
        text = self._contents[self.calls] if self.calls < len(self._contents) else "done"
        self.calls += 1
        yield LLMChunk(delta_content=text)


# An isolated, EMPTY workspace root for the fake-provider runs: their tool stubs
# never actually write to disk, so index_files() over this root is a clean [] —
# keeping the worker workspace manifest deterministic (and off the polluted /
# huge cwd tree that Path(".") would walk every run).
_WS_ROOT = Path(tempfile.mkdtemp(prefix="exec-ws-"))


def _ctx() -> ToolContext:
    # These fake-provider runs never invoke a real tool, so the backend is inert — it
    # only has to satisfy the ToolContext contract and answer index_files() (empty).
    return ToolContext.create(
        execution_id="e",
        run_id="s",
        agent_id="a",
        backend=ServerWorkspace(root=_WS_ROOT, sandbox=SubprocessSandbox()),
        user_id="u",
    )


def _executor(plan, provider, sink: EventSink, *, profile_set: TurnProfiles | None = None):
    return build_agent_executor(
        plan=plan,
        llm=provider,
        tools=ToolRegistry(),
        sink=sink,
        base_tool_context=_ctx(),
        profile_set=profile_set,
        system_prompt="SYS",
        user_message="原始请求",
        execution_id="e",
        approval_gate=None,
    )


def _flash_profiles() -> TurnProfiles:
    """Pin worker pricing to DeepSeek Flash so cost assertions stay stable."""
    return TurnProfiles(
        model=PLATFORM_MODEL_FLASH,
        model_overrides={
            "agent": PLATFORM_MODEL_FLASH,
        },
    )


class _UsageProvider:
    """Fake LLM that reports a usage chunk so the executor can price the run.
    Splits input into cache hit/miss to prove the split survives to RunState."""

    base_url = "http://test.invalid/v1"

    async def stream(self, request):
        yield LLMChunk(delta_content="OUT")
        yield LLMChunk(
            usage=TokenUsage(
                input_tokens=2_000_000,
                cache_hit_tokens=1_000_000,
                cache_miss_tokens=1_000_000,
                output_tokens=1_000_000,
            )
        )


class _ScriptedRounds:
    """Fake LLM yielding a pre-scripted chunk list per call (one call = one ReAct
    round), so a test can script a multi-round attempt that calls file_write.

    Records each call's first user message so a DAG test can assert what context
    (e.g. a 递指针 pointer block) reached a downstream worker's prompt."""

    base_url = "http://test.invalid/v1"

    def __init__(self, rounds: list[list[LLMChunk]]) -> None:
        self._rounds = rounds
        self.calls = 0
        self.user_messages: list[str] = []
        self.system_messages: list[str] = []

    async def stream(self, request):  # noqa: ANN001 - duck-typed for the loop
        user = next((m.content for m in request.messages if m.role == "user"), "")
        self.user_messages.append(user or "")
        system = next((m.content for m in request.messages if m.role == "system"), "")
        self.system_messages.append(system or "")
        chunks = (
            self._rounds[self.calls]
            if self.calls < len(self._rounds)
            else [LLMChunk(delta_content="done")]
        )
        self.calls += 1
        for chunk in chunks:
            yield chunk


class _FileWriteTool:
    """A stub named ``file_write`` that lands a file the way the real pen does.

    The ledger reads a tool's OWN self-report (``ToolResult.file_products``), not its
    name or its arguments — so a stub must report the path it "landed" for
    ``files_touched`` / the落盘 gate to see anything, exactly like the real tool.
    """

    def __init__(self) -> None:
        self.calls = 0

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="file_write",
            description="stub file write",
            parameters={
                "type": "object",
                "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
            },
            category=ToolCategory.EXECUTION,
            approval=ToolApproval.NEVER,
        )

    async def execute(self, arguments, context) -> ToolResult:  # noqa: ANN001
        self.calls += 1
        path = str((arguments or {}).get("path") or "").strip()
        return ToolResult(
            tool_call_id="",
            success=True,
            output="written",
            file_products=[file_product(path)] if path else [],
        )


class _ToolCallThenContent:
    """Fake LLM: round 1 calls a tool, round 2 returns content (no network)."""

    base_url = "http://test.invalid/v1"

    def __init__(self, tool_name: str, args: str, content: str) -> None:
        self._rounds = [
            [
                LLMChunk(
                    delta_tool_calls=[
                        ToolCallDelta(
                            index=0, id="c1", function_name=tool_name, arguments_delta=args
                        )
                    ]
                )
            ],
            [LLMChunk(delta_content=content)],
        ]
        self.calls = 0

    async def stream(self, request):  # noqa: ANN001 - duck-typed for the loop
        chunks = self._rounds[self.calls] if self.calls < len(self._rounds) else []
        self.calls += 1
        for chunk in chunks:
            yield chunk


class _GrantableTool:
    """A GRANTABLE stub recording whether it actually executed."""

    def __init__(self, name: str = "code_execute") -> None:
        self._name = name
        self.calls = 0

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self._name,
            description="stub grantable",
            parameters={"type": "object", "properties": {}},
            category=ToolCategory.EXECUTION,
            approval=ToolApproval.GRANTABLE,
        )

    async def execute(self, arguments, context) -> ToolResult:  # noqa: ANN001
        self.calls += 1
        return ToolResult(tool_call_id="", success=True, output="ran")


def _gate(timeout_seconds: float) -> ApprovalGate:
    # Pin CAUTIOUS: DEFAULT_PERMISSION_AXES is 少打断 (command=auto) which
    # auto-passes code_execute via permission_axes.auto_executes — that would
    # skip the gate and defeat deny-path assertions.
    from agentcore.core.types import AutonomyPolicy, recipe_to_axes

    return ApprovalGate(
        sink=EventSink(),
        conversation_id="conv-1",
        registry=InteractionRegistry(),
        timeout_seconds=timeout_seconds,
        permission_axes=recipe_to_axes(AutonomyPolicy.CAUTIOUS),
    )


class _OfferRecorder:
    """Fake LLM that records the tool definitions it was OFFERED each call (proves
    the allowed_tool_names wiring), then yields one content chunk and stops."""

    base_url = "http://test.invalid/v1"

    def __init__(self) -> None:
        self.offered: list[list[str]] = []
        self.choices: list[str] = []

    async def stream(self, request):  # noqa: ANN001 - duck-typed for the loop
        self.offered.append([t["function"]["name"] for t in (request.tools or [])])
        self.choices.append(request.tool_choice)
        yield LLMChunk(delta_content="DONE")


class _ResearchTool:
    """A SEARCH stub returning fixed citations (proves the executor collects a
    worker's web sources onto RunState)."""

    def __init__(self, name: str = "search", citations=None) -> None:  # noqa: ANN001
        self._name = name
        self._citations = citations or []
        self.calls = 0

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self._name,
            description="stub search",
            parameters={"type": "object", "properties": {}},
            category=ToolCategory.SEARCH,
        )

    async def execute(self, arguments, context) -> ToolResult:  # noqa: ANN001
        self.calls += 1
        return ToolResult(tool_call_id="", success=True, output="result", citations=self._citations)


class _MeteredRoundThenBoom:
    """Round 0: a tool call + usage chunk (the loop meters it and continues);
    round 1: raises. Proves a hard worker failure still bills the round that
    completed before the crash (B-deep 失败计费), instead of dropping its tokens."""

    base_url = "http://test.invalid/v1"

    def __init__(self) -> None:
        self.calls = 0

    async def stream(self, request):  # noqa: ANN001 - duck-typed for the loop
        c = self.calls
        self.calls += 1
        if c == 0:
            yield LLMChunk(
                delta_tool_calls=[
                    ToolCallDelta(index=0, id="c1", function_name="noop", arguments_delta="{}")
                ]
            )
            yield LLMChunk(
                usage=TokenUsage(input_tokens=1000, cache_miss_tokens=1000, output_tokens=400)
            )
            return
        raise RuntimeError("provider down")
        yield  # pragma: no cover - makes this an async generator


def _state(
    content: str = "",
    *,
    files: list[str] | None = None,
    debrief: dict | None = None,
) -> RunState:
    return RunState(
        phase=RunPhase.COMPLETED,
        content=content,
        files_touched=list(files or []),
        debrief=debrief,
    )


def _plan(*specs: RunSpec) -> RunPlan:
    plan = RunPlan()
    for spec in specs:
        plan.add(spec)
    return plan
