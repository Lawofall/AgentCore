"""Deterministic static seam check for web deliverables (HTML ↔ CSS ↔ JS).

When a single worker run lands an HTML file together with CSS and/or JS, cross-check
that HTML ``class`` / ``id`` tokens appear in CSS or JS selectors. Pure functions —
no browser. A miss rate above :data:`WEB_SEAM_MISS_THRESHOLD` fails the contract gate
so the executor can ``contract.retry`` with a concrete orphan list.

Inline ``<style>`` / ``<script>`` blocks in HTML feed the CSS / JS selector pools.
Remote stylesheets / scripts (``http(s):`` or protocol-relative ``//`` on
``link[rel=stylesheet]`` / ``script[src]``) cannot be verified statically — the gate
skips the whole batch and logs ``web_seam.skip_external`` (prefer pass over false fail).

Triggered only for web artifact batches; ordinary docs / single-file drops are ignored.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping

from agentcore.core.logging import get_logger

logger = get_logger(__name__)

# Fail when more than ~30% of HTML class/id tokens have no CSS/JS selector hit.
WEB_SEAM_MISS_THRESHOLD = 0.30
# Cap the orphan list in feedback so the retry prompt stays actionable.
_MAX_ORPHANS_LISTED = 40

_HTML_EXTS = frozenset({".html", ".htm"})
_STYLE_EXTS = frozenset({".css"})
_SCRIPT_EXTS = frozenset({".js", ".mjs", ".cjs"})
_WEB_EXTS = _HTML_EXTS | _STYLE_EXTS | _SCRIPT_EXTS

_CLASS_ATTR = re.compile(r"""\bclass\s*=\s*(["'])(.*?)\1""", re.IGNORECASE | re.DOTALL)
_ID_ATTR = re.compile(r"""\bid\s*=\s*(["'])([^"']*?)\1""", re.IGNORECASE)
_CSS_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_CSS_CLASS = re.compile(r"\.([A-Za-z_][A-Za-z0-9_-]*)")
_CSS_ID = re.compile(r"#([A-Za-z_][A-Za-z0-9_-]*)")
# Hex colors (#fff / #ffffff / #ffffffff) are not id selectors.
_HEX_COLOR = re.compile(r"^[0-9A-Fa-f]{3}([0-9A-Fa-f]{3}([0-9A-Fa-f]{2})?)?$")
_JS_QUERY = re.compile(
    r"""(?:querySelector(?:All)?|closest)\s*\(\s*(['"])(.*?)\1""",
    re.DOTALL,
)
_JS_BY_ID = re.compile(r"""getElementById\s*\(\s*(['"])([^"']*?)\1""")
_JS_BY_CLASS = re.compile(r"""getElementsByClassName\s*\(\s*(['"])([^"']*?)\1""")
_JS_CLASS_LIST = re.compile(
    r"""classList\.(?:add|remove|toggle|contains)\s*\(\s*(['"])([^"']*?)\1"""
)

_STYLE_BLOCK = re.compile(r"""<style\b[^>]*>(.*?)</style\s*>""", re.IGNORECASE | re.DOTALL)
_SCRIPT_BLOCK = re.compile(
    r"""<script\b([^>]*)>(.*?)</script\s*>""", re.IGNORECASE | re.DOTALL
)
_LINK_TAG = re.compile(r"""<link\b([^>]*)>""", re.IGNORECASE | re.DOTALL)
_ATTR = re.compile(
    r"""([^\s=<>/]+)\s*=\s*(?:(["'])(.*?)\2|([^\s"'`=<>]+))""",
    re.IGNORECASE | re.DOTALL,
)
_EXTERNAL_URL = re.compile(r"^(?://|https?:)", re.IGNORECASE)


def _ext(path: str) -> str:
    name = path.replace("\\", "/").rsplit("/", 1)[-1].casefold()
    if "." not in name:
        return ""
    return "." + name.rsplit(".", 1)[-1]


def is_web_artifact_batch(paths: Iterable[str]) -> bool:
    """True when the same batch includes HTML plus CSS and/or JS."""
    exts = {_ext(p) for p in paths if p}
    has_html = bool(exts & _HTML_EXTS)
    has_style_or_script = bool(exts & (_STYLE_EXTS | _SCRIPT_EXTS))
    return has_html and has_style_or_script


def web_paths(paths: Iterable[str]) -> list[str]:
    """Paths whose extension is HTML / CSS / JS (stable order, de-duped)."""
    out: list[str] = []
    seen: set[str] = set()
    for p in paths:
        if not p or _ext(p) not in _WEB_EXTS:
            continue
        if p in seen:
            continue
        seen.add(p)
        out.append(p)
    return out


def _attr_map(tag_attrs: str) -> dict[str, str]:
    """Parse HTML tag attribute text into a lowercased name → value map."""
    out: dict[str, str] = {}
    for m in _ATTR.finditer(tag_attrs or ""):
        name = m.group(1).casefold()
        value = m.group(3) if m.group(3) is not None else (m.group(4) or "")
        out[name] = value.strip()
    return out


def extract_inline_styles(html: str) -> list[str]:
    """Bodies of ``<style>…</style>`` blocks (stable document order)."""
    return [m.group(1) for m in _STYLE_BLOCK.finditer(html or "") if m.group(1)]


def extract_inline_scripts(html: str) -> list[str]:
    """Bodies of inline ``<script>`` blocks without ``src`` (stable document order)."""
    out: list[str] = []
    for m in _SCRIPT_BLOCK.finditer(html or ""):
        attrs = _attr_map(m.group(1))
        if attrs.get("src"):
            continue
        body = m.group(2)
        if body:
            out.append(body)
    return out


def find_external_asset_refs(html: str) -> list[str]:
    """External stylesheet / script URLs (http(s) or protocol-relative ``//``).

    Local relative / root-absolute paths are ignored — those can still be checked
    against landed files when present.
    """
    refs: list[str] = []
    seen: set[str] = set()

    def _add(url: str) -> None:
        u = (url or "").strip()
        if not u or not _EXTERNAL_URL.match(u) or u in seen:
            return
        seen.add(u)
        refs.append(u)

    for m in _LINK_TAG.finditer(html or ""):
        attrs = _attr_map(m.group(1))
        rel = attrs.get("rel", "").casefold()
        if "stylesheet" not in rel.split():
            continue
        _add(attrs.get("href", ""))
    for m in _SCRIPT_BLOCK.finditer(html or ""):
        attrs = _attr_map(m.group(1))
        _add(attrs.get("src", ""))
    # Self-closing / void script with src only (no body) — uncommon but covered by
    # a dedicated open-tag scan when SCRIPT_BLOCK misses non-closed forms.
    for m in re.finditer(r"""<script\b([^>]*)/?>""", html or "", flags=re.IGNORECASE):
        attrs = _attr_map(m.group(1))
        _add(attrs.get("src", ""))
    return refs


def _markup_without_embedded_blocks(html: str) -> str:
    """HTML with ``<style>`` / ``<script>`` bodies blanked (attributes stay on markup)."""
    text = _STYLE_BLOCK.sub("<style></style>", html or "")
    return _SCRIPT_BLOCK.sub("<script></script>", text)


def extract_html_tokens(html: str) -> tuple[set[str], set[str]]:
    """Return ``(classes, ids)`` declared on HTML attributes (embedded blocks ignored)."""
    markup = _markup_without_embedded_blocks(html)
    classes: set[str] = set()
    ids: set[str] = set()
    for m in _CLASS_ATTR.finditer(markup):
        for token in m.group(2).split():
            name = token.strip()
            if name:
                classes.add(name)
    for m in _ID_ATTR.finditer(markup):
        name = m.group(2).strip()
        if name:
            ids.add(name)
    return classes, ids


def extract_css_selectors(css: str) -> tuple[set[str], set[str]]:
    """Return ``(classes, ids)`` referenced by CSS selectors (comments stripped)."""
    text = _CSS_COMMENT.sub(" ", css or "")
    classes = set(_CSS_CLASS.findall(text))
    ids = {i for i in _CSS_ID.findall(text) if not _HEX_COLOR.fullmatch(i)}
    return classes, ids


def extract_js_selectors(js: str) -> tuple[set[str], set[str]]:
    """Return ``(classes, ids)`` referenced by common DOM selector APIs."""
    classes: set[str] = set()
    ids: set[str] = set()
    text = js or ""
    for m in _JS_QUERY.finditer(text):
        c, i = extract_css_selectors(m.group(2))
        classes |= c
        ids |= i
    for m in _JS_BY_ID.finditer(text):
        name = m.group(2).strip()
        if name:
            ids.add(name)
    for m in _JS_BY_CLASS.finditer(text):
        for token in m.group(2).split():
            name = token.strip()
            if name:
                classes.add(name)
    for m in _JS_CLASS_LIST.finditer(text):
        name = m.group(2).strip()
        if name:
            classes.add(name)
    return classes, ids


def check_web_seam_failures(
    artifact_contents: Mapping[str, str] | None,
) -> list[str]:
    """Failures when HTML class/id tokens miss CSS/JS selectors above the threshold.

    Returns ``[]`` when the batch is not a web artifact set, contents are missing,
    there are no HTML tokens to check, the miss rate is within the threshold, or the
    HTML references external stylesheets / scripts (static check skipped).
    """
    if not artifact_contents:
        return []
    by_ext: dict[str, list[str]] = {".html": [], ".css": [], ".js": []}
    for path, text in artifact_contents.items():
        if not path or text is None:
            continue
        ext = _ext(path)
        if ext in _HTML_EXTS:
            by_ext[".html"].append(text)
        elif ext in _STYLE_EXTS:
            by_ext[".css"].append(text)
        elif ext in _SCRIPT_EXTS:
            by_ext[".js"].append(text)
    if not by_ext[".html"] or (not by_ext[".css"] and not by_ext[".js"]):
        return []

    external_refs: list[str] = []
    seen_ext: set[str] = set()
    for html in by_ext[".html"]:
        for ref in find_external_asset_refs(html):
            if ref not in seen_ext:
                seen_ext.add(ref)
                external_refs.append(ref)
    if external_refs:
        logger.info(
            "web_seam.skip_external",
            ref_count=len(external_refs),
            refs=external_refs[:8],
        )
        return []

    html_classes: set[str] = set()
    html_ids: set[str] = set()
    for html in by_ext[".html"]:
        c, i = extract_html_tokens(html)
        html_classes |= c
        html_ids |= i
    if not html_classes and not html_ids:
        return []

    sel_classes: set[str] = set()
    sel_ids: set[str] = set()
    for css in by_ext[".css"]:
        c, i = extract_css_selectors(css)
        sel_classes |= c
        sel_ids |= i
    for js in by_ext[".js"]:
        c, i = extract_js_selectors(js)
        sel_classes |= c
        sel_ids |= i
    # Inline blocks in HTML count toward the selector pools (单文件风格页面).
    for html in by_ext[".html"]:
        for css in extract_inline_styles(html):
            c, i = extract_css_selectors(css)
            sel_classes |= c
            sel_ids |= i
        for js in extract_inline_scripts(html):
            c, i = extract_js_selectors(js)
            sel_classes |= c
            sel_ids |= i

    orphan_classes = sorted(html_classes - sel_classes)
    orphan_ids = sorted(html_ids - sel_ids)
    total = len(html_classes) + len(html_ids)
    orphans = len(orphan_classes) + len(orphan_ids)
    if total <= 0:
        return []
    miss_rate = orphans / total
    if miss_rate <= WEB_SEAM_MISS_THRESHOLD:
        return []

    listed_parts: list[str] = []
    budget = _MAX_ORPHANS_LISTED
    if orphan_classes:
        shown = orphan_classes[:budget]
        budget -= len(shown)
        listed_parts.append("class " + "、".join(f"`{n}`" for n in shown))
        more = len(orphan_classes) - len(shown)
        if more > 0:
            listed_parts[-1] += f" 等 {more} 个"
    if orphan_ids and budget > 0:
        shown = orphan_ids[:budget]
        listed_parts.append("id " + "、".join(f"`{n}`" for n in shown))
        more = len(orphan_ids) - len(shown)
        if more > 0:
            listed_parts[-1] += f" 等 {more} 个"

    pct = int(round(miss_rate * 100))
    threshold_pct = int(round(WEB_SEAM_MISS_THRESHOLD * 100))
    detail = "；".join(listed_parts) if listed_parts else "（无清单）"
    return [
        f"网页接缝静态检查未通过：HTML 中有 {orphans}/{total} 个 class/id "
        f"在同批 CSS/JS 选择器中无对应（未命中率 {pct}% > {threshold_pct}%）：{detail}。"
        "请补齐样式或脚本选择器，或删掉未使用的 class/id，使 HTML↔CSS↔JS 对齐。"
    ]
