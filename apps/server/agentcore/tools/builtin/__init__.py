"""Built-in tool implementations — registries collect from ``tools.registration``."""

from typing import Literal

from agentcore.config import settings
from agentcore.core.types import PermissionAxes, ToolApproval, ToolCategory
from agentcore.tools.registration import (
    AUDIENCE_CEO,
    ToolSurface,
    declared_tool_name,
    declared_tools,
    execution_class_tool_names,
    instantiate_declared,
    tool_registration,
)
from agentcore.tools.registry import ToolRegistry
from agentcore.workspace.protocol import WorkspaceBackend


def code_execution_enabled_for(backend: WorkspaceBackend | None) -> bool:
    """Whether the code-execution tool class may appear in a runtime worker toolset.

    Governs the WHOLE class that runs code through the sandbox chain — ``code_execute``
    AND ``test_run`` (a test suite executes arbitrary project code, so it is
    execution-equivalent). Local / sidecar execution stays on; cloud ``location=server``
    cloud defaults **on** via ``GVISOR_ENABLED`` (code default true；紧急可 false)；
    or ``CODE_EXECUTE_CLOUD_ENABLED`` escape hatch (a plain subprocess in the API
    container is not a real isolation boundary — 安全权限与治理 §5). The desktop
    sidecar process is never a gVisor host: a cloud desk from sidecar withholds
    the class (do not pretend isolation on the user's machine). Keeping both
    tools behind ONE predicate (not a per-tool special-case) is what makes the
    production-security posture cover the class consistently.

    When cloud execution is config-enabled, the sandbox ``health_check`` verdict
    (``tools.sandbox.cloud_health``) also gates this predicate: a failed probe withholds
    the class so registry registration and ``workspace_context`` stay truthful. The
    verdict is TTL-refreshed in the background on read, so a sandbox that rots after
    boot is caught rather than trusted for the process life. An unprobed process
    (tests, lifespan not run, config off) keeps config-only semantics.

    Does **not** fold ``command=ask`` withhold — callers that stamp capability lines or
    build registries must use :func:`execution_class_enabled_for` so ask / backend /
    health share one truth with the worker toolset.
    """
    if backend is None:
        return True
    if backend.location == "local":
        return True
    # gVisor lives in the cloud API process only. The desktop sidecar must not
    # assemble isolation execution for a cloud desk — Windows would fail
    # ``not_linux`` in 10ms and retire the family as if the local interpreter
    # were dead; Linux sidecar would isolate against sidecar-local files, not
    # the cloud volume. SEC-005 also forbids falling back to a plain subprocess
    # on ``location=server``.
    if _sidecar_process_hosts_no_cloud_sandbox():
        return False
    if not (settings.gvisor_enabled or settings.code_execute_cloud_enabled):
        return False
    from agentcore.tools.sandbox.cloud_health import cloud_sandbox_health

    # False → known unhealthy; True / None (never probed) → config gate alone.
    return cloud_sandbox_health() is not False


def _sidecar_process_hosts_no_cloud_sandbox() -> bool:
    """True when this process is the desktop engine (never a gVisor host)."""
    try:
        from agentcore.sidecar.server_pkg.core import is_sidecar_process
    except ImportError:
        return False
    return is_sidecar_process()


def execution_class_enabled_for(
    backend: WorkspaceBackend | None,
    permission_axes: "PermissionAxes | None" = None,
) -> bool:
    """Final include_execution predicate for worker registry + capability lines.

    ``code_execution_enabled_for(backend)`` ∧ ¬``command=ask`` withhold. Same bit
    ``build_worker_registry`` uses so ``workspace_context`` never claims
    ``code_execute=已装配`` while the toolset / identity says 未装配
    (案 20260803-docx-office-exec-capability-lie A).
    """
    if permission_axes is not None and permission_axes.withholds_execution_tools:
        return False
    return code_execution_enabled_for(backend)


def _desktop_bridge_ready() -> bool:
    """True when DesktopBrowserBridge credentials probe healthy for this turn."""
    from agentcore.runtime.browser.desktop_bridge import (
        desktop_bridge_configured,
        desktop_bridge_health,
        ensure_desktop_bridge_health,
    )

    # Cached True → allow; False → not ready; None → probe once if env/turn present.
    cached = desktop_bridge_health()
    if cached is True:
        return True
    if cached is False:
        return False
    if not desktop_bridge_configured():
        return False
    return ensure_desktop_bridge_health()


def _browser_sandbox_host_ready() -> bool:
    """gVisor + shape-B ``health("net")`` — shared server / local fallback.

    ``code_execute_cloud_enabled`` subprocess path does NOT enable browsers.
    Shape A ``cloud_sandbox_health`` does not gate this: an unhealthy code
    sandbox must not withhold a healthy browser netns. ``None`` (never probed)
    is fail-closed — do not assemble.
    """
    if not settings.gvisor_enabled:
        return False
    from agentcore.tools.sandbox.browser.netns import browser_netns_health

    return browser_netns_health() is True


def browser_host_kind_for(
    backend: WorkspaceBackend | None,
) -> Literal["local", "sandbox"] | None:
    """Registry / navigate host_kind aligned with :func:`browser_execution_enabled_for`.

    - Healthy DesktopBrowserBridge → ``local`` (real page).
    - No usable Bridge but gVisor/sandbox/netns ready → ``sandbox`` (covers cloud
      过桥: ``location=local`` while the API process cannot reach desktop loopback).
    - True local engine (no Bridge, no gVisor) → ``None`` (withhold; no fake success).
    - ``location=server`` → ``sandbox`` when sandbox host ready, else ``None``.

    Never returns ``local`` unless Bridge is ready — so factory must not call
    ``open_local_bridge_session`` on a sandbox-fallback assembly.
    """
    if backend is None:
        return None
    if backend.location == "local":
        if _desktop_bridge_ready():
            return "local"
        return "sandbox" if _browser_sandbox_host_ready() else None
    if backend.location != "server":
        return None
    return "sandbox" if _browser_sandbox_host_ready() else None


def browser_execution_enabled_for(backend: WorkspaceBackend | None) -> bool:
    """Whether the L3 team-browser tool class may appear in a worker toolset (D11 / C1).

    Paths (never mixed on one session — C4; host_kind from :func:`browser_host_kind_for`):

    - **local + DesktopBrowserBridge**: desktop re-sends ``browserBridge`` on each
      sidecar turn (``apply_desktop_bridge_from_turn``); successful ``GET /health``
      for the current credential generation → host_kind=local.
    - **local without Bridge + gVisor/netns**: host_kind=sandbox (大众默认云端过桥；
      cloud API cannot reach本机 loopback Bridge).
    - **server + gVisor**: host_kind=sandbox; folds shape-B ``health("net")`` (fail-closed).
    - True local engine with neither Bridge nor gVisor → withhold (no fake success).
    """
    return browser_host_kind_for(backend) is not None


def git_execution_enabled_for(
    backend: WorkspaceBackend | None,
    *,
    desktop_online: bool = False,
) -> bool:
    """Whether the ``git`` tool may appear in a runtime toolset for this workspace.

    ``git_ops`` has exactly two transports (see ``git_ops.spawn.git_transport_scope``):
    a same-process ``git`` spawned under ``backend.root`` — cloud ``ServerWorkspace``
    and the sidecar's ``ServerWorkspace(location="local")`` — or ``WorkspaceOp.GIT_RUN``
    over the desktop channel, which is the ONLY path a rootless ``LocalWorkspace`` has.
    That second transport needs a live desktop, so a channel-backed local workspace
    reached from a non-desktop session can never run git: withhold the whole tool
    instead of advertising one whose every call is an instant refusal.

    ``backend is None`` (capability catalog / tests) keeps the tool listed, same
    posture as :func:`code_execution_enabled_for`.

    The in-process transport additionally folds in the boot probe for a usable
    ``git`` binary (``git_ops.binary_health``) — an image or PATH without git turns
    every call into a raw ``FileNotFoundError``, so withhold the tool instead. The
    probe answers "can THIS process exec git", which is meaningless for the channel
    transport: there git runs on the user's machine, so that branch returns above
    and is never gated on it. ``None`` (never probed) keeps the tool offered.
    """
    if backend is None:
        return True
    if backend.location == "local" and getattr(backend, "root", None) is None:
        return desktop_online
    from agentcore.tools.builtin.git_ops.binary_health import git_binary_health

    return git_binary_health() is not False


def build_builtin_registry(
    *,
    include_execution_tools: bool = True,
    include_host_tools: bool = False,
    include_browser: bool = False,
    include_desktop_online_tools: bool = False,
    include_git: bool = True,
    location: Literal["server", "local"] | None = None,
    languages: tuple[str, ...] | list[str] | None = None,
) -> ToolRegistry:
    """Register the platform's built-in tools (single source: ``DECLARED_TOOLS``).

    Both the chat pipeline (worker toolset) and the read-only capability catalog
    build from declarations with ``surface=builtin``. CEO orchestration primitives
    and worker-only tools are separate surfaces.

    ``include_execution_tools`` gates the code-execution class as a unit
    (``test_run`` + ``code_execute`` + local ``terminal``): the worker registry
    withholds the class on a backend that can't run code safely (see
    ``code_execution_enabled_for``). ``terminal`` is additionally ``local_only``.

    ``include_host_tools`` gates the Host face (``host_class``): only when the
    desktop backfill channel is reachable and ``host≠off``.

    ``include_desktop_online_tools`` gates ``desktop_online_class`` tools (e.g.
    ``external_mount_readonly``): desktop online only — not ``host≠off``.

    ``include_git`` gates ``git_class`` (``git``): the workspace must have a root to
    spawn git under, or a live desktop channel (see ``git_execution_enabled_for``).

    ``include_browser`` gates the L3 browser class on the builtin surface
    (navigate/click/type/scroll/snapshot — CEO+worker; screenshot stays worker-only).
    Default False so a no-Bridge / no-gVisor process does not leak browser tools into the
    default builtin roster.

    ``location`` stamps ``code_execute``'s description to match the turn's backend
    and gates ``local_only`` tools (``terminal`` only when ``location=="local"``).
    ``languages`` trims ``code_execute``'s language enum after a local/sidecar probe
    (cloud / catalog leave ``None`` → full fixed surface).
    """
    registry = ToolRegistry()
    for cls in declared_tools(surface=ToolSurface.BUILTIN):
        reg = tool_registration(cls)
        if reg.browser_class:
            if not include_browser:
                continue
        elif reg.execution_class and not include_execution_tools:
            continue
        if reg.host_class and not include_host_tools:
            continue
        if reg.desktop_online_class and not include_desktop_online_tools:
            continue
        if reg.git_class and not include_git:
            continue
        if reg.local_only and location != "local":
            continue
        registry.register(
            instantiate_declared(cls, location=location, languages=languages)
        )
    return registry


def build_worker_registry(
    *,
    backend: WorkspaceBackend | None = None,
    permission_axes: "PermissionAxes | None" = None,
    languages: tuple[str, ...] | list[str] | None = None,
    desktop_online: bool = False,
) -> ToolRegistry:
    """The delegated worker's toolset: builtins PLUS worker-only declarations.

    ``command=ask`` withholds the entire execution class
    (``code_execute`` / ``test_run`` / ``terminal``) — read-only retrieval stays on.
    Host tools gate on ``desktop_online`` ∧ ``host≠off`` (orthogonal to command).
    ``git`` gates on the workspace's real git transport (``git_execution_enabled_for``).
    """
    location = backend.location if backend is not None else None
    include_execution = execution_class_enabled_for(backend, permission_axes)
    ask_withhold = (
        permission_axes is not None and permission_axes.withholds_execution_tools
    )
    # Shape A health must not withhold browser: only ask-withhold ∧ shape-B netns.
    include_browser = (not ask_withhold) and browser_execution_enabled_for(backend)
    include_host = desktop_online and (
        permission_axes is None or not permission_axes.host_disabled
    )
    include_git = git_execution_enabled_for(backend, desktop_online=desktop_online)
    # Prefer explicit languages; else reuse a probe cached on the backend by
    # ``resolve_exec_languages`` (prepare / resume). Cloud stays untrimmed.
    resolved_languages = languages
    if resolved_languages is None and backend is not None:
        resolved_languages = getattr(backend, "_exec_languages", None)
    if location != "local":
        resolved_languages = None
    registry = build_builtin_registry(
        include_execution_tools=include_execution,
        include_host_tools=include_host,
        include_desktop_online_tools=desktop_online,
        include_browser=include_browser,
        include_git=include_git,
        location=location,
        languages=resolved_languages,
    )
    for cls in declared_tools(surface=ToolSurface.WORKER_ONLY):
        reg = tool_registration(cls)
        if reg.manual_wire:
            # Manual-wire (e.g. conversation log tools): registered after registry
            # build — see ``_wire_worker_conversation_log_tools``.
            continue
        if reg.browser_class:
            if not include_browser:
                continue
        elif reg.execution_class and not include_execution:
            continue
        if reg.host_class and not include_host:
            continue
        if reg.desktop_online_class and not desktop_online:
            continue
        if reg.git_class and not include_git:
            continue
        if reg.local_only and (backend is None or backend.location != "local"):
            continue
        registry.register(instantiate_declared(cls, location=location))
    return registry


def build_ceo_tool_registry(
    *,
    desktop_online: bool = False,
    permission_axes: "PermissionAxes | None" = None,
    backend_location: str | None = None,
    include_browser: bool = False,
    include_git: bool = True,
) -> ToolRegistry:
    """The CEO chat agent's DIRECT toolset: read / retrieval + Host + local terminal.

    Collects ``surface=builtin`` tools whose declared audience includes ``ceo``.
    Historically aligned with ``approval=NEVER``; ``host`` stays NEVER at schema
    (runtime elevation for GRANTABLE actions; CEO holds status/os_log/shell,
    worker-only actions fail and require ``delegate``).
    **B2**: local ``terminal`` is also CEO-holdable (schema NEVER; ``start`` elevates
    at runtime like ``git`` write) for pure start/stop/list of workspace long-running
    processes — not a GRANTABLE schema exception.
    **Browser**: single ``browser`` (GRANTABLE · ``browser_class``),
    gated by ``include_browser`` — same tier as host / terminal; screenshot
    is a worker-only action on the same tool.
    Orchestration primitives are wired separately in ``tools.ceo_toolset``.
    Host tools appear only when ``desktop_online`` ∧ ``host≠off``.
    ``terminal`` appears only when ``backend_location=="local"``.
    **Git**: ``include_git`` mirrors the worker registry's ``git_execution_enabled_for``
    verdict — callers pass the worker roster's answer so both toolsets agree on whether
    this workspace can run git at all.
    """
    include_host = desktop_online and (
        permission_axes is None or not permission_axes.host_disabled
    )
    location: Literal["server", "local"] | None = (
        "local" if backend_location == "local" else None
    )
    full = build_builtin_registry(
        include_host_tools=include_host,
        include_desktop_online_tools=desktop_online,
        include_browser=include_browser,
        include_git=include_git,
        location=location,
    )
    registry = ToolRegistry()
    ceo_names = {
        declared_tool_name(cls)
        for cls in declared_tools(surface=ToolSurface.BUILTIN)
        if AUDIENCE_CEO in tool_registration(cls).audience
    }
    for schema in full.list_all():
        if schema.name in ceo_names:
            registry.register(full.get(schema.name))
    return registry


def approval_class_tool_names() -> frozenset[str]:
    """Tools covered by an ``APPROVE_ALWAYS_FILES`` turn grant.

    The file-mutation class plus ``git`` write (``git`` schema stays ``NEVER`` so
    CEO read-only subcommands stay in the filtered registry).
    """
    return file_mutation_tool_names() | frozenset({"git"})


def file_mutation_tool_names() -> frozenset[str]:
    """The GRANTABLE file-mutation tools as one class — what a
    「本轮内允许所有文件改动」grant covers.

    Derived from the single builtin registry as ``GRANTABLE ∩ FILESYSTEM``.
    """
    full = build_builtin_registry()
    return frozenset(
        schema.name
        for schema in full.list_all()
        if schema.approval is ToolApproval.GRANTABLE and schema.category is ToolCategory.FILESYSTEM
    )


def file_only_tool_names() -> frozenset[str]:
    """Tools an organize worker may hold: filesystem read + mutation (no execute/terminal)."""
    full = build_builtin_registry()
    names = {
        schema.name
        for schema in full.list_all()
        if schema.category is ToolCategory.FILESYSTEM
    }
    # Grep is FILESYSTEM-adjacent but often categorized separately — include if present.
    for extra in ("grep", "code_search", "code_diagnostics"):
        if full.get(extra) is not None:
            names.add(extra)
    return frozenset(names)


def delegation_grantable_tool_names() -> frozenset[str]:
    """Tools covered by a kickoff / per-delegation grant (统一授权白名单).

    File-mutation class + ``git`` writes + every declared ``execution_class`` tool.
    """
    return approval_class_tool_names() | execution_class_tool_names()


def per_call_tool_names() -> frozenset[str]:
    """Tools whose「本轮内都允许」is refused and downgraded to a one-shot approve.

    Empty by design (Cursor-aligned UX, 2026-07).
    """
    return frozenset()
