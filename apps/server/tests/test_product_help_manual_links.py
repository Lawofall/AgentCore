"""product_help* ↔ desktop manual deep-link drift gate.

Parses ``sectionIds.ts`` (+ ``paths.ts`` chapter keys) from the monorepo and
asserts every ``#/toolbox/manual/...`` / bare ``?s=`` in the ``product_help``
skill body lands in that registry
(canonical ids + aliases). No TS→Python export — lightweight regex parse only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from agentcore.runtime.skills import build_system_skill_registry

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SECTION_IDS_TS = (
    _REPO_ROOT
    / "apps"
    / "desktop"
    / "src"
    / "renderer"
    / "pages"
    / "toolbox"
    / "manual"
    / "sectionIds.ts"
)
_PATHS_TS = _SECTION_IDS_TS.with_name("paths.ts")

_FULL_LINK = re.compile(
    r"#/toolbox/manual/(?P<chapter>[a-z][a-z0-9_]*)"
    r"(?:\?s=(?P<section>[a-z0-9][a-z0-9_-]*))?"
)
_BARE_SECTION = re.compile(r"\?s=(?P<section>[a-z0-9][a-z0-9_-]*)")
_CHAPTER_HINT = re.compile(r"（(?P<chapter>[a-z][a-z0-9_]*)·")


@dataclass(frozen=True)
class ManualRegistry:
    chapters: frozenset[str]
    sections_by_chapter: dict[str, frozenset[str]]
    aliases: frozenset[str]
    owner: dict[str, str]  # section id or alias → chapter

    @property
    def all_section_ids(self) -> frozenset[str]:
        registered = set(self.aliases)
        for ids in self.sections_by_chapter.values():
            registered |= set(ids)
        return frozenset(registered)


@dataclass(frozen=True)
class LinkHit:
    kind: str  # "full" | "bare"
    text: str
    chapter: str | None
    section: str | None
    start: int
    end: int


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def parse_paths_chapters(src: str) -> frozenset[str]:
    m = re.search(
        r"export const MANUAL_CHAPTER_PATHS\s*:\s*[^=]*=\s*\{(.*?)\}\s*;",
        src,
        flags=re.DOTALL,
    )
    _require(
        m is not None,
        f"parse failure: MANUAL_CHAPTER_PATHS not found in {_PATHS_TS.name}",
    )
    assert m is not None
    keys = frozenset(re.findall(r"^\s*([a-z][a-z0-9_]*)\s*:", m.group(1), flags=re.M))
    _require(
        bool(keys),
        f"parse failure: MANUAL_CHAPTER_PATHS has no chapter keys in {_PATHS_TS.name}",
    )
    return keys


def parse_section_registry(section_src: str, paths_src: str) -> ManualRegistry:
    chapters_from_paths = parse_paths_chapters(paths_src)

    m = re.search(
        r"export const MANUAL_SECTION_IDS\s*=\s*\{(.*?)\}\s*as const",
        section_src,
        flags=re.DOTALL,
    )
    _require(
        m is not None,
        f"parse failure: MANUAL_SECTION_IDS not found in {_SECTION_IDS_TS.name}",
    )
    assert m is not None
    block = m.group(1)

    sections_by_chapter: dict[str, frozenset[str]] = {}
    for ch_m in re.finditer(
        r"([a-z][a-z0-9_]*)\s*:\s*\{([^{}]*)\}",
        block,
    ):
        chapter = ch_m.group(1)
        ids = frozenset(re.findall(r':\s*"([a-z0-9][a-z0-9_-]*)"', ch_m.group(2)))
        _require(
            bool(ids),
            f"parse failure: chapter {chapter!r} has no section string literals "
            f"in {_SECTION_IDS_TS.name}",
        )
        sections_by_chapter[chapter] = ids

    _require(
        bool(sections_by_chapter),
        f"parse failure: MANUAL_SECTION_IDS has no chapter blocks in {_SECTION_IDS_TS.name}",
    )
    chapter_keys = frozenset(sections_by_chapter)
    _require(
        chapter_keys == chapters_from_paths,
        "parse failure: MANUAL_SECTION_IDS chapters "
        f"{sorted(chapter_keys)} != MANUAL_CHAPTER_PATHS keys "
        f"{sorted(chapters_from_paths)}",
    )

    alias_m = re.search(
        r"export const MANUAL_SECTION_ALIASES\s*:\s*[^=]*=\s*\{(.*?)\}\s*;",
        section_src,
        flags=re.DOTALL,
    )
    _require(
        alias_m is not None,
        f"parse failure: MANUAL_SECTION_ALIASES not found in {_SECTION_IDS_TS.name}",
    )
    assert alias_m is not None
    alias_block = alias_m.group(1)
    aliases = frozenset(
        a or b
        for a, b in re.findall(
            r'(?:^|[,{])\s*(?:"([^"]+)"|([a-z][a-z0-9_-]*))\s*:',
            alias_block,
        )
    )
    # Resolve alias → canonical via MANUAL_SECTION_IDS.chapter.section refs
    alias_owner: dict[str, str] = {}
    for am in re.finditer(
        r'(?:"(?P<q>[^"]+)"|(?P<b>[a-z][a-z0-9_-]*))\s*:\s*'
        r"MANUAL_SECTION_IDS\.(?P<ch>[a-z][a-z0-9_]*)\.(?P<sec>[a-z][a-z0-9_]*)",
        alias_block,
    ):
        alias = am.group("q") or am.group("b")
        ch = am.group("ch")
        sec = am.group("sec")
        _require(
            ch in sections_by_chapter and sec in sections_by_chapter[ch],
            f"parse failure: alias {alias!r} targets "
            f"MANUAL_SECTION_IDS.{ch}.{sec} which is not a registered section",
        )
        alias_owner[alias] = ch

    _require(
        frozenset(alias_owner) == aliases,
        "parse failure: could not resolve every MANUAL_SECTION_ALIASES entry "
        f"(keys={sorted(aliases)}, resolved={sorted(alias_owner)})",
    )

    owner: dict[str, str] = {}
    for chapter, ids in sections_by_chapter.items():
        for sid in ids:
            owner[sid] = chapter
    owner.update(alias_owner)

    return ManualRegistry(
        chapters=chapter_keys,
        sections_by_chapter=sections_by_chapter,
        aliases=aliases,
        owner=owner,
    )


def load_manual_registry() -> ManualRegistry:
    _require(
        _SECTION_IDS_TS.is_file(),
        f"parse failure: missing desktop registry {_SECTION_IDS_TS}",
    )
    _require(
        _PATHS_TS.is_file(),
        f"parse failure: missing desktop paths {_PATHS_TS}",
    )
    return parse_section_registry(
        _SECTION_IDS_TS.read_text(encoding="utf-8"),
        _PATHS_TS.read_text(encoding="utf-8"),
    )


def extract_manual_links(body: str) -> list[LinkHit]:
    hits: list[LinkHit] = []
    covered: list[tuple[int, int]] = []
    for m in _FULL_LINK.finditer(body):
        covered.append((m.start(), m.end()))
        hits.append(
            LinkHit(
                kind="full",
                text=m.group(0),
                chapter=m.group("chapter"),
                section=m.group("section"),
                start=m.start(),
                end=m.end(),
            )
        )

    def _inside_full(start: int, end: int) -> bool:
        return any(s <= start and end <= e for s, e in covered)

    for m in _BARE_SECTION.finditer(body):
        if _inside_full(m.start(), m.end()):
            continue
        hits.append(
            LinkHit(
                kind="bare",
                text=m.group(0),
                chapter=None,
                section=m.group("section"),
                start=m.start(),
                end=m.end(),
            )
        )
    return hits


def infer_chapter_from_context(
    body: str,
    pos: int,
    chapters: frozenset[str],
) -> str | None:
    """Only same-line cues count — cross-bullet lookback misfires in FAQ lists."""
    line_start = body.rfind("\n", 0, pos) + 1
    window = body[line_start:pos]
    chapter_alt = "|".join(sorted(chapters, key=len, reverse=True))
    fulls = list(
        re.finditer(rf"#/toolbox/manual/({chapter_alt})\b", window),
    )
    if fulls:
        return fulls[-1].group(1)
    hints = list(_CHAPTER_HINT.finditer(window))
    for h in reversed(hints):
        ch = h.group("chapter")
        if ch in chapters:
            return ch
    return None


def collect_manual_link_errors(body: str, registry: ManualRegistry) -> list[str]:
    errors: list[str] = []
    for hit in extract_manual_links(body):
        if hit.kind == "full":
            assert hit.chapter is not None
            if hit.chapter not in registry.chapters:
                errors.append(
                    "unknown chapter "
                    f"{hit.chapter!r} in {hit.text!r} "
                    f"(registered chapters: {sorted(registry.chapters)})"
                )
                continue
            if hit.section is None:
                continue
            owner = registry.owner.get(hit.section)
            if owner is None:
                errors.append(
                    "unknown section "
                    f"{hit.section!r} in {hit.text!r} "
                    f"(not in {_SECTION_IDS_TS.name} registry, incl. aliases)"
                )
            elif owner != hit.chapter:
                errors.append(
                    "section "
                    f"{hit.section!r} is owned by chapter {owner!r}, "
                    f"not {hit.chapter!r} in {hit.text!r}"
                )
            continue

        assert hit.section is not None
        if hit.section not in registry.all_section_ids:
            errors.append(
                "unknown bare section "
                f"{hit.section!r} in {hit.text!r} "
                f"(not in {_SECTION_IDS_TS.name} registry, incl. aliases)"
            )
            continue
        inferred = infer_chapter_from_context(body, hit.start, registry.chapters)
        if inferred is None:
            continue
        owner = registry.owner[hit.section]
        if owner != inferred:
            errors.append(
                "bare section "
                f"{hit.section!r} inferred chapter {inferred!r} from context, "
                f"but registry owner is {owner!r} (near {hit.text!r})"
            )
    return errors


_PRODUCT_HELP_SKILL_NAMES = ("product_help",)


def _skill_bodies(names: tuple[str, ...]) -> str:
    reg = build_system_skill_registry()
    parts: list[str] = []
    for name in names:
        skill = reg.get(name)
        _require(skill is not None, f"{name} skill missing from system registry")
        assert skill is not None
        parts.append(skill.body)
    return "\n".join(parts)


def product_help_bodies() -> str:
    """Concatenate all product_help* bodies that may carry manual deep-links."""
    return _skill_bodies(_PRODUCT_HELP_SKILL_NAMES)


# --- tests -------------------------------------------------------------------


def test_desktop_manual_registry_parses_nonempty():
    reg = load_manual_registry()
    assert "intro" in reg.chapters
    assert "what" in reg.sections_by_chapter["intro"]
    assert "faq" in reg.sections_by_chapter["reference"]
    # aliases from sectionIds.ts must be registered
    assert "collab-overview" in reg.aliases
    assert reg.owner["collab-overview"] == "collaboration"
    assert reg.owner["turnflow"] == "mechanism"


def test_product_help_manual_deeplinks_match_section_registry():
    reg = load_manual_registry()
    body = product_help_bodies()
    hits = extract_manual_links(body)
    assert any(h.kind == "full" for h in hits), "expected at least one full manual deep-link"
    assert any(h.kind == "bare" for h in hits), "expected at least one bare ?s= fragment"
    errors = collect_manual_link_errors(body, reg)
    assert not errors, "product_help* manual deep-link drift:\n- " + "\n- ".join(errors)


def test_intentional_dead_manual_links_fail_gate():
    reg = load_manual_registry()
    body = product_help_bodies()
    poisoned = (
        body
        + "\n深链：`#/toolbox/manual/intro?s=dead_section_xyz`\n"
        + "坏章：`#/toolbox/manual/nosuchchapter?s=what`\n"
        + "裸死链：`?s=dead_bare_xyz`\n"
        + "错章：`#/toolbox/manual/reference?s=what`\n"
        + "同行错章：`#/toolbox/manual/collaboration?s=briefing` 然后 `?s=faq`\n"
    )
    errors = collect_manual_link_errors(poisoned, reg)
    joined = "\n".join(errors)
    assert any("dead_section_xyz" in e for e in errors), joined
    assert any("nosuchchapter" in e for e in errors), joined
    assert any("dead_bare_xyz" in e for e in errors), joined
    assert any("owned by chapter 'intro'" in e and "reference" in e for e in errors), joined
    assert any(
        "bare section 'faq'" in e and "collaboration" in e and "reference" in e
        for e in errors
    ), joined


# Desktop product copy uses「设置 · {侧栏}」；narrow hub is 底栏「我的」+ MorePage 行。
# Cheap fork gate: each desktop page name must share a sentence with 手机,
# and every「我的 → X」must be a real narrow-visible MorePage label.
# 「设置 → 反馈」在 product_help FAQ；手机无此入口，须写在同一句。
_SURFACE_FORK_SKILL_NAMES = _PRODUCT_HELP_SKILL_NAMES
_DESKTOP_SETTINGS_PAGES = ("设置 · 服务商", "设置 · 模型", "设置 · 用量", "设置 → 反馈")
_DESKTOP_MORE_PAGE = (
    _REPO_ROOT / "apps" / "desktop" / "src" / "renderer" / "pages" / "MorePage.tsx"
)
_NARROW_TAB_BAR = (
    _REPO_ROOT
    / "apps"
    / "desktop"
    / "src"
    / "renderer"
    / "components"
    / "layout"
    / "NarrowTabBar.tsx"
)
_NARROW_PRODUCT = (
    _REPO_ROOT / "apps" / "desktop" / "src" / "renderer" / "lib" / "narrowProduct.ts"
)
_NAV_ITEM = re.compile(
    r'label:\s*"(?P<label>[^"]+)",\s*path:\s*"(?P<path>/more[^"]*)"'
)
_TAB_LABEL = re.compile(r'\{ label: "([^"]+)", route:')
_HIDDEN_SETTINGS_PATH = re.compile(r'"(/more/[^"]+)"')
_MOBILE_ARROW_PAGE = re.compile(r"我的 → ([^」/（]+)")


def _sentence_units(body: str) -> list[str]:
    units: list[str] = []
    for para in re.split(r"\n- ", body):
        units.extend(u.strip() for u in para.split("。") if u.strip())
    return units


def _narrow_hidden_settings_paths(src: str) -> frozenset[str]:
    block = re.search(
        r"NARROW_HIDDEN_SETTINGS_PATHS = new Set\(\[([^\]]+)\]\)",
        src,
    )
    _require(
        block is not None,
        f"parse failure: NARROW_HIDDEN_SETTINGS_PATHS not found in {_NARROW_PRODUCT.name}",
    )
    assert block is not None
    return frozenset(_HIDDEN_SETTINGS_PATH.findall(block.group(1)))


def test_product_help_settings_page_names_fork_by_surface():
    """Wide settings names must fork; narrow「我的 →」must be visible MorePage rows."""
    more_src = _DESKTOP_MORE_PAGE.read_text(encoding="utf-8")
    tab_src = _NARROW_TAB_BAR.read_text(encoding="utf-8")
    hidden = _narrow_hidden_settings_paths(_NARROW_PRODUCT.read_text(encoding="utf-8"))
    items = [
        (m.group("label"), m.group("path")) for m in _NAV_ITEM.finditer(more_src)
    ]
    _require(items, f"expected NAV_GROUPS items in {_DESKTOP_MORE_PAGE.name}")
    all_labels = frozenset(label for label, _ in items)
    narrow_labels = frozenset(
        label for label, path in items if path not in hidden
    )
    tab_labels = frozenset(_TAB_LABEL.findall(tab_src))
    more_routes = frozenset(path for _, path in items)

    _require("模型" in all_labels, f"expected 模型 row in {_DESKTOP_MORE_PAGE.name}")
    _require("服务商" in all_labels, f"expected 服务商 row in {_DESKTOP_MORE_PAGE.name}")
    _require("用量" in all_labels, f"expected 用量 row in {_DESKTOP_MORE_PAGE.name}")
    _require("我的" in tab_labels, f"expected 我的 tab in {_NARROW_TAB_BAR.name}")
    _require("/more/model" in more_routes, "desktop /more/model route missing")
    _require("/more/providers" in more_routes, "desktop /more/providers route missing")
    _require("/more/usage" in more_routes, "desktop /more/usage route missing")
    _require("服务商" in narrow_labels, "narrow must keep 服务商")
    _require("反馈" not in narrow_labels, "narrow hides 反馈")

    body = _skill_bodies(_SURFACE_FORK_SKILL_NAMES)
    units = _sentence_units(body)
    for name in _DESKTOP_SETTINGS_PAGES:
        hits = [u for u in units if name in u]
        _require(hits, f"{name!r} disappeared from product_help* (desktop name must stay)")
        unforked = [u for u in hits if "手机" not in u]
        assert not unforked, (
            f"{name!r} appears without a 手机 fork in the same sentence:\n- "
            + "\n- ".join(unforked)
        )

    claimed = [n.strip() for n in _MOBILE_ARROW_PAGE.findall(body)]
    _require(claimed, "expected at least one「我的 → …」narrow path in product_help*")
    invented = [n for n in claimed if n not in narrow_labels]
    assert not invented, (
        "product_help* invented narrow page names not in visible MorePage labels: "
        + ", ".join(invented)
        + f" (have {sorted(narrow_labels)})"
    )


def test_section_ids_parse_failure_message_is_clear():
    paths = _PATHS_TS.read_text(encoding="utf-8")
    try:
        parse_section_registry("export const OTHER = 1;\n", paths)
    except AssertionError as exc:
        msg = str(exc)
        assert "parse failure" in msg
        assert "MANUAL_SECTION_IDS" in msg
    else:
        raise AssertionError("expected parse failure for missing MANUAL_SECTION_IDS")
