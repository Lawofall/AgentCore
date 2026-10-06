"""Cost ledger data access: per-call details + per-run aggregates.

``cost_calls`` is the authority; ``cost_events`` is the per-run materialized view
product surfaces read (工资单 / 仪表盘 / 配额). Writes go through the shared
Postgres ``cost_ledger_outbox`` (:class:`~agentcore.billing.cost_ledger_queue.CostLedgerQueue`)
for at-least-once durability.
"""

from collections.abc import Collection, Sequence
from datetime import datetime
from typing import Literal

from sqlalchemy import case, distinct, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from agentcore.core.types import new_id
from agentcore.costing import run_cost_from_calls
from agentcore.db.models import CostCall, CostEvent, User
from agentcore.llm.pricing import CURRENCY_CNY

from ._base import _json_int, _sum_int

CredentialSourceApi = Literal["user", "platform"]


def _fold_call_models_and_source(
    rows: Sequence[CostCall],
) -> tuple[list[str], CredentialSourceApi | None]:
    """Dedupe models (first-seen) + fold credential_source from cost JSONB.

    No rows → ``([], None)``. Rows present with missing ``credential_source`` key →
    ``platform`` (ledger ``split_cost`` default). ``vendor`` coerces to ``platform``
    for the admin API surface (``user|platform|null``). Mixed → ``user`` if any
    call is user, else ``platform``.
    """
    if not rows:
        return [], None
    models: list[str] = []
    seen: set[str] = set()
    any_user = False
    for row in rows:
        model = (row.model or "").strip()
        if model and model not in seen:
            seen.add(model)
            models.append(model)
        raw = (row.cost or {}).get("credential_source") or "platform"
        if str(raw) == "user":
            any_user = True
    return models, ("user" if any_user else "platform")


def _platform_prepaid_call() -> ColumnElement:
    """``cost_calls`` that hit the platform key — not BYOK, not vendor extras.

    Missing ``credential_source`` follows ledger ``split_cost`` (defaults to
    platform). This is「平台付的钱」, not「走了 Go」. Go-window reads further
    restrict to pool members whose persisted ``base_url`` is the Go endpoint.
    """
    src = func.coalesce(CostCall.cost["credential_source"].astext, "platform")
    return src == "platform"


def _run_row_values(
    *,
    user_id: str,
    conversation_id: str | None,
    message_id: str | None,
    runs: Sequence[dict],
    trace_id: str | None,
) -> list[dict]:
    return [
        {
            "id": new_id(),
            "user_id": user_id,
            "conversation_id": conversation_id or None,
            "message_id": message_id,
            "run_id": r["run_id"],
            "parent_run_id": r.get("parent_run_id"),
            "agent_id": r.get("agent_id"),
            "role": r.get("role", "member"),
            "persona": (str(r["persona"]).strip() if r.get("persona") else None),
            "model": r.get("model", ""),
            "tokens": r.get("tokens") or {},
            "cost": r.get("cost") or {},
            "cost_total_nano": int(r.get("cost_total_nano", 0)),
            "cost_estimated_nano": int(r.get("cost_estimated_nano", 0)),
            "currency": r.get("currency", "CNY"),
            "rounds": int(r.get("rounds", 0)),
            "duration_ms": int(r.get("duration_ms", 0)),
            "trace_id": trace_id,
        }
        for r in runs
    ]


class CostEventRepository:
    """Append-only cost ledger (per-call details + per-run aggregates).

    Product reads (payroll / dashboard / quota) hit ``cost_events``. Call details
    live in ``cost_calls`` and are the authority for proxy metering; per-run rows
    may be dual-written at finalize or materialized from calls.

    ``conversation_id`` is optional on every write: an account-level chrome call
    (AI 改写, ``role=assist``) belongs to no conversation and is
    written with NULL. The account-window reads below (which is what 用量页 /
    仪表盘 / ``enforce_quota`` use) therefore include it, while the
    conversation-scoped and per-message reads exclude it by construction.
    """

    def __init__(self, session: AsyncSession):
        self._session = session

    async def record_runs(
        self,
        *,
        user_id: str,
        conversation_id: str | None,
        message_id: str | None,
        runs: Sequence[dict],
        trace_id: str | None = None,
    ) -> int:
        """Append per-run aggregate rows; idempotent by ``run_id`` (DO NOTHING).

        Used by cloud finalize / handoff when the run aggregate is already known.
        Proxy materialization uses :meth:`upsert_runs_from_calls` instead so
        growing call counts replace the aggregate.
        """
        if not runs:
            return 0
        rows = _run_row_values(
            user_id=user_id,
            conversation_id=conversation_id,
            message_id=message_id,
            runs=runs,
            trace_id=trace_id,
        )
        stmt = pg_insert(CostEvent).values(rows).on_conflict_do_nothing(index_elements=["run_id"])
        result = await self._session.execute(stmt)
        await self._session.commit()
        return result.rowcount or 0

    async def record_calls(
        self,
        *,
        user_id: str,
        conversation_id: str | None,
        message_id: str | None,
        calls: Sequence[dict],
        trace_id: str | None = None,
        materialize_runs: bool = False,
    ) -> int:
        """Append per-call detail rows; idempotent by ``call_id``.

        When ``materialize_runs`` is true (proxy path), also upserts ``cost_events``
        by re-aggregating all calls for each touched ``run_id`` so the product
        view stays in sync without a separate finalize write.
        """
        if not calls:
            return 0
        rows = [
            {
                "id": new_id(),
                "user_id": user_id,
                "conversation_id": conversation_id or None,
                "message_id": message_id,
                "call_id": c["call_id"],
                "run_id": c["run_id"],
                "parent_run_id": c.get("parent_run_id"),
                "agent_id": c.get("agent_id"),
                "role": c.get("role", "member"),
                "persona": (str(c["persona"]).strip() if c.get("persona") else None),
                "model": c.get("model", ""),
                "tokens": c.get("tokens") or {},
                "cost": c.get("cost") or {},
                "cost_total_nano": int(c.get("cost_total_nano", 0)),
                "cost_estimated_nano": int(c.get("cost_estimated_nano", 0)),
                "currency": c.get("currency", "CNY"),
                "duration_ms": int(c.get("duration_ms", 0)),
                "trace_id": trace_id,
                "platform_credential_id": (
                    str(c["platform_credential_id"]).strip()
                    if c.get("platform_credential_id")
                    else None
                ),
            }
            for c in calls
        ]
        stmt = pg_insert(CostCall).values(rows).on_conflict_do_nothing(index_elements=["call_id"])
        result = await self._session.execute(stmt)
        written = result.rowcount or 0
        if materialize_runs:
            run_ids = sorted({str(c["run_id"]) for c in calls if c.get("run_id")})
            await self._materialize_runs_for(
                user_id=user_id,
                conversation_id=conversation_id,
                message_id=message_id,
                run_ids=run_ids,
                trace_id=trace_id,
            )
        await self._session.commit()
        return written

    async def _materialize_runs_for(
        self,
        *,
        user_id: str,
        conversation_id: str | None,
        message_id: str | None,
        run_ids: Sequence[str],
        trace_id: str | None,
    ) -> None:
        """Recompute ``cost_events`` for ``run_ids`` from ``cost_calls`` (upsert)."""
        if not run_ids:
            return
        result = await self._session.execute(
            select(CostCall).where(
                CostCall.user_id == user_id,
                CostCall.run_id.in_(list(run_ids)),
            )
        )
        by_run: dict[str, list[CostCall]] = {}
        for row in result.scalars().all():
            by_run.setdefault(row.run_id, []).append(row)

        aggregates: list[dict] = []
        for run_id in run_ids:
            call_rows = by_run.get(run_id) or []
            if not call_rows:
                continue
            # Stable order for first-call attribution.
            call_rows.sort(key=lambda r: (r.created_at, r.call_id))
            payloads = [
                {
                    "call_id": r.call_id,
                    "run_id": r.run_id,
                    "parent_run_id": r.parent_run_id,
                    "agent_id": r.agent_id,
                    "role": r.role,
                    "persona": r.persona,
                    "model": r.model,
                    "tokens": r.tokens or {},
                    "cost": r.cost or {},
                    "cost_total_nano": int(r.cost_total_nano or 0),
                    "cost_estimated_nano": int(getattr(r, "cost_estimated_nano", 0) or 0),
                    "currency": r.currency or "CNY",
                    "duration_ms": int(r.duration_ms or 0),
                }
                for r in call_rows
            ]
            agg = run_cost_from_calls(payloads)
            if agg is None:
                continue
            aggregates.append(
                {
                    "run_id": agg.run_id,
                    "parent_run_id": agg.parent_run_id,
                    "agent_id": agg.agent_id,
                    "role": agg.role,
                    "persona": agg.persona,
                    "model": agg.model,
                    "tokens": agg.tokens,
                    "cost": agg.cost,
                    "cost_total_nano": agg.cost_total_nano,
                    "cost_estimated_nano": agg.cost_estimated_nano,
                    "currency": agg.currency,
                    "rounds": agg.rounds,
                    "duration_ms": agg.duration_ms,
                }
            )
        if not aggregates:
            return
        rows = _run_row_values(
            user_id=user_id,
            conversation_id=conversation_id,
            message_id=message_id,
            runs=aggregates,
            trace_id=trace_id,
        )
        insert_stmt = pg_insert(CostEvent).values(rows)
        upsert = insert_stmt.on_conflict_do_update(
            index_elements=["run_id"],
            set_={
                "parent_run_id": insert_stmt.excluded.parent_run_id,
                "agent_id": insert_stmt.excluded.agent_id,
                "role": insert_stmt.excluded.role,
                "persona": insert_stmt.excluded.persona,
                "model": insert_stmt.excluded.model,
                "tokens": insert_stmt.excluded.tokens,
                "cost": insert_stmt.excluded.cost,
                "cost_total_nano": insert_stmt.excluded.cost_total_nano,
                "cost_estimated_nano": insert_stmt.excluded.cost_estimated_nano,
                "currency": insert_stmt.excluded.currency,
                "rounds": insert_stmt.excluded.rounds,
                "duration_ms": insert_stmt.excluded.duration_ms,
                "message_id": insert_stmt.excluded.message_id,
                "trace_id": insert_stmt.excluded.trace_id,
            },
        )
        await self._session.execute(upsert)

    async def materialize_message_runs(
        self,
        *,
        user_id: str,
        conversation_id: str | None,
        message_id: str,
        trace_id: str | None = None,
    ) -> set[str]:
        """Upsert ``cost_events`` for every ``run_id`` that has ``cost_calls`` on this message.

        Cloud finalize uses this so worker / captain / debate spend metered onto
        ``cost_calls`` (call authority) always lands in the product view — even when
        the in-memory ``cost_runs`` fold missed members (pause/resume / coordination).
        Returns the set of run ids that had at least one call row.
        """
        result = await self._session.execute(
            select(CostCall.run_id).where(
                CostCall.user_id == user_id,
                CostCall.message_id == message_id,
            )
        )
        run_ids = sorted({str(rid) for rid in result.scalars().all() if rid})
        if not run_ids:
            await self._session.commit()
            return set()
        # Prefer message-scoped calls when aggregating so a reused run_id cannot
        # pull another turn's tokens into this payroll (defensive; run_ids are
        # normally unique).
        calls = (
            (
                await self._session.execute(
                    select(CostCall).where(
                        CostCall.user_id == user_id,
                        CostCall.message_id == message_id,
                        CostCall.run_id.in_(run_ids),
                    )
                )
            )
            .scalars()
            .all()
        )
        by_run: dict[str, list[CostCall]] = {}
        for row in calls:
            by_run.setdefault(row.run_id, []).append(row)

        aggregates: list[dict] = []
        for run_id in run_ids:
            call_rows = by_run.get(run_id) or []
            if not call_rows:
                continue
            call_rows.sort(key=lambda r: (r.created_at, r.call_id))
            payloads = [
                {
                    "call_id": r.call_id,
                    "run_id": r.run_id,
                    "parent_run_id": r.parent_run_id,
                    "agent_id": r.agent_id,
                    "role": r.role,
                    "persona": r.persona,
                    "model": r.model,
                    "tokens": r.tokens or {},
                    "cost": r.cost or {},
                    "cost_total_nano": int(r.cost_total_nano or 0),
                    "cost_estimated_nano": int(getattr(r, "cost_estimated_nano", 0) or 0),
                    "currency": r.currency or "CNY",
                    "duration_ms": int(r.duration_ms or 0),
                }
                for r in call_rows
            ]
            agg = run_cost_from_calls(payloads)
            if agg is None:
                continue
            aggregates.append(
                {
                    "run_id": agg.run_id,
                    "parent_run_id": agg.parent_run_id,
                    "agent_id": agg.agent_id,
                    "role": agg.role,
                    "persona": agg.persona,
                    "model": agg.model,
                    "tokens": agg.tokens,
                    "cost": agg.cost,
                    "cost_total_nano": agg.cost_total_nano,
                    "cost_estimated_nano": agg.cost_estimated_nano,
                    "currency": agg.currency,
                    "rounds": agg.rounds,
                    "duration_ms": agg.duration_ms,
                }
            )
        if aggregates:
            rows = _run_row_values(
                user_id=user_id,
                conversation_id=conversation_id,
                message_id=message_id,
                runs=aggregates,
                trace_id=trace_id,
            )
            insert_stmt = pg_insert(CostEvent).values(rows)
            upsert = insert_stmt.on_conflict_do_update(
                index_elements=["run_id"],
                set_={
                    "parent_run_id": insert_stmt.excluded.parent_run_id,
                    "agent_id": insert_stmt.excluded.agent_id,
                    "role": insert_stmt.excluded.role,
                    "persona": insert_stmt.excluded.persona,
                    "model": insert_stmt.excluded.model,
                    "tokens": insert_stmt.excluded.tokens,
                    "cost": insert_stmt.excluded.cost,
                    "cost_total_nano": insert_stmt.excluded.cost_total_nano,
                    "cost_estimated_nano": insert_stmt.excluded.cost_estimated_nano,
                    "currency": insert_stmt.excluded.currency,
                    "rounds": insert_stmt.excluded.rounds,
                    "duration_ms": insert_stmt.excluded.duration_ms,
                    "message_id": insert_stmt.excluded.message_id,
                    "trace_id": insert_stmt.excluded.trace_id,
                },
            )
            await self._session.execute(upsert)
        await self._session.commit()
        return set(run_ids)

    async def list_for_message(self, message_id: str, *, user_id: str) -> Sequence[CostEvent]:
        """The per-run rows for one assistant turn — the team payroll (工资单).

        Scoped by ``user_id`` so a non-owner gets an empty list (never another
        user's spend, and no message-existence leak). Ordered oldest-first so the
        captain root (written first) heads the payroll. Off-turn and account-level
        rows carry ``message_id = NULL`` and so never land on any payroll.
        """
        result = await self._session.execute(
            select(CostEvent)
            .where(CostEvent.message_id == message_id, CostEvent.user_id == user_id)
            .order_by(CostEvent.created_at.asc())
        )
        return result.scalars().all()

    async def _aggregate(self, *conditions: ColumnElement) -> dict:
        """SUM tokens/cost/rounds + distinct-turn count over the given filter.

        One round-trip returns the whole rollup the cost endpoints need. Token
        and cost-breakdown components live in JSONB (summed via cast); the turn
        total uses the redundant ``cost_total_nano`` scalar column (precise, and
        index-friendly for the account window). ``turns`` counts distinct
        ``message_id`` — the「请求/回合」proxy for the conversation total + quota;
        NULL is ignored, so off-turn and account-level rows add their money and
        tokens without inflating the request count.
        """
        billed = CostEvent.cost_total_nano > 0
        estimated = CostEvent.cost_estimated_nano > 0
        stmt = select(
            _sum_int(_json_int(CostEvent.tokens, "input")).label("t_input"),
            _sum_int(_json_int(CostEvent.tokens, "output")).label("t_output"),
            _sum_int(_json_int(CostEvent.tokens, "reasoning")).label("t_reasoning"),
            _sum_int(_json_int(CostEvent.tokens, "cache_hit")).label("t_cache_hit"),
            _sum_int(_json_int(CostEvent.tokens, "cache_miss")).label("t_cache_miss"),
            _sum_int(case((billed, _json_int(CostEvent.cost, "input")), else_=0)).label("c_input"),
            _sum_int(case((billed, _json_int(CostEvent.cost, "cached")), else_=0)).label(
                "c_cached"
            ),
            _sum_int(case((billed, _json_int(CostEvent.cost, "output")), else_=0)).label(
                "c_output"
            ),
            _sum_int(CostEvent.cost_total_nano).label("c_total"),
            _sum_int(case((estimated, _json_int(CostEvent.cost, "input")), else_=0)).label(
                "e_input"
            ),
            _sum_int(case((estimated, _json_int(CostEvent.cost, "cached")), else_=0)).label(
                "e_cached"
            ),
            _sum_int(case((estimated, _json_int(CostEvent.cost, "output")), else_=0)).label(
                "e_output"
            ),
            _sum_int(CostEvent.cost_estimated_nano).label("c_estimated"),
            # Each bucket's currency, read off the rows. Both columns are CNY
            # (same curated card); they stay unmerged so quota SUM only sees billed.
            func.max(case((billed, CostEvent.currency))).label("c_currency"),
            func.max(case((estimated, CostEvent.currency))).label("e_currency"),
            _sum_int(CostEvent.rounds).label("rounds"),
            func.count(distinct(CostEvent.message_id)).label("turns"),
        ).where(*conditions)
        row = (await self._session.execute(stmt)).one()
        return {
            "usage": {
                "input": int(row.t_input),
                "output": int(row.t_output),
                "reasoning": int(row.t_reasoning),
                "cache_hit": int(row.t_cache_hit),
                "cache_miss": int(row.t_cache_miss),
            },
            "cost": {
                "input": int(row.c_input),
                "cached": int(row.c_cached),
                "output": int(row.c_output),
                "total": int(row.c_total),
                "currency": str(row.c_currency or CURRENCY_CNY),
                "pricing_source": "curated",
            },
            "estimated_cost": {
                "input": int(row.e_input),
                "cached": int(row.e_cached),
                "output": int(row.e_output),
                "total": int(row.c_estimated),
                "currency": str(row.e_currency or CURRENCY_CNY),
                "pricing_source": "estimated",
            },
            "rounds": int(row.rounds),
            "turns": int(row.turns),
        }

    async def aggregate_for_conversation(self, conversation_id: str, *, user_id: str) -> dict:
        """Cumulative spend for one conversation (对话累计).

        Account-level rows (NULL conversation) are excluded by the equality
        filter — they belong to no conversation, so no conversation may claim
        them. Their money shows on the account windows below instead.
        """
        return await self._aggregate(
            CostEvent.conversation_id == conversation_id,
            CostEvent.user_id == user_id,
        )

    async def aggregate_for_window(self, *, user_id: str | None = None, since: datetime) -> dict:
        """Spend since a cutoff. ``user_id`` scopes to one account (account dashboard
        today / month window, hits ``ix_cost_events_user_created``); ``None``
        aggregates platform-wide (admin 全站用量看板 — every account).

        Deliberately unfiltered on ``conversation_id``: this is the account's
        total, so it must include account-level rows (AI 改写)
        as well as every conversation's. Same query backs ``enforce_quota``, so
        that spend counts against the cap it was billed under.
        """
        conditions: list[ColumnElement] = [CostEvent.created_at >= since]
        if user_id is not None:
            conditions.append(CostEvent.user_id == user_id)
        return await self._aggregate(*conditions)

    async def aggregate_by_model_for_window(
        self, *, user_id: str | None = None, since: datetime
    ) -> list[dict]:
        """Per-model call spend since a cutoff (各模型调用次数 / tokens / 成本).

        **Must** scan ``cost_calls`` (``GROUP BY model``). Do **not** aggregate
        ``cost_events.model`` — that column only records the run's first call, so
        multi-model runs would mis-attribute spend. ``user_id`` scopes to one
        account; ``None`` is platform-wide (admin 全站看板). Account-level calls
        are included (a window total, not a conversation one).
        """
        total = _sum_int(CostCall.cost_total_nano)
        estimated = _sum_int(CostCall.cost_estimated_nano)
        tokens_per_call = (
            func.coalesce(_json_int(CostCall.tokens, "input"), 0)
            + func.coalesce(_json_int(CostCall.tokens, "output"), 0)
            + func.coalesce(_json_int(CostCall.tokens, "reasoning"), 0)
        )
        calls = func.count().label("calls")
        conditions: list[ColumnElement] = [CostCall.created_at >= since]
        if user_id is not None:
            conditions.append(CostCall.user_id == user_id)
        stmt = (
            select(
                CostCall.model.label("model"),
                calls,
                _sum_int(tokens_per_call).label("tokens_total"),
                total.label("c_total"),
                estimated.label("c_estimated"),
            )
            .where(*conditions)
            .group_by(CostCall.model)
            .order_by(total.desc(), estimated.desc(), calls.desc())
        )
        rows = (await self._session.execute(stmt)).all()
        return [
            {
                "model": row.model,
                "calls": int(row.calls),
                "tokens_total": int(row.tokens_total),
                "cost_total": int(row.c_total),
                "cost_estimated_total": int(row.c_estimated),
            }
            for row in rows
        ]

    async def aggregate_daily_for_window(
        self, *, user_id: str | None = None, since: datetime
    ) -> dict[str, int]:
        """Daily spend (UTC days) since a cutoff — the dashboard 7-day trend sparkline.

        Groups the window into UTC calendar days and SUMs ``cost_total_nano`` per
        day, returning an ``{iso_date: nano_total}`` map (only days that had rows).
        The caller zero-fills the absent days so the series is a fixed length. The
        day key is computed in UTC (``created_at AT TIME ZONE 'UTC'``) to match the
        account window boundaries. ``user_id`` scopes to one account (hits
        ``ix_cost_events_user_created``); ``None`` is platform-wide (admin 看板).
        """
        day = func.date_trunc("day", func.timezone("UTC", CostEvent.created_at))
        conditions: list[ColumnElement] = [CostEvent.created_at >= since]
        if user_id is not None:
            conditions.append(CostEvent.user_id == user_id)
        stmt = (
            select(
                day.label("day"),
                _sum_int(CostEvent.cost_total_nano).label("c_total"),
            )
            .where(*conditions)
            .group_by(day)
        )
        rows = (await self._session.execute(stmt)).all()
        return {row.day.date().isoformat(): int(row.c_total) for row in rows}

    async def aggregate_by_user_for_window(self, *, since: datetime, limit: int = 20) -> list[dict]:
        """Per-user spend since a cutoff — the platform 工资单 by user (admin 全站看板).

        Groups the (platform-wide) window by ``user_id`` and SUMs the scalar
        ``cost_total_nano`` plus a distinct-turn count, joining ``users`` for the
        display identity. Only accounts that actually spent (>0) are returned,
        ordered by spend desc and capped at ``limit`` (Top spenders) — no user
        filter, this is the whole platform. Money is integer nano-CNY; clients
        format ¥ as ``cost_total / 1e9``.

        The ``users`` join is on ``user_id``, which every ledger row carries, so
        account-level rows count toward their owner's total here too.
        """
        total = _sum_int(CostEvent.cost_total_nano)
        stmt = (
            select(
                CostEvent.user_id.label("user_id"),
                User.username.label("username"),
                User.display_name.label("display_name"),
                total.label("c_total"),
                func.count(distinct(CostEvent.message_id)).label("turns"),
            )
            .join(User, User.user_id == CostEvent.user_id)
            .where(CostEvent.created_at >= since)
            .group_by(CostEvent.user_id, User.username, User.display_name)
            .having(total > 0)
            .order_by(total.desc())
            .limit(limit)
        )
        rows = (await self._session.execute(stmt)).all()
        return [
            {
                "user_id": row.user_id,
                "username": row.username,
                "display_name": row.display_name,
                "cost_total": int(row.c_total),
                "turns": int(row.turns),
            }
            for row in rows
        ]

    async def aggregate_cost_by_message_for_conversation(
        self, conversation_id: str
    ) -> dict[str, int]:
        """Per-turn spend for one conversation, keyed by ``message_id`` (会话复盘).

        Groups the conversation's ledger rows by the assistant turn they belong to
        (``message_id``) and SUMs the scalar ``cost_total_nano``. Off-turn rows
        (标题/记忆, ``message_id`` NULL) are excluded — they belong to no turn. The
        admin 复盘 view attaches each turn's ¥ from this map (``nano / 1e9``; no FX).
        """
        stmt = (
            select(
                CostEvent.message_id.label("message_id"),
                _sum_int(CostEvent.cost_total_nano).label("c_total"),
            )
            .where(
                CostEvent.conversation_id == conversation_id,
                CostEvent.message_id.is_not(None),
            )
            .group_by(CostEvent.message_id)
        )
        rows = (await self._session.execute(stmt)).all()
        return {row.message_id: int(row.c_total) for row in rows}

    async def models_and_source_by_message_for_conversation(
        self, conversation_id: str
    ) -> dict[str, tuple[list[str], CredentialSourceApi | None]]:
        """Per-message models + credential_source from ``cost_calls`` (会话复盘).

        Groups call rows by ``message_id`` (NULL / off-turn excluded). Models are
        distinct first-seen; credential_source folds per ``_fold_call_models_and_source``.
        """
        result = await self._session.execute(
            select(CostCall)
            .where(
                CostCall.conversation_id == conversation_id,
                CostCall.message_id.is_not(None),
            )
            .order_by(CostCall.created_at.asc())
        )
        by_message: dict[str, list[CostCall]] = {}
        for row in result.scalars().all():
            mid = row.message_id
            if mid is None:
                continue
            by_message.setdefault(mid, []).append(row)
        return {mid: _fold_call_models_and_source(calls) for mid, calls in by_message.items()}

    async def models_and_source_by_trace(
        self, trace_ids: Sequence[str]
    ) -> dict[str, tuple[list[str], CredentialSourceApi | None]]:
        """Per-``trace_id`` models + credential_source from ``cost_calls`` (回合列表).

        ``turn_metrics.turn_id`` ≠ assistant ``message_id`` — join cost by trace only.
        """
        ids = [t for t in dict.fromkeys(trace_ids) if t]
        if not ids:
            return {}
        result = await self._session.execute(
            select(CostCall).where(CostCall.trace_id.in_(ids)).order_by(CostCall.created_at.asc())
        )
        by_trace: dict[str, list[CostCall]] = {}
        for row in result.scalars().all():
            tid = row.trace_id
            if not tid:
                continue
            by_trace.setdefault(tid, []).append(row)
        return {tid: _fold_call_models_and_source(calls) for tid, calls in by_trace.items()}

    async def aggregate_cost_by_conversations(
        self, conversation_ids: Sequence[str]
    ) -> dict[str, int]:
        """All-time spend per conversation, keyed by ``conversation_id`` (admin roster).

        One GROUP BY over the given ids so the 对话 page enriches each row without
        an N+1. Ids with no ledger rows are absent (callers default to 0).
        Account-level rows (NULL conversation) can never match an id in the list.
        """
        if not conversation_ids:
            return {}
        stmt = (
            select(
                CostEvent.conversation_id.label("conversation_id"),
                _sum_int(CostEvent.cost_total_nano).label("c_total"),
            )
            .where(CostEvent.conversation_id.in_(conversation_ids))
            .group_by(CostEvent.conversation_id)
        )
        rows = (await self._session.execute(stmt)).all()
        return {row.conversation_id: int(row.c_total) for row in rows}

    async def list_platform_prepaid_call_spend(
        self, *, platform_credential_ids: Collection[str]
    ) -> list[tuple[datetime, int]]:
        """Go-window ``(created_at, cost_total_nano)`` oldest-first.

        ``platform_credential_ids`` must already be the Go-endpoint pool
        members (exact preset match). Empty → no rows. Admin-only full scan.
        """
        tagged = await self.list_platform_prepaid_call_spend_tagged(
            platform_credential_ids=platform_credential_ids
        )
        return [(ts, amount) for ts, amount, _cid, _tokens, _model in tagged]

    async def list_platform_prepaid_call_spend_tagged(
        self, *, platform_credential_ids: Collection[str]
    ) -> list[tuple[datetime, int, str | None, dict, str]]:
        """Go-window series plus credential id, ``tokens``, and ``model``.

        Only platform-prepaid rows whose ``platform_credential_id`` is in
        ``platform_credential_ids``. Caller supplies the Go-endpoint set —
        this method does not guess by model name or URL prefix.
        """
        if not platform_credential_ids:
            return []
        stmt = (
            select(
                CostCall.created_at,
                CostCall.cost_total_nano,
                CostCall.platform_credential_id,
                CostCall.tokens,
                CostCall.model,
            )
            .where(_platform_prepaid_call())
            .where(CostCall.platform_credential_id.in_(platform_credential_ids))
            .order_by(CostCall.created_at.asc())
        )
        rows = (await self._session.execute(stmt)).all()
        return [
            (
                row.created_at,
                int(row.cost_total_nano or 0),
                (str(row.platform_credential_id).strip() if row.platform_credential_id else None),
                dict(row.tokens or {}),
                str(row.model or ""),
            )
            for row in rows
        ]
