"""Admin console routes, split by surface into one aggregated ``APIRouter``.

Split out of the former single ``admin.py`` along product seams: overview,
user management, usage, system status, audit trail, conversation rosters, and
observability/replay. Sub-routers are included in the original file's definition
order so the OpenAPI spec (path + method order, operationIds, tags) stays
identical — and ``main.py``'s ``app.include_router(admin.router, prefix="/v1")``
keeps working unchanged.
"""

from fastapi import APIRouter

from . import (
    agent_audit,
    audit_logs,
    beta_group,
    conversations,
    feedback,
    notices,
    observability,
    overview,
    platform_credentials,
    system,
    usage,
    users,
)

router = APIRouter(prefix="/admin", tags=["admin"])

# Original definition order (overview → users → usage → system → audit →
# conversations → observability) for stable OpenAPI path/method ordering.
router.include_router(overview.router)
router.include_router(users.router)
router.include_router(usage.router)
router.include_router(system.router)
router.include_router(platform_credentials.router)
router.include_router(audit_logs.router)
router.include_router(agent_audit.router)
router.include_router(conversations.router)
router.include_router(observability.router)
router.include_router(feedback.router)
router.include_router(notices.router)
router.include_router(beta_group.router)

__all__ = ["router"]
