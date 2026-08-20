"""账号级「AI 停住在等你」信号 (云对话多端同权 B2 · L1).

Public face: the two transitions (:func:`signal_attention_required` /
:func:`signal_attention_resolved`), the blocking-card taxonomy
(:class:`AttentionKind`), and the per-turn addressee
(:func:`bind_attention_scope`). See :mod:`agentcore.attention.signal` for the
channel split (firehose = signal, push = last resort) and why the push dedupe is
per-surface rather than per-account.
"""

from agentcore.attention.scope import (
    AttentionScope,
    bind_attention_scope,
    current_attention_scope,
    reset_attention_scope,
)
from agentcore.attention.signal import (
    ATTENTION_EVENT_TYPE,
    TITLE_MAX_CHARS,
    AttentionKind,
    attention_kind_of,
    attention_title,
    schedule_attention,
    signal_attention_required,
    signal_attention_resolved,
    signal_hot_card_required,
    signal_hot_card_resolved,
)

__all__ = [
    "ATTENTION_EVENT_TYPE",
    "TITLE_MAX_CHARS",
    "AttentionKind",
    "AttentionScope",
    "attention_kind_of",
    "attention_title",
    "bind_attention_scope",
    "current_attention_scope",
    "reset_attention_scope",
    "schedule_attention",
    "signal_attention_required",
    "signal_attention_resolved",
    "signal_hot_card_required",
    "signal_hot_card_resolved",
]
