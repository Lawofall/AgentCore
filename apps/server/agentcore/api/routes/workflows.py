"""User workflow CRUD + run-once (direct-start bypass) + official playbook copy.

Slot suggestion (``POST /workflows/{id}/suggest-slots``) also lives here: it is a
before-you-run step, not a save-time one — see
:mod:`agentcore.workflows.slot_extract`.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Header, Request

from agentcore.api.dependencies import (
    AuthUser,
    get_folder_repo,
    get_user_workflow_repo,
)
from agentcore.api.schemas import StatusResponse
from agentcore.api.schemas.workflows import (
    CreateWorkflowRequest,
    FireWorkflowWebhookResponse,
    FromPlaybookRequest,
    PlaybookTemplateSummary,
    PutWorkflowTriggerRequest,
    RotateWorkflowTriggerResponse,
    RunWorkflowRequest,
    RunWorkflowResponse,
    UpdateWorkflowRequest,
    WorkflowSummary,
)
from agentcore.core.errors import NotFoundError, ValidationError
from agentcore.core.types import new_id
from agentcore.db.repositories import FolderRepository, UserWorkflowRepository
from agentcore.workflows.paths import webhook_path
from agentcore.workflows.playbook_templates import (
    PlaybookTemplateError,
    instantiate_from_playbook,
    list_playbook_templates,
)
from agentcore.workflows.runner import dispatch_workflow_run
from agentcore.workflows.schedule import CronError, next_run_after, resolve_cron
from agentcore.workflows.slot_extract import (
    append_description_note,
    slots_note,
    suggest_slots_for_definition,
)
from agentcore.workflows.slots import slots_from_definition
from agentcore.workflows.source import is_turn_sourced
from agentcore.workflows.trigger import fire_workflow_trigger
from agentcore.workflows.webhook import (
    enforce_webhook_rate_limit,
    extract_event_text,
    generate_webhook_secret,
    idempotency_lookup,
    idempotency_store,
    require_webhook_secret,
)

router = APIRouter(tags=["workflows"])
hooks_router = APIRouter(prefix="/hooks", tags=["workflow-hooks"])


def _require_folder(folder) -> None:
    if folder is None:
        raise NotFoundError("工作区不存在")


def _require_cloud_folder(folder) -> None:
    if folder is None:
        raise NotFoundError("工作区不存在")
    if folder.local_root_id:
        raise ValidationError("触发仅支持云工作区")


@router.get("/workflows", response_model=list[WorkflowSummary])
async def list_workflows(
    user: AuthUser,
    repo: UserWorkflowRepository = Depends(get_user_workflow_repo),
):
    rows = await repo.list_by_user(user.user_id)
    return [WorkflowSummary.from_row(r) for r in rows]


@router.get(
    "/workflow-playbook-templates",
    response_model=list[PlaybookTemplateSummary],
)
async def list_workflow_playbook_templates(user: AuthUser):
    """Official playbooks as read-only workflow templates (使用 = 复制为我的)."""
    _ = user
    return [
        PlaybookTemplateSummary.model_validate(item)
        for item in list_playbook_templates()
    ]


@router.post("/workflows", response_model=WorkflowSummary, status_code=201)
async def create_workflow(
    body: CreateWorkflowRequest,
    user: AuthUser,
    repo: UserWorkflowRepository = Depends(get_user_workflow_repo),
):
    row = await repo.create(
        user_id=user.user_id,
        name=body.name,
        description=body.description,
        definition=body.definition.payload(),
    )
    return WorkflowSummary.from_row(row)


@router.post(
    "/workflows/from-playbook",
    response_model=WorkflowSummary,
    status_code=201,
)
async def create_workflow_from_playbook(
    body: FromPlaybookRequest,
    user: AuthUser,
    repo: UserWorkflowRepository = Depends(get_user_workflow_repo),
):
    """Expand an official playbook once and save as a user workflow (not into PLAYBOOKS)."""
    try:
        name, description, definition = instantiate_from_playbook(
            body.playbook,
            body.slots,
            name=body.name,
        )
    except PlaybookTemplateError as e:
        raise ValidationError(str(e)) from e
    row = await repo.create(
        user_id=user.user_id,
        name=name,
        description=description,
        definition=definition,
    )
    return WorkflowSummary.from_row(row)


@router.get("/workflows/{workflow_id}", response_model=WorkflowSummary)
async def get_workflow(
    workflow_id: str,
    user: AuthUser,
    repo: UserWorkflowRepository = Depends(get_user_workflow_repo),
):
    row = await repo.get_by_id(workflow_id, user_id=user.user_id)
    if row is None:
        raise NotFoundError("工作流不存在")
    return WorkflowSummary.from_row(row)


@router.patch("/workflows/{workflow_id}", response_model=WorkflowSummary)
async def update_workflow(
    workflow_id: str,
    body: UpdateWorkflowRequest,
    user: AuthUser,
    repo: UserWorkflowRepository = Depends(get_user_workflow_repo),
):
    desc_arg: object = ...
    if body.clear_description or body.description is not None:
        desc_arg = None if body.clear_description else body.description
    row = await repo.update(
        workflow_id,
        user_id=user.user_id,
        name=body.name,
        description=desc_arg,
        definition=body.definition.payload() if body.definition is not None else None,
    )
    if row is None:
        raise NotFoundError("工作流不存在")
    return WorkflowSummary.from_row(row)


@router.delete("/workflows/{workflow_id}", response_model=StatusResponse)
async def delete_workflow(
    workflow_id: str,
    user: AuthUser,
    repo: UserWorkflowRepository = Depends(get_user_workflow_repo),
):
    ok = await repo.delete(workflow_id, user_id=user.user_id)
    if not ok:
        raise NotFoundError("工作流不存在")
    return StatusResponse()


@router.post("/workflows/{workflow_id}/suggest-slots", response_model=WorkflowSummary)
async def suggest_workflow_slots(
    workflow_id: str,
    user: AuthUser,
    repo: UserWorkflowRepository = Depends(get_user_workflow_repo),
):
    """抽出「上一次的具体输入」并写回 definition（前端第一次点「跑一次」时调）。

    从一轮协作固化出来的工作流，任务描述里写死的是那一次的主题——用户第二次要用它才会撞上
    这件事，所以这一次背景模型调用挂在这里而不是保存路径上。抽到了就连占位符带 ``slots``
    一起落库，以后再跑不用重抽。

    三种「不抽」都返回**与调用前逐字一致**的 definition（不是报错）：已经有槽位（幂等）、
    不是固化来源（官方模板自带槽位、手画的归用户管）、抽槽本身失败或没认出可验证的片段。
    前端拿到没有 ``slots`` 的 definition 就照常直接跑。
    """
    row = await repo.get_by_id(workflow_id, user_id=user.user_id)
    if row is None:
        raise NotFoundError("工作流不存在")
    definition = dict(row.definition or {})
    # 来源读的是列，不是 definition：客户端在画布里塞一个 ``source`` 骗不出抽槽。
    if slots_from_definition(definition) or not is_turn_sourced(row.source):
        return WorkflowSummary.from_row(row)

    definition, slots = await suggest_slots_for_definition(
        definition, user_id=user.user_id
    )
    if not slots:
        return WorkflowSummary.from_row(row)

    updated = await repo.update(
        workflow_id,
        user_id=user.user_id,
        definition=definition,
        description=append_description_note(row.description or "", slots_note(slots)),
    )
    if updated is None:
        raise NotFoundError("工作流不存在")
    return WorkflowSummary.from_row(updated)


@router.post("/workflows/{workflow_id}/run", response_model=RunWorkflowResponse)
async def run_workflow(
    workflow_id: str,
    body: RunWorkflowRequest,
    user: AuthUser,
    folders: FolderRepository = Depends(get_folder_repo),
    repo: UserWorkflowRepository = Depends(get_user_workflow_repo),
):
    row = await repo.get_by_id(workflow_id, user_id=user.user_id)
    if row is None:
        raise NotFoundError("工作流不存在")
    folder = await folders.get_by_id(body.folder_id, user_id=user.user_id)
    _require_folder(folder)
    try:
        conversation_id = await dispatch_workflow_run(
            user_id=user.user_id,
            workflow_id=row.id,
            workflow_version=int(row.version or 1),
            definition=dict(row.definition or {}),
            folder_id=body.folder_id,
            note=body.note,
            conversation_id=body.conversation_id,
            workflow_name=row.name,
            slot_values=body.slots or None,
        )
    except LookupError as e:
        raise NotFoundError(str(e) or "资源不存在") from e
    except ValueError as e:
        raise ValidationError(str(e)) from e
    return RunWorkflowResponse(
        conversation_id=conversation_id,
        workflow_id=row.id,
        workflow_version=int(row.version or 1),
    )


@router.put("/workflows/{workflow_id}/trigger", response_model=WorkflowSummary)
async def put_workflow_trigger(
    workflow_id: str,
    body: PutWorkflowTriggerRequest,
    user: AuthUser,
    folders: FolderRepository = Depends(get_folder_repo),
    repo: UserWorkflowRepository = Depends(get_user_workflow_repo),
):
    row = await repo.get_by_id(workflow_id, user_id=user.user_id)
    if row is None:
        raise NotFoundError("工作流不存在")
    folder = await folders.get_by_id(body.folder_id, user_id=user.user_id)
    _require_cloud_folder(folder)

    plaintext_secret: str | None = None
    fields: dict[str, object] = {
        "trigger_kind": body.kind,
        "trigger_enabled": body.enabled,
        "trigger_folder_id": body.folder_id,
        "last_trigger_error": None,
    }
    if body.kind == "schedule":
        try:
            cron = resolve_cron(cron=body.cron, schedule_preset=body.schedule_preset)
            next_at = next_run_after(cron, datetime.now(UTC))
        except CronError as e:
            raise ValidationError(str(e)) from e
        fields.update(
            {
                "trigger_cron": cron,
                "trigger_next_run_at": next_at,
                "trigger_webhook_id": None,
                "trigger_webhook_secret_hash": None,
            }
        )
    else:
        existing_kind = getattr(row, "trigger_kind", None)
        webhook_id = getattr(row, "trigger_webhook_id", None)
        secret_hash = getattr(row, "trigger_webhook_secret_hash", None)
        if existing_kind != "webhook" or not webhook_id or not secret_hash:
            plaintext_secret, secret_hash = generate_webhook_secret()
            webhook_id = new_id()
        fields.update(
            {
                "trigger_cron": None,
                "trigger_next_run_at": None,
                "trigger_webhook_id": webhook_id,
                "trigger_webhook_secret_hash": secret_hash,
            }
        )

    updated = await repo.replace_trigger(
        workflow_id, user_id=user.user_id, **fields
    )
    if updated is None:
        raise NotFoundError("工作流不存在")
    return WorkflowSummary.from_row(updated, webhook_secret=plaintext_secret)


@router.delete("/workflows/{workflow_id}/trigger", response_model=WorkflowSummary)
async def delete_workflow_trigger(
    workflow_id: str,
    user: AuthUser,
    repo: UserWorkflowRepository = Depends(get_user_workflow_repo),
):
    row = await repo.get_by_id(workflow_id, user_id=user.user_id)
    if row is None:
        raise NotFoundError("工作流不存在")
    updated = await repo.clear_trigger(workflow_id, user_id=user.user_id)
    if updated is None:
        raise NotFoundError("工作流不存在")
    return WorkflowSummary.from_row(updated)


@router.post(
    "/workflows/{workflow_id}/trigger/rotate-secret",
    response_model=RotateWorkflowTriggerResponse,
)
async def rotate_workflow_trigger_secret(
    workflow_id: str,
    user: AuthUser,
    repo: UserWorkflowRepository = Depends(get_user_workflow_repo),
):
    row = await repo.get_by_id(workflow_id, user_id=user.user_id)
    if row is None:
        raise NotFoundError("工作流不存在")
    if getattr(row, "trigger_kind", None) != "webhook" or not row.trigger_webhook_id:
        raise ValidationError("仅 Webhook 触发可轮换密钥")
    plaintext, secret_hash = generate_webhook_secret()
    updated = await repo.replace_trigger(
        workflow_id,
        user_id=user.user_id,
        trigger_webhook_secret_hash=secret_hash,
    )
    if updated is None or not updated.trigger_webhook_id:
        raise NotFoundError("工作流不存在")
    return RotateWorkflowTriggerResponse(
        webhook_id=updated.trigger_webhook_id,
        webhook_url=webhook_path(updated.trigger_webhook_id),
        webhook_secret=plaintext,
    )


@hooks_router.post(
    "/workflows/{webhook_id}",
    response_model=FireWorkflowWebhookResponse,
    status_code=202,
)
async def fire_workflow_webhook(
    webhook_id: str,
    request: Request,
    repo: UserWorkflowRepository = Depends(get_user_workflow_repo),
    folders: FolderRepository = Depends(get_folder_repo),
    authorization: str | None = Header(None),
    x_agentcore_webhook_secret: str | None = Header(
        None, alias="X-AgentCore-Webhook-Secret"
    ),
    x_idempotency_key: str | None = Header(None, alias="X-Idempotency-Key"),
):
    """Public webhook fire — no user JWT; auth via shared secret header."""
    row = await repo.get_by_webhook_id(webhook_id)
    if row is None:
        raise NotFoundError("Webhook 不存在")
    require_webhook_secret(
        authorization=authorization,
        x_webhook_secret=x_agentcore_webhook_secret,
        expected_hash=row.trigger_webhook_secret_hash,
    )
    if not row.trigger_enabled:
        raise ValidationError("触发已停用")

    idem_key = (x_idempotency_key or "").strip() or None
    if idem_key:
        prior = idempotency_lookup(webhook_id, idem_key)
        if prior is not None:
            return FireWorkflowWebhookResponse(conversation_id=prior)

    enforce_webhook_rate_limit(row.id)

    folder = await folders.get_by_id(row.trigger_folder_id or "", user_id=row.user_id)
    _require_cloud_folder(folder)

    body = await request.body()
    event_text = extract_event_text(
        body, content_type=request.headers.get("content-type")
    )
    try:
        conversation_id = await fire_workflow_trigger(
            workflow_id=row.id,
            user_id=row.user_id,
            note=event_text or None,
        )
    except LookupError as e:
        raise NotFoundError(str(e) or "资源不存在") from e
    except ValueError as e:
        raise ValidationError(str(e)) from e
    if idem_key:
        idempotency_store(webhook_id, idem_key, conversation_id)
    return FireWorkflowWebhookResponse(conversation_id=conversation_id)
