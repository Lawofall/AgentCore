"""SandboxProvider Protocol for isolated code execution."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

from agentcore.core.text import truncate_head_tail


@dataclass
class ExecutionRequest:
    """Request to execute code in a sandbox."""

    code: str
    language: Literal["python", "javascript", "bash"]
    timeout_seconds: int = 30
    memory_limit_mb: int = 256
    stdin: str | None = None
    cwd: str | None = None  # working dir for the process; None = throwaway temp dir
    # Optional callback for streaming stdout/stderr chunks during execution.
    # ``stream`` is ``"stdout"`` or ``"stderr"``; ``chunk`` is a decoded text fragment.
    on_output: Callable[[str, str], None] | None = None
    # Primary hang detection: kill when no stdout/stderr for this many seconds.
    # ``None`` = wall-clock only (``timeout_seconds``). Idle resets on any output.
    idle_timeout_seconds: int | None = None
    # Resource / isolation knobs (optional; defaults preserve subprocess behaviour).
    env: dict[str, str] | None = None
    # Reserved historically; cloud desk guests always attach the packaging
    # allowlist netns. SubprocessSandbox ignores this. ``restricted`` vs ``none``
    # is no longer an independent gVisor execute mode.
    network_mode: Literal["none", "restricted"] = "none"
    # Optional DATA_DIR pkg-cache bucket (user_id / conversation id). Empty →
    # per-open ``ephemeral-*`` under pkg-cache (no shared global fallback).
    cache_bucket: str | None = None
    cpu_limit: float = 1.0
    pids_limit: int = 128


@dataclass(frozen=True)
class SandboxCapabilities:
    """Advertised isolation and resource limits of a sandbox backend."""

    isolation: Literal["subprocess", "gvisor", "microvm"]
    supports_network: bool
    max_memory_mb: int
    max_timeout_seconds: int


@dataclass
class ExecutionResult:
    """Result from sandbox code execution."""

    success: bool
    stdout: str
    stderr: str
    exit_code: int
    duration_ms: int
    truncated: bool = False
    # Bind-to-disk (cloud desk / SubprocessSandbox): workspace-relative paths
    # the execution created or modified. Empty when nothing changed or scan skipped.
    written_files: list[str] | None = None

    _MAX_OUTPUT_LEN = 8000

    def __post_init__(self):
        # HEAD+TAIL cut (not head-only): a long stdout's tail — traceback last line /
        # exit summary — must survive this sandbox-level cap, otherwise the downstream
        # ToolResult head+tail (tools/protocol.py) has nothing left to preserve (05 P3-3).
        capped_stdout = truncate_head_tail(self.stdout, self._MAX_OUTPUT_LEN)
        if capped_stdout != self.stdout:
            self.stdout = capped_stdout
            self.truncated = True
        capped_stderr = truncate_head_tail(self.stderr, self._MAX_OUTPUT_LEN)
        if capped_stderr != self.stderr:
            self.stderr = capped_stderr
            self.truncated = True


class SandboxProvider(Protocol):
    """Unified abstraction for code execution sandboxes."""

    async def execute(self, request: ExecutionRequest) -> ExecutionResult:
        """Execute code in an isolated environment."""
        ...

    async def health_check(self) -> bool:
        """Check if the sandbox is available."""
        ...

    def capabilities(self) -> SandboxCapabilities:
        """Describe the isolation boundary this provider offers."""
        ...


@runtime_checkable
class InterpreterProbe(Protocol):
    """Sandboxes whose exec-env health is a per-language question.

    ``SubprocessSandbox`` runs whatever the host happens to have on PATH, so
   「能不能跑」has one answer per language. ``probe_interpreter`` is the cloud
    boot / ``cloud_health`` hook — per-execute classification is driven by the
    real run. gVisor deliberately does NOT implement this: its ``health_check``
    smoke-runs the ``runsc`` runtime, which is cloud's only runtime health
    signal and says nothing about interpreters — one verdict for the whole
    backend is the correct scope there.
    """

    async def probe_interpreter(self, language: str) -> bool:
        """Cloud-health hook: can ``language`` run a minimal print on this host."""
        ...
