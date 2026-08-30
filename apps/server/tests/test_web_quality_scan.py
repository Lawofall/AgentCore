"""Tests for the static web quality gate (syntax / fake contacts / anti-slop)."""

from agentcore.runtime.runs.contract import check_contract
from agentcore.runtime.runs.types import Deliverable
from agentcore.runtime.runs.web_quality_rules import soft_rule_labels
from agentcore.runtime.runs.web_quality_scan import scan_web_quality


def test_soft_rule_labels_cover_scan_catalog():
    labels = soft_rule_labels()
    assert "emoji当图标" in labels
    assert "紫蓝渐变+glow默认皮" in labels


def test_hard_broken_css_declaration_comma():
    css = ".hero { color: #ffffff,; margin: 0; }"
    result = scan_web_quality({"site/styles.css": css})
    assert any("坏CSS声明" in f for f in result.failures)


def test_hard_fake_400_phone_and_icp():
    html = (
        "<html><body>"
        "<p>咨询热线 400-888-0000</p>"
        "<footer>京ICP备2025000001号</footer>"
        "</body></html>"
    )
    result = scan_web_quality({"site/index.html": html})
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
    result = scan_web_quality({"site/index.html": html})
    assert any("emoji当图标" in f for f in result.soft_failures)
    assert any("紫蓝渐变+glow默认皮" in f for f in result.soft_failures)
    assert result.failures == []


def test_contract_web_quality_auto_scans_landed_html():
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


def test_contract_web_quality_soft_flips_ok_for_retry():
    html = (
        '<html><body><span class="feature-icon">✨</span></body></html>'
    )
    v = check_contract(
        "ok",
        Deliverable(form="files", artifacts=["site/index.html"]),
        files_written=1,
        artifact_contents={"site/index.html": html},
        workspace_paths=["site/index.html"],
    )
    assert not v.ok
    assert v.failures == []
    assert any("emoji当图标" in f for f in v.soft_failures)


def test_soft_exempt_skips_anti_slop():
    html = '<html><body><span class="feature-icon">🚀</span></body></html>'
    result = scan_web_quality({"site/index.html": html}, soft_exempt=True)
    assert result.soft_failures == []


def test_unreplaced_mustache_hard_on_fill_phase():
    html = '<html><body><h2>{{section_title}}</h2></body></html>'
    result = scan_web_quality({"site/index.html": html})
    assert any("未替换模板槽" in f for f in result.failures)


def test_unreplaced_mustache_allowed_on_skeleton_soft_exempt():
    html = '<html><body><h2>{{section_title}}</h2></body></html>'
    result = scan_web_quality({"site/index.html": html}, soft_exempt=True)
    assert not any("未替换模板槽" in f for f in result.failures)
    assert result.soft_failures == []
