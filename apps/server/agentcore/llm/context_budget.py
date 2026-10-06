"""Assembly context budget: a shorter ceiling than the model's own window.

Empty means use the catalog window. A stored value is one of the fixed steps
below that window. The effective window for a role is the smaller of the budget
and that role's catalog window.
"""

from __future__ import annotations

from contextvars import ContextVar, Token

from agentcore.core.errors import ValidationError

# Steps offered under the model window. The window itself is always the last
# option and is stored as NULL (follow the model).
CONTEXT_BUDGET_LADDER: tuple[int, ...] = (128_000, 256_000, 512_000)
# RPC / client values above this are ignored. Saved rows only store ladder steps.
_MAX_CLIENT_BUDGET = 2_000_000

_BUDGET: ContextVar[int | None] = ContextVar("assembly_context_budget", default=None)


def context_budget_label(tokens: int) -> str:
    """``128000`` → ``128K``, ``1000000`` → ``1M``."""
    if tokens % 1_000_000 == 0:
        return f"{tokens // 1_000_000}M"
    if tokens % 1_000 == 0:
        return f"{tokens // 1_000}K"
    return str(tokens)


def context_budget_steps(context_length: int | None) -> tuple[int, ...] | None:
    """Fixed steps the UI can offer, or None when the control stays hidden.

    Hidden when the catalog window is unknown or no larger than 128K (only one
    choice, which is the window itself).
    """
    if context_length is None or context_length <= 128_000:
        return None
    steps = [n for n in CONTEXT_BUDGET_LADDER if n < context_length]
    steps.append(context_length)
    unique = tuple(dict.fromkeys(steps))
    if len(unique) < 2:
        return None
    return unique


def normalize_context_budget(model_id: str, value: int | None) -> int | None:
    """Persist a shorter step, or None for the model window.

    The window number itself is stored as None so a later catalog change still
    follows the model.
    """
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValidationError("上下文长度无效")
    from agentcore.llm.model_metadata import model_metadata_for

    steps = context_budget_steps(model_metadata_for(model_id).context_length)
    if steps is None:
        raise ValidationError("当前主模型不能调上下文长度")
    if value == steps[-1]:
        return None
    if value not in steps:
        labels = "、".join(context_budget_label(n) for n in steps)
        raise ValidationError(f"上下文长度须为其中一档：{labels}")
    return value


def snap_context_budget(model_id: str, stored: int | None) -> int | None:
    """Drop a stored step the (new) main model no longer offers."""
    if stored is None:
        return None
    if isinstance(stored, bool) or not isinstance(stored, int) or stored <= 0:
        return None
    from agentcore.llm.model_metadata import model_metadata_for

    steps = context_budget_steps(model_metadata_for(model_id).context_length)
    if steps is None or stored not in steps or stored == steps[-1]:
        return None
    return stored


def effective_context_length(model_id: str | None, budget: int | None) -> int | None:
    """Window used for the ring and the near-full fold.

    No budget → the catalog window (None when unknown, so the absolute fallback
    stays). A budget never exceeds that role's catalog window.
    """
    catalog: int | None = None
    if model_id:
        from agentcore.llm.model_metadata import model_metadata_for

        length = model_metadata_for(model_id).context_length
        if isinstance(length, int) and not isinstance(length, bool) and length > 0:
            catalog = length
    if budget is None or isinstance(budget, bool) or not isinstance(budget, int) or budget <= 0:
        return catalog
    if catalog is None:
        return budget
    return min(budget, catalog)


def coerce_context_budget(raw: object) -> int | None:
    """A positive client token count, or None when absent / unusable."""
    if isinstance(raw, bool) or not isinstance(raw, int):
        return None
    if raw <= 0 or raw > _MAX_CLIENT_BUDGET:
        return None
    return raw


def current_context_budget() -> int | None:
    """Budget bound for this turn, if the caller set one."""
    value = _BUDGET.get()
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return None


def bind_context_budget(budget: int | None) -> Token[int | None]:
    if budget is not None and (
        isinstance(budget, bool) or not isinstance(budget, int) or budget <= 0
    ):
        budget = None
    return _BUDGET.set(budget)


def reset_context_budget(token: Token[int | None]) -> None:
    _BUDGET.reset(token)


def bind_profile_context_budget(profile_budget: int | None) -> Token[int | None]:
    """Prefer the assembly row. When it is empty, keep a budget already bound
    (desktop turns pass one in before the pipeline starts).
    """
    if (
        isinstance(profile_budget, int)
        and not isinstance(profile_budget, bool)
        and profile_budget > 0
    ):
        return bind_context_budget(profile_budget)
    return bind_context_budget(current_context_budget())
