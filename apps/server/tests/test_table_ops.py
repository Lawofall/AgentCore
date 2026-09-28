"""In-memory table apply_ops / ``<表格>`` envelope — no PostgreSQL."""

from __future__ import annotations

from agentcore.runtime.resolve.prompt import (
    TURN_ENVELOPE_FENCE,
    compose_ceo_chat_prompt,
    render_ceo_turn_envelope,
)
from agentcore.table.context import render_table_section
from agentcore.table.ops import apply_ops
from agentcore.table.schema import blank_seed, new_id
from agentcore.table.state import TableState


def _blank(title: str = "任务") -> TableState:
    schema, rows, view = blank_seed(title)
    return TableState(
        id="t1",
        user_id="u1",
        title=title,
        columns=schema["columns"],
        rows=rows,
        views=[view],
        active_view_id=view["id"],
        schema_version=1,
    )


def test_l1_update_cells_applies_without_confirm():
    state = _blank()
    rid = state.rows[0]["id"]
    col = state.columns[0]["id"]
    result = apply_ops(
        state,
        [{"op": "update_cells", "row_id": rid, "cells": {col: "hello"}}],
        confirm=False,
    )
    assert result.ok
    assert result.needs_confirmation is False
    assert result.level == "L1"
    assert result.state is not None
    assert result.state.rows[0]["cells"][col] == "hello"
    assert state.rows[0]["cells"][col] == ""


def test_l2_add_column_dry_run_does_not_change_state():
    state = _blank()
    before = [c["id"] for c in state.columns]
    result = apply_ops(
        state,
        [{"op": "add_column", "label": "备注", "type": "text"}],
        confirm=False,
    )
    assert result.ok
    assert result.needs_confirmation is True
    assert result.level == "L2"
    assert [c["id"] for c in state.columns] == before
    assert result.state is state


def test_l3_insert_row_dry_run_does_not_change_state():
    state = _blank()
    n = len(state.rows)
    result = apply_ops(
        state,
        [{"op": "upsert_rows", "rows": [{}]}],
        confirm=False,
    )
    assert result.ok
    assert result.needs_confirmation is True
    assert result.level == "L3"
    assert len(state.rows) == n


def test_l3_insert_with_client_id_dry_run():
    state = _blank()
    n = len(state.rows)
    result = apply_ops(
        state,
        [{"op": "upsert_rows", "rows": [{"id": "new-row", "cells": {}}]}],
        confirm=False,
    )
    assert result.ok
    assert result.needs_confirmation is True
    assert result.level == "L3"
    assert len(state.rows) == n


def test_upsert_existing_row_is_l1():
    state = _blank()
    rid = state.rows[0]["id"]
    col = state.columns[0]["id"]
    result = apply_ops(
        state,
        [{"op": "upsert_rows", "rows": [{"id": rid, "cells": {col: "x"}}]}],
        confirm=False,
    )
    assert result.ok
    assert result.needs_confirmation is False
    assert result.level == "L1"
    assert result.state is not None
    assert result.touched_row_ids == frozenset({rid})


def test_undo_batch_restores_l1_cell():
    state = _blank()
    rid = state.rows[0]["id"]
    col = state.columns[0]["id"]
    applied = apply_ops(
        state,
        [{"op": "update_cells", "row_id": rid, "cells": {col: "x"}}],
        confirm=False,
    )
    assert applied.state is not None
    undone = apply_ops(applied.state, [{"op": "undo_batch"}], confirm=False)
    assert undone.ok
    assert undone.state is not None
    assert undone.state.rows[0]["cells"][col] == ""


def test_row_5001_fails_honestly():
    state = _blank()
    extra = [
        {"id": f"r{i}", "cells": {}, "position": 2000.0 + i}
        for i in range(4999)
    ]
    state.rows = list(state.rows) + extra
    assert len(state.rows) == 5000
    result = apply_ops(
        state,
        [{"op": "upsert_rows", "rows": [{}]}],
        confirm=True,
    )
    assert result.ok is False
    assert "一张表最多 5000 行" in (result.error or "")
    assert len(state.rows) == 5000


def test_same_batch_add_column_then_fill_unordered():
    state = _blank()
    rid = state.rows[0]["id"]
    new_col = new_id()
    result = apply_ops(
        state,
        [
            {"op": "update_cells", "row_id": rid, "cells": {new_col: "hello"}},
            {"op": "add_column", "id": new_col, "label": "备注", "type": "text"},
        ],
        confirm=True,
    )
    assert result.ok
    assert result.needs_confirmation is False
    assert result.state is not None
    assert any(c["id"] == new_col for c in result.state.columns)
    assert result.state.rows[0]["cells"].get(new_col) == "hello"


def test_empty_table_context_does_not_change_compose_bytes():
    bare = compose_ceo_chat_prompt("BASE", ceo_tool_names=set())
    assert compose_ceo_chat_prompt("BASE", ceo_tool_names=set()) == bare


def test_compose_renders_table_context():
    out = render_ceo_turn_envelope(
        table_context="<表格>\n选中 1 行\n</表格>",
        include_runtime=False,
    )
    assert "<表格>" in out


def test_build_chat_places_table_after_attachment():
    out = render_ceo_turn_envelope(
        attachment_context="<附件/>",
        table_context="<表格/>",
        include_runtime=False,
    )
    assert out == f"{TURN_ENVELOPE_FENCE}\n<附件/>\n<表格/>"


def test_empty_selection_omits_selected_block():
    text = render_table_section(_blank(), selected_rows=[])
    assert "<表格>" in text
    assert "选中" not in text


def test_delete_view_and_undo():
    state = _blank()
    saved = apply_ops(
        state,
        [{"op": "save_view", "name": "按状态", "display_mode": "table"}],
        confirm=True,
    )
    assert saved.state is not None
    assert len(saved.state.views) == 2
    created = saved.state.active_view_id
    default = next(v["id"] for v in saved.state.views if v["id"] != created)
    deleted = apply_ops(
        saved.state,
        [{"op": "delete_view", "view_id": created}],
        confirm=True,
    )
    assert deleted.ok
    assert deleted.state is not None
    assert [v["id"] for v in deleted.state.views] == [default]
    undone = apply_ops(deleted.state, [{"op": "undo_batch"}], confirm=True)
    assert undone.ok
    assert undone.state is not None
    assert any(v["id"] == created for v in undone.state.views)
    assert undone.state.active_view_id == created


def test_cannot_delete_last_view():
    state = _blank()
    result = apply_ops(
        state,
        [{"op": "delete_view", "view_id": state.active_view_id}],
        confirm=True,
    )
    assert result.ok is False
    assert "至少保留一个视图" in (result.error or "")


def test_save_view_undo_restores_active():
    state = _blank()
    original = state.active_view_id
    saved = apply_ops(
        state,
        [{"op": "save_view", "name": "副本"}],
        confirm=True,
    )
    assert saved.state is not None
    assert saved.state.active_view_id != original
    undone = apply_ops(saved.state, [{"op": "undo_batch"}], confirm=True)
    assert undone.state is not None
    assert undone.state.active_view_id == original
    assert len(undone.state.views) == 1


def test_update_column_can_change_type():
    state = _blank()
    cid = state.columns[0]["id"]
    assert state.columns[0]["type"] == "text"
    result = apply_ops(
        state,
        [{"op": "update_column", "column_id": cid, "type": "singleSelect"}],
        confirm=True,
    )
    assert result.ok
    assert result.state is not None
    col = next(c for c in result.state.columns if c["id"] == cid)
    assert col["type"] == "singleSelect"
    assert col["options"]
    undone = apply_ops(result.state, [{"op": "undo_batch"}], confirm=True)
    assert undone.state is not None
    restored = next(c for c in undone.state.columns if c["id"] == cid)
    assert restored["type"] == "text"


def test_set_view_persists_column_widths():
    state = _blank()
    cid = state.columns[0]["id"]
    result = apply_ops(
        state,
        [{"op": "set_view", "column_widths": {cid: 240}}],
        confirm=True,
    )
    assert result.ok
    assert result.state is not None
    assert result.state.views[0]["config"]["column_widths"][cid] == 240
