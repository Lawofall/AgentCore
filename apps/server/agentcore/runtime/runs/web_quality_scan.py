"""Deterministic frontend quality gate for landed HTML/CSS/JS/SVG.

Pure static scan — no browser, no new deps. Mounted from :mod:`contract` when
landed files include web extensions. Do **not** fold into placeholder_scan or
web_seam.

**Hard** (always fail → contract.retry): shallow syntax damage (unclosed tags /
broken CSS declarations) + fabricated contact fingerprints (fake 400 phones /
fake ICP / placeholder emails).

**Fill-phase hard** (when ``soft_exempt`` is false): unreplaced ``{{…}}`` slots
in HTML.

**Soft** (anti-slop visual fingerprints): at most one rework; demoted to warnings
after that shot. Skipped when ``web_quality_soft_exempt`` or the user-exempted
label set covers the hit. Blacklist wording shares :mod:`web_quality_rules`.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from agentcore.runtime.runs.web_quality_rules import soft_rule_labels

_MAX_HITS_LISTED = 12
_SNIPPET_CHARS = 56

_WEB_EXTS = frozenset({".html", ".htm", ".css", ".js", ".mjs", ".cjs", ".svg"})
_VOID_TAGS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)

# Hard: fabricated contact fingerprints (GEO-style shipping lies).
_HARD_CONTACT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "编造400电话",
        re.compile(
            r"(?:"
            r"400[-\s]?888[-\s]?0000"
            r"|400[-\s]?000[-\s]?0000"
            r"|400[-\s]?123[-\s]?4567"
            r"|1[-\s]?800[-\s]?000[-\s]?0000"
            r")",
            re.IGNORECASE,
        ),
    ),
    (
        "编造ICP备案",
        re.compile(
            r"(?:"
            r"京ICP备2025000001号?"
            r"|京ICP备\d{0,4}0{4,}号?"
            r"|ICP备00000000"
            r"|粤ICP备12345678号?"
            r")",
            re.IGNORECASE,
        ),
    ),
    (
        "占位邮箱",
        re.compile(
            r"(?:"
            r"\b(?:your|name|user|admin)@(?:email|company|domain|example)\.com\b"
            r"|\bexample@[a-z0-9.-]+\.[a-z]{2,}\b"
            r"|\btest@(?:test|example)\.com\b"
            r")",
            re.IGNORECASE,
        ),
    ),
)

# Fill-phase hard (gated by soft_exempt=False): catalog shells leave {{slot}} for
# section workers; shipping them after fill is a contract failure.
_HARD_MUSTACHE_PATTERN: re.Pattern[str] = re.compile(r"\{\{[^{}\n]+\}\}")
_MUSTACHE_LABEL = "未替换模板槽{{…}}"
_HTML_EXTS = frozenset({".html", ".htm"})

# Soft anti-slop — labels MUST stay in :func:`soft_rule_labels`.
_SOFT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "默认系统字体栈/Inter·Poppins味",
        re.compile(
            r"(?:"
            r"font-family\s*:\s*[^;}]*(?:Inter|Poppins|Roboto|Open\s*Sans|system-ui|"
            r"-apple-system|BlinkMacSystemFont|"
            r"['\"]Segoe UI['\"]|Arial|Helvetica\s*,\s*sans-serif)"
            r"|--font[^:]*:\s*[^;}]*(?:Inter|Poppins|Roboto)"
            r")",
            re.IGNORECASE,
        ),
    ),
    (
        "紫蓝渐变+glow默认皮",
        re.compile(
            r"(?:"
            r"linear-gradient\s*\([^)]*(?:#(?:7c3aed|8b5cf6|6366f1|4f46e5|a855f7|"
            r"9333ea|6d28d9|3b82f6|2563eb)|(?:violet|indigo|purple|fuchsia)"
            r"[^)]*(?:blue|cyan|sky))"
            r"|(?:box-shadow|text-shadow|filter)\s*:[^;}]*(?:0\s+0\s+\d+px|"
            r"glow|drop-shadow)"
            r".{0,80}(?:violet|purple|indigo|#(?:7c3aed|8b5cf6|6366f1))"
            r"|from-(?:violet|purple|indigo)-\d+.{0,40}to-(?:blue|cyan|sky|indigo)-\d+"
            r")",
            re.IGNORECASE | re.DOTALL,
        ),
    ),
    (
        "三等分feature卡八股",
        re.compile(
            r"(?:"
            r"grid-template-columns\s*:\s*(?:repeat\s*\(\s*3\s*,|1fr\s+1fr\s+1fr)"
            r"|class\s*=\s*[\"'][^\"']*(?:grid-cols-3|three-col|feature-grid|"
            r"features-grid)[^\"']*[\"']"
            r")",
            re.IGNORECASE,
        ),
    ),
    (
        "pill badge+渐变字堆叠",
        re.compile(
            r"(?:"
            r"rounded-full[^,{]{0,120}(?:badge|pill|chip)"
            r"|(?:badge|pill|chip)[^,{]{0,120}rounded-full"
            r"|background\s*:\s*linear-gradient[^;]+;\s*[^}]*"
            r"(?:-webkit-background-clip\s*:\s*text|background-clip\s*:\s*text)"
            r")",
            re.IGNORECASE | re.DOTALL,
        ),
    ),
    (
        "emoji当图标",
        re.compile(
            r"(?:"
            r"<(?:span|div|i|button|li)[^>]{0,80}class\s*=\s*[\"'][^\"']*"
            r"(?:icon|feature-icon|card-icon)[^\"']*[\"'][^>]*>\s*"
            r"[\U0001F300-\U0001FAFF\u2600-\u27BF]"
            r"|aria-hidden\s*=\s*[\"']true[\"'][^>]*>\s*"
            r"[\U0001F300-\U0001FAFF]"
            r")",
            re.IGNORECASE,
        ),
    ),
    (
        "装饰粒子canvas",
        re.compile(
            r"(?:"
            r"<canvas[^>]{0,120}(?:particle|particles|starfield|constellation)"
            r"|(?:particle|particles|starfield).*?getContext\s*\(\s*['\"]2d['\"]"
            r"|new\s+Particle(?:System|Engine|Field)?"
            r")",
            re.IGNORECASE | re.DOTALL,
        ),
    ),
    (
        "重复数字墙",
        re.compile(
            r"(?:"
            r"(?:<\w+[^>]*>\s*(?:\d[\d,]*(?:\+|k|K|万|亿)?|\d+\s*%)\s*</\w+"
            r">\s*){3,}"
            r"|class\s*=\s*[\"'][^\"']*(?:stats-row|metrics-strip|number-wall|"
            r"social-proof-stats)[^\"']*[\"']"
            r")",
            re.IGNORECASE,
        ),
    ),
    (
        "lorem/假联系方式板块",
        re.compile(r"\blorem\s+ipsum\b", re.IGNORECASE),
    ),
)

_TAG_RE = re.compile(
    r"<\s*(/)?\s*([A-Za-z][\w:-]*)\b[^>]*?(/)?\s*>",
    re.DOTALL,
)
_CSS_DECL_RE = re.compile(
    r"([^{\n}]+)\{([^{}]+)\}",
    re.DOTALL,
)
# Broken declaration: property value ends with a stray comma before `;` / `}` /
# or uses comma where a length/keyword list was mangled (GEO accident sample).
_BROKEN_CSS_VALUE = re.compile(
    r"(?:"
    r":\s*[^;{}]*,\s*[;}]"  # trailing comma before terminator
    r"|:\s*,\s*"  # empty value starting with comma
    r"|:\s*#[0-9A-Fa-f]{3,8}\s*,\s*(?:;|})"  # color then stray comma
    r")"
)


@dataclass(frozen=True)
class WebQualityHit:
    path: str
    kind: str  # hard | soft
    label: str
    snippet: str


@dataclass
class WebQualityScanResult:
    failures: list[str] = field(default_factory=list)
    soft_failures: list[str] = field(default_factory=list)
    hits: list[WebQualityHit] = field(default_factory=list)


def needs_web_quality_scan(paths: Iterable[str]) -> bool:
    """True when any landed path is a static web surface."""
    return any(_is_web_path(p) for p in paths if p)


def _ext(path: str) -> str:
    name = path.replace("\\", "/").rsplit("/", 1)[-1].casefold()
    if "." not in name:
        return ""
    return "." + name.rsplit(".", 1)[-1]


def _is_web_path(path: str) -> bool:
    return _ext(path) in _WEB_EXTS


def _snippet_at(text: str, start: int, end: int) -> str:
    lo = max(0, start - 10)
    hi = min(len(text), end + 10)
    chunk = text[lo:hi].replace("\n", " ").strip()
    if len(chunk) > _SNIPPET_CHARS:
        chunk = chunk[: _SNIPPET_CHARS - 1] + "…"
    return chunk


def _scan_unclosed_tags(path: str, text: str) -> list[WebQualityHit]:
    if _ext(path) not in {".html", ".htm", ".svg"}:
        return []
    stack: list[str] = []
    hits: list[WebQualityHit] = []
    for m in _TAG_RE.finditer(text):
        closing, name, self_close = m.group(1), m.group(2).casefold(), m.group(3)
        if name in ("!doctype", "!--") or name.startswith("!"):
            continue
        if self_close or name in _VOID_TAGS:
            continue
        if closing:
            if stack and stack[-1] == name:
                stack.pop()
            elif stack:
                expected = stack[-1]
                hits.append(
                    WebQualityHit(
                        path=path,
                        kind="hard",
                        label="未闭合标签",
                        snippet=_snippet_at(text, m.start(), m.end())
                        + f"（期望 </{expected}>）",
                    )
                )
                # Pop until match or empty — keep scanning.
                while stack and stack[-1] != name:
                    stack.pop()
                if stack and stack[-1] == name:
                    stack.pop()
        else:
            stack.append(name)
    for leftover in stack[:3]:
        hits.append(
            WebQualityHit(
                path=path,
                kind="hard",
                label="未闭合标签",
                snippet=f"<{leftover}>…（文件末尾仍未闭合）",
            )
        )
    return hits


def _scan_broken_css(path: str, text: str) -> list[WebQualityHit]:
    ext = _ext(path)
    chunks: list[str] = []
    if ext == ".css":
        chunks = [text]
    elif ext in {".html", ".htm"}:
        for m in re.finditer(
            r"<style\b[^>]*>(.*?)</style>", text, re.IGNORECASE | re.DOTALL
        ):
            chunks.append(m.group(1))
        # Also scan inline style="…" lightly.
        for m in re.finditer(r"style\s*=\s*[\"']([^\"']+)[\"']", text, re.IGNORECASE):
            chunks.append(m.group(1))
    else:
        return []
    hits: list[WebQualityHit] = []
    for chunk in chunks:
        for block in _CSS_DECL_RE.finditer(chunk):
            body = block.group(2)
            for dm in _BROKEN_CSS_VALUE.finditer(body):
                hits.append(
                    WebQualityHit(
                        path=path,
                        kind="hard",
                        label="坏CSS声明",
                        snippet=_snippet_at(body, dm.start(), dm.end()),
                    )
                )
        # Declarations outside blocks (inline style attr).
        broken = _BROKEN_CSS_VALUE.search(chunk) if "{" not in chunk else None
        if broken is not None:
            hits.append(
                WebQualityHit(
                    path=path,
                    kind="hard",
                    label="坏CSS声明",
                    snippet=_snippet_at(chunk, broken.start(), broken.end()),
                )
            )
    return hits


def _scan_unreplaced_mustache(path: str, text: str) -> list[WebQualityHit]:
    """Hard-fail unreplaced ``{{slot}}`` in HTML."""
    ext = "." + path.replace("\\", "/").rsplit("/", 1)[-1].rsplit(".", 1)[-1].casefold()
    if ext not in _HTML_EXTS:
        return []
    m = _HARD_MUSTACHE_PATTERN.search(text)
    if not m:
        return []
    return [
        WebQualityHit(
            path=path,
            kind="hard",
            label=_MUSTACHE_LABEL,
            snippet=_snippet_at(text, m.start(), m.end()),
        )
    ]


def _pattern_hits(
    path: str,
    text: str,
    patterns: tuple[tuple[str, re.Pattern[str]], ...],
    *,
    kind: str,
) -> list[WebQualityHit]:
    hits: list[WebQualityHit] = []
    for label, pat in patterns:
        for m in pat.finditer(text):
            hits.append(
                WebQualityHit(
                    path=path,
                    kind=kind,
                    label=label,
                    snippet=_snippet_at(text, m.start(), m.end()),
                )
            )
            break  # one hit per label per file keeps feedback actionable
    return hits


def _format_failure_lines(
    hits: list[WebQualityHit], *, prefix: str
) -> list[str]:
    lines: list[str] = []
    for hit in hits[:_MAX_HITS_LISTED]:
        lines.append(f"{prefix}{hit.label}（`{hit.path}`：{hit.snippet}）")
    extra = len(hits) - _MAX_HITS_LISTED
    if extra > 0:
        lines.append(f"{prefix}另有 {extra} 处同类命中未列出")
    return lines


def scan_web_quality(
    artifact_contents: Mapping[str, str] | None,
    *,
    soft_exempt: bool = False,
    soft_exempt_labels: Iterable[str] | None = None,
) -> WebQualityScanResult:
    """Scan landed web artifacts; return hard failures + soft anti-slop hits."""
    result = WebQualityScanResult()
    if not artifact_contents:
        return result
    exempt = {s.strip() for s in (soft_exempt_labels or []) if isinstance(s, str) and s.strip()}
    known_soft = soft_rule_labels()
    hard_hits: list[WebQualityHit] = []
    soft_hits: list[WebQualityHit] = []
    for path, text in artifact_contents.items():
        if not path or not text or not _is_web_path(path):
            continue
        hard_hits.extend(_scan_unclosed_tags(path, text))
        hard_hits.extend(_scan_broken_css(path, text))
        hard_hits.extend(
            _pattern_hits(path, text, _HARD_CONTACT_PATTERNS, kind="hard")
        )
        # Skeleton soft_exempt: empty catalog shells OK; fill-phase must replace.
        if not soft_exempt:
            hard_hits.extend(_scan_unreplaced_mustache(path, text))
        if soft_exempt:
            continue
        for hit in _pattern_hits(path, text, _SOFT_PATTERNS, kind="soft"):
            if hit.label in exempt:
                continue
            if hit.label not in known_soft and hit.label != "lorem/假联系方式板块":
                # Still report — keep detector usable if catalog drifts.
                pass
            soft_hits.append(hit)
    result.hits = [*hard_hits, *soft_hits]
    result.failures = _format_failure_lines(hard_hits, prefix="网页质量·硬：")
    result.soft_failures = _format_failure_lines(soft_hits, prefix="网页质量·软：")
    return result
