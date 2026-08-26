"""Tests for the static web_quality_scan gate (GEO-style frontend quality P0 + P1a DESIGN)."""

from agentcore.runtime.runs.contract import check_contract
from agentcore.runtime.runs.types import Deliverable
from agentcore.runtime.runs.web_quality_rules import soft_rule_labels
from agentcore.runtime.runs.web_quality_scan import scan_web_quality
from agentcore.runtime.runs.website_style import STYLE_ID_HEADING

_MIN_DESIGN = (
    f"# Design\n\n## {STYLE_ID_HEADING}\ns0\n\n"
    "## Tokens\n--color-primary: #1a1a2e;\n--color-accent: #e94560;\n"
)


def test_soft_rule_labels_cover_scan_catalog():
    labels = soft_rule_labels()
    assert "emoji当图标" in labels
    assert "紫蓝渐变+glow默认皮" in labels


def test_hard_broken_css_declaration_comma():
    css = ".hero { color: #ffffff,; margin: 0; }"
    result = scan_web_quality(
        {"site/styles.css": css, "site/DESIGN.md": _MIN_DESIGN}
    )
    assert any("坏CSS声明" in f for f in result.failures)


def test_hard_fake_400_phone_and_icp():
    html = (
        "<html><body>"
        "<p>咨询热线 400-888-0000</p>"
        "<footer>京ICP备2025000001号</footer>"
        "</body></html>"
    )
    result = scan_web_quality(
        {"site/index.html": html, "site/DESIGN.md": _MIN_DESIGN}
    )
    assert any("编造400电话" in f for f in result.failures)
    assert any("编造ICP备案" in f for f in result.failures)


def test_soft_emoji_icon_and_purple_gradient():
    html = (
        '<html><body>'
        '<span class="feature-icon">🚀</span>'
        '<style>.hero{background:linear-gradient(90deg,#7c3aed,#3b82f6);'
        "box-shadow:0 0 24px #8b5cf6;}</style>"
        "</body></html>"
    )
    # Purple hexes are also scattered vs DESIGN tokens → hard; soft still fires.
    design = (
        f"# Design\n\n## {STYLE_ID_HEADING}\ns0\n\n"
        "## Tokens\n--color-primary: #7c3aed;\n--color-accent: #3b82f6;\n"
        "--glow: #8b5cf6;\n"
    )
    result = scan_web_quality({"site/index.html": html, "site/DESIGN.md": design})
    assert any("emoji当图标" in f for f in result.soft_failures)
    assert any("紫蓝渐变+glow默认皮" in f for f in result.soft_failures)
    assert result.failures == []


def test_hard_missing_design_md_only_with_contract():
    html = "<html><body><p>ok</p></body></html>"
    auto = scan_web_quality({"site/index.html": html})
    assert not any("缺DESIGN.md" in f for f in auto.failures)
    flagged = scan_web_quality({"site/index.html": html}, design_contract=True)
    assert any("缺DESIGN.md" in f for f in flagged.failures)


def test_hard_missing_style_id_in_design():
    design = "# Design\n\n## Tokens\n--color-primary: #1a1a2e;\n"
    result = scan_web_quality(
        {
            "site/index.html": "<html><body></body></html>",
            "site/DESIGN.md": design,
        },
        design_contract=True,
    )
    assert any("缺选定风格id" in f for f in result.failures)


def test_hard_scattered_color_not_in_tokens():
    design = _MIN_DESIGN
    css = ".hero { color: #6366f1; }"
    result = scan_web_quality(
        {"site/styles.css": css, "site/DESIGN.md": design},
        design_contract=True,
    )
    assert any("实现散色" in f for f in result.failures)


def test_scattered_color_ok_when_in_tokens():
    design = _MIN_DESIGN
    css = ".hero { color: #1a1a2e; background: #fff; }"
    result = scan_web_quality(
        {"site/styles.css": css, "site/DESIGN.md": design},
        design_contract=True,
    )
    assert not any("实现散色" in f for f in result.failures)


def test_var_fallback_hex_not_scattered():
    """Catalog ``var(--token, #fallback)`` must not hard-fail as 散色 (GEO r4b)."""
    design = _MIN_DESIGN
    css = (
        ":root {\n"
        "  --color-bg: var(--design-bg, #ffffff);\n"
        "  --color-fg: var(--design-fg, #111111);\n"
        "  --color-muted: var(--design-muted, #666666);\n"
        "  --color-surface: var(--design-surface, #f5f5f5);\n"
        "  --color-border: var(--design-border, #e5e5e5);\n"
        "}\n"
    )
    result = scan_web_quality(
        {"site/styles.css": css, "site/DESIGN.md": design},
        design_contract=True,
    )
    assert not any("实现散色" in f for f in result.failures)


def test_bare_hex_still_scattered_beside_var_fallback():
    design = _MIN_DESIGN
    css = (
        ":root { --color-fg: var(--design-fg, #111111); }\n"
        ".hero { color: #6366f1; }\n"
    )
    result = scan_web_quality(
        {"site/styles.css": css, "site/DESIGN.md": design},
        design_contract=True,
    )
    assert any("实现散色" in f for f in result.failures)
    assert any("#6366f1" in f for f in result.failures)


def test_contract_web_quality_auto_scans_without_flag():
    html = "<html><body><p>call 400-888-0000</p></body></html>"
    v = check_contract(
        "ok",
        Deliverable(form="files", artifacts=["site/index.html"]),
        files_written=1,
        artifact_contents={"site/index.html": html},
        workspace_paths=["site/index.html"],
    )
    assert not v.ok
    assert any("编造400电话" in f for f in v.failures)
    assert not any("缺DESIGN.md" in f for f in v.failures)


def test_contract_web_quality_design_hard_only_with_flag():
    html = "<html><body><p>ok</p></body></html>"
    no_flag = check_contract(
        "ok",
        Deliverable(form="files", artifacts=["site/index.html"]),
        files_written=1,
        artifact_contents={"site/index.html": html},
        workspace_paths=["site/index.html"],
    )
    assert not any("缺DESIGN.md" in f for f in no_flag.failures)
    flagged = check_contract(
        "ok",
        Deliverable(
            form="files",
            artifacts=["site/index.html"],
            web_quality_scan=True,
        ),
        files_written=1,
        artifact_contents={"site/index.html": html},
        workspace_paths=["site/index.html"],
    )
    assert not flagged.ok
    assert any("缺DESIGN.md" in f for f in flagged.failures)


def test_contract_web_quality_hard_fails_when_enabled():
    html = "<html><body><p>call 400-888-0000</p></body></html>"
    v = check_contract(
        "ok",
        Deliverable(
            form="files",
            artifacts=["site/index.html"],
            web_quality_scan=True,
        ),
        files_written=1,
        artifact_contents={"site/index.html": html, "site/DESIGN.md": _MIN_DESIGN},
        workspace_paths=["site/index.html", "site/DESIGN.md"],
    )
    assert not v.ok
    assert any("编造400电话" in f for f in v.failures)


def test_contract_web_quality_soft_flips_ok_for_retry():
    html = (
        '<html><body><span class="feature-icon">✨</span></body></html>'
    )
    v = check_contract(
        "ok",
        Deliverable(
            form="files",
            artifacts=["site/index.html"],
            web_quality_scan=True,
        ),
        files_written=1,
        artifact_contents={"site/index.html": html, "site/DESIGN.md": _MIN_DESIGN},
        workspace_paths=["site/index.html", "site/DESIGN.md"],
    )
    assert not v.ok
    assert v.failures == []
    assert any("emoji当图标" in f for f in v.soft_failures)


def test_soft_exempt_skips_anti_slop():
    html = '<html><body><span class="feature-icon">🚀</span></body></html>'
    result = scan_web_quality(
        {"site/index.html": html, "site/DESIGN.md": _MIN_DESIGN},
        soft_exempt=True,
    )
    assert result.soft_failures == []


def test_unreplaced_mustache_hard_on_fill_phase():
    html = '<html><body><h2>{{section_title}}</h2></body></html>'
    result = scan_web_quality(
        {"site/index.html": html, "site/DESIGN.md": _MIN_DESIGN},
    )
    assert any("未替换模板槽" in f for f in result.failures)


def test_unreplaced_mustache_allowed_on_skeleton_soft_exempt():
    html = '<html><body><h2>{{section_title}}</h2></body></html>'
    result = scan_web_quality(
        {"site/index.html": html, "site/DESIGN.md": _MIN_DESIGN},
        soft_exempt=True,
    )
    assert not any("未替换模板槽" in f for f in result.failures)
    assert result.soft_failures == []
