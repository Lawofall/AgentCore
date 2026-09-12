from agentcore.doc.body import (
    BODY_SCHEMA_VERSION,
    MAX_BLOCKS,
    MAX_CHART_ITEMS,
    MAX_CHART_LABEL_CHARS,
    MAX_CHART_TITLE_CHARS,
    MAX_CHART_VALUE,
    MAX_TABLE_CELL_CHARS,
    MAX_TABLE_COLUMNS,
    MAX_TABLE_ROWS,
    empty_body,
    sanitize_body,
)


def test_empty_body_shape():
    body = empty_body()
    assert body == {"schemaVersion": BODY_SCHEMA_VERSION, "blocks": []}


def test_sanitize_keeps_heading_paragraph_list_and_drops_unknown():
    body = sanitize_body(
        {
            "schemaVersion": 9,
            "blocks": [
                {"id": "h1", "type": "heading", "level": 2, "text": "结论"},
                {"id": "p1", "type": "paragraph", "text": "正文"},
                {
                    "id": "l1",
                    "type": "list",
                    "ordered": True,
                    "items": ["一", "二"],
                },
                {"id": "x", "type": "chart", "data": [1]},
                {"type": "paragraph", "text": "无 id"},
                "not-a-block",
            ],
        }
    )
    assert body["schemaVersion"] == BODY_SCHEMA_VERSION
    types = [b["type"] for b in body["blocks"]]
    assert types == ["heading", "paragraph", "list", "chart", "paragraph"]
    chart = body["blocks"][3]
    assert chart["kind"] == "bar"
    assert chart["items"] == []
    assert body["blocks"][0]["level"] == 2
    assert body["blocks"][2]["ordered"] is True
    assert body["blocks"][2]["items"] == ["一", "二"]
    assert body["blocks"][3]["id"]


def test_sanitize_clamps_heading_level_and_non_dict_payload():
    body = sanitize_body({"blocks": [{"type": "heading", "level": 9, "text": "x"}]})
    assert body["blocks"][0]["level"] == 1
    assert sanitize_body(None)["blocks"] == []
    assert sanitize_body([])["blocks"] == []


def test_sanitize_table_columns_rows_and_limits():
    long_cell = "x" * (MAX_TABLE_CELL_CHARS + 50)
    many_cols = [f"c{i}" for i in range(MAX_TABLE_COLUMNS + 5)]
    many_rows = [[str(i)] for i in range(MAX_TABLE_ROWS + 5)]
    body = sanitize_body(
        {
            "blocks": [
                {
                    "type": "table",
                    "columns": many_cols,
                    "rows": [["a", "b", "extra"]] + many_rows,
                },
                {"type": "table", "columns": [], "rows": None},
            ]
        }
    )
    t0 = body["blocks"][0]
    assert t0["type"] == "table"
    assert len(t0["columns"]) == MAX_TABLE_COLUMNS
    assert t0["columns"][0] == "c0"
    assert len(t0["rows"]) == MAX_TABLE_ROWS
    assert t0["rows"][0][:3] == ["a", "b", "extra"]
    assert len(t0["rows"][0]) == MAX_TABLE_COLUMNS

    narrow = sanitize_body(
        {
            "blocks": [
                {
                    "type": "table",
                    "columns": ["A", "B"],
                    "rows": [["a", "b", "drop-me"]],
                }
            ]
        }
    )
    assert narrow["blocks"][0]["rows"][0] == ["a", "b"]
    t1 = body["blocks"][1]
    assert t1["columns"] == [""]
    assert t1["rows"] == []

    clipped = sanitize_body(
        {"blocks": [{"type": "table", "columns": ["H"], "rows": [[long_cell]]}]}
    )
    assert len(clipped["blocks"][0]["rows"][0][0]) == MAX_TABLE_CELL_CHARS


def test_sanitize_chart_kind_title_items_and_limits():
    long_title = "t" * (MAX_CHART_TITLE_CHARS + 10)
    long_label = "l" * (MAX_CHART_LABEL_CHARS + 10)
    body = sanitize_body(
        {
            "blocks": [
                {
                    "type": "chart",
                    "kind": "line",
                    "title": "丢整块",
                    "items": [{"label": "a", "value": 1}],
                },
                {
                    "id": "c1",
                    "type": "chart",
                    "title": long_title,
                    "items": [
                        {"label": long_label, "value": 3},
                        {"label": "neg", "value": -5},
                        {"label": "big", "value": MAX_CHART_VALUE * 2},
                        {"label": "nan", "value": "nope"},
                        {"label": "ok", "value": "42"},
                    ],
                },
                {"type": "chart", "items": []},
                {"type": "chart", "kind": "", "items": [{"label": "z", "value": 1}]},
            ]
        }
    )
    assert len(body["blocks"]) == 3
    kept = body["blocks"][0]
    assert kept["id"] == "c1"
    assert kept["kind"] == "bar"
    assert len(kept["title"]) == MAX_CHART_TITLE_CHARS
    assert len(kept["items"]) == 4
    assert kept["items"][0]["label"] == "l" * MAX_CHART_LABEL_CHARS
    assert kept["items"][1]["value"] == 0
    assert kept["items"][2]["value"] == MAX_CHART_VALUE
    assert kept["items"][3]["value"] == 42.0
    assert body["blocks"][1]["items"] == []
    assert body["blocks"][2]["kind"] == "bar"
    assert body["blocks"][2]["items"] == [{"label": "z", "value": 1.0}]

    many = [{"label": str(i), "value": i} for i in range(MAX_CHART_ITEMS + 5)]
    capped = sanitize_body({"blocks": [{"type": "chart", "items": many}]})
    assert len(capped["blocks"][0]["items"]) == MAX_CHART_ITEMS


def test_sanitize_caps_block_count():
    raw = {
        "blocks": [
            {"type": "paragraph", "text": str(i)} for i in range(MAX_BLOCKS + 20)
        ]
    }
    assert len(sanitize_body(raw)["blocks"]) == MAX_BLOCKS
