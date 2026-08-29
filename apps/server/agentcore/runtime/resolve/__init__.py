"""Turn resolve: prompt assembly, profile variants, CEO toolset wiring."""

from __future__ import annotations

from typing import Any

__all__ = [
    "FRAGMENT_BASE",
    "FRAGMENT_CEO_CORE",
    "OVERRIDABLE_KEYS",
    "PromptProfile",
    "_assemble_ceo_toolset",
    "_build_attachment_context",
    "active_profile",
    "assemble_system_prompt",
    "compose_ceo_chat_prompt",
    "resolve",
    "use_profile",
]


def __getattr__(name: str) -> Any:
    # Lazy: importing ``ceo_surface`` / ``prompt`` must not pull the heavy
    # prepare → sessions → runs → debate chain (parallel modules may be mid-edit).
    if name == "_assemble_ceo_toolset":
        from agentcore.tools.ceo_toolset import _assemble_ceo_toolset as _fn

        return _fn
    if name == "_build_attachment_context":
        from agentcore.runtime.resolve import prepare as _prepare

        return getattr(_prepare, name)
    if name in (
        "FRAGMENT_BASE",
        "FRAGMENT_CEO_CORE",
        "OVERRIDABLE_KEYS",
        "PromptProfile",
        "active_profile",
        "resolve",
        "use_profile",
    ):
        from agentcore.runtime.resolve import profile as _profile

        return getattr(_profile, name)
    if name in (
        "assemble_system_prompt",
        "compose_ceo_chat_prompt",
    ):
        from agentcore.runtime.resolve import prompt as _prompt

        return getattr(_prompt, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
