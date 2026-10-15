from datetime import UTC, datetime

from agentcore.doc.share import freeze_share_snapshot, render_doc_share_html


def test_freeze_keeps_markdown_and_drops_legacy_blocks():
    frozen = freeze_share_snapshot({"markdown": "可见"})
    assert frozen == {"markdown": "可见"}
    assert freeze_share_snapshot(
        {"blocks": [{"type": "paragraph", "text": "旧稿"}]}
    ) == {"markdown": ""}


def test_render_table_and_escapes_html():
    html = render_doc_share_html(
        title="表",
        snapshot={
            "markdown": "| <script>h</script> | 列2 |\n| --- | --- |\n| <script>c</script> | 正常 |"
        },
        created_at=None,
    )
    assert "<script" not in html
    assert "&lt;script&gt;h&lt;/script&gt;" in html
    assert "&lt;script&gt;c&lt;/script&gt;" in html
    assert "<table>" in html
    assert "<th>" in html and "<td>" in html
    assert "正常" in html
    assert 'class="site"' in html
    assert 'class="hero"' in html


def test_render_does_not_emit_user_images():
    html = render_doc_share_html(
        title="图",
        snapshot={"markdown": "![跟踪](https://evil.example/t.png)"},
        created_at=None,
    )
    assert "<img" not in html
    assert "跟踪" in html


def test_render_escapes_title_and_markdown_html():
    html = render_doc_share_html(
        title="<script>alert(1)</script>",
        snapshot={
            "markdown": "<img src=x onerror=alert(1)>\n\n<script>alert('p')</script>\n第二行\n\n**粗**"
        },
        created_at=datetime(2026, 9, 12, 12, 0, tzinfo=UTC),
    )
    assert "<script" not in html
    assert "<img" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in html
    assert "&lt;script&gt;alert(" in html
    assert "第二行" in html
    assert "<strong>粗</strong>" in html
    assert "noindex, nofollow" in html
    assert "2026-09-12" in html
    assert "由 GAgent 发布" in html


def test_render_toc_from_headings_and_escapes():
    html = render_doc_share_html(
        title="方案",
        snapshot={
            "markdown": "## 结论\n\n正文\n\n## <script>x</script>\n\n### 细节\n"
        },
        created_at=None,
    )
    assert 'aria-label="目录"' in html
    assert 'href="#结论"' in html
    assert ">结论</a>" in html
    assert "&lt;script&gt;x&lt;/script&gt;" in html
    assert "细节" in html
    assert 'id="结论"' in html
    assert 'class="layout has-toc"' in html
    assert "<script" not in html


def test_render_skips_toc_when_fewer_than_two_headings():
    html = render_doc_share_html(
        title="短",
        snapshot={"markdown": "只有一段，没有标题。"},
        created_at=None,
    )
    assert 'aria-label="目录"' not in html
    assert 'class="layout has-toc"' not in html
    html_one = render_doc_share_html(
        title="短",
        snapshot={"markdown": "## 仅一节\n\n正文"},
        created_at=None,
    )
    assert 'aria-label="目录"' not in html_one
    assert 'class="layout has-toc"' not in html_one
