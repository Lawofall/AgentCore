"""Worker 内部路由 — Escalation Gate 的结构化数据。

Escalation Gate 区分执行层自愈 vs 方案层上报。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class ProblemLayer(StrEnum):
    """Escalation Gate 对障碍的分层。"""

    EXECUTION = "execution"  # 工具报错 / 重试 / 路径 — Worker 自愈
    SCHEME = "scheme"  # 需求矛盾 / 契约 / 权限 — 停下上报


class EscalationKind(StrEnum):
    """方案层升级的细分类（对齐现有 escalate kind 语义，便于调度层消费）。"""

    NORMAL = "normal"
    SCOPE = "scope"
    DEP = "dep"
    # gate_kind 保留；wire 不得默认占 scope（见 to_run_escalation_payload）
    CONTRACT = "contract"  # 需改接口契约 / 权限越界
    CONTRADICTION = "contradiction"  # 需求矛盾


class EscalationSignal(BaseModel):
    """方案层问题的结构化升级信号（Gate 产出 → 调度层消费）。"""

    layer: ProblemLayer = ProblemLayer.SCHEME
    kind: EscalationKind = EscalationKind.NORMAL
    question: str = Field(min_length=1, description="需上级拍板 / 知晓的问题（自包含）")
    assumption: str = Field(
        default="",
        description="Worker 在等待答复前采用的假设（可空；阻塞路径建议填写）",
    )
    evidence: str = Field(
        default="",
        description="触发判定的证据摘要（工具名 / 错误片段，preview 级）",
    )
    tool_name: str = Field(default="", description="触发检查的工具名（可空）")
    source: str = Field(
        default="escalation_gate",
        description="信号来源：escalation_gate（确定性门）vs escalate_tool（模型主动）",
    )

    @field_validator("question")
    @classmethod
    def _question_stripped(cls, value: str) -> str:
        text = (value or "").strip()
        if not text:
            raise ValueError("question must be non-empty")
        return text

    def to_run_escalation_payload(self) -> dict[str, Any]:
        """Shape compatible with ``RunState.escalations`` / CEO aggregate harvest."""
        kind = self.kind.value
        # 后面要看只来自结构化 escalate(reason=adjust)；gate_kind 不得占 wire reason。
        reason = "adjust" if kind in ("scope", "dep") else ""
        payload: dict[str, Any] = {
            "question": self.question,
            "assumption": self.assumption,
            "source": self.source,
            "gate_kind": kind,
            "evidence": self.evidence,
            "tool_name": self.tool_name,
            "layer": self.layer.value,
        }
        if reason:
            payload["reason"] = reason
        return payload


class GateVerdict(BaseModel):
    """一次 tool_exec 轮次后的 Escalation Gate 判定。"""

    layer: ProblemLayer = ProblemLayer.EXECUTION
    action: str = Field(
        default="continue",
        description="continue = 自愈继续；escalate = 方案层停下并上报",
    )
    signals: list[EscalationSignal] = Field(default_factory=list)

    @property
    def should_escalate(self) -> bool:
        return self.action == "escalate" and self.layer is ProblemLayer.SCHEME
