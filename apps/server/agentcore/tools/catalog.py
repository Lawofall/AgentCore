"""Capability catalog — the single read-side projection of what this platform's
agents can do (every tool + who may call it), powering ``GET /v1/capabilities`` and
the desktop 能力图鉴.

Single source of truth: schemas and audience come from the SAME declared tool
classes the runtime wires (``tools.registration``). Worker built-ins via
:func:`build_worker_registry`; CEO orchestration primitives via
``surface=ceo_orchestration`` + ``read_static_schema`` (no heavy ``__init__``).
"""

from __future__ import annotations

from dataclasses import dataclass

from agentcore.tools.builtin import build_worker_registry
from agentcore.tools.protocol import ToolSchema
from agentcore.tools.registration import (
    AUDIENCE_CEO,
    AUDIENCE_WORKER,
    ToolSurface,
    declared_tool_name,
    declared_tools,
    read_static_schema,
    tool_registration,
)

# Who may call a tool. Same tokens as ``tools.registration.AUDIENCE_*``.
AVAILABLE_TO_CEO = AUDIENCE_CEO
AVAILABLE_TO_WORKER = AUDIENCE_WORKER


@dataclass(frozen=True)
class CatalogTool:
    """One tool in the capability catalog: its schema + who may call it."""

    schema: ToolSchema
    available_to: tuple[str, ...]


def _static_schema(tool_cls: type) -> ToolSchema:
    """Read a tool class's static ``schema`` without running its heavy ``__init__``.

    Safe ONLY because these tools' ``schema`` properties are pure static descriptors
    (no ``self`` access). Guarded by ``test_catalog`` which asserts every catalog tool
    exposes a non-empty name/description — so a future schema that needs instance state
    fails loudly instead of silently returning a half-built object.
    """
    return read_static_schema(tool_cls)


def _runtime_audience_by_name() -> dict[str, tuple[str, ...]]:
    """Audience map for builtin + worker_only declarations (excludes orchestration)."""
    out: dict[str, tuple[str, ...]] = {}
    for cls in declared_tools():
        reg = tool_registration(cls)
        if reg.surface is ToolSurface.CEO_ORCHESTRATION:
            continue
        out[declared_tool_name(cls)] = reg.audience
    return out


def build_capability_catalog() -> list[CatalogTool]:
    """Every tool an agent on this platform can call, annotated with CEO/worker reach.

    Order is stable and groupable: the worker built-ins first (CEO-shared read-only and
    worker-only mutation interleaved by registration order), then the CEO
    orchestration primitives (declaration order).
    """
    audience_by_name = _runtime_audience_by_name()
    catalog: list[CatalogTool] = []
    # Catalog advertises Host tools even when the calling session has no desktop —
    # runtime registries still gate on desktop_online ∧ host≠off.
    for schema in build_worker_registry(
        desktop_online=True,
    ).list_all():
        available = audience_by_name.get(schema.name, (AVAILABLE_TO_WORKER,))
        catalog.append(CatalogTool(schema=schema, available_to=available))
    # manual_wire conversation log tools: catalog-advertised; runtime wires after
    # ``build_*_registry`` (CEO + worker). Product-always-on, opening-table resident.
    for tool_cls in declared_tools(surface=ToolSurface.WORKER_ONLY):
        reg = tool_registration(tool_cls)
        if not reg.manual_wire:
            continue
        catalog.append(
            CatalogTool(
                schema=_static_schema(tool_cls),
                available_to=reg.audience,
            )
        )
    for tool_cls in declared_tools(surface=ToolSurface.CEO_ORCHESTRATION):
        reg = tool_registration(tool_cls)
        catalog.append(
            CatalogTool(
                schema=_static_schema(tool_cls),
                available_to=reg.audience,
            )
        )
    return catalog
