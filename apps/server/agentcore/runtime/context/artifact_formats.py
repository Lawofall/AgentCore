"""产物格式能力行 — derived from tool registration + this-turn assembly gates.

``<工作区>`` 只陈述事实：目标后缀能不能产、由谁产、经哪把工具、什么前置。
格式清单来自各工具 ``ToolRegistration.produces_formats``，装没装配走与
``build_worker_registry`` 同一套闸（execution / browser / host / git / desktop /
local_only / manual_wire）。禁止在本模块再写一份格式白名单。
"""

from __future__ import annotations

from collections.abc import Collection
from typing import Literal

from agentcore.tools.registration import (
    AUDIENCE_CEO,
    AUDIENCE_WORKER,
    ToolRegistration,
    declared_tool_name,
    declared_tools,
    tool_registration,
)

_Location = Literal["server", "local"]


def _normalize_suffix(raw: str) -> str:
    suffix = (raw or "").strip().lower()
    if not suffix:
        return ""
    return suffix if suffix.startswith(".") else f".{suffix}"


def _holder(reg: ToolRegistration) -> str:
    has_ceo = AUDIENCE_CEO in reg.audience
    has_worker = AUDIENCE_WORKER in reg.audience
    if has_worker and not has_ceo:
        return "worker"
    if has_ceo and not has_worker:
        return "CEO"
    return "CEO/worker"


def _precond(reg: ToolRegistration) -> str:
    bits: list[str] = []
    if reg.execution_class:
        bits.append("沙箱")
    if reg.host_class or reg.desktop_online_class:
        bits.append("本机通道")
    if not bits:
        return "不吃沙箱"
    return "+".join(bits)


def _rank(reg: ToolRegistration) -> int:
    """Prefer a dedicated exporter over an execution-class script path."""
    return 1 if reg.execution_class else 0


def tool_would_assemble(
    reg: ToolRegistration,
    *,
    include_execution: bool,
    include_browser: bool,
    include_host: bool,
    include_git: bool,
    desktop_online: bool,
    location: _Location | None,
) -> bool:
    """Same include predicates as ``build_worker_registry`` / ``build_builtin_registry``."""
    if reg.manual_wire:
        return False
    if reg.execution_class and not include_execution:
        return False
    if reg.browser_class and not include_browser:
        return False
    if reg.host_class and not include_host:
        return False
    if reg.desktop_online_class and not desktop_online:
        return False
    if reg.git_class and not include_git:
        return False
    return not (reg.local_only and location != "local")


def iter_format_producers() -> list[tuple[str, str, ToolRegistration]]:
    """``(suffix, tool_name, registration)`` for every declared format producer."""
    rows: list[tuple[str, str, ToolRegistration]] = []
    for cls in declared_tools():
        reg = tool_registration(cls)
        if not reg.produces_formats:
            continue
        name = declared_tool_name(cls)
        for raw in reg.produces_formats:
            suffix = _normalize_suffix(raw)
            if suffix:
                rows.append((suffix, name, reg))
    return rows


def assembled_format_producer_names(
    *,
    include_execution: bool,
    include_browser: bool,
    include_host: bool,
    include_git: bool,
    desktop_online: bool,
    location: _Location | None,
) -> frozenset[str]:
    """Tool names that declare a format and would be on the worker roster this turn."""
    names: set[str] = set()
    for _suffix, name, reg in iter_format_producers():
        if tool_would_assemble(
            reg,
            include_execution=include_execution,
            include_browser=include_browser,
            include_host=include_host,
            include_git=include_git,
            desktop_online=desktop_online,
            location=location,
        ):
            names.add(name)
    return frozenset(names)


def build_artifact_format_line(assembled_names: Collection[str]) -> str:
    """One fact line: ``产物格式：.docx=可产（…）；.xlsx=不可产（…）。``

    ``assembled_names`` is this-turn worker assembly (or a test override). Formats
    come only from ``produces_formats``; a name missing from the set flips that
    tool's formats to 不可产.
    """
    assembled = set(assembled_names)
    by_suffix: dict[str, list[tuple[str, ToolRegistration]]] = {}
    for suffix, name, reg in iter_format_producers():
        by_suffix.setdefault(suffix, []).append((name, reg))
    if not by_suffix:
        return ""

    parts: list[str] = []
    for suffix in sorted(by_suffix):
        producers = by_suffix[suffix]
        assembled_here = [(n, r) for n, r in producers if n in assembled]
        pool = assembled_here or producers
        name, reg = min(pool, key=lambda item: (_rank(item[1]), item[0]))
        holder = _holder(reg)
        precond = _precond(reg)
        if assembled_here:
            if precond == "不吃沙箱":
                parts.append(f"{suffix}=可产（{holder}/{name}，不吃沙箱）")
            else:
                parts.append(f"{suffix}=可产（{holder}/{name}，需{precond}）")
        else:
            parts.append(f"{suffix}=不可产（需{holder}/{name}+{precond}）")
    return "产物格式：" + "；".join(parts) + "。"


def format_artifact_capability_line(
    *,
    include_execution: bool,
    include_browser: bool,
    include_host: bool,
    include_git: bool,
    desktop_online: bool,
    location: _Location | None,
    assembled_names: Collection[str] | None = None,
) -> str:
    """Render the 产物格式 line from this-turn gates (or an explicit assembled set)."""
    names = (
        frozenset(assembled_names)
        if assembled_names is not None
        else assembled_format_producer_names(
            include_execution=include_execution,
            include_browser=include_browser,
            include_host=include_host,
            include_git=include_git,
            desktop_online=desktop_online,
            location=location,
        )
    )
    return build_artifact_format_line(names)
