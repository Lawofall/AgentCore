"""replan: the CEO's 波边界续跑 primitive — tell / append and resume the SAME
delegate plan (受监督的波循环).

The companion to ``delegate``. The ``WaveScheduler``
YIELDs control back to the CEO at a *decision boundary* (instead of running a
mis-specified tail) when a finished worker flagged that the rest of the plan
needs a look (``escalate reason=adjust``). The CEO reads the signal + output
and tells a not-yet-run person (``tell``), appends someone (``add``), resumes
as-is, or wraps up (``stop``).

``tell`` also answers a worker parked on ``escalate(reason=wait)`` while the
team is still running, without requiring a yielded plan.

Non-terminal, exactly like ``delegate``: the result returns to the CEO loop (a further
boundary brief, or the terminal team result).

A thin wrapper: it holds the turn's :class:`~agentcore.tools.builtin.delegate.DelegateTool`
and forwards to :meth:`DelegateTool.replan`, which owns the paused state (``_supervised``),
the validation, the in-place steer / append, and the resume drive. Worker usage / ledger /
citations therefore accumulate on the SAME DelegateTool instance the pipeline already
folds into the turn totals — this tool adds no accumulator of its own.

范围：tell（对停着等的人说决定，或给还没开始的人补一句）+ add（追加新节点，
id 生成 / 依赖接线见 ``build_added_nodes``）+ stop（收口）。

→ 见设计: docs/03-AI核心/编排器与CEO主Agent.md §一 replan 原语（续跑入口=专用 replan 工具）
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agentcore.core.logging import get_logger
from agentcore.core.types import ToolApproval, ToolFace
from agentcore.runtime.delegate.task_models import TASK_MODEL_SCHEMA_PROPS
from agentcore.tools.builtin.delegate.schema import TASK_ARTIFACTS_SCHEMA
from agentcore.tools.protocol import ToolContext, ToolResult, ToolSchema
from agentcore.tools.registration import (
    AUDIENCE_CEO_ONLY,
    CeoWire,
    ToolRegistration,
    ToolSurface,
)

if TYPE_CHECKING:
    from agentcore.tools.builtin.delegate import DelegateTool

logger = get_logger(__name__)

# Schema layer: short trigger. 字段 HOW 在让出简报与参数，不指空 consult。
_REPLAN_DESCRIPTION = (
    "对已派出的人说一句话、加人，或让已暂停的计划继续、收口。"
)

_REPLAN_PARAMETERS = {
    "type": "object",
    "properties": {
        "tell": {
            "type": "array",
            "description": (
                "对这个人说的话。他正停着等拍板：这句就是决定，他接着干。"
                "他还没开始：开工前交给他。"
                "偏好、授权或花钱先 ask_user，再把用户的话写在这里。"
            ),
            "items": {
                "type": "object",
                "properties": {
                    "run_id": {
                        "type": "string",
                        "description": "队员的 run_id，或能唯一对应的角色名。",
                    },
                    "note": {
                        "type": "string",
                        "description": "要说的话。",
                    },
                },
                "required": ["run_id", "note"],
            },
        },
        "add": {
            "type": "array",
            "description": (
                "追加全新步骤（role+task；可 depends_on 现有 run_id 或本批 id）。"
            ),
            "items": {
                "type": "object",
                "properties": {
                    "id": {
                        "type": "string",
                        "description": "本批临时 id，供其它新步 depends_on。",
                    },
                    "role": {
                        "type": "string",
                        "description": "角色名。",
                    },
                    "task": {
                        "type": "string",
                        "description": "子任务。",
                    },
                    "depends_on": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "上游 run_id 或本批 id。",
                    },
                    "artifacts": TASK_ARTIFACTS_SCHEMA,
                    **TASK_MODEL_SCHEMA_PROPS,
                },
                "required": ["role", "task"],
            },
        },
        "stop": {
            "type": "boolean",
            "description": "true=跳过未跑步并收口。",
        },
    },
    "required": [],
}


class ReplanTool:
    """CEO-agent tool that re-steers / appends and resumes a paused delegate
    plan (non-terminal, like ``delegate``). Thin wrapper over
    :meth:`DelegateTool.replan` — it shares the DelegateTool's paused state and
    accumulator, so it carries no usage surface of its own.
    """

    registration = ToolRegistration(
        surface=ToolSurface.CEO_ORCHESTRATION,
        audience=AUDIENCE_CEO_ONLY,
        ceo_wire=CeoWire.COORDINATION,
        catalog_summary="调整已派出的计划",
        blurb="改正在跑的分工，而不是从头再派",
    )

    def __init__(self, *, delegate: DelegateTool) -> None:
        # The turn's DelegateTool: owns ``_supervised`` (the paused plan), the
        # validation + in-place steer / append, the resume drive, and the shared
        # accumulator the pipeline folds. This tool just forwards the call.
        self._delegate = delegate

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="replan",
            description=_REPLAN_DESCRIPTION,
            parameters=_REPLAN_PARAMETERS,
            face=ToolFace.ORCHESTRATION,
            approval=ToolApproval.NEVER,
        )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        return await self._delegate.replan(arguments)
