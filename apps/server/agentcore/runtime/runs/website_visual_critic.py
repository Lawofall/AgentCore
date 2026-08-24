"""P1c — website visual critic (screenshot → VisionReader → structured findings).

Runs **after** ``web_quality`` hard gates on site QA. Critic never
replaces syntax / fake-contact / DESIGN-token hard checks.

Capability posture (honest degrade, board_read-shaped):

- no ``VisionReader`` **or** no screenshot port → ``skipped`` + 「未目验」;
  never mark visual QA as passed.
- both present → multi-viewport screenshots → VLM → structured findings;
  critical findings drive up to ``MAX_VISUAL_REWORK`` contract reworks, then
  demote to partial warnings.

→ 见代码接缝: deliverable ``visual_critic`` + executor.node visual rework loop.
"""

from __future__ import annotations

import base64
import json
import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any, Literal, Protocol

from agentcore.core.logging import get_logger
from agentcore.runtime.runs.web_quality_rules import soft_rule_labels
from agentcore.runtime.runs.website_visual_critic_preview import (
    DEFAULT_VIEWPORTS,
    ViewportName,
    assemble_preview_document,
    capture_html_preview_products,
    load_preview_asset_bytes,
    other_html_source_paths,
    preview_shot_path,
    resolve_html_source_path,
)
from agentcore.tools.file_products import FileProduct
from agentcore.vision.protocol import VisionReader, VisionReading

logger = get_logger(__name__)

MAX_VISUAL_REWORK = 2
VISUAL_CRITIC_ARTIFACT = "site/VISUAL_CRITIC.json"
UNINSPECTED_MARKER = "未目验"

FindingSeverity = Literal["critical", "major", "minor"]
CriticStatus = Literal["passed", "findings", "skipped", "error"]

_SEVERITIES = frozenset({"critical", "major", "minor"})
_VIEWPORT_NAMES = frozenset({"desktop", "narrow"})


@dataclass(frozen=True)
class VisualFinding:
    severity: FindingSeverity
    viewport: ViewportName | str
    category: str
    target: str
    issue: str
    fix_hint: str = ""

    def as_line(self) -> str:
        hint = f"；修补：{self.fix_hint}" if self.fix_hint else ""
        return (
            f"[{self.severity}/{self.viewport}/{self.category}] "
            f"{self.target}: {self.issue}{hint}"
        )


@dataclass
class VisualCriticResult:
    status: CriticStatus
    findings: list[VisualFinding] = field(default_factory=list)
    viewports_shot: list[str] = field(default_factory=list)
    reason: str = ""
    raw_texts: list[str] = field(default_factory=list)
    preview_products: list[FileProduct] = field(default_factory=list)

    @property
    def critical_findings(self) -> list[VisualFinding]:
        return [f for f in self.findings if f.severity == "critical"]

    def to_artifact_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "viewports_shot": list(self.viewports_shot),
            "findings": [asdict(f) for f in self.findings],
            "visual_qa_passed": self.status == "passed",
            "uninspected": self.status == "skipped",
        }


class PageScreenshotPort(Protocol):
    """Capture jpeg/png bytes for an assembled HTML document at a viewport size."""

    async def capture(
        self, *, document_html: str, width: int, height: int
    ) -> bytes | None:
        """Return image bytes, or ``None`` when capture is unavailable / failed."""
        ...


def build_critic_prompt(*, design_md: str, viewport: str) -> str:
    """Structured critic brief — DESIGN + anti-slop; forbid vague praise."""
    labels = "、".join(soft_rule_labels())
    design_excerpt = (design_md or "").strip()
    if len(design_excerpt) > 6000:
        design_excerpt = design_excerpt[:6000] + "\n…(truncated)"
    return (
        "你是独立视觉 QA critic（与实现模型分离）。对照下列 DESIGN 契约与 anti-slop "
        "黑名单，审阅截图。禁止空泛「好看/还行/不错」；只输出可行动的结构化 findings。\n"
        f"当前视口：{viewport}\n"
        f"anti-slop 标签：{labels}\n"
        "—— DESIGN.md ——\n"
        f"{design_excerpt or '（缺失）'}\n"
        "—— 输出要求 ——\n"
        "只输出一个 JSON 对象（不要 markdown 围栏），形状：\n"
        '{"findings":[{"severity":"critical|major|minor","viewport":"'
        f'{viewport}'
        '","category":"design_mismatch|anti_slop|layout|contrast|typography|other",'
        '"target":"区域或选择器","issue":"具体问题（对照 DESIGN/anti-slop）",'
        '"fix_hint":"定向修补建议"}]}\n'
        "无问题时返回 {\"findings\":[]}。"
        "severity=critical 仅用于明显违背 DESIGN tokens / anti-slop / 严重可读性或布局崩坏。"
    )


def parse_critic_response(text: str, *, default_viewport: str) -> list[VisualFinding]:
    """Parse VisionReader text into findings; tolerate fenced JSON."""
    raw = (text or "").strip()
    if not raw:
        return []
    payload = _extract_json_object(raw)
    if payload is None:
        return []
    items = payload.get("findings")
    if not isinstance(items, list):
        return []
    out: list[VisualFinding] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        issue = str(item.get("issue") or "").strip()
        if not issue or _is_vague_praise(issue):
            continue
        sev = str(item.get("severity") or "major").strip().lower()
        if sev not in _SEVERITIES:
            sev = "major"
        vp = str(item.get("viewport") or default_viewport).strip().lower()
        if vp not in _VIEWPORT_NAMES:
            vp = default_viewport
        out.append(
            VisualFinding(
                severity=sev,  # type: ignore[arg-type]
                viewport=vp,
                category=str(item.get("category") or "other").strip() or "other",
                target=str(item.get("target") or "page").strip() or "page",
                issue=issue,
                fix_hint=str(item.get("fix_hint") or "").strip(),
            )
        )
    return out


def _is_vague_praise(issue: str) -> bool:
    t = issue.strip()
    if len(t) > 24:
        return False
    vague = ("好看", "不错", "还行", "很好", "漂亮", "精美", "优秀", "完美", "ok", "fine", "good")
    return any(t == v or t.endswith(v) for v in vague)


def _extract_json_object(text: str) -> dict[str, Any] | None:
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL | re.IGNORECASE)
    candidate = fence.group(1) if fence else text
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(candidate[start : end + 1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def format_visual_feedback(findings: Sequence[VisualFinding], *, round_idx: int) -> str:
    """Correction prompt for visual rework (定向修补)."""
    lines = "\n".join(f"- {f.as_line()}" for f in findings)
    return (
        f"视觉 critic 第 {round_idx}/{MAX_VISUAL_REWORK} 轮回炉："
        "下列为结构化 findings（对照 DESIGN.md + anti-slop）。"
        "请用 str_replace / file_append **定向修补** site/ 下既有文件，"
        "禁止整站重写；修完更新 site/QA.md 与 site/VISUAL_CRITIC.json。\n"
        f"{lines}"
    )


def format_uninspected_warning(reason: str) -> str:
    return (
        f"视觉 QA：{UNINSPECTED_MARKER}"
        f"（{reason or '无 browser 截图能力或无 VisionReader'}）；"
        "不标记视觉 QA 通过。"
    )


def format_partial_warning(findings: Sequence[VisualFinding]) -> str:
    lines = "；".join(f.as_line() for f in findings[:5])
    more = f" 等共 {len(findings)} 条" if len(findings) > 5 else ""
    return (
        f"视觉 QA：第 {MAX_VISUAL_REWORK} 轮后仍有 critical findings → partial"
        f"（不无限循环）。残留：{lines}{more}"
    )


async def run_visual_critic(
    *,
    vision_reader: VisionReader | None,
    screenshot: PageScreenshotPort | None,
    document_html: str,
    design_md: str,
    viewports: Sequence[tuple[ViewportName, int, int]] = DEFAULT_VIEWPORTS,
    bill: Callable[[VisionReading], None] | None = None,
    persist_preview_shot: Callable[[str, bytes], Awaitable[None]] | None = None,
    preview_derived_from: str = "",
) -> VisualCriticResult:
    """Screenshot → VLM critic. Missing capability ⇒ skipped（未目验）."""
    if vision_reader is None and screenshot is None:
        return VisualCriticResult(
            status="skipped",
            reason="无 browser 截图能力且无 VisionReader（VISION_API_KEY）",
        )
    if vision_reader is None:
        return VisualCriticResult(
            status="skipped",
            reason="无 VisionReader（未配置 VISION_API_KEY）",
        )
    if screenshot is None:
        return VisualCriticResult(
            status="skipped",
            reason="无 browser 截图能力（未装配 browser / 截图端口）",
        )

    findings: list[VisualFinding] = []
    shot: list[str] = []
    raw_texts: list[str] = []
    capture_errors: list[str] = []
    preview_products: list[FileProduct] = []

    for name, width, height in viewports:
        try:
            frame = await screenshot.capture(
                document_html=document_html, width=width, height=height
            )
        except Exception as exc:  # noqa: BLE001 — critic must not crash the run
            logger.warning("website.visual_critic_capture_failed", viewport=name, error=str(exc))
            capture_errors.append(f"{name}:{exc}")
            continue
        if not frame:
            capture_errors.append(f"{name}:empty")
            continue
        shot.append(name)
        if persist_preview_shot is not None and preview_derived_from:
            preview_path = preview_shot_path(name, source_path=preview_derived_from)
            try:
                await persist_preview_shot(preview_path, frame)
                preview_products.append(
                    FileProduct(
                        path=preview_path,
                        kind="image",
                        derived_from=preview_derived_from,
                    )
                )
            except Exception:  # noqa: BLE001
                logger.warning(
                    "website.visual_critic_preview_write_failed",
                    path=preview_path,
                    viewport=name,
                    exc_info=True,
                )
        png_b64 = base64.b64encode(frame).decode("ascii")
        prompt = build_critic_prompt(design_md=design_md, viewport=name)
        try:
            reading = await vision_reader.read(png_b64, prompt)
        except Exception as exc:  # noqa: BLE001
            logger.warning("website.visual_critic_vision_failed", viewport=name, error=str(exc))
            return VisualCriticResult(
                status="error",
                reason=f"VisionReader 失败（{name}）：{exc}",
                viewports_shot=shot,
                preview_products=preview_products,
            )
        if bill is not None:
            try:
                bill(reading)
            except Exception:  # noqa: BLE001
                logger.warning("website.visual_critic_billing_failed", exc_info=True)
        raw_texts.append(reading.text)
        findings.extend(parse_critic_response(reading.text, default_viewport=name))

    if not shot:
        return VisualCriticResult(
            status="skipped",
            reason="截图失败：" + ("；".join(capture_errors) if capture_errors else "无帧"),
            preview_products=preview_products,
        )

    if findings:
        return VisualCriticResult(
            status="findings",
            findings=findings,
            viewports_shot=shot,
            raw_texts=raw_texts,
            preview_products=preview_products,
        )
    return VisualCriticResult(
        status="passed",
        viewports_shot=shot,
        raw_texts=raw_texts,
        preview_products=preview_products,
    )


def make_vision_bill(
    *,
    cost_sink: list[Any] | None,
    parent_run_id: str,
) -> Callable[[VisionReading], None] | None:
    """Bill vision spend like board_read（role=vision）."""
    if cost_sink is None:
        return None

    def _bill(reading: VisionReading) -> None:
        if not reading.model or reading.usage.total_tokens == 0:
            return
        # Lazy: costing ↔ runs 包循环导入（costing → runs.types → executor → 本模块）。
        from agentcore.runtime.costing import vision_run_cost

        cost_sink.append(
            vision_run_cost(
                reading.model,
                reading.usage,
                parent_run_id=parent_run_id,
                credential_source="platform",
            )
        )

    return _bill


@dataclass
class StubPageScreenshot:
    """Test double: returns canned bytes per viewport size (or None)."""

    frames: dict[tuple[int, int], bytes | None] = field(default_factory=dict)
    default_frame: bytes | None = b"\xff\xd8\xfffakejpeg"
    calls: list[tuple[int, int]] = field(default_factory=list)

    async def capture(
        self, *, document_html: str, width: int, height: int
    ) -> bytes | None:
        self.calls.append((width, height))
        if (width, height) in self.frames:
            return self.frames[(width, height)]
        return self.default_frame


async def capture_via_browser_session(
    *,
    conversation_id: str,
    document_html: str,
    width: int,
    height: int,
) -> bytes | None:
    """Best-effort host-side capture using the conversation browser session.

    Uses driver commands ``set_viewport`` / ``set_content`` / ``screenshot``
    (not exposed as model tools). Returns ``None`` on any failure.
    """
    if not conversation_id:
        return None
    try:
        from agentcore.config import settings
        from agentcore.runtime.browser.registry import default_browser_session_registry
        from agentcore.tools.sandbox.browser.protocol import (
            BrowserCommand,
            BrowserSessionError,
            BrowserSessionRequest,
            BrowserSessionsBusyError,
        )
    except Exception:  # noqa: BLE001
        return None

    registry = default_browser_session_registry()
    request = BrowserSessionRequest(
        conversation_id=conversation_id,
        workspace_root=None,
        viewport_width=width,
        viewport_height=height,
        jpeg_quality=int(settings.browser_keyframe_jpeg_quality),
    )
    try:
        session, _keyframes = await registry.acquire(request)
    except (BrowserSessionsBusyError, BrowserSessionError, Exception):  # noqa: BLE001
        return None

    try:
        await session.send(
            BrowserCommand(
                action="set_viewport",
                args={"width": width, "height": height, "capture": False},
            )
        )
        await session.send(
            BrowserCommand(
                action="set_content",
                args={"html": document_html, "capture": False},
            )
        )
        result = await session.send(
            BrowserCommand(action="screenshot", args={"capture": True})
        )
    except Exception:  # noqa: BLE001
        logger.warning("website.visual_critic_browser_rpc_failed", exc_info=True)
        return None
    if not result.ok:
        return None
    return result.frame


@dataclass
class BrowserPageScreenshot:
    """Production screenshot port over the conversation's browser session."""

    conversation_id: str

    async def capture(
        self, *, document_html: str, width: int, height: int
    ) -> bytes | None:
        return await capture_via_browser_session(
            conversation_id=self.conversation_id,
            document_html=document_html,
            width=width,
            height=height,
        )


def resolve_screenshot_port(
    *,
    conversation_id: str,
    browser_tool_available: bool,
    override: PageScreenshotPort | None = None,
) -> PageScreenshotPort | None:
    """Pick screenshot port: explicit override → browser session → None."""
    if override is not None:
        return override
    if browser_tool_available and conversation_id:
        return BrowserPageScreenshot(conversation_id=conversation_id)
    return None


def browser_tool_available(tools: Any) -> bool:
    """True when ``browser`` is offered to the worker (screenshot is an action).

    Dual-recognizes pre-merge ``browser_screenshot`` so old fixtures still match.
    """
    get = getattr(tools, "get_optional", None)
    if callable(get):
        return get("browser") is not None or get("browser_screenshot") is not None
    names = getattr(tools, "list_all", None)
    if callable(names):
        try:
            offered = {getattr(s, "name", "") for s in names()}
            return "browser" in offered or "browser_screenshot" in offered
        except Exception:  # noqa: BLE001
            return False
    return False


async def apply_visual_critic_to_verdict(
    verdict: Any,
    *,
    vision_reader: VisionReader | None,
    screenshot: PageScreenshotPort | None,
    artifact_contents: dict[str, str],
    visual_rework_used: int,
    bill: Callable[[VisionReading], None] | None = None,
    persist_artifact: Callable[[str, str], Awaitable[None]] | None = None,
    persist_preview_shot: Callable[[str, bytes], Awaitable[None]] | None = None,
    read_bytes: Callable[[str], Awaitable[bytes]] | None = None,
) -> tuple[Any, VisualCriticResult, int]:
    """Run critic after hard gates; merge into verdict; return (verdict, result, rework_used).

    ``verdict`` is a :class:`~agentcore.runtime.runs.contract.ContractVerdict` (typed
    loosely to avoid an import cycle with the executor). Critical findings flip
    ``ok`` via ``visual_failures`` while ``visual_rework_used < MAX_VISUAL_REWORK``;
    otherwise demote to warnings (partial).

    Preview frames: primary shell → ``site/preview-{viewport}.jpg``; other HTML
    candidates → ``{stem}.preview-{viewport}.jpg``. Each row is ``kind=image`` /
    ``derived_from`` the HTML source. Inline assembly failure ⇒ that file gets
    **no** preview shot (primary failure also skips the VLM critic).
    """
    html_source = resolve_html_source_path(artifact_contents)
    html = artifact_contents.get(html_source, "")
    design = artifact_contents.get("site/DESIGN.md") or artifact_contents.get(
        "DESIGN.md", ""
    )
    asset_bytes = await load_preview_asset_bytes(
        html, html_source, artifact_contents, read_bytes
    )
    assembled = assemble_preview_document(
        html,
        artifact_contents=artifact_contents,
        artifact_bytes=asset_bytes,
        source_path=html_source,
    )
    if not assembled.ok:
        result = VisualCriticResult(
            status="skipped",
            reason=assembled.reason or "预览 HTML 未能内联本地资源",
        )
    else:
        result = await run_visual_critic(
            vision_reader=vision_reader,
            screenshot=screenshot,
            document_html=assembled.document,
            design_md=design,
            bill=bill,
            persist_preview_shot=persist_preview_shot,
            preview_derived_from=html_source,
        )
    if persist_preview_shot is not None and screenshot is not None:
        for extra in other_html_source_paths(artifact_contents, html_source):
            extra_products = await capture_html_preview_products(
                screenshot=screenshot,
                persist_preview_shot=persist_preview_shot,
                read_bytes=read_bytes,
                artifact_contents=artifact_contents,
                source_path=extra,
            )
            result.preview_products.extend(extra_products)
    if persist_artifact is not None:
        try:
            await persist_artifact(
                VISUAL_CRITIC_ARTIFACT,
                json.dumps(result.to_artifact_dict(), ensure_ascii=False, indent=2),
            )
        except Exception:  # noqa: BLE001
            logger.warning("website.visual_critic_artifact_write_failed", exc_info=True)

    warnings = list(verdict.warnings)
    soft = list(verdict.soft_failures)
    visual = list(verdict.visual_failures)
    failures = list(verdict.failures)
    rework = visual_rework_used

    if result.status == "skipped":
        warnings.append(format_uninspected_warning(result.reason))
    elif result.status == "error":
        warnings.append(format_uninspected_warning(result.reason or "视觉 critic 错误"))
    elif result.status == "passed":
        warnings.append("视觉 QA：critic 通过（多视口截图 + VisionReader）")
    elif result.critical_findings:
        lines = [f.as_line() for f in result.critical_findings]
        non_critical = [f for f in result.findings if f.severity != "critical"]
        if non_critical:
            warnings.extend(
                f"视觉QA·{f.severity}：{f.as_line()}" for f in non_critical
            )
        if rework < MAX_VISUAL_REWORK:
            visual = [f"视觉QA·critical：{line}" for line in lines]
            rework = rework + 1
        else:
            warnings.append(format_partial_warning(result.critical_findings))
            visual = []
    elif result.findings:
        # Non-critical only → surface as warnings, do not block.
        warnings.extend(f"视觉QA·{f.severity}：{f.as_line()}" for f in result.findings)

    ok = not failures and not soft and not visual
    updated = type(verdict)(
        ok=ok,
        failures=failures,
        warnings=warnings,
        soft_failures=soft,
        visual_failures=visual,
    )
    return updated, result, rework

