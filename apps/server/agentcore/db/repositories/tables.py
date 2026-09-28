"""Creation-tool 多维表格 data access (account-scoped, IDOR → None)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agentcore.db.models.tables import Table, TableRow, TableView
from agentcore.db.repositories._base import commit_or_flush
from agentcore.table.schema import (
    blank_seed,
    empty_view_config,
    new_id,
    sanitize_cells,
    sanitize_columns,
    sanitize_view,
)
from agentcore.table.state import TableState


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


class TableRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def create(self, *, user_id: str, title: str, commit: bool = True) -> TableState:
        schema, rows, view = blank_seed(title)
        table = Table(
            user_id=user_id,
            title=title,
            columns_schema=schema,
            schema_version=1,
        )
        self._session.add(table)
        await self._session.flush()
        for raw in rows:
            self._session.add(
                TableRow(
                    id=raw["id"],
                    table_id=table.id,
                    cells=raw["cells"],
                    position=raw["position"],
                )
            )
        self._session.add(
            TableView(
                id=view["id"],
                table_id=table.id,
                name=view["name"],
                display_mode=view["display_mode"],
                config=view["config"],
                is_default=True,
            )
        )
        # fk_tables_active_view_id is checked immediately; the view row must exist first.
        await self._session.flush()
        table.active_view_id = view["id"]
        await commit_or_flush(self._session, commit=commit)
        await self._session.refresh(table)
        return await self.get_state(table.id, user_id=user_id)  # type: ignore[return-value]

    async def create_from_csv(
        self,
        *,
        user_id: str,
        title: str,
        columns: list[dict[str, Any]],
        rows: list[dict[str, Any]],
        workspace_key: str,
        path: str,
        commit: bool = True,
    ) -> TableState:
        columns = sanitize_columns(columns)
        view_id = new_id()
        table = Table(
            user_id=user_id,
            title=title,
            columns_schema={"columns": columns},
            schema_version=1,
            source_workspace_key=workspace_key,
            source_path=path,
        )
        self._session.add(table)
        await self._session.flush()
        for raw in rows:
            self._session.add(
                TableRow(
                    id=raw["id"],
                    table_id=table.id,
                    cells=sanitize_cells(raw.get("cells"), columns),
                    position=float(raw.get("position") or 1000),
                )
            )
        self._session.add(
            TableView(
                id=view_id,
                table_id=table.id,
                name="表格",
                display_mode="table",
                config=empty_view_config(),
                is_default=True,
            )
        )
        await self._session.flush()
        table.active_view_id = view_id
        await commit_or_flush(self._session, commit=commit)
        await self._session.refresh(table)
        return await self.get_state(table.id, user_id=user_id)  # type: ignore[return-value]

    async def replace_csv_snapshot(
        self,
        state: TableState,
        *,
        title: str,
        columns: list[dict[str, Any]],
        rows: list[dict[str, Any]],
        commit: bool = True,
    ) -> TableState:
        columns = sanitize_columns(columns)
        state.title = title
        state.columns = columns
        state.rows = [
            {
                "id": raw["id"],
                "cells": sanitize_cells(raw.get("cells"), columns),
                "position": float(raw.get("position") or 1000),
            }
            for raw in rows
        ]
        state.schema_version = int(state.schema_version) + 1
        state.undo_batch = None
        cleaned: list[dict[str, Any]] = []
        for view in state.views:
            item = sanitize_view(view, columns)
            if item is not None:
                cleaned.append(item)
        if not cleaned:
            vid = new_id()
            cleaned = [
                {
                    "id": vid,
                    "name": "表格",
                    "display_mode": "table",
                    "config": empty_view_config(),
                    "is_default": True,
                }
            ]
            state.active_view_id = vid
        elif state.active_view_id not in {v["id"] for v in cleaned}:
            state.active_view_id = cleaned[0]["id"]
        state.views = cleaned
        await self.persist_state(state, commit=commit)
        return await self.get_state(state.id, user_id=state.user_id)  # type: ignore[return-value]

    async def get_by_id(self, table_id: str, *, user_id: str) -> Table | None:
        result = await self._session.execute(
            select(Table).where(
                Table.id == table_id,
                Table.user_id == user_id,
                Table.deleted_at.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def get_by_conversation_id(
        self, conversation_id: str, *, user_id: str
    ) -> Table | None:
        result = await self._session.execute(
            select(Table).where(
                Table.conversation_id == conversation_id,
                Table.user_id == user_id,
                Table.deleted_at.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def get_by_source(
        self, *, user_id: str, workspace_key: str, path: str
    ) -> Table | None:
        result = await self._session.execute(
            select(Table).where(
                Table.user_id == user_id,
                Table.source_workspace_key == workspace_key,
                Table.source_path == path,
                Table.deleted_at.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def get_state(self, table_id: str, *, user_id: str) -> TableState | None:
        table = await self.get_by_id(table_id, user_id=user_id)
        if not table:
            return None
        row_q = await self._session.execute(
            select(TableRow)
            .where(TableRow.table_id == table_id, TableRow.deleted_at.is_(None))
            .order_by(TableRow.position)
        )
        view_q = await self._session.execute(
            select(TableView).where(
                TableView.table_id == table_id, TableView.deleted_at.is_(None)
            )
        )
        rows = row_q.scalars().all()
        views = view_q.scalars().all()
        columns = sanitize_columns((table.columns_schema or {}).get("columns"))
        return TableState(
            id=table.id,
            user_id=table.user_id,
            title=table.title,
            columns=columns,
            rows=[
                {
                    "id": r.id,
                    "cells": dict(r.cells or {}),
                    "position": float(r.position),
                    "updated_at": _iso(r.updated_at),
                }
                for r in rows
            ],
            views=[
                {
                    "id": v.id,
                    "name": v.name,
                    "display_mode": v.display_mode,
                    "config": dict(v.config or {}),
                    "is_default": bool(v.is_default),
                }
                for v in views
            ],
            active_view_id=table.active_view_id or (views[0].id if views else ""),
            schema_version=table.schema_version,
            conversation_id=table.conversation_id,
            source_path=table.source_path,
            undo_batch=dict(table.undo_batch) if table.undo_batch else None,
            created_at=_iso(table.created_at),
            updated_at=_iso(table.updated_at),
        )

    async def list_by_user(self, user_id: str) -> list[dict[str, Any]]:
        live = (
            select(TableRow.table_id, func.count(TableRow.id).label("n"))
            .where(TableRow.deleted_at.is_(None))
            .group_by(TableRow.table_id)
            .subquery()
        )
        result = await self._session.execute(
            select(Table, live.c.n)
            .outerjoin(live, live.c.table_id == Table.id)
            .where(Table.user_id == user_id, Table.deleted_at.is_(None))
            .order_by(Table.updated_at.desc())
        )
        out: list[dict[str, Any]] = []
        for table, n in result.all():
            out.append(
                {
                    "id": table.id,
                    "title": table.title,
                    "conversation_id": table.conversation_id,
                    "schema_version": table.schema_version,
                    "row_count": int(n or 0),
                    "source_path": table.source_path,
                    "created_at": table.created_at,
                    "updated_at": table.updated_at,
                }
            )
        return out

    async def summary_of(self, table: Table) -> dict[str, Any]:
        n = await self._session.scalar(
            select(func.count(TableRow.id)).where(
                TableRow.table_id == table.id,
                TableRow.deleted_at.is_(None),
            )
        )
        return {
            "id": table.id,
            "title": table.title,
            "conversation_id": table.conversation_id,
            "schema_version": table.schema_version,
            "row_count": int(n or 0),
            "source_path": table.source_path,
            "created_at": table.created_at,
            "updated_at": table.updated_at,
        }

    async def persist_state(
        self,
        state: TableState,
        *,
        commit: bool = True,
        touched_row_ids: frozenset[str] | None = None,
        deleted_row_ids: frozenset[str] = frozenset(),
    ) -> None:
        table = await self.get_by_id(state.id, user_id=state.user_id)
        if not table:
            return
        table.title = state.title
        table.columns_schema = {"columns": state.columns}
        table.schema_version = state.schema_version
        table.undo_batch = state.undo_batch
        table.updated_at = datetime.now(UTC)
        stamp = datetime.now(UTC)
        existing_rows = {
            r.id: r
            for r in (
                await self._session.execute(
                    select(TableRow).where(TableRow.table_id == state.id)
                )
            )
            .scalars()
            .all()
        }
        keep_rows = {raw["id"] for raw in state.rows}
        row_filter = touched_row_ids
        for raw in state.rows:
            if row_filter is not None and raw["id"] not in row_filter:
                continue
            row = existing_rows.get(raw["id"])
            cells = sanitize_cells(raw.get("cells"), state.columns)
            if row is None:
                self._session.add(
                    TableRow(
                        id=raw["id"],
                        table_id=state.id,
                        cells=cells,
                        position=float(raw.get("position") or 1000),
                        updated_at=stamp,
                        deleted_at=None,
                    )
                )
            else:
                row.cells = cells
                row.position = float(raw.get("position") or row.position)
                row.updated_at = stamp
                row.deleted_at = None
        if row_filter is None:
            for rid, row in existing_rows.items():
                if rid not in keep_rows and row.deleted_at is None:
                    row.deleted_at = datetime.now(UTC)
        else:
            for rid in deleted_row_ids:
                row = existing_rows.get(rid)
                if row is not None and row.deleted_at is None:
                    row.deleted_at = datetime.now(UTC)
        existing_views = {
            v.id: v
            for v in (
                await self._session.execute(
                    select(TableView).where(TableView.table_id == state.id)
                )
            )
            .scalars()
            .all()
        }
        keep_views = set()
        for raw in state.views:
            keep_views.add(raw["id"])
            view = existing_views.get(raw["id"])
            if view is None:
                self._session.add(
                    TableView(
                        id=raw["id"],
                        table_id=state.id,
                        name=raw.get("name") or "未命名视图",
                        display_mode=raw.get("display_mode") or "table",
                        config=raw.get("config") or {},
                        is_default=bool(raw.get("is_default")),
                        deleted_at=None,
                    )
                )
            else:
                view.name = raw.get("name") or view.name
                view.display_mode = raw.get("display_mode") or view.display_mode
                view.config = raw.get("config") or view.config
                view.is_default = bool(raw.get("is_default"))
                view.deleted_at = None
        for vid, view in existing_views.items():
            if vid not in keep_views and view.deleted_at is None:
                view.deleted_at = datetime.now(UTC)
        await self._session.flush()
        table.active_view_id = state.active_view_id or None
        await self._realign_undo_stamps(table, state)
        await commit_or_flush(self._session, commit=commit)

    async def _realign_undo_stamps(self, table: Table, state: TableState) -> None:
        """Rewrite undo ``row_stamps`` from DB ``updated_at`` after flush.

        In-memory ISO stamps from ``apply_ops`` will not match PostgreSQL
        ``timestamptz`` round-trips; undo compares them strictly.
        """
        if not state.undo_batch:
            table.undo_batch = None
            return
        result = await self._session.execute(
            select(TableRow).where(
                TableRow.table_id == state.id, TableRow.deleted_at.is_(None)
            )
        )
        stamps = {r.id: _iso(r.updated_at) for r in result.scalars().all()}
        batch = dict(state.undo_batch)
        batch["row_stamps"] = stamps
        state.undo_batch = batch
        table.undo_batch = batch

    async def update_title(
        self, table_id: str, *, user_id: str, title: str, commit: bool = True
    ) -> Table | None:
        table = await self.get_by_id(table_id, user_id=user_id)
        if not table:
            return None
        table.title = title
        table.updated_at = datetime.now(UTC)
        await commit_or_flush(self._session, commit=commit)
        return table

    async def attach_conversation(
        self,
        table_id: str,
        *,
        user_id: str,
        conversation_id: str,
        commit: bool = True,
    ) -> Table | None:
        table = await self.get_by_id(table_id, user_id=user_id)
        if not table:
            return None
        table.conversation_id = conversation_id
        await commit_or_flush(self._session, commit=commit)
        return table

    async def soft_delete(self, table_id: str, *, user_id: str, commit: bool = True) -> bool:
        table = await self.get_by_id(table_id, user_id=user_id)
        if not table:
            return False
        now = datetime.now(UTC)
        table.deleted_at = now
        await commit_or_flush(self._session, commit=commit)
        return True

    async def soft_delete_all_for_user(self, user_id: str, *, commit: bool = True) -> None:
        result = await self._session.execute(
            select(Table).where(Table.user_id == user_id, Table.deleted_at.is_(None))
        )
        now = datetime.now(UTC)
        for table in result.scalars().all():
            table.deleted_at = now
        await commit_or_flush(self._session, commit=commit)
