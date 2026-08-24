"""Scan logger.* call sites and regenerate agentcore/observability/catalog.py.

Also pair with ``gen_log_event_docs.py`` to refresh the markdown event table::

    uv run python scripts/sync_log_event_registry.py
    uv run python scripts/gen_log_event_docs.py

Read-only drift check (release gate / CI — never rewrites catalog.py)::

    uv run python scripts/sync_log_event_registry.py --check
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
import unicodedata
from pathlib import Path

# Match ruff E501: East-Asian wide chars count as 2 columns.
_LINE_LIMIT = 100

ROOT = Path(__file__).resolve().parents[1]
AGENTCORE = ROOT / "agentcore"
OUT = AGENTCORE / "observability" / "catalog.py"
LEVELS = {"info", "warning", "error", "debug", "exception", "critical"}
NAME_RE = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+$")

# Lightweight field schemas for high-value events (docs / debugging).
KEY_FIELDS: dict[str, dict[str, str]] = {
    "journal.append_failed": {
        "turn_id": "str",
        "kind": "str",
        "critical": "bool",
        "error": "str",
    },
    "journal.live_seq_near_overflow": {
        "seq": "int",
        "overflow_start": "int",
        "remaining": "int",
        "op": "str",
        "turn_id": "str",
    },
    "journal.sealed_drop": {
        "turn_id": "str",
        "kind": "str",
        "reason": "str",
    },
    "journal.sealed_overflow": {
        "turn_id": "str",
        "kind": "str",
    },
    "journal.sealed_skip": {
        "turn_id": "str",
        "kind": "str",
        "reason": "str",
    },
    "llm.rate_limit_no_retry": {
        "provider": "str",
        "scenario": "str",
        "attempt": "int",
        "retry_after_sec": "float",
        "cooldown_sec": "float",
        "cooldown_source": "str",
        "ceiling_sec": "float",
        "reason": "str",
    },
    "llm.tool_surface.limit_exceeded": {
        "platform_credential_id": "str",
        "tool_count": "int",
        "properties_total": "int",
        "properties_per_tool_max": "int",
        "max_tools": "int",
        "max_properties_total": "int",
        "max_properties_per_tool": "int",
        "exceeded": "list",
    },
    "platform_pool.blocked": {
        "credential_id": "str",
        "reason": "str",
    },
    "platform_pool.cooling": {
        "credential_id": "str",
        "status": "str",
        "limit_name": "str",
        "recovery_at": "float",
        "source": "str",
    },
    "platform_pool.failover": {
        "from_credential_id": "str",
        "to_credential_id": "str",
    },
    "platform_pool.redis_fail_open": {
        "op": "str",
        "error": "str",
    },
    "chat.turn_start": {
        "preview": "str",
        "chars": "int",
        "history": "int",
        "location": "str",
        "via": "str",
    },
    "chat.turn_complete": {
        "finish_reason": "str",
        "rounds": "int",
        "input_tokens": "int",
        "output_tokens": "int",
        "reply_preview": "str",
        "delegated": "bool",
        "workers": "int",
        "duration_ms": "int",
        "boundary_yields": "int",
        "scope_signals": "int",
        "revises": "int",
        "escalations": "int",
        "prepare_ms": "int",
        "assemble_ms": "int",
        "ttft_reasoning_ms": "int",
        "ttft_content_ms": "int",
        "model": "str",
        "credential_source": "str",
        "provider_id": "str",
    },
    "chat.resume_complete": {
        "finish_reason": "str",
        "delegated": "bool",
        "duration_ms": "int",
        "boundary_yields": "int",
        "scope_signals": "int",
        "revises": "int",
        "escalations": "int",
    },
    "chat.regenerate_rejected": {
        "conversation_id": "str",
        "message_id": "str",
        "user_id": "str",
        "reason": "str",
        "found_role": "str",
    },
    "chat.zero_output_send_deleted": {
        "conversation_id": "str",
        "message_id": "str",
        "user_message_id": "str",
        "error_code": "str",
    },
    "chat.zero_output_send_delete_failed": {
        "conversation_id": "str",
        "message_id": "str",
        "user_message_id": "str",
    },
    "chat.prepare_phase": {
        "phase": "str",
        "ms": "int",
    },
    "chat.local_presence_gate": {
        "reason": "str",
        "root_id": "str",
        "user": "str",
    },
    "chat.prepare_local_io_abort": {
        "reason": "str",
        "detail": "str",
    },
    "conversation.created": {
        "user_id": "str",
        "conversation_id": "str",
        "folder_id": "str",
        "client_request_id": "str",
        "idempotent_hit": "bool",
    },
    "chat.title_degraded": {
        "conversation_id": "str",
        "reason": "str",
        "title_chars": "int",
        "persisted": "bool",
    },
    "fulfill.no_fulfiller": {
        "reason": "str",
        "channel": "str",
        "root_id": "str",
        "origin_device": "str",
        "devices": "int",
        "user": "str",
        "conversation_id": "str",
    },
    "fulfill.reconnect_grace": {
        "channel": "str",
        "root_id": "str",
        "origin_device": "str",
        "request_id": "str",
        "grace_seconds": "float",
        "user": "str",
        "conversation_id": "str",
    },
    "fulfill.grace_expired": {
        "channel": "str",
        "request_id": "str",
        "user": "str",
        "conversation_id": "str",
    },
    "stream_state.retention_swept": {
        "deleted": "int",
    },
    "stream_state.retention_failed": {
        "error": "str",
    },
    "desktop.mcp_list_ok": {
        "duration_ms": "int",
        "tool_count": "int",
        "ready_servers": "int",
        "failed_servers": "int",
    },
    "desktop.mcp_list_degraded": {
        "detail": "str",
        "duration_ms": "int",
        "tool_count": "int",
        "failed_servers": "int",
    },
    "desktop.mcp_list_cache_hit": {
        "conversation_id": "str",
        "cache_scope": "str",
        "degraded": "bool",
        "tool_count": "int",
        "duration_ms": "int",
    },
    "desktop.mcp_list_cache_miss": {
        "conversation_id": "str",
        "cache_scope": "str",
        "detail": "str",
        "duration_ms": "int",
        "tool_count": "int",
        "origin": "str",
    },
    "desktop.mcp_list_cache_seed": {
        "conversation_id": "str",
        "cache_scope": "str",
        "degraded": "bool",
        "tool_count": "int",
    },
    "account.rules_memory_cache_hit": {
        "user_id": "str",
        "folder_id": "str",
        "degraded": "bool",
        "topic_count": "int",
    },
    "account.rules_memory_cache_miss": {
        "user_id": "str",
        "folder_id": "str",
        "origin": "str",
    },
    "account.rules_memory_cache_seed": {
        "user_id": "str",
        "folder_id": "str",
        "degraded": "bool",
        "topic_count": "int",
        "memory_file_count": "int",
        "ttl_seconds": "float",
    },
    "account.rules_memory_warm_failed": {
        "user_id": "str",
        "folder_id": "str",
        "part": "str",
        "error": "str",
    },
    "sidecar.warm_mcp_discover": {
        "user_id": "str",
        "tool_count": "int",
        "ready_servers": "int",
        "failed_servers": "int",
        "degraded": "bool",
        "ttl_seconds": "float",
    },
    "sidecar.warm_account_rules_memory": {
        "user_id": "str",
        "folder_id": "str",
        "degraded": "bool",
        "topic_count": "int",
        "memory_file_count": "int",
        "ttl_seconds": "float",
    },
    "sidecar.warm_account_rules_memory_failed": {
        "user_id": "str",
        "folder_id": "str",
        "error": "str",
    },
    "delegate.started": {
        "nodes": "int",
        "call": "str",
        "parallel": "int",
        "agents": "list",
        "plan": "list",
        "waves": "list",
        "task_chars": "list",
    },
    "delegate.context_capped": {
        "site": "str",
        "original_chars": "int",
        "final_chars": "int",
        "original_count": "int",
        "final_count": "int",
        "execution_id": "str",
    },
    "delegate.completed": {
        "escalations": "int",
        "scope": "int",
        "scope_ratio": "float",
    },
    "delegate.yielded": {"reason": "str"},
    "delegate.continuation_ok": {"run_id": "str"},
    "delegate.continuation_rejected": {
        "run_id": "str",
        "reason": "str",
        "cause": "str",
    },
    "roster.conversation_evicted": {
        "evicted_conversation_id": "str",
    },
    "roster.session_evicted": {
        "run_id": "str",
        "reason": "str",
        "bytes": "int",
        "total_bytes": "int",
        "max_bytes": "int",
        "n_sessions": "int",
    },
    "search_cache.conversation_evicted": {
        "evicted_conversation_id": "str",
    },
    "url_cache.conversation_evicted": {
        "evicted_conversation_id": "str",
    },
    "session_roster.wired": {
        "persist": "bool",
        "loader": "bool",
        "conversation_id": "str",
    },
    "delegate.run_redirect_hot": {
        "execution_id": "str",
        "cancelled_run_id": "str",
        "continuation_run_id": "str",
        "recall_count": "int",
    },
    "delegate.delivery_status_empty": {
        "execution_id": "str",
        "delivered_count": "int",
        "gaps_count": "int",
        "rejected_count": "int",
    },
    "delegate.delivery_status_emitted": {
        "execution_id": "str",
        "state": "str",
        "artifacts_count": "int",
        "accepted_count": "int",
        "rejected_count": "int",
        "gaps_count": "int",
    },
    "worker.escalate": {
        "kind": "str",
        "blocking": "bool",
        "question": "str",
        "assumption": "str",
    },
    "tool.execute_start": {"tool": "str"},
    "tool.execute_end": {
        "tool": "str",
        "status": "str",
        "duration_ms": "int",
        "reason": "str",
        "index_status": "str",
        "subcommand": "str",
        "command_preview": "str",
        "cwd_preview": "str",
    },
    "tool.args_parse_failed": {
        "pos": "int",
        "msg": "str",
        "args_preview": "str",
        "parse_class": "str",
    },
    "tool.args_salvaged": {"args_preview": "str"},
    "tool.web_search": {"query": "str", "hosts": "list"},
    "worker.handoff": {
        "run_id": "str",
        "has_summary": "bool",
        "chars": "int",
        "body_chars": "int",
        "has_motion_card": "bool",
    },
    "worker.prepare_phase": {
        "phase": "str",
        "ms": "int",
    },
    "react.round_start": {"round": "int"},
    "react.round_end": {
        "round": "int",
        "tools": "int",
        "input_tokens": "int",
        "output_tokens": "int",
        "reasoning_tokens": "int",
        "done": "bool",
    },
    "engine.loop_nudge": {},
    "engine.loop_finalize": {},
    "engine.ceiling_finalize": {
        "reason": "str",
        "thrashing": "bool",
        "rounds": "int",
        "tokens": "int",
        "token_budget": "int",
    },
    "engine.retrieval_budget_awareness": {
        "round": "int",
        "limit": "int",
        "used": "int",
        "searches": "int",
        "reads": "int",
        "remaining": "int",
        "critical": "bool",
        "final": "bool",
    },
    "engine.finish_guard_honesty_shadow": {
        "verdict_state": "str",
        "hit": "str",
        "has_delivered_files": "bool",
        "gap_reasons": "list",
        "requires_draft_ack": "bool",
        "execution_id": "str",
        "tier_label": "str",
    },
    "llm.call": {
        "scenario": "str",
        "model": "str",
        "latency_ms": "int",
        "finish_reason": "str",
        "input_tokens": "int",
        "output_tokens": "int",
        "reasoning_tokens": "int",
        "stream": "bool",
        "cost_nano": "int",
        "platform_credential_id": "str",
    },
    "llm.request": {"scenario": "str", "model": "str"},
    "llm.response": {"scenario": "str", "model": "str"},
    "llm.call_failed": {
        "error": "str",
        "scenario": "str",
        "model": "str",
        "credential_source": "str",
        "provider_id": "str",
        "platform_credential_id": "str",
    },
    "llm.stream_stalled": {
        "model": "str",
        "credential_source": "str",
        "provider_id": "str",
        "scenario": "str",
        "committed": "bool",
        "platform_credential_id": "str",
    },
    "contract.retry": {},
    "contract.failed": {},
    "run.failed": {"error": "str"},
    "run.captain_failed": {"error": "str"},
    "cost.recorded": {
        "runs": "int",
        "total_nano": "int",
        "total_usd": "float",
        "models": "list",
        "by_role": "dict",
    },
    "cost.prompt_assembled": {
        "scope": "str",
        "total_chars": "int",
        "sections": "dict",
        "section_digests": "dict",
        "assembly_hash": "str",
        "over_soft_cap": "bool",
        "soft_cap": "int",
    },
    "cost.prefix_cache": {
        "scenario": "str",
        "model": "str",
        "breach": "str",
        "breach_section": "str",
        "changed_sections": "list",
        "cache_reported": "bool",
        "input_tokens": "int",
        "cache_hit_tokens": "int",
        "hit_ratio": "float",
        "reusable_tokens": "int",
        "reusable_basis": "str",
        "forfeited_tokens": "int",
        "prompt_messages": "int",
        "stable_prefix_messages": "int",
        "prompt_chars": "int",
        "stable_prefix_chars": "int",
        "chain_calls": "int",
    },
    "cost.ledger_write_failed": {"error": "str"},
    "cost.ledger_drain_before_reconcile_failed": {},
    "cost.currency_mixed": {"bucket": "str", "currencies": "list", "kept": "str"},
    "workspace.snapshot_created": {},
    "workspace.snapshot_failed": {"error": "str"},
    "workspace.system_snapshot_prune_failed": {"error": "str"},
    "workspace.index_build_start": {
        "force": "bool",
    },
    "workspace.index_build_complete": {
        "force": "bool",
        "updated": "bool",
        "duration_ms": "int",
        "generation": "int",
        "truncated": "bool",
        "files": "int",
    },
    "workspace.index_skip_channel_busy": {
        "force": "bool",
        "wait_ms": "int",
        "inflight": "int",
    },
    "workspace.index_failed": {
        "force": "bool",
        "duration_ms": "int",
        "error": "str",
    },
    "pipeline.error": {"error": "str"},
    "http.unhandled_error": {"method": "str", "path": "str", "error": "str"},
    "http.db_pool_exhausted": {"method": "str", "path": "str", "error": "str"},
    "db.pool_exhausted_snapshot": {
        "pool": "str",
        "checked_out": "int",
        "capacity": "int",
        "holders": "list",
    },
    "db.pool_checkout_slow": {
        "pool": "str",
        "held_s": "float",
        "task_name": "str",
        "stack": "list",
        "trace_id": "str",
        "conversation_id": "str",
        "run_id": "str",
        "agent_id": "str",
    },
    "approval.sandbox_auto_pass": {"tool": "str"},
    "approval.timeout": {"tool": "str"},
    "firehose.backpressure_drop": {
        "user": "str",
        "type": "str",
        "dropped_delta": "int",
        "dropped_total": "int",
    },
    "auth.login_failed": {
        "reason": "str",
        "user_id": "str",
        "subject": "str",
        "platform": "str",
        "method": "str",
    },
    "auth.mfa_enrolled": {"user_id": "str"},
    "auth.mfa_recovery_used": {"user_id": "str"},
    "security.csrf_rejected": {
        "path": "str",
        "method": "str",
        "reason": "str",
        "user_id": "str",
        "client_platform": "str",
        "client_version": "str",
    },
    "llm_provider.key_updated": {"user_id": "str", "provider_id": "str"},
    "llm_provider.deleted": {"user_id": "str", "provider_id": "str"},
    "sidecar.turn_cancel_requested": {
        "turn_id": "str",
        "reason": "str",
        "coordination_cascaded": "bool",
        "task_cancelled": "bool",
    },
    "sidecar.turn_cancelled": {
        "turn_id": "str",
        "reason": "str",
        "salvaged": "bool",
    },
    "compaction.done": {
        "conversation_id": "str",
        "folded": "int",
        "kept": "int",
        "summary_chars": "int",
        "trigger_input_tokens": "int",
    },
    "compaction.failed": {
        "conversation_id": "str",
        "error": "str",
    },
    "compaction.timeout": {
        "conversation_id": "str",
    },
    "compaction.schedule_failed": {
        "conversation_id": "str",
        "error": "str",
    },
    "billing.background_byok_provider_error": {
        "user_id": "str",
        "purpose": "str",
        "provider_id": "str",
        "reason": "str",
        "error": "str",
    },
    "billing.background_quota_skip": {
        "user_id": "str",
        "purpose": "str",
        "error": "str",
        "declared_recovery_sec": "float",
    },
    "billing.call_quota_refused": {
        "user_id": "str",
        "dimension": "str",
        "used": "int",
        "limit": "int",
        "model": "str",
        "scenario": "str",
    },
    "memory.consolidation_failed": {
        "conversation_id": "str",
        "error": "str",
        "error_type": "str",
        "reason": "str",
    },
    "memory.consolidation_window_dropped": {
        "conversation_id": "str",
        "error": "str",
        "error_type": "str",
        "reason": "str",
        "window_through": "str",
    },
    "server.started": {
        "host": "str",
        "port": "int",
        "turn_lease_enabled": "bool",
        "version": "str",
        "git_sha": "str",
    },
    "server.shutdown_teardown_timeout": {
        "timeout_seconds": "float",
    },
    "browser.close_all_timeout": {
        "session_count": "int",
        "timeout_seconds": "float",
    },
    "browser.cgroup_unwritable_ignore": {
        "reason": "str",
    },
    "browser.session_open_failed": {
        "conversation_id": "str",
        "error": "str",
        "error_type": "str",
    },
    "sandboxd.health_failed": {
        "shape": "str",
        "detail": "str",
    },
    "compaction.shutdown_timeout": {
        "pending": "int",
        "timeout_seconds": "float",
    },
    "rate_limit.redis_fail_open": {
        "prefix": "str",
        "error": "str",
        "count": "int",
    },
    "event_sink.backpressure_drop": {
        "conversation_id": "str",
        "message_id": "str",
        "label": "str",
        "type": "str",
        "dropped_delta": "int",
        "dropped_total": "int",
    },
    "event_sink.attach": {
        "conversation_id": "str",
        "message_id": "str",
        "label": "str",
        "mode": "str",
        "started_at": "str",
        "http_req_id": "str",
    },
    "event_sink.detach": {
        "reason": "str",
        "conversation_id": "str",
        "message_id": "str",
        "already_detached": "bool",
        "started_at": "str",
        "duration_ms": "int",
        "idle_ms": "int",
        "label": "str",
        "mode": "str",
        "http_req_id": "str",
    },
    "conversation_stream.watch": {
        "conversation_id": "str",
        "message_id": "str",
        "watchers": "int",
        "started_at": "str",
        "mode": "str",
        "http_req_id": "str",
    },
    "conversation_stream.unwatch": {
        "conversation_id": "str",
        "started_at": "str",
        "duration_ms": "int",
        "idle_ms": "int",
        "mode": "str",
        "http_req_id": "str",
    },
    "http.readyz": {
        "ok": "bool",
        "status": "str",
        "database": "bool",
        "redis": "bool",
        "probe_ms": "int",
        "unlogged_failures": "int",
    },
    "http.readyz_failed": {
        "ok": "bool",
        "status": "str",
        "database": "bool",
        "redis": "bool",
        "probe_ms": "int",
        "fail_count": "int",
    },
    "disk.high_watermark": {
        "used_pct": "float",
        "path": "str",
        "total_bytes": "int",
        "free_bytes": "int",
        "fstype": "str",
        "overlay": "bool",
        "threshold_pct": "float",
        "suppressed": "int",
        "reason": "str",
    },
    "disk.probe_failed": {
        "path": "str",
        "error": "str",
        "suppressed": "int",
    },
    "event_loop.lag": {
        "lag_ms": "int",
        "interval_s": "float",
        "threshold_ms": "int",
        "suppressed": "int",
    },
    "event_loop.lag_summary": {
        "max_lag_ms": "int",
        "samples": "int",
        "over_threshold": "int",
        "threshold_ms": "int",
        "window_s": "float",
    },
    "event_sink.close": {
        "reason": "str",
        "conversation_id": "str",
        "message_id": "str",
        "was_detached": "bool",
    },
    "attention.signalled": {
        "state": "str",
        "kind": "str",
        "conversation_id": "str",
        "interaction_id": "str",
        "pushed": "bool",
        "push_outcome": "str",
    },
    "push.fcm_configured": {
        "project_id": "str",
    },
    "push.fcm_token_minted": {
        "project_id": "str",
        "expires_in": "int",
    },
    "push.fcm_sent": {
        "device": "str",
        "message_id": "str",
    },
    "push.fcm_token_stale": {
        "device": "str",
        "status": "int",
    },
    "push.skipped": {
        "user_id": "str",
        "reason": "str",
    },
    "push.notified": {
        "user_id": "str",
        "devices": "int",
        "accepted": "int",
        "pruned": "int",
        "failed": "int",
    },
}

# S3-retired names: no emit site, kept so old JSONL still validates against the registry.
# Descriptions must say 历史兼容 — do not present as current contract.
HISTORICAL_COMPAT: dict[str, str] = {
    "browser.cgroup_unwritable_ignore": (
        "历史兼容：曾在探测 subtree_control 只读后自动加 --ignore-cgroups；"
        "现形状 B 固定 ignore-cgroups，不再发此事件"
    ),
    "handoff.empty_body_blocked": (
        "历史兼容：曾因空正文拒收 handoff；实测误伤已撤，不再发此事件"
    ),
    "contract.hard_gap_blocked_completion": (
        "历史兼容：曾因空交/未落盘把节点打成 FAILED；已撤"
    ),
    "coordination.idle_yield_to_captain": (
        "历史兼容：曾在有在飞工作时 idle-yield 回 CEO；现改为 held_inflight"
    ),
    "team_preview.list_pending_failed": (
        "历史兼容：曾在列出待处理开工卡失败时发出；开工卡产品位已拆，不再发此事件"
    ),
    "team_preview.orphaned": (
        "历史兼容：曾在发新开工卡前结算旧 pending 时发出；开工卡产品位已拆，不再发此事件"
    ),
}

KEY_DESC: dict[str, str] = {
    "llm.rate_limit_no_retry": (
        "429 冷却超出本次调用能等的上限，放弃重试。cooldown_sec / cooldown_source = 判定所依据的"
        "冷却及其来源：upstream_header（上游声明，reason=retry_after_too_large）/ local_backoff"
        "（上游没带头，这个数是我们自己的退避链，reason=backoff_exceeds_budget）；"
        "交互回合 chat/agent 无头或头>2s 立刻放弃（reason=interactive_fail_fast），"
        "不再走 2→4→8→16；retry_after_sec 只记上游声明值，无头时为 null；"
        "ceiling_sec = 该上限（后台一次性调用按剩余预算算，交互回合为 30s）"
    ),
    "llm.tool_surface.limit_exceeded": (
        "平台凭据声明的上游工具面上限装不下当前装配的工具面；未发给上游、未自动裁剪。"
        "exceeded 为触发的维度名；max_* 为声明值（未声明的维度为 null）"
    ),
    "platform_pool.blocked": (
        "平台池成员 401（封号或坏 key）或 403 RegionError 已摘除，需人工重新启用。"
        "401 不换号重试；403 允许 commit 前换号"
    ),
    "platform_pool.cooling": (
        "平台池成员因上游 429 进入 cooling/exhausted；recovery_at 为 unix 秒（上游 Retry-After）"
    ),
    "platform_pool.failover": (
        "流式 commit 前换号：from_credential_id → to_credential_id（稳定账号 id，非 key）"
    ),
    "platform_pool.redis_fail_open": (
        "池状态 Redis 读写失败，本操当无记录（fail-open）；construct 失败则退回内存实现"
    ),
    "chat.zero_output_send_deleted": (
        "本发新建 user + 空失败助手（LLM_RATE_LIMIT / KEY_INVALID / 余额不足，"
        "无正文/工具/token）已硬删，发送在库里当没发生；cost_events 留下"
    ),
    "chat.zero_output_send_delete_failed": (
        "本发零产出回滚硬删失败（助手或 user 行未去掉）；客户端仍可能撤泡，重载以库为准"
    ),
    "chat.turn_start": "回合起点（preview/chars/history）",
    "chat.turn_complete": "回合收尾（含 Phase-0 延迟：prepare/assemble/ttft_*；model/credential_source）",
    "chat.resume_complete": "暂停恢复回合收尾（终态带协作计数；STOP 终结不带）",
    "chat.regenerate_rejected": (
        "regenerate 早退拒绝（会话不存在 / 目标非用户消息或已删除）；排前端传错 id"
    ),
    "chat.prepare_phase": "prepare/assemble 分段耗时（phase + ms；每 phase 一行）",
    "conversation.created": (
        "POST /v1/conversations 受理一次新建；client_request_id 为空表示该客户端没传幂等键，"
        "idempotent_hit=true 表示这次重复请求原样返回了首次那条（没新建、没跑回合）"
    ),
    "chat.title_degraded": (
        "铸题未产出模型标题；persisted=false 未写入 conversations.title（保持空以便后续回合再铸），"
        "persisted=true 为历史语义（曾把 fallback_title 落库）；"
        "reason=rate_limit/timeout/empty_model_title/gate_* 归因"
    ),
    "fulfill.no_fulfiller": (
        "回合中途派单落空（reason=desktop_offline 桌面未连接 / root_not_held 桌面在线未声明该 root；"
        "第三态来源设备离线另见 fulfill.origin_offline）；紧跟 fulfill.reconnect_grace 者未结算"
    ),
    "fulfill.reconnect_grace": (
        "派单落空但该设备刚刚还在线（SSE 重连盲窗）：op 挂住不结算，等重连 rehang 重推；"
        "grace_seconds 为本次上限（已卡在该 op 自身 deadline 之内）"
    ),
    "fulfill.grace_expired": (
        "重连宽限到点设备仍没回来：按当下真实状态重派一次，仍落空则结算原有 typed 失败"
    ),
    "desktop.mcp_list_ok": "MCP list 成功（duration_ms / tool_count）",
    "desktop.mcp_list_degraded": "MCP list 超时或降级（带 duration_ms）",
    "desktop.mcp_list_cache_hit": "MCP list 命中进程内缓存（含 cache_scope / duration_ms）",
    "desktop.mcp_list_cache_miss": (
        "MCP list 只读缓存未命中（prepare/resume；不发 ClientTool；"
        "origin=execution_harvest 时为收口空装配）"
    ),
    "desktop.mcp_list_cache_seed": "MCP list 结果写入进程内缓存（非回合暖）",
    "delegate.started": "编排委派开始（agents/plan/waves；task_chars=完整 task 长度）",
    "delegate.context_capped": (
        "上下文管线帽触发（site + 原长/切后长或条数；不落正文）"
    ),
    "delegate.completed": "委派批次完成（escalations/scope）",
    "delegate.yielded": "委派中途让出（replan 边界）",
    "delegate.run_redirect_hot": "redirect 热修续派（revise 重算桶，与 continuation_ok 同义）",
    "delegate.delivery_status_empty": (
        "交付卡判定无物质不发（delivered/gaps/rejected 计数；巡检可证静默原因）"
    ),
    "delegate.delivery_status_emitted": (
        "交付卡已发射（state + artifacts/accepted/rejected/gaps 计数）"
    ),
    "worker.escalate": "worker 升级求决策",
    "tool.execute_end": "工具执行结束（status/duration_ms；error 时带 reason）",
    "tool.args_salvaged": "handoff 参数 JSON 窄 salvage 成功（裸字符串字段 / 截断闭合）",
    "worker.handoff": "worker 交接（chars=summary 长；body_chars=交付正文长）",
    "worker.prepare_phase": "worker 冷开分段耗时（phase + ms；每 phase 一行）",
    "react.round_end": "ReAct 轮结束（reasoning_tokens/tools）",
    "engine.loop_nudge": "收敛治理：循环提醒",
    "engine.loop_finalize": "收敛治理：强制收尾",
    "engine.ceiling_finalize": "收敛治理：硬顶强制收尾（reason=max_rounds 轮预算耗尽 / token_budget）",
    "engine.retrieval_budget_awareness": (
        "检索余额注入（分项用量变化记一行；final=true 是每个 worker run 的最终 searches/reads）"
    ),
    "engine.finish_guard_honesty_shadow": (
        "收口诚实性本该回炉但只观测：verdict_state / "
        "hit=posture_a|draft_ack|overview_length / "
        "has_delivered_files / gap_reasons（不记正文；不回炉不 reset）"
    ),
    "llm.call": "单次 LLM 调用（latency/tokens/cost_nano；平台代付可带 platform_credential_id）",
    "llm.request": "LLM prompt 截断脱敏（需 LOG_LLM_BODIES）",
    "llm.response": "LLM 回复截断脱敏（需 LOG_LLM_BODIES）",
    "llm.call_failed": (
        "LLM 调用失败（model/credential_source；可取则带 provider_id / platform_credential_id）"
    ),
    "llm.stream_stalled": (
        "LLM 流式空闲超时（model/credential_source；可取则带 provider_id / "
        "platform_credential_id）"
    ),
    "cost.recorded": "回合落账成功（含 by_role 角色拆解）",
    "cost.currency_mixed": (
        "同一钱袋汇总到两种币种（平台模型漏配 curated CNY 卡）；无 FX 不可相加，保留首个"
    ),
    "cost.prompt_assembled": (
        "系统提示装配观测（段 chars + section_digests + assembly_hash；零行为副作用）"
    ),
    "cost.tools_offered": (
        "发给模型的工具 schema JSON 体积（scope=ceo_turn|worker_run；只观测不闸）"
    ),
    "cost.prefix_cache": (
        "前缀缓存实测（hit_ratio 命中率 + breach/breach_section 击穿归因 + "
        "reusable/forfeited；cache_reported=false 表示上游没报缓存，不等于 0% 命中）"
        "；debug 级：默认 LOG_LEVEL=info 查不到，要留行须 LOG_LEVEL=DEBUG"
    ),
    "pipeline.error": "回合管线未捕获异常",
    "http.unhandled_error": "HTTP 层未捕获异常",
    "http.db_pool_exhausted": "主库连接池耗尽（快失败 503，非 PG 宕机）",
    "db.pool_exhausted_snapshot": (
        "连接池枯竭快照：当前持有者上下文/已持时长（非 readiness）"
    ),
    "db.pool_checkout_slow": "连接归还过慢（持有超过阈值；含 checkout 时上下文）",
    "auth.login_failed": "敏感操作审计：登录失败（password/unknown/locked/mfa/role；无明文凭据）",
    "auth.mfa_enrolled": "敏感操作审计：Admin MFA 绑定确认成功",
    "auth.mfa_recovery_used": "敏感操作审计：Admin MFA 恢复码成功消费",
    "security.csrf_rejected": (
        "Cookie 会话变更请求被 CSRF 拒（403）；reason=missing/malformed/expired/"
        "signature_mismatch，带 user_id 可归因到会话"
    ),
    "llm_provider.key_updated": "敏感操作审计：BYOK API Key 轮换保存（无明文）",
    "llm_provider.deleted": "敏感操作审计：BYOK 服务商（含密钥）删除",
    "sidecar.turn_cancel_requested": (
        "桌面 cancel RPC 到达 sidecar（solo blocking 无 coordination.user_stop_* 时的指纹）"
    ),
    "sidecar.turn_cancelled": (
        "本地回合 CancelledError salvage；reason=cancelled_without_rpc 表示非 RPC cancel"
    ),
    "billing.background_byok_provider_error": (
        "后台 chrome 因非重试配置形失败将用户 BYOK 服务商标为 error"
        "（设置页红色徽章；error 字段表示写库失败）"
    ),
    "billing.background_platform_auth_fallback": (
        "后台 chrome 平台 key 被上游 auth 拒绝后一次回落用户 BYOK"
    ),
    "billing.background_quota_skip": (
        "后台 chrome 平台额度用尽而跳过（静默降级，不冒泡用户面）；"
        "declared_recovery_sec = 上游自己申报的恢复秒数，空表示这次拒绝没给日期"
    ),
    "billing.call_quota_refused": (
        "逐调用配额闸拒绝一次平台代付上游调用（云内联与 sidecar 代理同粒度）"
    ),
    "compaction.done": "长对话压缩成功（folded/kept/summary_chars）",
    "compaction.failed": "长对话压缩失败（顶层异常；不推水位）",
    "compaction.timeout": "长对话压缩 LLM 超时（空摘要；不推水位）",
    "compaction.schedule_failed": "压缩调度 due 判定异常",
    "memory.consolidation_failed": (
        "consolidation 失败但保留水位（下轮重选）；error_type = 异常类名，"
        "与 reason 对照可定位是哪一层把上游异常包装丢了分类"
    ),
    "server.started": (
        "服务端启动完成；version（包元数据 semver）+ git_sha（构建期注入）"
        "标明该进程构建来源，与 GET /version 同源"
    ),
    "server.shutdown_teardown_timeout": "lifespan 抢救后的收尾超过 shutdown_teardown_seconds",
    "browser.close_all_timeout": "停机 close_all 超过墙钟上限，放弃等待交重启/reaper",
    "browser.session_open_failed": (
        "浏览器会话握手失败（API 侧）；error / error_type 为包装异常。"
        "runsc 探针失败看 sandboxd.health_failed"
    ),
    "sandboxd.health_failed": (
        "sandboxd 形状探针失败：shape=code（A）或 net（B）；detail 为 runsc/ip 尾部"
    ),
    "compaction.shutdown_timeout": "停机 flush 在飞 fold 超时（best-effort，取消剩余 task）",
    "memory.consolidation_window_dropped": (
        "不可重试 consolidation 失败：推进水位并丢弃本窗口（防 sweeper 无限重选）"
    ),
    "rate_limit.redis_fail_open": (
        "Redis 限流请求中途失败 → fail-open 放行本请求（可告警；与 construct 期 "
        "security.rate_limit_redis_fallback 对偶）"
    ),
    "event_sink.backpressure_drop": (
        "SSE 慢消费者弃最旧帧：首丢立刻一条，之后心跳，订阅结束冲余数"
    ),
    "firehose.backpressure_drop": (
        "IM firehose 慢连接弃最旧帧：首丢立刻一条，之后心跳，订阅结束冲余数"
    ),
    "event_sink.attach": (
        "SSE 消费者 subscribe（连上）；mode=attach|follow|turn，"
        "与同 conversation_id+message_id 的 event_sink.detach 对表"
    ),
    "event_sink.detach": (
        "SSE 消费者 detach（断线/排队无 waiter 等）；already_detached 区分幂等再 detach；"
        "duration_ms=订阅后第几毫秒断开，idle_ms=距上一帧/心跳空闲（看门狗看空闲不是总长）；"
        "mode/http_req_id 与后续 attach 配对"
    ),
    "conversation_stream.watch": (
        "仅 follow 模式（GET /stream?follow=true）开始跟播；attach 不走这条，看 event_sink.attach"
    ),
    "conversation_stream.unwatch": (
        "对话级 SSE 断开；duration_ms=连接总长，idle_ms=距上一帧/心跳（含 : ping）"
    ),
    "http.readyz": "/readyz 首次就绪或从失败恢复（状态翻转才记，避免探针刷屏）",
    "http.readyz_failed": "/readyz 失败（每次 not_ready；database 硬依赖决定 503）",
    "disk.high_watermark": (
        "宿主挂载点用量达到阈值（默认 80%）；/readyz body 可观测但不参与 200/503"
    ),
    "disk.probe_failed": "读磁盘水位失败（水位缺测；同样不翻转 /readyz 状态）",
    "event_loop.lag": "事件环 sleep 超限（默认 ≥250ms）；lag_ms 即卡住多久",
    "event_loop.lag_summary": "事件环 60s 摘要（max_lag_ms / over_threshold；沉默≠没探针）",
    "event_sink.close": (
        "EventSink 真 close（开→关仅一条）；was_detached 区分先前仅断线 vs 仍附着收口"
    ),
    "workspace.index_build_start": "后台代码索引 ensure 开始（IndexMaintainer）",
    "workspace.index_build_complete": (
        "后台代码索引 ensure 完成（duration_ms；可取则带 generation/truncated/files）"
    ),
    "workspace.index_skip_channel_busy": (
        "Local channel 仍忙，跳过本轮索引并 coalesce 重试"
    ),
    "workspace.index_failed": "后台代码索引 ensure 失败（带 error/duration_ms）",
    "sidecar.warm_code_index": "静默暖代码索引（initialize / warmCodeIndex RPC schedule）",
    "sidecar.warm_mcp_discover": "静默暖 MCP 列表进进程缓存（warmMcpDiscover RPC seed）",
    "sidecar.warm_account_rules_memory": (
        "静默暖账户 rules/memory 进 prepare 快照缓存（warmAccountRulesMemory）"
    ),
    "sidecar.warm_account_rules_memory_failed": "warmAccountRulesMemory 拉取失败",
    "account.rules_memory_cache_hit": "prepare rules/memory 命中进程快照缓存",
    "account.rules_memory_cache_miss": (
        "prepare rules/memory 只读缓存未命中（空注入；不 await 云；"
        "origin=execution_harvest 时为收口空注入）"
    ),
    "account.rules_memory_cache_seed": "账户 rules/memory 快照写入进程缓存（非回合暖）",
    "account.rules_memory_warm_failed": "warm 拉取 rules/memory 部分失败（degraded seed）",
    "attention.signalled": (
        "「在等你」信号已发（阻塞卡）；push_outcome = delivered / "
        "undelivered / skipped_mobile_online / not_requested，pushed 只在真有设备收下时为 true"
    ),
    "stream_state.retention_swept": "流式在飞快照超保留期扫表删除的行数（对齐 paused_turns 7 天）",
    "stream_state.retention_failed": "流式在飞快照 TTL 扫表整轮失败",
    "push.fcm_configured": "FCM sender 装配成功；project_id 须与真机注册的 Firebase 项目一致",
    "push.fcm_token_minted": (
        "服务账号 JWT 换 OAuth2 access token 成功（凭据可用；此后未达即非凭据问题）"
    ),
    "push.fcm_sent": (
        "FCM 已接收该设备的推送；message_id 可在 FCM 控制台续查「发了但没到」"
    ),
    "push.fcm_token_stale": "FCM 报 token 失效（404 / UNREGISTERED）→ 剪掉该设备",
    "push.skipped": "推送未发出（reason=unconfigured 未配置推送 / no_devices 无注册设备）",
    "push.notified": (
        "用户级推送扇出结果；accepted=0 表示一台都没送出（区分「压根没发」与「发了但没到」）"
    ),
    "journal.live_seq_near_overflow": (
        "live-band seq 逼近或越过 overflow 段起点；只告警，不改分配"
    ),
    "journal.sealed_drop": (
        "pause 封盘后 execution 终态帧无法写入（无 event loop / 仍封的 host）；"
        "不允许静默丢弃"
    ),
    "journal.sealed_overflow": "pause 封盘后的 run_*/execution_* 终态转到未封 overflow writer",
    "journal.sealed_skip": "pause 快照流在 seal 后被拒绝追加（trailing *_required 等，有意定格）",
    "sidecar.outbox_ready_overflow": "outbox 已 READY 仍追加 execution 终态（pause 快照定格、终态不丢）",
    "sidecar.outbox_ready_skip": "outbox 已 READY，非 execution 终态的 journal append 被跳过",
    "roster.conversation_evicted": (
        "空闲 TTL 清掉另一会话的进程内 roster；victim 记 evicted_conversation_id，"
        "不写 canonical conversation_id（本行发生在驱逐方请求的 contextvars 里）"
    ),
    "search_cache.conversation_evicted": (
        "空闲 TTL 清掉另一会话的检索缓存；victim 记 evicted_conversation_id，"
        "不写 canonical conversation_id（本行发生在驱逐方请求的 contextvars 里）"
    ),
    "url_cache.conversation_evicted": (
        "空闲 TTL 清掉另一会话的 URL 缓存；victim 记 evicted_conversation_id，"
        "不写 canonical conversation_id（本行发生在驱逐方请求的 contextvars 里）"
    ),
}


def scan_events() -> set[str]:
    events: set[str] = set()
    for path in AGENTCORE.rglob("*.py"):
        if "observability" in path.parts and path.name in {"catalog.py", "events.py"}:
            continue
        # utf-8-sig: a BOM makes ``ast.parse`` raise, and swallowing that used to
        # drop every event in the file without a word — 7 files were invisible.
        # An unparseable source file now fails the sync loudly for the same reason.
        try:
            tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        except SyntaxError as e:
            raise SystemExit(f"无法解析 {path}（日志事件会被静默漏登记）：{e}") from e
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute) or func.attr not in LEVELS:
                continue
            if not node.args:
                continue
            arg0 = node.args[0]
            if isinstance(arg0, ast.Constant) and isinstance(arg0.value, str):
                name = arg0.value
                if NAME_RE.fullmatch(name):
                    events.add(name)
    return events


def _display_width(s: str) -> int:
    return sum(2 if unicodedata.east_asian_width(c) in ("F", "W") else 1 for c in s)


def _format_description_arg(indent: str, desc: str) -> list[str]:
    """Emit ``description=...`` lines, each ≤ ``_LINE_LIMIT`` display cols."""
    single = f"{indent}description={desc!r},"
    if _display_width(single) <= _LINE_LIMIT:
        return [single]
    # Parenthesized implicit string concat so long CJK desc stays under limit.
    inner = indent + "    "
    lines = [f"{indent}description=("]
    remaining = desc
    while remaining:
        lo, hi, best = 1, len(remaining), 1
        while lo <= hi:
            mid = (lo + hi) // 2
            candidate = f"{inner}{remaining[:mid]!r}"
            if _display_width(candidate) <= _LINE_LIMIT:
                best = mid
                lo = mid + 1
            else:
                hi = mid - 1
        lines.append(f"{inner}{remaining[:best]!r}")
        remaining = remaining[best:]
    lines.append(f"{indent}),")
    return lines


def _format_spec(name: str) -> list[str]:
    """Emit one EventSpec as one or more lines (keep ≤100 cols)."""
    desc = KEY_DESC.get(name, "")
    fields = KEY_FIELDS.get(name, {})
    if not fields and not desc:
        return [f"    EventSpec(name={name!r}),"]
    if not fields:
        one = f"    EventSpec(name={name!r}, description={desc!r}),"
        if _display_width(one) <= _LINE_LIMIT:
            return [one]
        out = ["    EventSpec(", f"        name={name!r},"]
        out.extend(_format_description_arg("        ", desc))
        out.append("    ),")
        return out
    out = ["    EventSpec(", f"        name={name!r},"]
    if desc:
        out.extend(_format_description_arg("        ", desc))
    out.append("        fields={")
    for k, v in sorted(fields.items()):
        out.append(f"            {k!r}: FieldType({v!r}),")
    out.append("        },")
    out.append("    ),")
    return out


def render_catalog(events: list[str]) -> str:
    lines = [
        '"""Auto-maintained event catalog for product AI logs.',
        "",
        "Source of truth for event *names* currently emitted via ``logger.*``.",
        "Regenerate with::",
        "",
        "    uv run python scripts/sync_log_event_registry.py",
        "",
        "Do not hand-edit the ``EVENTS`` list — add field/description enrichments",
        "via ``KEY_FIELDS`` / ``KEY_DESC`` in the sync script, then re-run.",
        '"""',
        "",
        "from __future__ import annotations",
        "",
        "from agentcore.observability.events import EventSpec, FieldType",
        "",
        "# fields empty means name-only registration.",
        "EVENTS: list[EventSpec] = [",
    ]
    for name in events:
        lines.extend(_format_spec(name))
    lines.append("]")
    lines.append("")
    return "\n".join(lines) + "\n"


def write_catalog(events: list[str]) -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(render_catalog(events), encoding="utf-8")


_CATALOG_NAME_RE = re.compile(r"\bname=(['\"])([^'\"]+)\1")


def catalog_names_from_text(text: str) -> list[str]:
    return [m.group(2) for m in _CATALOG_NAME_RE.finditer(text)]


def planned_catalog(scanned: set[str]) -> tuple[list[str], list[str]]:
    """Return (sorted catalog names, dead enrichment names)."""
    events = sorted(scanned | set(HISTORICAL_COMPAT))
    known = scanned | set(HISTORICAL_COMPAT)
    dead = sorted((set(KEY_FIELDS) | set(KEY_DESC)) - known)
    return events, dead


def apply_historical_descriptions() -> None:
    for name, desc in HISTORICAL_COMPAT.items():
        KEY_DESC.setdefault(name, desc)


def check_catalog() -> int:
    """Compare emit sites to catalog.py without rewriting it."""
    scanned = scan_events()
    events, dead = planned_catalog(scanned)
    apply_historical_descriptions()
    expected = render_catalog(events)
    actual = OUT.read_text(encoding="utf-8") if OUT.is_file() else ""
    expected_names = events
    actual_names = catalog_names_from_text(actual)
    missing = sorted(set(expected_names) - set(actual_names))
    extra = sorted(set(actual_names) - set(expected_names))
    text_differs = expected != actual

    if not missing and not extra and not text_differs and not dead:
        print(
            f"✓ log event catalog matches emit sites "
            f"({len(events)} events; {len(HISTORICAL_COMPAT)} historical-compat)"
        )
        return 0

    print("✗ log event catalog drift — catalog.py does not match logger.* emit sites:")
    if missing:
        print(f"  missing from catalog ({len(missing)}; emitted in code):")
        for name in missing:
            print(f"    + {name}")
    if extra:
        print(
            f"  extra in catalog ({len(extra)}; no emit site, not HISTORICAL_COMPAT):"
        )
        for name in extra:
            print(f"    - {name}")
    if text_differs and not missing and not extra:
        print(
            "  catalog.py text differs from the sync renderer "
            "(order / wrapping / descriptions); names match"
        )
        if actual_names != expected_names:
            print("  name order drift (disk → renderer):")
            shown = 0
            for disk_name, want_name in zip(actual_names, expected_names, strict=False):
                if disk_name == want_name:
                    continue
                print(f"    {disk_name!r} → {want_name!r}")
                shown += 1
                if shown >= 8:
                    break
        disk_specs = {
            name: "\n".join(_format_spec(name)) for name in expected_names
        }
        actual_text = actual
        spec_drift = [
            name
            for name in expected_names
            if disk_specs[name] not in actual_text
        ]
        if spec_drift:
            print(f"  EventSpec body drift ({len(spec_drift)}):")
            for name in spec_drift[:8]:
                print(f"    ~ {name}")
    if not OUT.is_file():
        print(f"  catalog missing: {OUT}")
    for name in dead:
        print(f"  WARNING: enrichment for {name!r} has no emit site (dead name?)")
    print("  Fix: uv run python scripts/sync_log_event_registry.py")
    print("  Gate mode is --check only; it never rewrites catalog.py.")
    return 1


def write_catalog_from_scan() -> int:
    scanned = scan_events()
    events, dead = planned_catalog(scanned)
    for name in dead:
        print(f"WARNING: enrichment for {name!r} has no emit site (dead name?)")
    apply_historical_descriptions()
    write_catalog(events)
    print(
        f"wrote {OUT} ({len(events)} events; {len(HISTORICAL_COMPAT)} historical-compat)"
    )
    return 1 if dead else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Scan logger.* call sites and regenerate observability/catalog.py, "
            "or --check that the committed catalog matches emit sites (read-only)."
        )
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Fail on drift; never write catalog.py (release gate / CI)",
    )
    args = parser.parse_args(argv)
    if args.check:
        return check_catalog()
    return write_catalog_from_scan()


if __name__ == "__main__":
    sys.exit(main())
