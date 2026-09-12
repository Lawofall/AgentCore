from datetime import UTC, datetime

from agentcore.doc.share import freeze_share_snapshot, render_doc_share_html


def test_freeze_drops_unknown_block_types():
    frozen = freeze_share_snapshot(
        {
            "blocks": [
                {"type": "paragraph", "text": "可见"},
                {"type": "html", "text": "<script>x</script>"},
            ]
        }
    )
    types = [b["type"] for b in frozen["blocks"]]
    assert types == ["paragraph"]


def test_render_table_escapes_headers_and_cells():
    html = render_doc_share_html(
        title="表",
        snapshot={
            "blocks": [
                {
                    "type": "table",
                    "columns": ["<script>h</script>", "列2"],
                    "rows": [
                        ["<script>c</script>", "正常\n换行"],
                        ["a", "b", "ignored-extra"],
                    ],
                }
            ]
        },
        created_at=None,
    )
    assert "<script" not in html
    assert "&lt;script&gt;h&lt;/script&gt;" in html
    assert "&lt;script&gt;c&lt;/script&gt;" in html
    assert "<table class=\"doc-table\">" in html
    assert "<th>" in html and "<td>" in html
    assert "<br>" in html
    assert "换行" in html
    assert "ignored-extra" not in html


def test_render_chart_bar_svg_escapes_labels():
    html = render_doc_share_html(
        title="图",
        snapshot={
            "blocks": [
                {
                    "type": "chart",
                    "kind": "bar",
                    "title": "<script>t</script>",
                    "items": [
                        {"label": "<script>x</script>", "value": 10},
                        {"label": "正常", "value": 5},
                    ],
                }
            ]
        },
        created_at=None,
    )
    assert "<script" not in html
    assert "&lt;script&gt;t&lt;/script&gt;" in html
    assert "&lt;script&gt;x&lt;/script&gt;" in html
    assert '<figure class="doc-chart">' in html
    assert '<svg class="doc-chart-svg"' in html
    assert "<rect " in html
    assert "正常" in html


def test_render_escapes_title_and_block_text():
    html = render_doc_share_html(
        title="<script>alert(1)</script>",
        snapshot={
            "schemaVersion": 1,
            "blocks": [
                {"type": "heading", "level": 1, "text": "<img src=x onerror=alert(1)>"},
                {"type": "paragraph", "text": "<script>alert('p')</script>\n第二行"},
                {
                    "type": "list",
                    "ordered": False,
                    "items": ["<b>粗</b>", "正常"],
                },
            ],
        },
        created_at=datetime(2026, 9, 12, 12, 0, tzinfo=UTC),
    )
    assert "<script" not in html
    assert "<img" not in html
    assert "<b>粗</b>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in html
    assert "&lt;script&gt;alert(" in html
    assert "<br>" in html
    assert "第二行" in html
    assert "&lt;b&gt;粗&lt;/b&gt;" in html
    assert "noindex, nofollow" in html
    assert "2026-09-12" in html
