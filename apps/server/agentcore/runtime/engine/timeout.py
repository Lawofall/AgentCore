"""Engine-level wall-clock ceilings for tool execution."""

from typing import Any

from agentcore.config import settings
from agentcore.core.types import ToolCategory
from agentcore.tools.protocol import ToolSchema

from .constants import TIMEOUT_EXEMPT_CATEGORIES


def resolve_tool_timeout(
    schema: ToolSchema, arguments: dict[str, Any] | None = None
) -> float | None:
    """The engine-level wall-clock ceiling (seconds) for one call of this tool.

    ``None`` ⇒ no engine backstop (the tool manages its own lifecycle). Precedence:
    ``terminal`` / ``git`` / ``host`` derive a dynamic ceiling from arguments (must outlive
    per-op deadlines + kill slack); else an explicit ``schema.timeout_seconds``
    wins; else the tool's category decides — ORCHESTRATION / INTERACTION are
    exempt (``None``), EXECUTION gets the higher execution ceiling (it runs code),
    everything else the default. This is a coarse safety net layered above each
    tool's own finer timeout, never a replacement (B1).

    For FILESYSTEM / Local workspace ops this value is the **liveness budget
    owner**: ``tool_exec`` binds it on a ContextVar and ``WorkspaceChannel``
    derives its transport deadline from it (minus settle slack). Capacity
    ceilings (bytes / extract) fail via ``contract_failure`` before this matters.
    """
    if schema.name == "terminal":
        from agentcore.tools.builtin.terminal import terminal_op_timeout_seconds

        return terminal_op_timeout_seconds(arguments)
    if schema.name == "git":
        from agentcore.tools.builtin.git_ops import git_tool_timeout_seconds

        return git_tool_timeout_seconds(arguments)
    if schema.name == "host":
        from agentcore.tools.builtin.host import host_tool_timeout_seconds

        return host_tool_timeout_seconds(arguments)
    if schema.timeout_seconds is not None:
        return schema.timeout_seconds
    if schema.category in TIMEOUT_EXEMPT_CATEGORIES:
        return None
    if schema.category is ToolCategory.EXECUTION:
        return settings.tool_execution_timeout_seconds
    return settings.tool_default_timeout_seconds


def outer_liveness_timeout_meta(
    name: str,
    ctx: object,
    hang_msg: str,
) -> tuple[str, str, dict[str, Any]]:
    """Outer hang face: message, failure code, ToolAttempt meta.

    Cloud exec-env tools that hang share the dead-sandbox face, not a generic
    liveness string.
    """
    from agentcore.runtime.facts import CrossTurnRetry, cross_turn_retry_meta
    from agentcore.runtime.loop_controller import (
        ERROR_CLASS_PERMANENT,
        EXEC_ENV_TIMEOUT_FAMILY,
    )

    failure_code = "liveness_timeout"
    attempt_extra: dict[str, Any] = {
        "liveness_timeout": True,
        "timeout_layer": "outer",
        "error_class": ERROR_CLASS_PERMANENT,
        **cross_turn_retry_meta(CrossTurnRetry.NOT_FUTILE),
    }
    timeout_msg = hang_msg
    backend = getattr(ctx, "backend", None)
    if (
        name in EXEC_ENV_TIMEOUT_FAMILY
        and getattr(backend, "location", None) == "server"
    ):
        from agentcore.tools.sandbox.exec_env import (
            EXEC_ENV_SANDBOX_UNAVAILABLE_USER_MESSAGE,
            sandbox_unavailable_tool_meta,
        )

        timeout_msg = EXEC_ENV_SANDBOX_UNAVAILABLE_USER_MESSAGE
        failure_code = "exec_env_sandbox_unavailable"
        attempt_extra.update(sandbox_unavailable_tool_meta())
        attempt_extra["timeout_layer"] = "outer"
        attempt_extra["liveness_timeout"] = True
    execution_id = getattr(ctx, "execution_id", None)
    if execution_id:
        attempt_extra.setdefault("execution_id", execution_id)
    return timeout_msg, failure_code, attempt_extra
