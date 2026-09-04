"""Conversation routes, split by domain into one aggregated ``APIRouter``.

This package was split out of a single ``conversations.py`` along domain seams
(file-splitting.mdc): CRUD, messages, interactions, local-mode binding, local→云
handoff, turn re-execution/resume, workspace snapshots, and workspace files.

The sub-routers are included in the original file's definition order, so the
generated OpenAPI spec (path + method order, operationIds, tags) stays
byte-identical to the pre-split build — and ``main.py``'s
``app.include_router(conversations.router, prefix="/v1")`` keeps working unchanged.
"""

from fastapi import APIRouter

from . import (
    audit,
    binding,
    browser_live,
    browser_sessions,
    browser_takeover,
    crud,
    debate_steer,
    external_grants,
    files,
    handoff,
    interactions,
    llm_window,
    messages,
    run_redirect,
    run_stop,
    snapshots,
    trash,
    turn_files_diff,
    turns,
)

# Each domain sub-router carries the original ``prefix="/conversations",
# tags=["conversations"]`` so this aggregate stays a plain concatenator.
router = APIRouter()

# Included in the original file's definition order so the OpenAPI path/method order
# stays byte-identical (the spec is the single source for the generated TS types).
router.include_router(crud.router)
router.include_router(messages.router)
router.include_router(audit.router)
router.include_router(llm_window.router)
router.include_router(interactions.router)
router.include_router(run_redirect.router)
router.include_router(run_stop.router)
router.include_router(debate_steer.router)
router.include_router(binding.router)
router.include_router(external_grants.router)
router.include_router(handoff.router)
router.include_router(turns.router)
router.include_router(snapshots.router)
router.include_router(trash.router)
router.include_router(files.router)
router.include_router(turn_files_diff.router)
# L3 团队浏览器 M1 直播旁路端点（新增路径追加在末尾，不改既有 OpenAPI 顺序）。
router.include_router(browser_live.router)
# L3 团队浏览器 M2 用户接管端点（同样追加在末尾）。
router.include_router(browser_takeover.router)
# M0 多 session_id：list / create / close（追加在末尾）。
router.include_router(browser_sessions.router)
