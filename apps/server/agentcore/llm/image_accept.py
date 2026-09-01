"""Vendor contract: whether a chat model accepts image parts on the wire.

Catalog ``capabilities.vision`` and native-multimodal routing share this bit.
The table is vendor SKU truth — not display-family inherit, and not id-keyword
guessing (``vl`` / ``vision`` / ``4o``). A process-local negative example
(:func:`note_images_rejected`) wins over the table for that id.
"""

from __future__ import annotations

import threading

# Same separator set as display family matching, so ``gpt-4o`` cannot claim
# ``gpt-4omni`` / ``mystery-4o-clone``.
_FAMILY_BOUNDARY = frozenset({"-", "_", "."})

_EXACT_ACCEPT = frozenset(
    {
        "deepseek-v4-flash-vision-exp",
        "kimi-k2.5",
        "kimi-k2.6",
        "kimi-k3",
    }
)

# OpenAI gpt-4o / gpt-4.1, 智谱 glm-4v, 通义 qwen-vl — exact or SKU-boundary prefix.
_PREFIX_ACCEPT = ("gpt-4o", "gpt-4.1", "glm-4v", "qwen-vl")

_rejected: set[str] = set()
_lock = threading.Lock()


def _normalize(model_id: str) -> str:
    key = (model_id or "").strip().lower()
    if "/" in key:
        _prefix, _, rest = key.partition("/")
        if rest:
            key = rest
    return key


def _prefix_hit(key: str, prefix: str) -> bool:
    if key == prefix:
        return True
    if len(key) <= len(prefix) or not key.startswith(prefix):
        return False
    return key[len(prefix)] in _FAMILY_BOUNDARY


def _table_accepts(key: str) -> bool:
    if key in _EXACT_ACCEPT:
        return True
    return any(_prefix_hit(key, prefix) for prefix in _PREFIX_ACCEPT)


def model_accepts_images(model_id: str) -> bool:
    """True iff this id may carry native image parts (catalog + routing)."""
    key = _normalize(model_id)
    if not key:
        return False
    with _lock:
        if key in _rejected:
            return False
    return _table_accepts(key)


def note_images_rejected(model_id: str) -> None:
    """Record a process-local negative example; takes priority over the table."""
    key = _normalize(model_id)
    if not key:
        return
    with _lock:
        _rejected.add(key)


def clear_images_rejected() -> None:
    """Drop process-local negative examples (tests)."""
    with _lock:
        _rejected.clear()
