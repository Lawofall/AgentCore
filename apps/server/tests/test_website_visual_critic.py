"""P1c website visual critic — structured findings, degrade, rework budget."""

from __future__ import annotations

import json

import pytest

from agentcore.llm.provider.protocol import TokenUsage
from agentcore.runtime.runs.contract import ContractVerdict
from agentcore.runtime.runs.playbooks import expand_playbook
from agentcore.runtime.runs.website_style import STYLE_ID_HEADING
from agentcore.runtime.runs.website_visual_critic import (
    MAX_VISUAL_REWORK,
    UNINSPECTED_MARKER,
    VISUAL_CRITIC_ARTIFACT,
    StubPageScreenshot,
    apply_visual_critic_to_verdict,
    assemble_preview_document,
    browser_tool_available,
    build_critic_prompt,
    parse_critic_response,
    run_visual_critic,
)
from agentcore.vision.protocol import VisionReading

pytestmark = pytest.mark.anyio

_DESIGN = (
    f"# Design\n\n## {STYLE_ID_HEADING}\ns0\n\n"
    "## Tokens\n--color-primary: #1a1a2e;\n"
)


class _FakeReader:
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls: list[tuple[str, str]] = []

    async def read(self, png_base64: str, prompt: str) -> VisionReading:
        self.calls.append((png_base64, prompt))
        return VisionReading(
            text=self.text,
            usage=TokenUsage(input_tokens=10, output_tokens=5),
            model="stub-vl",
        )


def test_browser_tool_available_accepts_new_and_legacy_names():
    class _Reg:
        def __init__(self, names: set[str]) -> None:
            self._names = names

        def get_optional(self, name: str):
            return object() if name in self._names else None

    assert browser_tool_available(_Reg({"browser"})) is True
    assert browser_tool_available(_Reg({"browser_screenshot"})) is True
    assert browser_tool_available(_Reg({"file_read"})) is False


def test_build_website_qa_enables_visual_critic():
    tasks, errors = expand_playbook(
        "build_website",
        {"topic": "Demo", "audience": "访客"},
    )
    assert errors == []
    qa = next(t for t in tasks if t["id"] == "qa")
    assert qa["deliverable"].get("visual_critic") is True
    assert "未目验" in qa["task"] or "VisionReader" in qa["task"]


def test_assemble_preview_inlines_css():
    doc = assemble_preview_document(
        "<html><head></head><body><h1>x</h1></body></html>",
        "h1{color:red}",
    )
    assert "h1{color:red}" in doc
    assert "</head>" in doc


def test_parse_critic_response_structured_and_skips_vague():
    raw = json.dumps(
        {
            "findings": [
                {
                    "severity": "critical",
                    "viewport": "desktop",
                    "category": "anti_slop",
                    "target": "hero",
                    "issue": "紫蓝渐变+glow 默认皮，违背 DESIGN",
                    "fix_hint": "改用 --color-primary 实底",
                },
                {
                    "severity": "minor",
                    "viewport": "desktop",
                    "category": "other",
                    "target": "page",
                    "issue": "好看",
                },
            ]
        },
        ensure_ascii=False,
    )
    findings = parse_critic_response(raw, default_viewport="desktop")
    assert len(findings) == 1
    assert findings[0].severity == "critical"
    assert "紫蓝" in findings[0].issue


def test_critic_prompt_mentions_design_and_anti_slop():
    prompt = build_critic_prompt(design_md=_DESIGN, viewport="narrow")
    assert "DESIGN" in prompt or "design" in prompt.lower()
    assert "anti-slop" in prompt or "anti_slop" in prompt
    assert "好看" in prompt  # forbid instruction


async def test_run_visual_critic_skips_without_capability():
    result = await run_visual_critic(
        vision_reader=None,
        screenshot=None,
        document_html="<html></html>",
        design_md=_DESIGN,
    )
    assert result.status == "skipped"
    assert UNINSPECTED_MARKER in result.reason or "VisionReader" in result.reason or "browser" in result.reason
    assert result.to_artifact_dict()["uninspected"] is True
    assert result.to_artifact_dict()["visual_qa_passed"] is False


async def test_run_visual_critic_skips_without_vision_only():
    result = await run_visual_critic(
        vision_reader=None,
        screenshot=StubPageScreenshot(),
        document_html="<html></html>",
        design_md=_DESIGN,
    )
    assert result.status == "skipped"
    assert "VisionReader" in result.reason


async def test_run_visual_critic_skips_without_screenshot_only():
    reader = _FakeReader('{"findings":[]}')
    result = await run_visual_critic(
        vision_reader=reader,
        screenshot=None,
        document_html="<html></html>",
        design_md=_DESIGN,
    )
    assert result.status == "skipped"
    assert "browser" in result.reason or "截图" in result.reason
    assert reader.calls == []


async def test_run_visual_critic_passed_with_stubs():
    reader = _FakeReader('{"findings":[]}')
    shot = StubPageScreenshot()
    result = await run_visual_critic(
        vision_reader=reader,
        screenshot=shot,
        document_html="<html><body>ok</body></html>",
        design_md=_DESIGN,
    )
    assert result.status == "passed"
    assert set(result.viewports_shot) == {"desktop", "narrow"}
    assert len(reader.calls) == 2
    assert len(shot.calls) == 2


async def test_run_visual_critic_findings_with_stubs():
    payload = json.dumps(
        {
            "findings": [
                {
                    "severity": "critical",
                    "viewport": "desktop",
                    "category": "layout",
                    "target": "#hero",
                    "issue": "首屏多构图堆叠，违反 anti-slop",
                    "fix_hint": "只留品牌+一句主文案+CTA",
                }
            ]
        },
        ensure_ascii=False,
    )
    reader = _FakeReader(payload)
    result = await run_visual_critic(
        vision_reader=reader,
        screenshot=StubPageScreenshot(),
        document_html="<html></html>",
        design_md=_DESIGN,
    )
    assert result.status == "findings"
    assert len(result.critical_findings) >= 1


async def test_apply_verdict_uninspected_does_not_fake_pass():
    base = ContractVerdict(ok=True, failures=[], warnings=[], soft_failures=[])
    updated, result, rework = await apply_visual_critic_to_verdict(
        base,
        vision_reader=None,
        screenshot=None,
        artifact_contents={"site/index.html": "<html></html>", "site/DESIGN.md": _DESIGN},
        visual_rework_used=0,
    )
    assert result.status == "skipped"
    assert updated.ok is True
    assert updated.visual_failures == []
    assert any(UNINSPECTED_MARKER in w for w in updated.warnings)
    assert rework == 0


async def test_apply_verdict_critical_triggers_rework_then_partial():
    payload = json.dumps(
        {
            "findings": [
                {
                    "severity": "critical",
                    "viewport": "narrow",
                    "category": "contrast",
                    "target": "cta",
                    "issue": "对比度不足，按钮不可读",
                    "fix_hint": "提高文字对比",
                }
            ]
        },
        ensure_ascii=False,
    )
    reader = _FakeReader(payload)
    shot = StubPageScreenshot()
    arts = {
        "site/index.html": "<html><body>x</body></html>",
        "site/styles.css": "body{}",
        "site/DESIGN.md": _DESIGN,
    }
    written: dict[str, str] = {}

    async def persist(path: str, text: str) -> None:
        written[path] = text

    base = ContractVerdict(ok=True)
    v1, r1, used1 = await apply_visual_critic_to_verdict(
        base,
        vision_reader=reader,
        screenshot=shot,
        artifact_contents=arts,
        visual_rework_used=0,
        persist_artifact=persist,
    )
    assert r1.status == "findings"
    assert v1.ok is False
    assert v1.visual_failures
    assert used1 == 1
    assert VISUAL_CRITIC_ARTIFACT in written

    v2, _r2, used2 = await apply_visual_critic_to_verdict(
        ContractVerdict(ok=True),
        vision_reader=reader,
        screenshot=shot,
        artifact_contents=arts,
        visual_rework_used=used1,
    )
    assert v2.ok is False
    assert used2 == 2

    v3, _r3, used3 = await apply_visual_critic_to_verdict(
        ContractVerdict(ok=True),
        vision_reader=reader,
        screenshot=shot,
        artifact_contents=arts,
        visual_rework_used=used2,
    )
    assert used3 == MAX_VISUAL_REWORK
    assert v3.ok is True
    assert v3.visual_failures == []
    assert any("partial" in w for w in v3.warnings)
