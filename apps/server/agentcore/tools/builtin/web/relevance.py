"""Lightweight web_search result relevance for model-facing injection.

Evidence (dev, no production traffic): conversation
``d63dfc35-d63e-4539-8ec4-543fac794e8b`` / trace ``2cbb9ff853b743e1b89af1b5922cf4d5``
showed workers ingesting SERP junk (Microsoft support, Vietnam gov portals, weather
sites) that shared almost no query tokens — burning context and pushing ceiling.
Domain allow/deny lists are too coarse (``louisvuitton.com`` is on-topic for an LV
case). This module keeps a pure token-overlap heuristic + injection caps; thresholds
are trace-informed defaults, not calibrated on real traffic.

Debate evidence posture (``search_policy=debate_evidence``): additionally drop
``citation_tier=weak`` and mall/dictionary/hospital-encyclopedia hosts (task-book
「商城/词典不算证据」) into ``dropped`` — ordinary research keeps the default policy.

Academic literature posture (``search_policy=academic_literature``): prefer paper /
preprint / DOI hosts; demote encyclopedia / dictionary / portal junk (do **not**
copy the debate deny-all-weak set — preprints and discussion pages may stay). When
the SERP is uniformly weak or yields no preferred academic host, surface a
structured ``evidence_gap`` flag for delivery downgrade consumers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlparse

from agentcore.core.citation_tier import citation_tier_for_url
from agentcore.tools.builtin.web.search_backend import SearchResult

# Caps for what the model sees (not what the backend fetched).
# Trace: default 8 raw hits × long snippets × many parallel searches → context blowup.
_MAX_INJECT_RESULTS = 6
_MIN_KEEP_RESULTS = 2
_SNIPPET_MAX_CHARS = 160
# Fraction of query tokens that must appear in title/snippet/url to "pass".
# Trace junk (support.microsoft.com vs 商标法… / weather.com vs 茉莉奶白…) scored ~0;
# on-topic news cards usually hit ≥2 CJK tokens → comfortably above this floor.
_MIN_SCORE = 0.12
# Academic: demoted hosts ≥ this fraction of score-passers → evidence_gap even if
# a residual scrap was kept (case: encyclopedia/dict/portal-dominated SERP).
_ACADEMIC_JUNK_FRACTION = 0.6

# Per-run search posture (structured signal on RunSpec / ToolContext — never prompt text).
SearchPolicy = Literal["", "debate_evidence", "academic_literature"]
SEARCH_POLICY_DEBATE_EVIDENCE: SearchPolicy = "debate_evidence"
SEARCH_POLICY_ACADEMIC_LITERATURE: SearchPolicy = "academic_literature"

_LATIN_TOKEN_RE = re.compile(r"[A-Za-z]{2,}|\d{2,}")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
# Japanese kana / Korean hangul: shared Han ideographs must NOT count as
# Chinese-consistent (trace 3367d122: YouTube JP SERP passed the CJK gate).
_KANA_RE = re.compile(r"[\u3040-\u30ff]")
_HANGUL_RE = re.compile(r"[\uac00-\ud7af]")
# Shared with A3 query-contract quote exemption (search.py): ASCII quotes + CJK
# book-title / corner / curly quote marks. Chinese legal proper names are typically
# wrapped in 《》; recognising only ASCII quotes mis-rejects those full titles.
_QUOTED_PHRASE_RE = re.compile(
    r'"[^"]*"'
    r"|'[^']*'"
    r"|「[^」]*」"
    r"|『[^』]*』"
    r"|“[^”]*”"
    r"|‘[^’]*’"
    r"|《[^》]*》"
)
# Query-conditioned brand expansions (NOT a domain allowlist): when the query
# already names LV / 路易威登, also match louisvuitton.* hosts/titles.
_BRAND_EXPAND: tuple[tuple[re.Pattern[str], tuple[str, ...]], ...] = (
    # ``LV诉…`` / ``"LV"`` / bare ``LV`` — not a domain allowlist, query-conditioned only.
    (re.compile(r"(?<![A-Za-z])LV(?![A-Za-z])", re.IGNORECASE), ("louisvuitton", "vuitton")),
    (re.compile(r"路易威登"), ("louisvuitton", "vuitton", "lv")),
)

# Debate-evidence hard deny (unknown-tier mall / dict / hospital encyclopedia).
# Weak-tier hosts (baike/zhihu/…) are rejected separately via citation_tier.
_DEBATE_DENY_DOMAINS: frozenset[str] = frozenset(
    {
        # 商城 / 电商
        "jd.com",
        "taobao.com",
        "tmall.com",
        "pinduoduo.com",
        "yangkeduo.com",
        "amazon.com",
        "amazon.cn",
        "suning.com",
        "vip.com",
        "dangdang.com",
        "1688.com",
        "gome.com.cn",
        "kaola.com",
        "youzan.com",
        "dewu.com",
        "smzdm.com",
        # 词典 / 字词条
        "dict.youdao.com",
        "dict.cn",
        "iciba.com",
        "zdic.net",
        "hanyu.baidu.com",
        "cidian.911cha.com",
        "dictionary.cambridge.org",
        "oxfordlearnersdictionaries.com",
        "merriam-webster.com",
        "collinsdictionary.com",
        # 医院百科 / 问诊聚合（非判决或权威通报）
        "xywy.com",
        "haodf.com",
        "39.net",
        "chunyuyisheng.com",
        "dxy.com",
        "familydoctor.com.cn",
        "jianke.com",
        "yaozh.com",
        "baikemy.com",
    }
)
_DEBATE_DENY_PATH_RE = re.compile(
    r"/(?:product|products|item|goods|sku|shop|store|dict|dictionary|baike|"
    r"pedia|encyclop(?:a?edia)?)(?:/|$|\?)",
    re.IGNORECASE,
)

# Academic literature: prefer paper / preprint / DOI / indexed scholarship hosts.
# Not an allowlist — preferred rows float to the inject window; others may stay.
_ACADEMIC_PREFERRED_DOMAINS: frozenset[str] = frozenset(
    {
        "arxiv.org",
        "biorxiv.org",
        "medrxiv.org",
        "ssrn.com",
        "pubmed.ncbi.nlm.nih.gov",
        "ncbi.nlm.nih.gov",
        "doi.org",
        "dx.doi.org",
        "nature.com",
        "science.org",
        "sciencemag.org",
        "sciencedirect.com",
        "springer.com",
        "springerlink.com",
        "ieee.org",
        "ieeexplore.ieee.org",
        "acm.org",
        "dl.acm.org",
        "wiley.com",
        "onlinelibrary.wiley.com",
        "plos.org",
        "frontiersin.org",
        "mdpi.com",
        "tandfonline.com",
        "oup.com",
        "academic.oup.com",
        "nih.gov",
        "semanticscholar.org",
        "openreview.net",
        "aclweb.org",
        "aclanthology.org",
        "cnki.net",
        "wanfangdata.com.cn",
        "cqvip.com",
        "cochranelibrary.com",
        "who.int",
        "nejm.org",
        "thelancet.com",
        "bmj.com",
        "jama.jamanetwork.com",
        "jamanetwork.com",
    }
)
# Path / host heuristics for DOI landing pages not on doi.org itself.
_ACADEMIC_DOI_PATH_RE = re.compile(r"(?:^|/)doi(?:/|$|\?)", re.IGNORECASE)

# Demote only (not debate-style hard deny of all weak): encyclopedia / dictionary /
# mass portals that dominated hollow survey SERPs. Preprint discussion / Zhihu may
# remain as residual when no preferred host exists (still flags evidence_gap).
_ACADEMIC_DEMOTE_DOMAINS: frozenset[str] = frozenset(
    {
        # 百科
        "baike.baidu.com",
        "baike.com",
        "wikipedia.org",
        "zh.wikipedia.org",
        "en.wikipedia.org",
        "baikemy.com",
        # 词典 / 字词条（与辩论 deny 交集，但不扩到商城全集）
        "dict.youdao.com",
        "dict.cn",
        "iciba.com",
        "zdic.net",
        "hanyu.baidu.com",
        "cidian.911cha.com",
        "dictionary.cambridge.org",
        "oxfordlearnersdictionaries.com",
        "merriam-webster.com",
        "collinsdictionary.com",
        "weblio.jp",
        "kotobank.jp",
        # 门户 / 内容农场（案面常见假文献命中）
        "163.com",
        "sohu.com",
        "sina.com.cn",
        "qq.com",
        "ifeng.com",
        "toutiao.com",
        "jianshu.com",
        "csdn.net",
        "blog.csdn.net",
        "zhihu.com",
        "zhuanlan.zhihu.com",
        "baijiahao.baidu.com",
        "wenku.baidu.com",
    }
)
_ACADEMIC_DEMOTE_PATH_RE = re.compile(
    r"/(?:baike|pedia|encyclop(?:a?edia)?|dict|dictionary)(?:/|$|\?)",
    re.IGNORECASE,
)


def unquoted_span(query: str) -> str:
    """Query text with quoted / book-title phrases removed (A3 quote exemption)."""
    return _QUOTED_PHRASE_RE.sub(" ", query or "")


def _query_unquoted_has_cjk(query: str) -> bool:
    return bool(_CJK_RE.search(unquoted_span(query)))


def _result_has_cjk(result: SearchResult) -> bool:
    """Whether title+snippet look Chinese-consistent for a CJK query.

    Requires Han ideographs and rejects Japanese (kana) / Korean (hangul) pages that
    also carry shared kanji — those are off-locale for 中文检索, not "has CJK".
    URL script is ignored (hosts are language-agnostic).
    """
    text = f"{result.title or ''}{result.snippet or ''}"
    if not _CJK_RE.search(text):
        return False
    return not (_KANA_RE.search(text) or _HANGUL_RE.search(text))


@dataclass(frozen=True)
class RelevanceFilterOutcome:
    """Kept / dropped split after relevance + injection-length governance."""

    kept: list[SearchResult]
    dropped: list[SearchResult]
    truncated_snippets: bool
    # True when NOT ONE hit reached ``min_score`` — a uniformly weak SERP (全垃圾结果).
    # Injection returns empty (no min_keep residual); the caller uses this judgement
    # for the model-facing quality note.
    uniformly_weak: bool = False
    # Structured「证据差」signal (academic_literature / uniformly_weak junk): delivery
    # downgrade consumers read this — not just the model-facing note.
    evidence_gap: bool = False


def tokenize_query(query: str) -> set[str]:
    """Deterministic query tokens for overlap scoring (no NLP / stemmer)."""
    q = (query or "").strip()
    if not q:
        return set()
    tokens: set[str] = set()
    for m in _LATIN_TOKEN_RE.finditer(q):
        tokens.add(m.group(0).lower())
    cjk = _CJK_RE.findall(q)
    for ch in cjk:
        tokens.add(ch)
    for i in range(len(cjk) - 1):
        tokens.add(cjk[i] + cjk[i + 1])
    for pattern, extras in _BRAND_EXPAND:
        if pattern.search(q):
            tokens.update(extras)
    return tokens


def score_result(query_tokens: set[str], result: SearchResult) -> float:
    """Overlap fraction of query tokens found in title + snippet + url."""
    if not query_tokens:
        return 1.0
    hay = f"{result.title or ''} {result.snippet or ''} {result.url or ''}".lower()
    hits = sum(1 for t in query_tokens if t.lower() in hay)
    return hits / len(query_tokens)


def _truncate_snippet(snippet: str, *, max_chars: int = _SNIPPET_MAX_CHARS) -> str:
    text = re.sub(r"\s+", " ", (snippet or "").strip())
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def _host_of(url: str) -> str:
    try:
        return urlparse(url if "://" in url else f"https://{url}").netloc.removeprefix(
            "www."
        )
    except Exception:
        return ""


def _domain_matches(host: str, domain: str) -> bool:
    d = domain.casefold()
    h = host.casefold()
    return h == d or h.endswith("." + d)


def debate_evidence_denied(url: str) -> bool:
    """True when a hit must not inject under ``search_policy=debate_evidence``.

    Rejects ``citation_tier=weak`` (baike/zhihu/…) plus mall / dictionary /
    hospital-encyclopedia hosts and product/dict/baike path heuristics.
    """
    if citation_tier_for_url(url) == "weak":
        return True
    host = _host_of(url)
    if host:
        for d in _DEBATE_DENY_DOMAINS:
            if _domain_matches(host, d):
                return True
    try:
        path = urlparse(url if "://" in url else f"https://{url}").path or ""
    except Exception:
        path = ""
    return bool(_DEBATE_DENY_PATH_RE.search(path))


def _apply_debate_evidence_policy(
    passed: list[SearchResult],
) -> tuple[list[SearchResult], list[SearchResult]]:
    """Split score/language survivors into admissible vs debate-denied."""
    keep: list[SearchResult] = []
    denied: list[SearchResult] = []
    for r in passed:
        if debate_evidence_denied(r.url):
            denied.append(r)
        else:
            keep.append(r)
    return keep, denied


def academic_preferred(url: str) -> bool:
    """True when the URL is a preferred paper / preprint / DOI-class host."""
    host = _host_of(url)
    if host:
        for d in _ACADEMIC_PREFERRED_DOMAINS:
            if _domain_matches(host, d):
                return True
    try:
        path = urlparse(url if "://" in url else f"https://{url}").path or ""
    except Exception:
        path = ""
    return bool(_ACADEMIC_DOI_PATH_RE.search(path))


def academic_demoted(url: str) -> bool:
    """True when the hit is encyclopedia / dictionary / portal junk under academic policy.

    Demotion only — not the debate deny-all-weak set. Preferred hosts win; demoted
    rows sort last and are dropped when any preferred/neutral survivor exists.
    """
    if academic_preferred(url):
        return False
    host = _host_of(url)
    if host:
        for d in _ACADEMIC_DEMOTE_DOMAINS:
            if _domain_matches(host, d):
                return True
    try:
        path = urlparse(url if "://" in url else f"https://{url}").path or ""
    except Exception:
        path = ""
    return bool(_ACADEMIC_DEMOTE_PATH_RE.search(path))


def _apply_academic_literature_policy(
    passed: list[SearchResult],
) -> tuple[list[SearchResult], list[SearchResult]]:
    """Prefer academic hosts; demote encyclopedia/dict/portal when better rows exist.

    Order: preferred → neutral → (demoted only if nothing else). Demoted rows are
    dropped into ``denied`` when any preferred or neutral survivor exists so they
    do not consume the inject window.
    """
    preferred: list[SearchResult] = []
    neutral: list[SearchResult] = []
    demoted: list[SearchResult] = []
    for r in passed:
        if academic_preferred(r.url):
            preferred.append(r)
        elif academic_demoted(r.url):
            demoted.append(r)
        else:
            neutral.append(r)
    if preferred or neutral:
        return preferred + neutral, demoted
    # Only demoted survived scoring — keep them (降权, not hard empty) but caller
    # will still raise evidence_gap because no preferred host remains.
    return demoted, []


def _academic_evidence_gap(
    *,
    uniformly_weak: bool,
    passed_before_policy: list[SearchResult],
    kept: list[SearchResult],
) -> bool:
    """Structured gap when academic SERP has no preferred host or is junk-dominated."""
    if uniformly_weak:
        return True
    if not kept:
        return True
    if any(academic_preferred(r.url) for r in kept):
        return False
    # No preferred academic host in injection.
    if passed_before_policy:
        demoted_n = sum(1 for r in passed_before_policy if academic_demoted(r.url))
        if demoted_n / len(passed_before_policy) >= _ACADEMIC_JUNK_FRACTION:
            return True
    # Residual scraps without a paper/DOI host → still a gap for literature tasks.
    return True


def _select_by_score(
    ranked: list[tuple[float, int, SearchResult]],
    *,
    min_keep: int,
    min_score: float,
) -> tuple[list[SearchResult], bool]:
    """Score-threshold selection + min_keep pad for near-misses (pre-language-gate).

    Uniformly weak (zero score-passers) → empty keep — no residual scraps.
    """
    passed = [r for score, _, r in ranked if score >= min_score]
    uniformly_weak = not passed
    if uniformly_weak:
        return [], True
    if len(passed) < min_keep:
        # Pad only with positive-overlap near-misses; never refill with zero-score junk.
        for score, _, r in ranked:
            if len(passed) >= min_keep:
                break
            if score > 0 and r not in passed:
                passed.append(r)
    return passed, uniformly_weak


def _apply_language_consistency(
    ranked: list[tuple[float, int, SearchResult]],
    score_passed: list[SearchResult],
    *,
    min_keep: int,
    min_score: float,
    uniformly_weak: bool,
) -> list[SearchResult]:
    """Drop title+snippet rows with zero CJK when the unquoted query has CJK.

    Not a domain allowlist. Prefer language-consistent rows. When the SERP is
    uniformly weak (no score-passers), return empty — do not pad with scraps.
    """
    consistent_scored = [r for r in score_passed if _result_has_cjk(r)]
    if consistent_scored:
        passed = list(consistent_scored)
        if len(passed) < min_keep:
            for score, _, r in ranked:
                if len(passed) >= min_keep:
                    break
                if score > 0 and _result_has_cjk(r) and r not in passed:
                    passed.append(r)
        return passed

    if uniformly_weak:
        return []

    # No language-consistent score-passers — prefer any consistent row for min_keep
    # (even below min_score) over English-only junk that happened to share Latin tokens.
    consistent_ranked = [r for _, _, r in ranked if _result_has_cjk(r)]
    if consistent_ranked:
        return consistent_ranked[: min(min_keep, len(consistent_ranked))]

    # Score-passers exist but are all language-mismatched: keep a tiny set (honest
    # off-locale signal) rather than emptying a partially overlapping SERP.
    passed = [r for score, _, r in ranked if score >= min_score][:min_keep]
    if len(passed) < min_keep:
        for _, _, r in ranked:
            if len(passed) >= min_keep:
                break
            if r not in passed:
                passed.append(r)
    return passed


def filter_results_for_injection(
    query: str,
    results: list[SearchResult],
    *,
    max_inject: int = _MAX_INJECT_RESULTS,
    min_keep: int = _MIN_KEEP_RESULTS,
    min_score: float = _MIN_SCORE,
    snippet_max: int = _SNIPPET_MAX_CHARS,
    search_policy: SearchPolicy | str = "",
) -> RelevanceFilterOutcome:
    """Rank by query overlap, drop near-zero / language-mismatched hits, cap, shorten.

    Uniformly weak SERPs (no hit ≥ ``min_score``) inject **empty** with
    ``uniformly_weak=True`` so the model rephrases instead of citing scraps.
    When the unquoted query contains CJK, an extra language-consistency layer drops
    rows whose title+snippet have zero CJK (preferring consistent rows for min_keep
    when at least one score-passer exists).
    ``search_policy=debate_evidence`` additionally drops weak-tier + mall/dict/
    hospital-encyclopedia hits into ``dropped``.
    ``search_policy=academic_literature`` prefers paper/DOI hosts, demotes
    encyclopedia/dict/portal junk, and sets ``evidence_gap`` when the inject set
    has no preferred academic host (or the SERP is uniformly weak / junk-heavy).
    """
    if not results:
        return RelevanceFilterOutcome(
            kept=[],
            dropped=[],
            truncated_snippets=False,
            evidence_gap=search_policy == SEARCH_POLICY_ACADEMIC_LITERATURE,
        )

    tokens = tokenize_query(query)
    ranked = sorted(
        ((score_result(tokens, r), i, r) for i, r in enumerate(results)),
        key=lambda t: (-t[0], t[1]),
    )
    # Capture the "not one hit passed" verdict BEFORE language refill —
    # single source of the uniformly-weak判据 (see the outcome).
    score_passed = [r for score, _, r in ranked if score >= min_score]
    uniformly_weak = not score_passed

    if _query_unquoted_has_cjk(query):
        passed = _apply_language_consistency(
            ranked,
            score_passed,
            min_keep=min_keep,
            min_score=min_score,
            uniformly_weak=uniformly_weak,
        )
    else:
        passed, _ = _select_by_score(ranked, min_keep=min_keep, min_score=min_score)

    policy_denied: list[SearchResult] = []
    passed_before_policy = list(passed)
    if search_policy == SEARCH_POLICY_DEBATE_EVIDENCE and passed:
        passed, policy_denied = _apply_debate_evidence_policy(passed)
    elif search_policy == SEARCH_POLICY_ACADEMIC_LITERATURE and passed:
        passed, policy_denied = _apply_academic_literature_policy(passed)

    if uniformly_weak:
        # Empty success — every raw hit is dropped; no min_keep residual.
        dropped = [r for _, _, r in ranked]
        return RelevanceFilterOutcome(
            kept=[],
            dropped=dropped,
            truncated_snippets=False,
            uniformly_weak=True,
            # Academic posture: uniformly-weak SERP is an observable evidence gap
            # (delivery consumers), not only a model-facing note.
            evidence_gap=search_policy == SEARCH_POLICY_ACADEMIC_LITERATURE,
        )

    kept_raw = passed[: max(1, max_inject)] if passed else []
    kept_ids = {id(r) for r in kept_raw}
    dropped = [r for _, _, r in ranked if id(r) not in kept_ids]
    # Policy-denied rows that somehow lacked id identity still surface in dropped.
    for r in policy_denied:
        if id(r) not in kept_ids and r not in dropped:
            dropped.append(r)

    truncated = False
    kept: list[SearchResult] = []
    for r in kept_raw:
        snip = _truncate_snippet(r.snippet, max_chars=snippet_max)
        if snip != (r.snippet or ""):
            kept.append(SearchResult(title=r.title, url=r.url, snippet=snip))
            truncated = True
        else:
            kept.append(r)

    evidence_gap = False
    if search_policy == SEARCH_POLICY_ACADEMIC_LITERATURE:
        evidence_gap = _academic_evidence_gap(
            uniformly_weak=False,
            passed_before_policy=passed_before_policy,
            kept=kept,
        )

    return RelevanceFilterOutcome(
        kept=kept,
        dropped=dropped,
        truncated_snippets=truncated,
        uniformly_weak=False,
        evidence_gap=evidence_gap,
    )


# Uniformly weak (全垃圾): nothing reached ``min_score``, injection is empty.
# Not "engine degraded", and not a default to re-query or hedge known claims.
# This batch is not evidence. Answer from knowledge already held; another query
# only when a claim still lacks a source the model does not have.
_WEAK_SERP_NOTE = (
    "本次结果与查询字面重合不足，疑似离题 SERP（并非确认该信息不存在）。"
    "未注入任何结果，本批不是证据。"
    "用已有知识作答；只有主张仍缺一个你没有的出处时，才换词再搜。"
)

_ACADEMIC_GAP_NOTE = (
    "【证据差】本批检索未拿到论文库/预印本/DOI 等优先来源（或百科词典门户占比过高）。"
    "须报告证据缺口并换论文站策略；禁止脑补成全面综述。"
)


def relevance_note(
    *,
    dropped: list[SearchResult],
    truncated_snippets: bool,
    uniformly_weak: bool = False,
    evidence_gap: bool = False,
) -> str | None:
    """Short model-facing note when filtering changed the payload.

    When ``uniformly_weak`` (not one hit reached ``min_score``), injection is empty
    and the note says this batch is not evidence — it replaces any "dropped N" line
    (there is nothing on-topic to keep). ``evidence_gap`` adds the academic
    literature gap line (structured flag is separate on the ToolResult metadata).
    """
    parts: list[str] = []
    if uniformly_weak:
        parts.append(_WEAK_SERP_NOTE)
    elif dropped:
        hosts: list[str] = []
        for r in dropped:
            h = _host_of(r.url)
            if h and h not in hosts:
                hosts.append(h)
            if len(hosts) >= 4:
                break
        host_bit = f"（例：{'、'.join(hosts)}）" if hosts else ""
        parts.append(
            f"已按与查询的字面重合度剔除 {len(dropped)} 条低相关结果{host_bit}。"
            "若误杀，请把关键专有名词/域名写进查询后重搜；不要把已剔除条目当证据引用。"
        )
    if evidence_gap:
        parts.append(_ACADEMIC_GAP_NOTE)
    if truncated_snippets:
        parts.append(f"摘要已截断至约 {_SNIPPET_MAX_CHARS} 字以控制上下文体积。")
    if not parts:
        return None
    return "".join(parts)


def dropped_host_samples(dropped: list[SearchResult], *, limit: int = 4) -> list[str]:
    """Stable host samples for metadata / display (not a blocklist)."""
    hosts: list[str] = []
    for r in dropped:
        h = _host_of(r.url)
        if h and h not in hosts:
            hosts.append(h)
        if len(hosts) >= limit:
            break
    return hosts


def injection_limits() -> dict[str, Any]:
    """Expose tunables for tests / ops notes."""
    return {
        "max_inject": _MAX_INJECT_RESULTS,
        "min_keep": _MIN_KEEP_RESULTS,
        "min_score": _MIN_SCORE,
        "snippet_max": _SNIPPET_MAX_CHARS,
    }
