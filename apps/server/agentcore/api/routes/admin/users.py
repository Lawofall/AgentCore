"""Admin user management (用户管理): roster, patch, password ops, 注销, drill-down."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from agentcore.admin import AdminService
from agentcore.admin.audit import record_admin_audit
from agentcore.api.account_cleanup import cleanup_account_resources
from agentcore.api.cost_view import cost_breakdown, estimated_cost_breakdown, usage_breakdown
from agentcore.api.dependencies import (
    AdminUser,
    get_admin_service,
    get_asset_storage,
    get_auth_service,
    get_conversation_repo,
    get_conversation_share_repo,
    get_cost_event_repo,
    get_db,
    get_folder_desk_service,
    get_message_repo,
    get_turn_metrics_repo,
    get_user_llm_provider_repo,
    get_user_repo,
)
from agentcore.api.routes.admin._shared import (
    _TREND_DAYS,
    _admin_user_list_item,
    _admin_user_response,
)
from agentcore.api.schemas import (
    AdminConversationLine,
    AdminResetPasswordResponse,
    AdminSetPasswordRequest,
    AdminUpdateUserRequest,
    AdminUserDetail,
    AdminUserListResponse,
    AdminUserResponse,
    DailyCost,
    ModelCostLine,
    SessionSummary,
    StatusResponse,
    TurnMetricLine,
    UsageWindow,
)
from agentcore.auth import AuthService
from agentcore.config import settings
from agentcore.core.errors import NotFoundError
from agentcore.db.repositories import (
    ConversationRepository,
    ConversationShareRepository,
    CostEventRepository,
    LlmModelProfileRepository,
    MessageRepository,
    TurnMetricsRepository,
    UserLlmProviderRepository,
    UserRepository,
)
from agentcore.storage.assets import AssetStorage

router = APIRouter(tags=["admin"])

# 用户详情下钻 caps: the most recent conversations + turns shown for one account
# (a bounded glance — deeper history is the per-conversation 复盘's concern).
_USER_CONVERSATIONS = 15
_USER_RECENT_TURNS = 20


@router.get("/users", response_model=AdminUserListResponse)
async def list_users(
    admin: AdminUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    q: str | None = Query(None, max_length=100),
    role: Literal["user", "admin"] | None = Query(None),
    status: Literal["active", "disabled"] | None = Query(None),
    ip: str | None = Query(None, max_length=64),
    since: datetime | None = Query(None),
    until: datetime | None = Query(None),
    sort: Literal["created_at", "cost"] = Query("created_at"),
    order: Literal["asc", "desc"] = Query("desc"),
    include_deleted: bool = Query(False),
    service: AdminService = Depends(get_admin_service),
) -> AdminUserListResponse:
    """The full account roster, paginated, each row carrying its all-time spend.

    Filters (AND): ``q`` substring-matches username/display_name, ``role``/``status``
    pin those dimensions, ``ip`` matches ``registration_ip`` or any refresh-token IP,
    ``since``/``until`` bound registration ``created_at``. ``sort`` ∈ {``created_at``,
    ``cost``} (累计成本) with ``order`` ∈ {``asc``, ``desc``}. ``include_deleted``
    surfaces 注销 (soft-deleted, anonymized) accounts — hidden by default as
    tombstones, shown on demand for audit. Admin-only directory — enumeration is
    intended here. Money is nano-CNY; clients format ¥ as ``cost_total / 1e9``.
    """
    rows, total = await service.list_users(
        page=page,
        page_size=page_size,
        query=q,
        role=role,
        status=status,
        ip=ip,
        since=since,
        until=until,
        sort=sort,
        order=order,
        include_deleted=include_deleted,
    )
    return AdminUserListResponse(
        data=[_admin_user_list_item(u, cost_total) for u, cost_total in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.patch("/users/{user_id}", response_model=AdminUserResponse)
async def update_user(
    user_id: str,
    body: AdminUpdateUserRequest,
    admin: AdminUser,
    service: AdminService = Depends(get_admin_service),
    db: AsyncSession = Depends(get_db),
) -> AdminUserResponse:
    """Partially update an account's role / status / quota.

    Only fields *present* in the body are applied (tri-state — see
    ``AdminUpdateUserRequest``): a quota field sent as ``null`` clears the override,
    a value sets it; absent fields are left untouched. Returns the fresh record.
    """
    fields = body.model_fields_set
    # Resolve the quota patch from the set-fields so the route owns the API-shape
    # concern and the repo gets only the dimensions the operator actually changed.
    quota: dict[str, object] = {}
    if "is_unlimited" in fields and body.is_unlimited is not None:
        quota["is_unlimited"] = body.is_unlimited
    if "quota_daily_tokens" in fields:
        quota["daily_tokens"] = body.quota_daily_tokens
    if "quota_monthly_cost_cny" in fields:
        quota["monthly_cost_cny"] = body.quota_monthly_cost_cny
    if "quota_daily_cost_cny" in fields:
        quota["daily_cost_cny"] = body.quota_daily_cost_cny
    if "quota_daily_requests" in fields:
        quota["daily_requests"] = body.quota_daily_requests

    updated = await service.update_user(
        actor=admin,
        user_id=user_id,
        role=body.role if "role" in fields else None,
        status=body.status if "status" in fields else None,
        quota=quota or None,
    )
    audit_detail: dict[str, object] = {}
    if "role" in fields and body.role is not None:
        audit_detail["role"] = body.role
    if "status" in fields and body.status is not None:
        audit_detail["status"] = body.status
    if quota:
        audit_detail["quota"] = quota
    if audit_detail:
        await record_admin_audit(
            db,
            actor_id=admin.user_id,
            action="user.update",
            target_type="user",
            target_id=user_id,
            detail=audit_detail,
        )
    return _admin_user_response(updated)


@router.post("/users/{user_id}/reset-password", response_model=AdminResetPasswordResponse)
async def reset_user_password(
    user_id: str,
    admin: AdminUser,
    auth_service: AuthService = Depends(get_auth_service),
    db: AsyncSession = Depends(get_db),
) -> AdminResetPasswordResponse:
    """Reset an account's password to a fresh one-off (重置密码), returned once for the
    admin to hand over. Revokes the user's sessions (forces re-login on every device)
    and clears any lockout. 404 for an unknown account. The credential mechanics live
    in ``AuthService`` (password/session domain); this route is the admin-gated entry.
    """
    temp_password = await auth_service.admin_reset_password(user_id=user_id)
    await record_admin_audit(
        db,
        actor_id=admin.user_id,
        action="user.reset_password",
        target_type="user",
        target_id=user_id,
    )
    return AdminResetPasswordResponse(temporary_password=temp_password)


@router.post("/users/{user_id}/set-password", response_model=StatusResponse)
async def set_user_password(
    user_id: str,
    body: AdminSetPasswordRequest,
    admin: AdminUser,
    auth_service: AuthService = Depends(get_auth_service),
    db: AsyncSession = Depends(get_db),
) -> StatusResponse:
    """Set an account's password to an admin-chosen value (设置密码).

    Revokes the user's sessions (forces re-login on every device) and clears any
    lockout. The plaintext is never returned — the operator already knows it.
    ``force_change`` (default true) requires the user to pick a new password on
    next login. 404 for an unknown account.
    """
    await auth_service.admin_set_password(
        user_id=user_id,
        new_password=body.new_password,
        force_change=body.force_change,
    )
    await record_admin_audit(
        db,
        actor_id=admin.user_id,
        action="user.set_password",
        target_type="user",
        target_id=user_id,
        detail={"force_change": body.force_change},
    )
    return StatusResponse(status="ok")


@router.delete("/users/{user_id}", response_model=AdminUserResponse)
async def delete_user(
    user_id: str,
    admin: AdminUser,
    auth_service: AuthService = Depends(get_auth_service),
    conversations: ConversationRepository = Depends(get_conversation_repo),
    shares: ConversationShareRepository = Depends(get_conversation_share_repo),
    llm_providers: UserLlmProviderRepository = Depends(get_user_llm_provider_repo),
    assets: AssetStorage = Depends(get_asset_storage),
    folder_desk_svc=Depends(get_folder_desk_service),
    db: AsyncSession = Depends(get_db),
) -> AdminUserResponse:
    """注销 (soft-delete + anonymize) an account, admin-initiated (用户管理 强操作).

    The stronger sibling of 停用 (a reversible status flip): this anonymizes the
    account (username → ``deleted_<id>``, email/avatar cleared), disables it (live
    tokens die on the next request), revokes its sessions, and cascades cross-domain
    cleanup (conversations soft-deleted for the retention sweeper, public shares
    revoked, BYOK key dropped, avatar object removed) — the same destructive path as
    self-service 注销, minus the password. Refuses self-deletion (no self-lockout →
    ≥1 active admin always remains); 404 for an unknown account. The append-only cost
    ledger is intentionally retained. Returns the tombstone record (carries
    ``deleted_at``) so the client can flag the row 「已注销」or drop it from the roster.
    """
    updated, avatar_key = await auth_service.admin_delete_account(
        actor_id=admin.user_id, user_id=user_id
    )
    await cleanup_account_resources(
        user_id,
        avatar_key=avatar_key,
        conversations=conversations,
        shares=shares,
        llm_providers=llm_providers,
        assets=assets,
        folder_desk=folder_desk_svc,
    )
    await record_admin_audit(
        db,
        actor_id=admin.user_id,
        action="user.delete",
        target_type="user",
        target_id=user_id,
    )
    return _admin_user_response(updated)


@router.get("/users/{user_id}/detail", response_model=AdminUserDetail)
async def user_detail(
    user_id: str,
    admin: AdminUser,
    users: UserRepository = Depends(get_user_repo),
    cost_repo: CostEventRepository = Depends(get_cost_event_repo),
    conversations: ConversationRepository = Depends(get_conversation_repo),
    messages_repo: MessageRepository = Depends(get_message_repo),
    metrics_repo: TurnMetricsRepository = Depends(get_turn_metrics_repo),
    llm_providers: UserLlmProviderRepository = Depends(get_user_llm_provider_repo),
    auth_service: AuthService = Depends(get_auth_service),
    session: AsyncSession = Depends(get_db),
) -> AdminUserDetail:
    """用户详情下钻 (用户管理 P0): one account's record + configured model names +
    its own usage (today / month / 7-day trend / by-model) + recent
    conversations + recent turn activity + active login sessions (加强可查).

    The per-user counterpart of the platform 用量看板 — same windows / 口径 but scoped
    to one account — composed with the account's recent conversation roster (message
    counts batched, no N+1) and its recent turns (each carries ``conversation_id`` to
    drill into 会话复盘). Configured model names come from the account default
    模型组合 (``main_model`` / ``background_model``; never the API key) + provider
    count from ``user_llm_providers``. Per-model stats scan ``cost_calls`` (last 30
    days). Sessions reuse the owner ``AuthService.list_sessions`` shape (read-only).
    Admin cross-user; 404 for an unknown id.
    """
    user = await users.get_by_id(user_id)
    if user is None:
        raise NotFoundError("用户不存在")

    now = datetime.now(UTC)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = day_start.replace(day=1)
    since_30d = now - timedelta(days=30)

    # Account default 模型组合 (names only) + how many providers are configured.
    default_model: str | None = None
    background_model: str | None = None
    profile_id = getattr(user, "default_model_profile_id", None)
    if profile_id:
        profile = await LlmModelProfileRepository(session).get(
            profile_id, user_id=user_id
        )
        if profile is not None:
            default_model = profile.main_model
            background_model = profile.background_model
    provider_count = await llm_providers.count_for_user(user_id)

    today = await cost_repo.aggregate_for_window(user_id=user_id, since=day_start)
    month = await cost_repo.aggregate_for_window(user_id=user_id, since=month_start)
    recent_by_model = await cost_repo.aggregate_by_model_for_window(
        user_id=user_id, since=since_30d
    )

    # 近 7 日趋势: zero-fill into a fixed, oldest-first series ending today (same
    # shape as /v1/usage/summary) so the sparkline is stable even for sparse spend.
    trend_start = day_start - timedelta(days=_TREND_DAYS - 1)
    daily = await cost_repo.aggregate_daily_for_window(user_id=user_id, since=trend_start)
    recent_daily_cost = []
    for i in range(_TREND_DAYS):
        iso = (trend_start + timedelta(days=i)).date().isoformat()
        recent_daily_cost.append(DailyCost(date=iso, cost_total=daily.get(iso, 0)))

    # Recent conversations (live list, newest-activity first) + their message counts
    # in one batched query (no per-row N+1).
    convs, _ = await conversations.list_by_user(user_id, limit=_USER_CONVERSATIONS)
    counts = await messages_repo.counts_for_conversations([c.id for c in convs])
    conversation_lines = [
        AdminConversationLine(
            id=c.id,
            title=c.title or None,
            created_at=c.created_at,
            updated_at=c.updated_at,
            messages=counts.get(c.id, 0),
        )
        for c in convs
    ]

    recent_turns = await metrics_repo.list_recent_for_user(user_id, limit=_USER_RECENT_TURNS)

    # Active login devices (refresh-token families). Admin is never "current".
    auth_sessions = await auth_service.list_sessions(user_id=user_id, current_family=None)
    session_lines = [
        SessionSummary(
            id=s.id,
            platform=s.platform,
            user_agent=s.user_agent,
            ip=s.ip,
            created_at=s.created_at,
            last_used_at=s.last_used_at,
            current=False,
        )
        for s in auth_sessions
    ]

    return AdminUserDetail(
        user=_admin_user_response(user),
        default_model=default_model,
        background_model=background_model,
        provider_count=provider_count,
        today=UsageWindow(
            usage=usage_breakdown(today["usage"]),
            cost=cost_breakdown(today["cost"]),
            estimated_cost=estimated_cost_breakdown(cost=today.get("estimated_cost") or {}),
            requests=today["turns"],
        ),
        month=UsageWindow(
            usage=usage_breakdown(month["usage"]),
            cost=cost_breakdown(month["cost"]),
            estimated_cost=estimated_cost_breakdown(cost=month.get("estimated_cost") or {}),
            requests=month["turns"],
        ),
        recent_by_model=[
            ModelCostLine(
                model=row["model"],
                calls=int(row["calls"]),
                tokens_total=int(row["tokens_total"]),
                cost_total=int(row["cost_total"]),
                cost_estimated_total=int(row.get("cost_estimated_total", 0) or 0),
            )
            for row in recent_by_model
        ],
        recent_daily_cost=recent_daily_cost,
        conversations=conversation_lines,
        recent_turns=[TurnMetricLine.model_validate(r) for r in recent_turns],
        sessions=session_lines,
        billing_mode=settings.billing_mode,
    )
