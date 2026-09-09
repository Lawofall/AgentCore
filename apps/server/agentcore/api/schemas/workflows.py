"""User workflow API schemas."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agentcore.workflows.definition import (
    client_owned_definition,
    validate_workflow_definition,
)
from agentcore.workflows.paths import webhook_path
from agentcore.workflows.schedule import infer_schedule_preset
from agentcore.workflows.slots import (
    MAX_SLOT_DEFAULT_CHARS,
    MAX_SLOT_LABEL_CHARS,
    MAX_SLOT_VALUE_CHARS,
    MAX_SLOTS,
)
from agentcore.workflows.source import normalize_source


class WorkflowSlotModel(BaseModel):
    """One canvas slot: ``{{key}}`` in task text, ``default`` = 原轮的原值。

    Optional — a definition without slots behaves exactly as before.
    """

    key: str = Field(..., min_length=1, max_length=24)
    label: str = Field(..., min_length=1, max_length=MAX_SLOT_LABEL_CHARS)
    default: str = Field("", max_length=MAX_SLOT_DEFAULT_CHARS)

    # 声明的三个字段是给 OpenAPI / 客户端看的，不是「允许的全集」——多出来的原样带走。
    model_config = ConfigDict(extra="allow")


class WorkflowDefinitionModel(BaseModel):
    """Canvas JSON: nodes + edges (agent_step | human_gate) + optional slots.

    **校验用它，落库用** :meth:`payload` —— 不要用 ``model_dump()``。这几个字段声明的是
    「服务端认识什么」，不是「客户端能存什么」：definition 归用户所有，未知字段必须原样
    透传（``extra="allow"`` 在这里和 :class:`WorkflowSlotModel` 上都是为了这件事）。按已知
    字段重建这份 JSON，是 ``deliverable`` / ``slots`` / ``source`` 先后被抹掉的同一个根因
    —— 见 :mod:`agentcore.workflows.definition` 的所有权约定。
    """

    nodes: list[dict[str, Any]] = Field(default_factory=list)
    edges: list[dict[str, Any]] = Field(default_factory=list)
    slots: list[WorkflowSlotModel] = Field(default_factory=list, max_length=MAX_SLOTS)

    model_config = ConfigDict(extra="allow")

    @field_validator("nodes", "edges", "slots", mode="before")
    @classmethod
    def _coerce_list(cls, v: object) -> object:
        if v is None:
            return []
        return v

    @model_validator(mode="after")
    def _validate_structure(self) -> "WorkflowDefinitionModel":
        errors = validate_workflow_definition(self.payload())
        if errors:
            raise ValueError("；".join(errors))
        return self

    def payload(self) -> dict[str, Any]:
        """落库用的画布内容：客户端送来的原样内容，去掉服务端拥有的键（``source``）。"""
        return client_owned_definition(self.model_dump())


class WorkflowSourceModel(BaseModel):
    """这条工作流是从哪儿来的——服务端权威，客户端只读。

    ``kind`` 今天只有 ``"turn"``（历史行：曾经从一轮协作固化，不是现行写入），带原对话 /
    消息定位。手画的、官方模板复制的没有来源（``null``）。为什么它不在 ``definition`` 里 →
    :mod:`agentcore.workflows.source`。
    """

    kind: str
    conversation_id: str | None = None
    message_id: str | None = None

    model_config = ConfigDict(extra="allow")


class CreateWorkflowRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: str | None = Field(None, max_length=4000)
    definition: WorkflowDefinitionModel


class UpdateWorkflowRequest(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = Field(None, max_length=4000)
    definition: WorkflowDefinitionModel | None = None
    # Explicit clear: pass description="" → stored as NULL via route.
    clear_description: bool = False


class WorkflowTriggerModel(BaseModel):
    """One clock on a workflow: schedule XOR webhook."""

    kind: Literal["schedule", "webhook"]
    enabled: bool
    folder_id: str
    cron: str | None = None
    schedule_preset: str | None = None
    webhook_id: str | None = None
    webhook_url: str | None = None
    webhook_secret: str | None = None
    next_run_at: datetime | None = None
    last_run_at: datetime | None = None
    last_error: str | None = None


class PutWorkflowTriggerRequest(BaseModel):
    """Full replace of the workflow clock."""

    kind: Literal["schedule", "webhook"]
    folder_id: str
    enabled: bool = True
    cron: str | None = None
    schedule_preset: str | None = None

    @model_validator(mode="after")
    def _xor(self) -> "PutWorkflowTriggerRequest":
        preset = self.schedule_preset
        if self.kind == "webhook":
            if self.cron or preset:
                raise ValueError("kind=webhook 时不要传 schedule_preset / cron")
            return self
        if not self.cron and not preset:
            raise ValueError("须提供 schedule_preset 或 cron")
        if preset and preset.strip().lower() != "custom" and self.cron:
            raise ValueError("命名 schedule_preset 时不要同时传 cron")
        if preset and preset.strip().lower() == "custom" and not self.cron:
            raise ValueError("schedule_preset=custom 时须提供 cron")
        return self


class RotateWorkflowTriggerResponse(BaseModel):
    webhook_id: str
    webhook_url: str
    webhook_secret: str


class FireWorkflowWebhookResponse(BaseModel):
    conversation_id: str


class WorkflowSummary(BaseModel):
    id: str
    name: str
    description: str | None = None
    definition: dict[str, Any]
    # 与 definition 平级：客户端整份覆盖画布也带不走它。
    source: WorkflowSourceModel | None = None
    trigger: WorkflowTriggerModel | None = None
    version: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

    @classmethod
    def from_row(cls, row, *, webhook_secret: str | None = None) -> "WorkflowSummary":
        source = normalize_source(row.source)
        return cls(
            id=row.id,
            name=row.name,
            description=row.description,
            definition=dict(row.definition or {}),
            source=WorkflowSourceModel.model_validate(source) if source else None,
            trigger=trigger_from_row(row, webhook_secret=webhook_secret),
            version=int(row.version or 1),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


def trigger_from_row(row, *, webhook_secret: str | None = None) -> WorkflowTriggerModel | None:
    kind = getattr(row, "trigger_kind", None)
    if kind not in ("schedule", "webhook"):
        return None
    folder_id = getattr(row, "trigger_folder_id", None)
    if not folder_id:
        return None
    cron = getattr(row, "trigger_cron", None)
    webhook_id = getattr(row, "trigger_webhook_id", None)
    last_error = getattr(row, "last_trigger_error", None)
    return WorkflowTriggerModel(
        kind=kind,
        enabled=bool(getattr(row, "trigger_enabled", False)),
        folder_id=folder_id,
        cron=cron,
        schedule_preset=infer_schedule_preset(cron) if cron else None,
        webhook_id=webhook_id if kind == "webhook" else None,
        webhook_url=webhook_path(webhook_id) if webhook_id and kind == "webhook" else None,
        webhook_secret=webhook_secret,
        next_run_at=getattr(row, "trigger_next_run_at", None),
        last_run_at=getattr(row, "trigger_last_run_at", None),
        last_error=(last_error or None),
    )


class RunWorkflowRequest(BaseModel):
    folder_id: str
    # Optional per-run supplement (does not rewrite the saved definition).
    note: str | None = Field(None, max_length=16_000)
    conversation_id: str | None = None
    # Per-run slot overrides; omitted / blank keys fall back to each slot's default
    # (= the original turn's value), so an untouched form reruns the saved workflow
    # verbatim. Unknown keys are ignored — the definition owns the slot list.
    slots: dict[str, str] = Field(default_factory=dict)

    @field_validator("slots", mode="before")
    @classmethod
    def _check_slots(cls, v: object) -> object:
        if v is None:
            return {}
        if not isinstance(v, dict):
            raise ValueError("slots 必须是对象")
        if len(v) > MAX_SLOTS:
            raise ValueError(f"槽位覆盖值不能超过 {MAX_SLOTS} 个")
        for key, value in v.items():
            if not isinstance(value, str):
                raise ValueError(f"槽位 `{key}` 的值须为字符串")
            if len(value) > MAX_SLOT_VALUE_CHARS:
                raise ValueError(f"槽位 `{key}` 的值不能超过 {MAX_SLOT_VALUE_CHARS} 字")
        return v


class RunWorkflowResponse(BaseModel):
    conversation_id: str
    workflow_id: str
    workflow_version: int


class PlaybookSlotChoice(BaseModel):
    """One allowed value of an enumerated slot (render a picker, not a textbox)."""

    value: str
    label: str

    model_config = {"from_attributes": True}


class PlaybookTemplateSlotModel(BaseModel):
    """Machine-readable primary slot — clients build the copy form from this."""

    key: str
    label: str
    required: bool
    hint: str | None = None
    choices: list[PlaybookSlotChoice] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class PlaybookTemplateSummary(BaseModel):
    """Official playbook listed as a read-only workflow template."""

    id: str
    title: str
    summary: str
    # Prose one-liner for help copy; ``slots`` is the machine-readable source.
    primary_slots: str
    slots: list[PlaybookTemplateSlotModel] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class FromPlaybookRequest(BaseModel):
    """Copy an official playbook into a user workflow (use = 复制为我的)."""

    playbook: str = Field(..., min_length=1, max_length=80)
    slots: dict[str, Any] = Field(default_factory=dict)
    name: str | None = Field(None, min_length=1, max_length=200)
