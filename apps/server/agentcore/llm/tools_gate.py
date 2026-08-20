"""Tool-calling soft gate copy (Phase 1d, 开放主流AI模型接入 §4.5).

``supports_tools`` from the BYOK probe is a hint, not a hard block (``True`` /
``False`` / ``None``). Preflight surfaces the warning only when the probe recorded
``False``; ``None`` (unknown) does not. The runtime surfaces a graceful message when
tools were offered, the probe said ``False``, and the model never returned tool_calls.
"""

from __future__ import annotations

# Copy does not name a client page: the same warning is sent to every client;
# each routes via its own CTA.
TOOLS_SOFT_GATE_WARNING = (
    "该模型可能不支持工具调用，委派/辩论可能失败。"
    "可更换支持工具调用的模型，或继续尝试。"
)

TOOLS_UNAVAILABLE_RUNTIME_MESSAGE = (
    "当前模型不支持工具调用，无法完成委派或辩论。"
    "请更换支持工具调用的模型后重试。"
)
