"""delegate: the CEO main-agent's single orchestration primitive (统一 Run 模型 阶段3).

→ 见设计: docs/03-AI核心/编排器与CEO主Agent.md §一（delegate 原语）
"""

from __future__ import annotations

from agentcore.runtime.runs.constants import DELEGATE_OUTPUT_LIMIT
from agentcore.tools.builtin.graph.tool import GraphTool

__all__ = [
    "DELEGATE_OUTPUT_LIMIT",
    "GraphTool",
]
