"""OpenAI-compatible wire dialects — declarative flags looked up by model id.

Provider payload / probe paths consult :func:`resolve_wire_dialect` instead of
scattered ``model.startswith`` / ``_is_deepseek_*`` branches. Adding a new
dialect is a table row (or flag overlay), not a new ``if`` in the provider.

Match kinds (against the wire leaf after stripping optional ``provider/``):

- ``exact`` — leaf equals pattern
- ``prefix`` — leaf startswith pattern
- ``contains`` — pattern appears anywhere in the leaf

Overlays OR-merge True flags (e.g. ``deepseek-v4-*`` hits both the DeepSeek
echo prefix and the V4 thinking-type prefix). Overlay fields that are ``None``
mean "no opinion" and leave the running default alone.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from agentcore.llm.byok_provider_presets import match_byok_provider_preset

MatchKind = Literal["exact", "prefix", "contains"]


# Official Chat Completions control tokens for DeepSeek V4 / Flash (aliases
# like medium/xhigh are accepted upstream but map onto this set — product UI
# only lists these).
DEEPSEEK_REASONING_EFFORT_OPTIONS: tuple[str, ...] = ("low", "high", "max")
DEEPSEEK_REASONING_EFFORT_DEFAULT = "high"


@dataclass(frozen=True)
class WireDialect:
    """Wire-shape behaviour for one resolved model id (defaults = clean OpenAI)."""

    # Assistant+tool_calls turns must echo ``reasoning_content`` (incl. "").
    echo_reasoning_content: bool = False
    # Honors ``thinking: {type: enabled|disabled}`` on the request body.
    thinking_type_switch: bool = False
    # Official vendor ``reasoning_effort`` control tokens. Empty = do not send.
    reasoning_effort_options: tuple[str, ...] = ()
    reasoning_effort_default: str | None = None
    # Upstream rejects wire ``temperature`` — omit rather than send a default.
    omit_temperature: bool = False
    # ``probe_tools``: HTTP 400 under ``tool_choice=required`` → retry without it.
    # Default True preserves the historical universal fallback (DeepSeek V4 etc.).
    retry_forced_tool_choice_on_400: bool = True
    # Official o-series Chat Completions wants ``max_completion_tokens``, not
    # ``max_tokens``. Probe and the hot path share :meth:`apply_token_limit`.
    use_max_completion_tokens: bool = False
    # Model family speaks vendor tool-search (Claude 4.5+ / GPT-5.4+).
    # Stamping still requires an official vendor + native protocol — see
    # ``tools.defer_loading.should_stamp_vendor_defer_loading``. Completions
    # and OpenCode never stamp even when this is True.
    defer_loading: bool = False

    @property
    def token_limit_field(self) -> Literal["max_tokens", "max_completion_tokens"]:
        return "max_completion_tokens" if self.use_max_completion_tokens else "max_tokens"

    def apply_token_limit(self, payload: dict, max_tokens: int | None) -> None:
        """Write the dialect's token-cap field when a limit is set."""
        if max_tokens:
            payload[self.token_limit_field] = max_tokens


@dataclass(frozen=True)
class _DialectOverlay:
    """One match rule → optional flag overlays (``None`` = leave running value)."""

    kind: MatchKind
    pattern: str
    echo_reasoning_content: bool | None = None
    thinking_type_switch: bool | None = None
    reasoning_effort_options: tuple[str, ...] | None = None
    reasoning_effort_default: str | None = None
    omit_temperature: bool | None = None
    retry_forced_tool_choice_on_400: bool | None = None
    use_max_completion_tokens: bool | None = None
    defer_loading: bool | None = None

    def matches(self, leaf: str) -> bool:
        if self.kind == "exact":
            return leaf == self.pattern
        if self.kind == "prefix":
            return leaf.startswith(self.pattern)
        return self.pattern in leaf


def wire_model_leaf(model: str) -> str:
    """Strip optional ``provider/`` prefix; compare on the leaf id only."""
    return model.rsplit("/", 1)[-1].lower()


# Declarative overlays. Order does not matter (OR-merge of True / explicit sets).
# Prefer adding a row here over a new model-name branch in ``openai_compatible``.
_DIALECT_OVERLAYS: tuple[_DialectOverlay, ...] = (
    # DeepSeek family: tool-loop must echo reasoning_content.
    _DialectOverlay("prefix", "deepseek", echo_reasoning_content=True),
    # DeepSeek V4 / V4.1 Flash: thinking.type + official effort tokens.
    # Hy3 below stays switch-only (do not inherit this effort table).
    _DialectOverlay(
        "prefix",
        "deepseek-v4",
        thinking_type_switch=True,
        reasoning_effort_options=DEEPSEEK_REASONING_EFFORT_OPTIONS,
        reasoning_effort_default=DEEPSEEK_REASONING_EFFORT_DEFAULT,
    ),
    _DialectOverlay(
        "prefix",
        "deepseek-flash",
        thinking_type_switch=True,
        reasoning_effort_options=DEEPSEEK_REASONING_EFFORT_OPTIONS,
        reasoning_effort_default=DEEPSEEK_REASONING_EFFORT_DEFAULT,
    ),
    # Hy3 / Hy3 Preview only — other TokenHub ``hy-*`` stay clean OpenAI.
    _DialectOverlay(
        "exact",
        "hy3",
        echo_reasoning_content=True,
        thinking_type_switch=True,
    ),
    _DialectOverlay(
        "exact",
        "hy3-preview",
        echo_reasoning_content=True,
        thinking_type_switch=True,
    ),
    # Anthropic effort / sampling-restricted leaves reject ``temperature``.
    # Keep markers narrow; do not blanket all ``claude-*``.
    *tuple(
        _DialectOverlay("contains", marker, omit_temperature=True)
        for marker in (
            "opus-4-7",
            "opus-4.7",
            "opus-4-8",
            "opus-4.8",
            "opus-5",
            "fable-5",
            "mythos-5",
        )
    ),
    # Kimi leaf ids (incl. OpenCode Zen / Go / other relays hosting ``kimi-*``).
    # Do not blanket whole endpoints that happen to list Kimi models.
    _DialectOverlay("prefix", "kimi-", omit_temperature=True),
    # OpenAI o-series: official Chat Completions rejects ``temperature`` and
    # requires ``max_completion_tokens``. Keyed on the leaf (not base_url) so
    # third-party relays hosting the same ids are covered. Bare names stay
    # ``exact`` and families carry the hyphen: a two-char ``o1`` prefix would
    # swallow any unrelated leaf starting with those characters.
    *tuple(
        _DialectOverlay(
            "exact",
            name,
            omit_temperature=True,
            use_max_completion_tokens=True,
            reasoning_effort_options=("low", "medium", "high"),
            reasoning_effort_default="medium",
        )
        for name in ("o1", "o3", "o4")
    ),
    *tuple(
        _DialectOverlay(
            "prefix",
            prefix,
            omit_temperature=True,
            use_max_completion_tokens=True,
            reasoning_effort_options=("low", "medium", "high"),
            reasoning_effort_default="medium",
        )
        for prefix in ("o1-", "o3-", "o4-")
    ),
    # GPT-5/6 Responses: effort tokens + no custom temperature (Astra rejects it).
    # Completions still skips these — sending is gated on thinking_type_switch.
    _DialectOverlay(
        "prefix",
        "gpt-5",
        omit_temperature=True,
        reasoning_effort_options=("minimal", "low", "medium", "high"),
        reasoning_effort_default="medium",
    ),
    _DialectOverlay(
        "prefix",
        "gpt-6",
        omit_temperature=True,
        reasoning_effort_options=("low", "medium", "high"),
        reasoning_effort_default="medium",
    ),
    # Vendor tool-search. Anthropic: Opus 4.1 and earlier are out.
    # OpenAI: gpt-5.4 and later on /responses (not bare gpt-5).
    *tuple(
        _DialectOverlay("contains", marker, defer_loading=True)
        for marker in (
            "haiku-4-5",
            "haiku-4.5",
            "haiku-5",
            "haiku-9",
            "sonnet-4-5",
            "sonnet-4.5",
            "sonnet-4-6",
            "sonnet-4.6",
            "sonnet-5",
            "opus-4-5",
            "opus-4.5",
            "opus-4-6",
            "opus-4.6",
            "opus-4-7",
            "opus-4.7",
            "opus-4-8",
            "opus-4.8",
            "opus-5",
            "fable-5",
            "mythos-5",
        )
    ),
    *tuple(
        _DialectOverlay("prefix", prefix, defer_loading=True)
        for prefix in (
            "gpt-5.4",
            "gpt-5-4",
            "gpt-5.5",
            "gpt-5-5",
            "gpt-5.6",
            "gpt-5-6",
            "gpt-6",
        )
    ),
)


def resolve_wire_dialect(model: str, *, base_url: str | None = None) -> WireDialect:
    """Look up wire dialect flags for ``model`` (supports ``provider/leaf`` ids).

    Optional ``base_url`` enables vendor-endpoint overlays (e.g. Moonshot BYOK
    short ids like ``k3``). Multi-model hubs (OpenCode Zen / Go) must not omit
    for every model just because the catalog includes ``kimi-*`` — both rely on
    the ``kimi-`` leaf rule above.
    """
    leaf = wire_model_leaf(model)
    echo = False
    thinking = False
    effort_options: tuple[str, ...] = ()
    effort_default: str | None = None
    omit_temp = False
    # Universal default: keep forced-tool_choice 400 retry (historical behaviour).
    retry_required = True
    use_max_completion = False
    defer_loading = False
    for rule in _DIALECT_OVERLAYS:
        if not rule.matches(leaf):
            continue
        if rule.echo_reasoning_content is not None:
            echo = echo or rule.echo_reasoning_content
        if rule.thinking_type_switch is not None:
            thinking = thinking or rule.thinking_type_switch
        if rule.reasoning_effort_options is not None:
            effort_options = rule.reasoning_effort_options
        if rule.reasoning_effort_default is not None:
            effort_default = rule.reasoning_effort_default
        if rule.omit_temperature is not None:
            omit_temp = omit_temp or rule.omit_temperature
        if rule.retry_forced_tool_choice_on_400 is not None:
            retry_required = rule.retry_forced_tool_choice_on_400
        if rule.use_max_completion_tokens is not None:
            use_max_completion = use_max_completion or rule.use_max_completion_tokens
        if rule.defer_loading is not None:
            defer_loading = defer_loading or rule.defer_loading
    # Moonshot BYOK: sampling is fixed for current Kimi leaves; legacy
    # ``moonshot-v1*`` still accepts temperature → keep sending it.
    if base_url:
        preset = match_byok_provider_preset(base_url)
        if preset is not None and preset.id == "moonshot" and not leaf.startswith("moonshot-v1"):
            omit_temp = True
    return WireDialect(
        echo_reasoning_content=echo,
        thinking_type_switch=thinking,
        reasoning_effort_options=effort_options,
        reasoning_effort_default=effort_default,
        omit_temperature=omit_temp,
        retry_forced_tool_choice_on_400=retry_required,
        use_max_completion_tokens=use_max_completion,
        defer_loading=defer_loading,
    )


def reasoning_effort_spec(model: str) -> tuple[tuple[str, ...], str] | None:
    """Official vendor effort tokens + default, or None when the leaf does not send it."""
    dialect = resolve_wire_dialect(model)
    if not dialect.reasoning_effort_options:
        return None
    default = dialect.reasoning_effort_default or dialect.reasoning_effort_options[0]
    return dialect.reasoning_effort_options, default


def effective_reasoning_effort(
    model: str,
    *,
    thinking: bool | None,
    stored: str | None,
    base_url: str | None = None,
) -> str | None:
    """Token that Chat Completions would send, or None when the leaf omits the field.

    Matches ``OpenAICompatibleProvider._build_payload``: thinking-off and
    non-switch leaves skip it; invalid / empty stored values snap to the
    vendor default.
    """
    dialect = resolve_wire_dialect(model, base_url=base_url)
    if not dialect.thinking_type_switch or thinking is False:
        return None
    options = dialect.reasoning_effort_options
    if not options:
        return None
    if stored in options:
        return stored
    return dialect.reasoning_effort_default or options[0]
