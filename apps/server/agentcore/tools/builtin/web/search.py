"""Built-in tool: web_search (via the configured search backend).

Academic literature posture (``search_policy=academic_literature``): junk /
uniformly-weak / empty inject stamps structured ``evidence_gap`` on ToolResult
metadata + JSON payload and sticky ``ToolContext.retrieval_budget.evidence_gap``.
Delivery downgrade (``research_quality``) consumes that true source — not a
separate ``evidence_deficit`` search stamp.
"""

import json
import re
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from agentcore.core.citation_tier import citation_tier_for_url, stamp_citation_tier
from agentcore.core.logging import get_logger
from agentcore.core.net import describe_net_error, site_of
from agentcore.core.types import ToolApproval, ToolCategory
from agentcore.tools.builtin.web.cloud_fallback import (
    CLOUD_FALLBACK_NOTE,
    try_cloud_web_search_fallback,
)
from agentcore.tools.builtin.web.relevance import (
    dropped_host_samples,
    filter_results_for_injection,
    relevance_note,
    unquoted_span,
)
from agentcore.tools.builtin.web.search_backend import (
    FallbackSearchBackend,
    PhaseCallback,
    SearchBackend,
    SearchResult,
    SearXNGBackend,
    TavilyBackend,
    describe_search_error,
    get_search_backend,
    infer_search_language,
    track_phase_durations,
)
from agentcore.tools.builtin.web.search_cache import (
    SearchCacheEntry,
    default_search_cache_registry,
)
from agentcore.tools.builtin.web.source_domains import default_source_domain_registry
from agentcore.tools.protocol import ToolContext, ToolResult, ToolSchema
from agentcore.tools.registration import (
    AUDIENCE_BOTH,
    ToolRegistration,
    ToolSurface,
)

logger = get_logger(__name__)

_DEFAULT_MAX_RESULTS = 8
_MAX_RESULTS_CAP = 12
# Structured JSON results stay readable up to ~12 hits; lift the default 4000
# model-facing budget so a full result set is never truncated into invalid JSON.
_OUTPUT_LIMIT = 8000
# Empty-result honesty: queries with more than this many whitespace-separated
# tokens get an explicit "split to 2–3 core words" tip (schema owns the word-count hint).
_VERBOSE_QUERY_WORD_THRESHOLD = 4

# A3 query contract (检索与交付约束前置提案): mechanical limits at the tool boundary.
# Tunable constants — calibrated near log P95. Overflow: mechanical normalize (quote
# proper-name runs / drop trailing venue+year) then truncate to budget and search with
# explicit note. Word/char overflow never hard-rejects; only absolute length does
# (bomb guard). Never silently rewrite semantics / word order.
# 拉丁词数上限 07-20 按 dev 日志重标定 6→8；08-01 再放宽 8→12 / CJK 32→48，超限改截断。
_QUERY_LATIN_WORD_LIMIT = 12
_QUERY_CJK_CHAR_LIMIT = 48
# 含 CJK 的混合查询用加权字数：拉丁单词每词折算成这么多「字」参与 CJK 字预算，避免逐
# 字符计数把 multi-agent 这类技术词过度惩罚（合理的中英混合技术查询不再被误拒）。
_QUERY_LATIN_WORD_WEIGHT = 4
# 整串绝对长度硬拒（防炸弹）；词数/字数超限走规范化+截断，不走此门。
_QUERY_ABSOLUTE_CHAR_LIMIT = 500
# Quoted phrases (error strings / citations / 书名号专名) are exempt from the
# word/char budget. Regex lives in relevance.py so language-consistency uses the
# same strip set (ASCII quotes + 《》/「」/『』/“”/‘’).
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
# 加权计数用的「拉丁单词」：字母/数字的连续串（含内部连字符/撇号），如 multi-agent、
# GPT-4、OpenAI；整体折 _QUERY_LATIN_WORD_WEIGHT 字。其余非空白字符（CJK 等）按字计。
_LATIN_WORD_RE = re.compile(r"[0-9A-Za-z]+(?:[-'][0-9A-Za-z]+)*")
# Title-case 专名启发式：首字母大写且含小写（Limaye / O'Brien）；全大写短词交给 venue 丢弃。
_TITLE_CASE_TOKEN_RE = re.compile(r"^[A-Z][A-Za-z]*(?:[-'][A-Za-z]+)*$")
_YEAR_TOKEN_RE = re.compile(r"^(?:19|20)\d{2}$")
# 常见 CS venue 缩写（casefold 匹配）；适度覆盖、不膨胀。
_VENUE_TOKENS: frozenset[str] = frozenset(
    {
        "stoc",
        "focs",
        "soda",
        "icalp",
        "icml",
        "neurips",
        "nips",
        "iclr",
        "cvpr",
        "eccv",
        "iccv",
        "acl",
        "emnlp",
        "naacl",
        "aaai",
        "ijcai",
        "kdd",
        "sigmod",
        "vldb",
        "osdi",
        "sosp",
        "nsdi",
        "ccs",
        "chi",
        "www",
        "sigcomm",
        "siggraph",
        "pldi",
        "popl",
        "asplos",
        "eurosys",
        "fast",
        "usenix",
        "ndss",
        "sp",
        "ieee",
        "acm",
    }
)


@dataclass(frozen=True)
class PreparedSearchQuery:
    """Outcome of A3 prepare: actual search query, optional adjustment note, or hard error."""

    query: str
    original_query: str
    adjustment_note: str | None = None
    error: str | None = None


def _query_word_count(query: str) -> int:
    """Count whitespace-separated tokens in a search query (no NLP / rewrite)."""
    return len(query.split())


def _unquoted_span(query: str) -> str:
    """Query text with quoted phrases removed (A3 quote exemption)."""
    return unquoted_span(query)


def _weighted_char_cost(text: str) -> int:
    """A3 加权字数（含 CJK 的混合查询预算）：拉丁单词按 ``_QUERY_LATIN_WORD_WEIGHT``
    字/词折算，其余非空白字符（CJK 等非拉丁字符）按字计。

    合理的中英混合技术查询（如 ``multi-agent orchestration 系统 架构``）因此不再被逐字符
    计数过度惩罚；纯中文查询无拉丁词，口径退化为「非空白字数」，与旧行为一致。
    """
    latin_words = _LATIN_WORD_RE.findall(text)
    remainder = _LATIN_WORD_RE.sub(" ", text)
    non_latin_chars = sum(1 for ch in remainder if not ch.isspace())
    return len(latin_words) * _QUERY_LATIN_WORD_WEIGHT + non_latin_chars


def _is_title_case_proper(token: str) -> bool:
    """Title-case 专名：首字母大写且含小写（Limaye / O'Brien）。全大写短词不当专名。"""
    if not token or token.startswith('"'):
        return False
    if token.isupper() and len(token) <= 5:
        return False
    if not any(c.islower() for c in token):
        return False
    return bool(_TITLE_CASE_TOKEN_RE.match(token))


def _is_low_info_token(token: str) -> bool:
    """尾部可丢的低信息 token：venue 缩写或纯年份（已加引号的不算）。"""
    if not token or token.startswith('"'):
        return False
    if _YEAR_TOKEN_RE.match(token):
        return True
    return token.casefold() in _VENUE_TOKENS


def _tokenize_preserving_quotes(query: str) -> list[tuple[str, bool]]:
    """Whitespace tokenize; ASCII ``"..."`` spans stay one quoted token."""
    parts: list[tuple[str, bool]] = []
    i = 0
    s = query.strip()
    n = len(s)
    while i < n:
        if s[i].isspace():
            i += 1
            continue
        if s[i] == '"':
            j = s.find('"', i + 1)
            if j < 0:
                parts.append((s[i:], False))
                break
            parts.append((s[i : j + 1], True))
            i = j + 1
            continue
        j = i + 1
        while j < n and not s[j].isspace() and s[j] != '"':
            j += 1
        parts.append((s[i:j], False))
        i = j
    return parts


def _quote_proper_name_runs(query: str) -> tuple[str, bool]:
    """Wrap consecutive unquoted Title-case runs (≥2) in ASCII double quotes.

    Already-quoted spans are left untouched. Returns ``(new_query, changed)``.
    """
    tokens = _tokenize_preserving_quotes(query)
    if not tokens:
        return query, False
    out: list[str] = []
    i = 0
    changed = False
    while i < len(tokens):
        text, quoted = tokens[i]
        if quoted or not _is_title_case_proper(text):
            out.append(text)
            i += 1
            continue
        run = [text]
        j = i + 1
        while j < len(tokens):
            nxt, nxt_q = tokens[j]
            if nxt_q or not _is_title_case_proper(nxt):
                break
            run.append(nxt)
            j += 1
        if len(run) >= 2:
            out.append('"' + " ".join(run) + '"')
            changed = True
        else:
            out.append(run[0])
        i = j
    return " ".join(out), changed


def _drop_trailing_low_info(query: str) -> tuple[str, bool]:
    """Drop trailing venue abbreviations / years from the end of the query."""
    tokens = _tokenize_preserving_quotes(query)
    if not tokens:
        return query, False
    end = len(tokens)
    while end > 0:
        text, quoted = tokens[end - 1]
        if quoted or not _is_low_info_token(text):
            break
        end -= 1
    if end == len(tokens):
        return query, False
    if end == 0:
        # Would empty the query — keep original (let hard-reject handle).
        return query, False
    return " ".join(t for t, _ in tokens[:end]), True


def _passes_query_contract(query: str) -> bool:
    """True when ``query`` is within A3 word/char budgets (no message, no rewrite)."""
    unquoted = _unquoted_span(query).strip()
    if not unquoted:
        return True
    if _CJK_RE.search(unquoted):
        return _weighted_char_cost(unquoted) <= _QUERY_CJK_CHAR_LIMIT
    return _query_word_count(unquoted) <= _QUERY_LATIN_WORD_LIMIT


def _absolute_length_error(query: str) -> str | None:
    """Hard-reject message when the raw query exceeds the bomb-guard length."""
    if len(query) <= _QUERY_ABSOLUTE_CHAR_LIMIT:
        return None
    return (
        f"查询极端过长：{len(query)} 字符，上限 {_QUERY_ABSOLUTE_CHAR_LIMIT}。"
        "请大幅缩短后重试。"
    )


def _contract_error_for(query: str) -> str | None:
    """Build the A3 contract error for ``query`` (no rewrite). None if ok.

    Used by ``validate_search_query`` for diagnosis. Execute path uses
    ``prepare_search_query`` (normalize + truncate); word/char overflow there is
    not a hard reject.
    """
    abs_err = _absolute_length_error(query)
    if abs_err is not None:
        return abs_err
    if _passes_query_contract(query):
        return None
    unquoted = _unquoted_span(query).strip()
    tip = "每次只搜 2–3 个核心词；专名用引号/书名号可豁免上限。"
    if _CJK_RE.search(unquoted):
        weighted = _weighted_char_cost(unquoted)
        over = weighted - _QUERY_CJK_CHAR_LIMIT
        return (
            f"查询过长：未加引号折合 {weighted} 字，上限 "
            f"{_QUERY_CJK_CHAR_LIMIT}（超出 {over}；英文词每词折 "
            f"{_QUERY_LATIN_WORD_WEIGHT} 字）。请删约 {over} 字或给长专名加引号后重试。"
            f"{tip}"
        )
    word_count = _query_word_count(unquoted)
    over = word_count - _QUERY_LATIN_WORD_LIMIT
    example = _latin_reject_example(query)
    return (
        f"查询词过多：未加引号 {word_count} 词，上限 "
        f"{_QUERY_LATIN_WORD_LIMIT}（超出 {over}）。"
        f"请改为「{example}」后重试。{tip}"
    )


def _latin_reject_example(query: str) -> str:
    """Smart reject tip: prefer quoting a Title-case run when present, else first N words."""
    quoted, changed = _quote_proper_name_runs(query)
    if changed and _passes_query_contract(quoted):
        return quoted
    if changed:
        # Quoting alone not enough — still show quoted form as the tip base (trim unquoted).
        unquoted = _unquoted_span(quoted).strip()
        kept = " ".join(unquoted.split()[:_QUERY_LATIN_WORD_LIMIT])
        # Rebuild: keep quoted spans from ``quoted``, append trimmed free words.
        q_tokens = _tokenize_preserving_quotes(quoted)
        quoted_parts = [t for t, q in q_tokens if q]
        free = kept.split()
        if quoted_parts and free:
            return " ".join(quoted_parts + free)
        if quoted_parts:
            return " ".join(quoted_parts)
        return kept
    unquoted = _unquoted_span(query).strip()
    return " ".join(unquoted.split()[:_QUERY_LATIN_WORD_LIMIT])


def validate_search_query(query: str) -> str | None:
    """A3: pure contract check — returns error or None. Does **not** rewrite.

    Used by unit tests and callers that only need pass/fail. Execute path uses
    ``prepare_search_query`` (normalize → truncate → search with note).
    """
    return _contract_error_for(query)


def _adjustment_note(
    original: str,
    adjusted: str,
    *,
    quoted: bool,
    dropped: bool,
    truncated: bool = False,
) -> str:
    reasons: list[str] = []
    if quoted:
        reasons.append("专名已加引号")
    if dropped:
        reasons.append("已去掉尾部会议名/年份")
    if truncated:
        reasons.append("已截断至上限")
    reason = "；".join(reasons) if reasons else "已规范化"
    return (
        f"【query_adjusted】原文「{original}」→ 实搜「{adjusted}」（{reason}）。"
    )


def _truncate_to_contract(query: str) -> str:
    """Drop trailing tokens (Latin) or chars (CJK/mixed) until the contract passes.

    Preserves leading content; never returns empty when the input had content.
    """
    if _passes_query_contract(query):
        return query
    unquoted = _unquoted_span(query).strip()
    if _CJK_RE.search(unquoted):
        # CJK / mixed: delete characters from the end.
        working = query.rstrip()
        while working and not _passes_query_contract(working):
            working = working[:-1].rstrip()
        return working if working else query[:1]
    # Latin: delete whitespace-separated tokens from the end (quote-aware).
    tokens = _tokenize_preserving_quotes(query)
    while tokens and not _passes_query_contract(
        " ".join(t for t, _ in tokens)
    ):
        tokens = tokens[:-1]
    if not tokens:
        # Degenerate: keep the first original token so we still search something.
        first = _tokenize_preserving_quotes(query)
        return first[0][0] if first else query
    return " ".join(t for t, _ in tokens)


def prepare_search_query(query: str) -> PreparedSearchQuery:
    """Check → mechanical normalize → truncate → ok / absolute-length hard reject.

    Normalization (deterministic, no LLM):
    1. Quote consecutive Title-case proper-name runs (≥2).
    2. Drop trailing venue abbreviations / years (preferred even when quoting alone suffices).
    3. If still over (Latin or CJK): truncate from the end to the budget and search
       with an explicit ``adjustment_note`` (含「已截断至上限」).
    Only ``len(query) > _QUERY_ABSOLUTE_CHAR_LIMIT`` hard-rejects.
    """
    original = query
    abs_err = _absolute_length_error(query)
    if abs_err is not None:
        return PreparedSearchQuery(query=original, original_query=original, error=abs_err)

    if _passes_query_contract(query):
        return PreparedSearchQuery(query=query, original_query=original)

    quoted_q, did_quote = _quote_proper_name_runs(query)
    working = quoted_q
    did_drop = False
    # Prefer also dropping trailing venue/year after quoting (cleaner SERP), even when
    # quoting alone already brought the query under budget.
    dropped_q, dropped_ok = _drop_trailing_low_info(working)
    if dropped_ok:
        working = dropped_q
        did_drop = True

    if (did_quote or did_drop) and _passes_query_contract(working):
        note = _adjustment_note(original, working, quoted=did_quote, dropped=did_drop)
        return PreparedSearchQuery(
            query=working,
            original_query=original,
            adjustment_note=note,
        )

    # Still over (or normalize produced no usable change) → truncate to budget.
    truncated = _truncate_to_contract(working)
    if truncated and _passes_query_contract(truncated):
        note = _adjustment_note(
            original,
            truncated,
            quoted=did_quote,
            dropped=did_drop,
            truncated=True,
        )
        return PreparedSearchQuery(
            query=truncated,
            original_query=original,
            adjustment_note=note,
        )

    # Truncate failed to produce a passing query (should be rare) — diagnose via validate copy.
    err = _contract_error_for(original)
    return PreparedSearchQuery(
        query=original,
        original_query=original,
        error=err or "查询无法规范化",
    )


def _backend_label(
    backend: SearchBackend | None, *, cached: bool, cloud_fallback: bool = False
) -> str:
    """Stable backend name for ``tool.web_search`` observability."""
    if cached:
        return "cache"
    if cloud_fallback:
        return "cloud_inference"
    if backend is None:
        return "unknown"
    if isinstance(backend, FallbackSearchBackend):
        return "searxng+tavily"
    if isinstance(backend, SearXNGBackend):
        return "searxng"
    if isinstance(backend, TavilyBackend):
        return "tavily"
    return type(backend).__name__


def _is_debate_run(run_id: str) -> bool:
    """Debate carve-out seam: moderator/debater run_ids are ``debate_*`` (existing convention)."""
    return (run_id or "").startswith("debate_")


def _empty_result_note(
    query: str, *, empty_streak: int = 0, web_fetch_retired: bool = False
) -> str:
    """Honest, actionable note when a live/cached search returned zero hits.

    Does not rewrite the query or re-search — feedback only, at the failure site.
    After consecutive empties, require an explicit strategy change (成篇质量定案).
    When ``web_fetch`` is already retired, do not urge deep-read as the next move.
    """
    base = (
        "本次搜索未返回任何结果。可能是查询过于具体/生僻，或搜索引擎暂时受限"
        "（如被限流）。不要据此断定该信息不存在。"
    )
    if _query_word_count(query) > _VERBOSE_QUERY_WORD_THRESHOLD:
        tip = (
            "当前查询词明显过多——建议拆分：一次只搜 2–3 个核心词，"
            "其余概念留到下一轮再搜。"
        )
    else:
        tip = "建议换用更通用或同义的关键词重试，或改用其他信息来源。"
    streak_tip = ""
    if empty_streak >= 2:
        if web_fetch_retired:
            streak_tip = (
                f"【须换策略】已连续 {empty_streak} 次空结果："
                "禁止沿用同一空转 query 再搜；基于已有材料收口写作，"
                "勿再催 web_fetch（已停用）或把继续检索当默认出路。"
            )
        else:
            streak_tip = (
                f"【须换策略】已连续 {empty_streak} 次空结果："
                "禁止沿用同一空转 query 再搜；必须改写关键词/缩短专名、"
                "换权威来源类型，或先对已有命中 web_fetch 深读后再搜。"
            )
    return f"{base}{tip}{streak_tip}"


def _hit_read_nudge() -> str:
    """Soft tip after non-empty SERP: prefer deep-read before another search."""
    return (
        "【少搜多读】已有命中：优先对相关链接 web_fetch 深读核对后再开新搜，"
        "勿把预算耗在重复空转检索上。"
    )


def _strategy_change_note(*, empty_streak: int, web_fetch_retired: bool) -> str:
    """Consecutive empty/weak injection: strategy change (no web_fetch when retired)."""
    if web_fetch_retired:
        return (
            f"【须换策略】已连续 {empty_streak} 次无效/空检索："
            "禁止沿用同一空转 query；基于已有材料收口，"
            "勿再催 web_fetch（已停用）或把继续检索当默认出路。"
        )
    return (
        f"【须换策略】已连续 {empty_streak} 次无效/空检索："
        "禁止沿用同一空转 query；须改写关键词或先 web_fetch 深读已有材料。"
    )



class WebSearchTool:
    """Search the web via a self-hosted SearXNG instance."""

    registration = ToolRegistration(
        surface=ToolSurface.BUILTIN,
        audience=AUDIENCE_BOTH,
    )

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="web_search",
            description=(
                "搜索互联网获取实时信息（新闻、事实、天气、公司信息、概念定义等）。"
                "返回按相关性排序的标题、链接与内容摘要；默认摘要优先。"
                "先一两个聚焦查询看摘要，再决定是否补搜；不要一上来并行抛一堆没看过的猜测。"
                "核对原文用 web_fetch。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "搜索查询词。请精简到核心词"
                            "（超限会自动规范化/截断并明示实搜词；仅极端过长拒绝）："
                            f"纯拉丁语系≤{_QUERY_LATIN_WORD_LIMIT} 个词；"
                            f"含中文时按加权字数≤{_QUERY_CJK_CHAR_LIMIT}"
                            f"（中文按字计、英文单词每词折 {_QUERY_LATIN_WORD_WEIGHT} 字）；"
                            "长专名/法规名用书名号或引号包住可豁免此上限"
                            "（报错原文、专有名等亦同）。"
                            "建议一次只搜 2–3 个核心词，其余概念下一轮再搜。"
                        ),
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "返回结果数量上限，默认 8，最多 12",
                    },
                },
                "required": ["query"],
            },
            category=ToolCategory.RESEARCH,
            approval=ToolApproval.NEVER,
        )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        start = time.monotonic()
        query = (arguments.get("query") or "").strip()
        if not query:
            return ToolResult(
                tool_call_id="",
                success=False,
                output="",
                error="缺少必填参数：query",
                duration_ms=0,
            )

        # A3: mechanical normalize on overflow (quote proper names / drop venue+year /
        # truncate to budget), then search with explicit note; hard reject only when
        # the raw query exceeds the absolute length bomb guard.
        prep = prepare_search_query(query)
        if prep.error is not None:
            logger.info(
                "tool.web_search_query_rejected",
                query=query,
                reason="query_contract",
            )
            # 参数契约拒绝: zero-cost, self-correctable打回 (the error already carries the
            # 「拆分到 2–3 个核心词重试」tip). Flag it so a same-round fan-out of over-long
            # queries never trips the run-scoped tool-failure circuit breaker before the model
            # sees the fix hint — it stays an honest failed call in every other governance path.
            return ToolResult(
                tool_call_id="",
                success=False,
                output="",
                error=prep.error,
                duration_ms=int((time.monotonic() - start) * 1000),
                contract_failure=True,
            )

        query = prep.query
        adjustment_note = prep.adjustment_note
        original_query = prep.original_query if adjustment_note else None
        if adjustment_note:
            logger.info(
                "tool.web_search_query_adjusted",
                query=query,
                original_query=prep.original_query,
                adjusted_query=query,
            )

        try:
            raw = int(arguments.get("max_results", _DEFAULT_MAX_RESULTS))
            max_results = max(1, min(raw, _MAX_RESULTS_CAP))
        except (TypeError, ValueError):
            max_results = _DEFAULT_MAX_RESULTS

        # Conversation-scoped result cache (案例1 #5 检索去重 / 共享检索缓存): a repeat of
        # the same query within the conversation — including across delegated workers,
        # which share the conversation_id — is served from memory instead of re-hitting
        # SearXNG/Tavily, cutting duplicate searches that pressure the shared instance.
        # Unscoped call sites (conversation_id == "") skip the cache entirely.
        # Task-language proxy: pin SearXNG/Tavily locale so IP / default_lang=auto
        # cannot hijack 中文调研 into Japanese SERPs.
        language = infer_search_language(query)
        # A4 debate carve-out: debate runs keep exact keys (no Latin word-order share).
        exact_cache = _is_debate_run(context.run_id)

        cache = (
            default_search_cache_registry().get_or_create(context.conversation_id)
            if context.conversation_id
            else None
        )
        if cache is not None:
            hit = cache.get(
                query, min_results=max_results, language=language, exact=exact_cache
            )
            if hit is not None:
                logger.info("tool.web_search_cache_hit", query=query, result_count=len(hit.results))
                self._record_source_domains(context.conversation_id, hit.results)
                return self._success_result(
                    query,
                    hit.results,
                    start,
                    cached=True,
                    search_policy=context.search_policy or "",
                    backend=None,
                    context=context,
                    original_query=original_query,
                    adjustment_note=adjustment_note,
                )
            # 负缓存（案例1 防重搜风暴）：同一查询刚返回空（常见于引擎 CAPTCHA 后 HTTP 200 +
            # 空结果），短时内直接回空、不再打网，避免降级 worker 对同一空查询反复重搜把共享
            # SearXNG 再次打爆。空结果会自然过期，CAPTCHA 大概率解除后才真正重搜。
            if cache.is_recently_empty(query, language=language, exact=exact_cache):
                logger.info("tool.web_search_negative_cache_hit", query=query)
                return self._success_result(
                    query,
                    [],
                    start,
                    cached=True,
                    search_policy=context.search_policy or "",
                    backend=None,
                    context=context,
                    original_query=original_query,
                    adjustment_note=adjustment_note,
                )

        # A6: wrap the existing on_phase channel to emit structured phase durations.
        on_phase, finish_phases = track_phase_durations(context.on_phase)
        cloud_fallback = False
        backend: SearchBackend | None = None
        try:
            backend = get_search_backend()
            # 工具执行阶段进度 (联网搜索前端展示优化): thread the engine-injected phase
            # callback so the backend can surface「排队中 / 正在检索 / 改用备用引擎」live
            # while this blocking request is in flight. ``None`` on unscoped call
            # sites (tests / evals) — the backend skips it; duration logging still runs
            # when phases fire.
            try:
                results = await backend.search(
                    query,
                    max_results=max_results,
                    on_phase=on_phase,
                    language=language,
                )
            except Exception as primary_exc:
                # Sidecar-only cloud leg: local SearXNG unreachable + inference JWT bound
                # via ContextVar → POST /v1/inference/web_search. Cloud API never binds
                # creds → None → original error. Not for HTTP 403 / empty SERP.
                cloud = await try_cloud_web_search_fallback(
                    primary_exc,
                    query=query,
                    max_results=max_results,
                    language=language,
                    on_phase=on_phase,
                )
                if cloud is None:
                    raise primary_exc
                results = cloud
                cloud_fallback = True
        except Exception as e:
            finish_phases()
            reason = describe_search_error(e, backend)
            logger.warning("tool.web_search_error", query=query, error=reason, error_repr=repr(e))
            # Local SearXNG product copy → stable code for curated user face (never lift
            # ``: detail`` / host tokens from describe_net_error onto failure.message).
            searxng_face = reason.startswith("本地搜索服务")
            return ToolResult(
                tool_call_id="",
                success=False,
                output="",
                error=f"搜索失败：{reason}",
                duration_ms=int((time.monotonic() - start) * 1000),
                failure_code="searxng_unreachable" if searxng_face else None,
                metadata={"code": "searxng_unreachable"} if searxng_face else {},
            )
        if not results:
            # Observability (D5): a LIVE search returned zero results — the passive signal
            # for "search may be degraded" (a CAPTCHA-suspended engine returns HTTP 200 +
            # empty, indistinguishable from a genuine no-hit at the transport layer).
            # Logged at warning so ops can alert on the empty-search RATE in logs/dev.jsonl
            # WITHOUT an active probe that would itself add the CAPTCHA-triggering load this
            # whole change fights. A suppressed repeat logs negative_cache_hit (info) above,
            # so this fires once per genuinely-live empty, not per retry.
            logger.warning("tool.web_search_empty", query=query)

        # 全垃圾 SERP 兜底重试 (tool 层决策): a LIVE, non-empty set whose relevance filter fell
        # to the uniformly-weak path (HTTP 200 + 全垃圾, not caught by the backend's
        # exception-only fallback) is retried ONCE via the Tavily leg when configured — see
        # ``_maybe_retry_weak_serp``. Kept BEFORE ``finish_phases`` so the retry's
        # 「改用备用引擎 / 正在检索」phases are still tracked and drive the waiting UI. The
        # final adopted set flows into the cache write + ``_success_result`` below unchanged.
        # Skip when results already came from cloud inference (sidecar has no Tavily).
        if not cloud_fallback:
            results = await self._maybe_retry_weak_serp(
                query,
                results,
                backend,
                max_results=max_results,
                language=language,
                on_phase=on_phase,
            )
        finish_phases()

        # Cache the outcome: a non-empty set positively (served for the TTL), an EMPTY
        # set negatively (案例1 防重搜风暴) — a query that just came back empty is
        # suppressed briefly so degraded workers re-issuing it don't restorm the shared
        # SearXNG. The negative marker expires fast so a genuine retry happens once the
        # transient cause (engine CAPTCHA) likely cleared.
        if cache is not None:
            if results:
                cache.put(
                    SearchCacheEntry(
                        query=query,
                        results=results,
                        max_results=max_results,
                        stored_at=time.time(),
                        language=language,
                    ),
                    exact=exact_cache,
                )
            else:
                cache.note_empty(query, language=language, exact=exact_cache)
        self._record_source_domains(context.conversation_id, results)
        return self._success_result(
            query,
            results,
            start,
            cached=False,
            search_policy=context.search_policy or "",
            backend=backend,
            context=context,
            original_query=original_query,
            adjustment_note=adjustment_note,
            cloud_fallback=cloud_fallback,
        )

    async def _maybe_retry_weak_serp(
        self,
        query: str,
        results: list[SearchResult],
        backend: SearchBackend,
        *,
        max_results: int,
        language: str,
        on_phase: PhaseCallback | None,
    ) -> list[SearchResult]:
        """全垃圾 SERP 兜底 (tool 层): retry a uniformly-weak LIVE result set once via Tavily.

        Fires ONLY when (1) the primary set is non-empty, (2) it went through the relevance
        filter's uniformly-weak path (reusing that exact判据 — no new threshold),
        and (3) the process backend carries a Tavily fallback leg. The retry result passes
        the SAME relevance filter: non-weak → adopt it; still weak (or the retry raised /
        came back empty) → keep the original raw set (injection empties + quality note).
        Exactly one extra search; the backend's exception-only fallback semantics stay
        untouched (the decision lives here, not there).
        """
        if not results:
            return results
        # Only FallbackSearchBackend carries a Tavily leg; without a key configured the
        # backend is bare SearXNG → zero behaviour change (no retry, no extra cost).
        if not isinstance(backend, FallbackSearchBackend):
            return results
        if not self._is_uniformly_weak(query, results):
            return results

        logger.info("search.weak_serp_retry", query=query, result_count=len(results))
        # Reuse the「改用备用引擎」phase so the waiting UI explains the Tavily leg's latency.
        if on_phase:
            on_phase("fallback")
        try:
            retry = await backend.fallback.search(
                query, max_results=max_results, on_phase=on_phase, language=language
            )
        except Exception as exc:  # noqa: BLE001 - best-effort; a failed retry keeps the primary set
            logger.warning(
                "search.weak_serp_retry_failed",
                query=query,
                error=describe_net_error(exc),
                error_repr=repr(exc),
            )
            return results

        if retry and not self._is_uniformly_weak(query, retry):
            logger.info(
                "search.weak_serp_retry_adopted", query=query, result_count=len(retry)
            )
            return retry
        # Retry no better than the primary (still weak / empty) → keep the original raw
        # set; injection will still empty + note (uniformly_weak empty-success).
        logger.info(
            "search.weak_serp_retry_still_weak",
            query=query,
            primary_count=len(results),
            retry_count=len(retry),
        )
        return results

    @staticmethod
    def _is_uniformly_weak(query: str, results: list[SearchResult]) -> bool:
        """Whether ``results`` would inject as a uniformly-weak set (全垃圾 SERP判据).

        Mirrors ``_success_result``'s pipeline exactly — hard-blocked hits are dropped
        first, then the SAME relevance filter runs — so the retry decision matches the set
        the model would actually see. Pure / cheap: it only re-runs in-memory scoring.
        """
        kept, _ = _split_blocked(results)
        return filter_results_for_injection(query, kept).uniformly_weak

    @staticmethod
    def _record_source_domains(conversation_id: str, results: list[SearchResult]) -> None:
        """Record the domains this search surfaced so a later ``web_fetch`` of one of
        them is recognised as a legitimate deep-read, not a novel-domain exfil (PI-002).

        No-op when unscoped (``conversation_id == ""``) or empty. Best-effort: a failure
        to record only degrades a future read to "treated as novel" (logged, blocked only
        under the opt-in flag), never breaks the search.
        """
        if not conversation_id or not results:
            return
        domains = {site_of(r.url) for r in results}
        domains.discard("")
        if domains:
            default_source_domain_registry().record(conversation_id, domains)

    def _success_result(
        self,
        query: str,
        results: list[SearchResult],
        start: float,
        *,
        cached: bool,
        search_policy: str = "",
        backend: SearchBackend | None = None,
        context: ToolContext | None = None,
        original_query: str | None = None,
        adjustment_note: str | None = None,
        cloud_fallback: bool = False,
    ) -> ToolResult:
        """Build the (identical-shape) success ToolResult for a live or cached hit.

        硬拦（``blocked``）域名在检索出口剔除，不进模型可见结果与 citations；低质
        （``weak``）默认仍回模型；``search_policy=debate_evidence`` 下 weak 与
        商城/词典/医院百科硬剔（可进 dropped）。``search_policy=academic_literature``
        偏论文/DOI、降权百科词典门户，并在 junk/空结果时戳 ``evidence_gap``。
        可被 ``#rN`` 显式引用并带弱源徽标（P2）。
        """
        kept, blocked_hosts = _split_blocked(results)

        # Relevance + injection-length governance (context guardrail): drop near-zero
        # query-overlap hits and shorten snippets before they enter the worker window.
        # Not a domain allow/deny list — see ``relevance.py`` (trace-informed caps).
        filtered = filter_results_for_injection(
            query, kept, search_policy=search_policy or ""
        )
        kept = filtered.kept
        dropped_hosts = dropped_host_samples(filtered.dropped)

        items = [{"title": r.title, "url": r.url, "snippet": r.snippet} for r in kept]
        hosts = [site_of(r.url) for r in kept if site_of(r.url)]
        payload: dict[str, Any] = {"query": query, "results": items}
        if original_query:
            payload["original_query"] = original_query
        notes: list[str] = []
        if adjustment_note:
            notes.append(adjustment_note)
        if cloud_fallback:
            notes.append(CLOUD_FALLBACK_NOTE)
        empty_streak = 0
        budget = getattr(context, "retrieval_budget", None) if context is not None else None
        run_id = getattr(context, "run_id", "") if context is not None else ""
        from agentcore.tools.builtin.web._net import (
            consume_post_read_retire_search_hint,
            is_web_fetch_retired,
        )

        web_fetch_retired = is_web_fetch_retired(run_id or "")
        is_empty_injection = bool(filtered.uniformly_weak) or not items
        if is_empty_injection and budget is not None:
            empty_streak = budget.note_search_empty()
        elif not is_empty_injection and budget is not None:
            budget.note_search_hit()
        if filtered.evidence_gap and budget is not None:
            budget.note_evidence_gap()
        if filtered.uniformly_weak:
            # Uniformly-weak → empty injection + accurate quality note (not the generic
            # empty-SERP tip). Model must rephrase; do not cite SERP scraps.
            rel_note = relevance_note(
                dropped=filtered.dropped,
                truncated_snippets=filtered.truncated_snippets,
                uniformly_weak=True,
                evidence_gap=filtered.evidence_gap,
            )
            if rel_note:
                notes.append(rel_note)
            if empty_streak >= 2:
                notes.append(
                    _strategy_change_note(
                        empty_streak=empty_streak, web_fetch_retired=web_fetch_retired
                    )
                )
        elif not items:
            if filtered.dropped:
                # Backend returned hits but all were filtered (e.g. debate_evidence deny).
                rel_note = relevance_note(
                    dropped=filtered.dropped,
                    truncated_snippets=filtered.truncated_snippets,
                    uniformly_weak=False,
                    evidence_gap=filtered.evidence_gap,
                )
                notes.append(
                    rel_note
                    or _empty_result_note(
                        query,
                        empty_streak=empty_streak,
                        web_fetch_retired=web_fetch_retired,
                    )
                )
            else:
                # Honesty (D5): genuine empty SERP (HTTP 200, zero hits).
                notes.append(
                    _empty_result_note(
                        query,
                        empty_streak=empty_streak,
                        web_fetch_retired=web_fetch_retired,
                    )
                )
        else:
            rel_note = relevance_note(
                dropped=filtered.dropped,
                truncated_snippets=filtered.truncated_snippets,
                uniformly_weak=False,
                evidence_gap=filtered.evidence_gap,
            )
            if rel_note:
                notes.append(rel_note)
            if web_fetch_retired:
                close_hint = consume_post_read_retire_search_hint(run_id or "")
                if close_hint:
                    notes.append(close_hint)
            else:
                notes.append(_hit_read_nudge())
        if notes:
            payload["note"] = "".join(notes)
        if dropped_hosts:
            # Model-visible discard feedback (no schema change / no re-fetch API):
            # workers can see what was trimmed and refine the query if needed.
            payload["dropped_hosts"] = dropped_hosts
        if filtered.evidence_gap:
            # Model-visible twin of metadata so workers see the gap without parsing
            # tool metadata; delivery consumers prefer metadata / budget sticky flag.
            payload["evidence_gap"] = True
        output = json.dumps(payload, ensure_ascii=False)
        citations = [
            stamp_citation_tier(
                {
                    "url": r.url,
                    "title": r.title,
                    "snippet": r.snippet,
                    "site": site_of(r.url),
                    "query": query,
                }
            )
            for r in kept
        ]
        # Render-oriented twin of ``output`` (工具结果富渲染): the client shows the
        # hits as source-style cards (favicon · title · snippet) instead of raw
        # JSON. Carries ``site`` (the parsed display host) so the card needs no
        # client-side URL parsing.
        display = {
            "query": query,
            "results": [
                {"title": r.title, "url": r.url, "snippet": r.snippet, "site": site_of(r.url)}
                for r in kept
            ],
        }
        backend_name = _backend_label(backend, cached=cached, cloud_fallback=cloud_fallback)
        metadata: dict[str, Any] = {
            "result_count": len(items),
            "query": query,
            "hosts": hosts,
            "backend": backend_name,
            "dropped_count": len(filtered.dropped),
        }
        if blocked_hosts:
            metadata["blocked_hosts"] = blocked_hosts
        if filtered.dropped:
            metadata["dropped_hosts"] = dropped_hosts
        if cached:
            metadata["cached"] = True
        if cloud_fallback:
            metadata["cloud_fallback"] = True
        if not items:
            metadata["empty"] = True
        if empty_streak:
            metadata["empty_streak"] = empty_streak
        if filtered.uniformly_weak:
            # Honest observability: uniformly near-irrelevant SERP (empty injection).
            metadata["low_relevance"] = True
        if filtered.evidence_gap:
            # Structured「证据差」for delivery downgrade (not prompt-only).
            metadata["evidence_gap"] = True
        if search_policy:
            metadata["search_policy"] = search_policy
        # 检索观测：query + backend + 命中/剔除计数，便于还原「搜了什么 / 拿回什么」。
        logger.info(
            "tool.web_search",
            query=query,
            hosts=hosts,
            result_count=len(items),
            blocked_count=len(blocked_hosts),
            dropped_count=len(filtered.dropped),
            low_relevance=bool(filtered.uniformly_weak),
            evidence_gap=bool(filtered.evidence_gap),
            empty_streak=empty_streak,
            backend=backend_name,
            cached=cached,
            cloud_fallback=cloud_fallback,
            search_policy=search_policy or "",
        )
        return ToolResult(
            tool_call_id="",
            success=True,
            output=output,
            duration_ms=int((time.monotonic() - start) * 1000),
            output_limit=_OUTPUT_LIMIT,
            metadata=metadata,
            citations=citations or None,
            display=display,
        )


def _host_hint(url: str) -> str:
    """Best-effort host for blocked-hit logging when ``site_of`` is empty."""
    return urlparse(url if "://" in url else f"https://{url}").netloc.removeprefix("www.")


def _split_blocked(
    results: list[SearchResult],
) -> tuple[list[SearchResult], list[str]]:
    """Split hard-blocked (``blocked`` tier) hits out of a result set at the retrieval exit.

    Returns ``(kept, blocked_hosts)``: ``blocked`` hits never reach the model or citations
    (纯垃圾 / 零引用价值域名); only their hosts are surfaced for observability. Shared by
    ``_success_result`` and the weak-SERP retry judge so both operate on the identical kept
    set (a high-relevance blocked hit can't skew the two apart).
    """
    kept: list[SearchResult] = []
    blocked_hosts: list[str] = []
    for r in results:
        if citation_tier_for_url(r.url) == "blocked":
            host = site_of(r.url) or _host_hint(r.url)
            if host:
                blocked_hosts.append(host)
            continue
        kept.append(r)
    return kept, blocked_hosts
