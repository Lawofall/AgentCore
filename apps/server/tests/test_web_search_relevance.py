"""web_search relevance filter (trace 2cbb9ff853b743e1b89af1b5922cf4d5).

Guards the model-facing injection path: SERP junk that shares almost no query
tokens must not fill worker context. Pure unit + one offline tool integration.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentcore.tools.builtin.web import search as search_mod
from agentcore.tools.builtin.web.relevance import (
    filter_results_for_injection,
    relevance_note,
    score_result,
    tokenize_query,
)
from agentcore.tools.builtin.web.search import WebSearchTool
from agentcore.tools.builtin.web.search_backend import SearchResult
from agentcore.tools.protocol import ToolContext
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace.server import ServerWorkspace


def _ctx() -> ToolContext:
    return ToolContext.create(
        execution_id="e",
        run_id="s",
        agent_id="a",
        backend=ServerWorkspace(root=Path("."), sandbox=SubprocessSandbox()),
        user_id="u",
        conversation_id="",
    )


def _r(title: str, url: str, snippet: str = "") -> SearchResult:
    return SearchResult(title=title, url=url, snippet=snippet)


def test_tokenize_query_cjk_bigrams_and_latin():
    tokens = tokenize_query('LV诉茉莉奶白 商标 2026')
    assert "lv" in tokens
    assert "茉莉" in tokens or ("茉" in tokens and "莉" in tokens)
    assert "商标" in tokens
    assert "2026" in tokens
    # Brand expansion when query names LV / 路易威登
    assert "louisvuitton" in tokens or "vuitton" in tokens


def test_score_result_junk_near_zero_on_topic_high():
    q = "茉莉奶白 官方回应 声明 一审判决 上诉 公关"
    tokens = tokenize_query(q)
    junk = _r("Weather", "https://weather.com/", "Today forecast")
    hit = _r(
        "茉莉奶白回应LV诉讼",
        "https://mp.weixin.qq.com/s/x",
        "茉莉奶白官方就一审判决发表声明",
    )
    assert score_result(tokens, junk) < 0.12
    assert score_result(tokens, hit) >= 0.12


def test_filter_ranks_on_topic_first_and_drops_excess_junk():
    q = "茉莉奶白 官方回应 声明 一审判决 上诉 公关"
    results = [
        _r("Weather", "https://weather.com/", "forecast"),
        _r("OpenWeather", "https://openweathermap.org/", "API"),
        _r("AccuWeather", "https://accuweather.com/", "rain"),
        _r(
            "茉莉奶白回应LV诉讼",
            "https://mp.weixin.qq.com/s/x",
            "茉莉奶白官方就一审判决发表声明与上诉安排",
        ),
        _r(
            "公关声明：一审判决后的回应",
            "https://thepaper.cn/newsDetail_x",
            "茉莉奶白官方回应与上诉公关安排",
        ),
        _r("Zoom Earth", "https://zoom.earth/", "satellite"),
    ]
    out = filter_results_for_injection(q, results)
    kept_urls = [r.url for r in out.kept]
    assert "https://mp.weixin.qq.com/s/x" in kept_urls
    assert "https://thepaper.cn/newsDetail_x" in kept_urls
    # With ≥min_keep on-topic hits, zero-overlap weather/map junk is dropped.
    assert "https://weather.com/" not in kept_urls
    assert "https://openweathermap.org/" not in kept_urls
    assert "https://zoom.earth/" not in kept_urls
    assert len(out.dropped) >= 3


def test_filter_uniformly_weak_returns_empty_success():
    """Uniform junk → empty keep + uniformly_weak (no min_keep residual scraps)."""
    q = "茉莉奶白 官方回应 声明 一审判决"
    results = [_r(f"t{i}", f"https://weather{i}.example/", "forecast") for i in range(6)]
    out = filter_results_for_injection(q, results)
    assert out.kept == []
    assert len(out.dropped) == 6
    assert out.uniformly_weak is True


def test_filter_not_uniformly_weak_when_any_hit_passes():
    q = "茉莉奶白 官方回应 声明 一审判决 上诉 公关"
    results = [
        _r("Weather", "https://weather.com/", "forecast"),
        _r(
            "茉莉奶白回应LV诉讼",
            "https://mp.weixin.qq.com/s/x",
            "茉莉奶白官方就一审判决发表声明与上诉安排",
        ),
    ]
    out = filter_results_for_injection(q, results)
    assert out.uniformly_weak is False


def test_filter_empty_results_not_flagged_uniformly_weak():
    # An empty SERP is "empty" (its own honest note), not "uniformly weak".
    out = filter_results_for_injection("q", [])
    assert out.uniformly_weak is False


def test_relevance_note_uniformly_weak_upgrades_to_quality_warning():
    # 弱结果诚实提示：uniformly-weak → 字面重合不足 / 疑似离题 SERP（非「搜索引擎降级」）。
    dropped = [_r("W", "https://weather.com/", "")]
    note = relevance_note(dropped=dropped, truncated_snippets=False, uniformly_weak=True)
    assert note is not None
    assert "字面重合不足" in note
    assert "离题 SERP" in note
    assert "搜索引擎降级" not in note
    assert "待核实" in note
    assert "低相关" not in note  # the dropped-count wording is replaced, not appended


def test_relevance_note_weak_warning_still_appends_truncation():
    note = relevance_note(dropped=[], truncated_snippets=True, uniformly_weak=True)
    assert note is not None
    assert "字面重合不足" in note
    assert "摘要已截断" in note


def test_filter_caps_inject_count_and_truncates_snippets():
    q = "深圳 天气"
    long = "晴 " * 120
    results = [
        _r(f"深圳天气日报{i}", f"https://news.example/{i}", long) for i in range(10)
    ]
    out = filter_results_for_injection(q, results, max_inject=6)
    assert len(out.kept) <= 6
    assert out.truncated_snippets is True
    assert all(len(r.snippet) <= 160 for r in out.kept)


def test_relevance_note_mentions_dropped_hosts():
    dropped = [
        _r("W", "https://weather.com/", ""),
        _r("M", "https://support.microsoft.com/", ""),
    ]
    note = relevance_note(dropped=dropped, truncated_snippets=False)
    assert note is not None
    assert "低相关" in note
    assert "weather.com" in note


def test_language_filter_drops_english_only_when_query_has_cjk():
    """CJK query + English-only title/snippet → drop (not a domain allowlist)."""
    q = "起诉第三者 立案 实务"
    results = [
        _r(
            "起诉第三者立案实务研究",
            "https://zh.example/ok",
            "第三人侵权与立案审查要点",
        ),
        _r(
            "Third-party liability filing guide",
            "https://en.example/junk",
            "How to file a third-party claim in common law",
        ),
        _r(
            "Microsoft Support",
            "https://support.microsoft.com/kb/1",
            "Sign in to your account",
        ),
    ]
    out = filter_results_for_injection(q, results)
    kept_urls = [r.url for r in out.kept]
    assert "https://zh.example/ok" in kept_urls
    assert "https://en.example/junk" not in kept_urls
    assert "https://support.microsoft.com/kb/1" not in kept_urls
    assert any(r.url == "https://en.example/junk" for r in out.dropped)


def test_language_filter_drops_japanese_kana_when_query_has_cjk():
    """CJK query must not treat Japanese (kanji+kana) as Chinese-consistent.

    Regression: trace 3367d122 injected YouTube JP Play Store hits for a 中文法律 query
    because shared Han ideographs alone passed the old ``_result_has_cjk`` probe.
    """
    q = "消费者调查问卷 商标混淆可能性 认知实验 司法认定"
    results = [
        _r(
            "商标混淆可能性消费者调查的司法采信",
            "https://zh.example/survey",
            "法院对混淆认知实验证据的审查要点",
        ),
        _r(
            "YouTube - Google Play のアプリ",
            "https://play.google.com/store/apps/details?id=com.google.android.youtube&hl=ja",
            "自分や家族に合った視聴体験 オンライン動画",
        ),
        _r(
            "YouTube Japan",
            "https://blog.youtube/intl/ja-jp/",
            "YouTube で 2026 FIFA ワールドカップ を視聴する方法",
        ),
    ]
    out = filter_results_for_injection(q, results)
    kept_urls = [r.url for r in out.kept]
    assert "https://zh.example/survey" in kept_urls
    assert not any("youtube" in u or "play.google.com" in u for u in kept_urls)
    assert out.uniformly_weak is False


def test_language_filter_min_keep_prefers_cjk_over_english():
    """When some rows have CJK, min_keep must not pad with English-only junk."""
    q = "商标法 司法解释"
    results = [
        _r("Trademark FAQ", "https://en.example/a", "USPTO overview"),
        _r("WIPO guide", "https://en.example/b", "Madrid Protocol"),
        _r("商标法司法解释要点", "https://zh.example/c", "最高法司法解释摘要"),
        _r("Weather", "https://weather.com/", "forecast"),
    ]
    out = filter_results_for_injection(q, results, min_keep=2)
    kept_urls = [r.url for r in out.kept]
    assert kept_urls == ["https://zh.example/c"]
    assert all(
        any("\u4e00" <= ch <= "\u9fff" for ch in f"{r.title}{r.snippet}") for r in out.kept
    )


def test_language_filter_all_english_uniformly_weak_returns_empty():
    """All language-mismatched + zero score-passers → empty (no min_keep residual)."""
    q = "起诉第三者 立案"
    results = [
        _r(f"English hit {i}", f"https://en.example/{i}", f"third party filing {i}")
        for i in range(5)
    ]
    out = filter_results_for_injection(q, results, min_keep=2)
    assert out.kept == []
    assert out.uniformly_weak is True
    assert len(out.dropped) == 5


def test_debate_evidence_policy_rejects_weak_keeps_media():
    """辩论姿态：weak 不注入；普通姿态保留 weak。"""
    from agentcore.tools.builtin.web.relevance import SEARCH_POLICY_DEBATE_EVIDENCE

    q = "茉莉奶白 官方回应 声明 一审判决 上诉 公关"
    weak = _r(
        "茉莉奶白回应LV诉讼百科",
        "https://baike.baidu.com/item/x",
        "茉莉奶白官方就一审判决发表声明与上诉安排",
    )
    media = _r(
        "茉莉奶白回应LV诉讼",
        "https://thepaper.cn/newsDetail_x",
        "茉莉奶白官方就一审判决发表声明与上诉安排",
    )
    default_out = filter_results_for_injection(q, [weak, media])
    assert "https://baike.baidu.com/item/x" in [r.url for r in default_out.kept]
    assert "https://thepaper.cn/newsDetail_x" in [r.url for r in default_out.kept]

    debate_out = filter_results_for_injection(
        q, [weak, media], search_policy=SEARCH_POLICY_DEBATE_EVIDENCE
    )
    kept_urls = [r.url for r in debate_out.kept]
    assert "https://baike.baidu.com/item/x" not in kept_urls
    assert "https://thepaper.cn/newsDetail_x" in kept_urls
    assert any(r.url == "https://baike.baidu.com/item/x" for r in debate_out.dropped)


def test_debate_evidence_policy_denies_mall_and_dict_hosts():
    from agentcore.tools.builtin.web.relevance import (
        SEARCH_POLICY_DEBATE_EVIDENCE,
        debate_evidence_denied,
    )

    assert debate_evidence_denied("https://item.jd.com/100.html")
    assert debate_evidence_denied("https://dict.youdao.com/w/商标")
    assert debate_evidence_denied("https://www.xywy.com/baike/x")
    assert debate_evidence_denied("https://news.example/product/sku-1")
    assert not debate_evidence_denied("https://www.reuters.com/world/foo")

    q = "茉莉奶白 商标 一审判决 上诉"
    mall = _r(
        "茉莉奶白 商标周边 京东",
        "https://item.jd.com/100.html",
        "茉莉奶白商标相关商品 一审判决热议 上诉",
    )
    media = _r(
        "茉莉奶白商标一审判决",
        "https://caixin.com/a/b",
        "茉莉奶白商标一审判决与上诉进展",
    )
    out = filter_results_for_injection(
        q, [mall, media], search_policy=SEARCH_POLICY_DEBATE_EVIDENCE
    )
    assert [r.url for r in out.kept] == ["https://caixin.com/a/b"]
    assert any("jd.com" in r.url for r in out.dropped)


def test_language_filter_skips_when_cjk_only_inside_quotes():
    """Quoted/书名号 CJK does not arm the language gate — unquoted span has no CJK."""
    q = "《商标法》 LV"
    results = [
        _r(
            "Louis Vuitton trademark FAQ",
            "https://en.example/lv",
            "LV brand guide louisvuitton",
        ),
        _r("WIPO LV case", "https://en.example/wipo", "LV opposition louisvuitton"),
    ]
    out = filter_results_for_injection(q, results, min_keep=2)
    # Gate off → English rows may be kept (score / min_keep path), not force-dropped.
    assert len(out.kept) >= 1
    assert out.uniformly_weak is False


@pytest.mark.asyncio
async def test_web_search_tool_filters_junk_before_model(monkeypatch):
    class _FakeBackend:
        async def search(self, query, max_results=5, on_phase=None, *, language=None):
            return [
                _r("Weather", "https://weather.com/", "Today forecast"),
                _r("Microsoft Support", "https://support.microsoft.com/kb/1", "Sign in"),
                _r(
                    "茉莉奶白官方回应一审判决",
                    "https://mp.weixin.qq.com/s/ok",
                    "茉莉奶白就商标侵权一审判决发表声明",
                ),
                _r(
                    "上诉公关安排说明",
                    "https://thepaper.cn/newsDetail_ok",
                    "茉莉奶白官方回应声明与上诉安排",
                ),
                _r("OpenWeather", "https://openweathermap.org/", "API docs"),
            ]

    monkeypatch.setattr(search_mod, "get_search_backend", lambda: _FakeBackend())
    result = await WebSearchTool().execute(
        {"query": "茉莉奶白 官方回应 声明 一审判决 上诉 公关"},
        _ctx(),
    )
    assert result.success is True
    payload = json.loads(result.output)
    urls = [r["url"] for r in payload["results"]]
    assert "https://mp.weixin.qq.com/s/ok" in urls
    assert "https://thepaper.cn/newsDetail_ok" in urls
    assert "https://weather.com/" not in urls
    assert "https://support.microsoft.com/kb/1" not in urls
    assert "https://openweathermap.org/" not in urls
    assert result.metadata.get("dropped_count", 0) >= 2
    assert "dropped_hosts" in payload
    assert "note" in payload
    assert "低相关" in payload["note"]
    assert result.metadata.get("backend") == "searxng"


@pytest.mark.asyncio
async def test_web_search_debate_policy_rejects_weak_keeps_default(monkeypatch):
    """辩论姿态拒绝 weak；普通姿态保留 weak。"""
    from dataclasses import replace

    class _FakeBackend:
        async def search(self, query, max_results=5, on_phase=None, *, language=None):
            return [
                _r(
                    "茉莉奶白回应百科",
                    "https://baike.baidu.com/item/x",
                    "茉莉奶白官方就一审判决发表声明与上诉安排",
                ),
                _r(
                    "茉莉奶白官方回应一审判决",
                    "https://thepaper.cn/newsDetail_ok",
                    "茉莉奶白官方回应声明与上诉安排",
                ),
            ]

    monkeypatch.setattr(search_mod, "get_search_backend", lambda: _FakeBackend())
    q = "茉莉奶白 官方回应 声明 一审判决 上诉 公关"
    default = await WebSearchTool().execute({"query": q}, _ctx())
    default_urls = [r["url"] for r in json.loads(default.output)["results"]]
    assert "https://baike.baidu.com/item/x" in default_urls

    debate = await WebSearchTool().execute(
        {"query": q}, replace(_ctx(), search_policy="debate_evidence")
    )
    debate_urls = [r["url"] for r in json.loads(debate.output)["results"]]
    assert "https://baike.baidu.com/item/x" not in debate_urls
    assert "https://thepaper.cn/newsDetail_ok" in debate_urls
    assert debate.metadata.get("search_policy") == "debate_evidence"


def test_academic_literature_prefers_arxiv_demotes_baike():
    """学术挡位：arxiv 优先；百科降权（有论文站时不进 inject）。"""
    from agentcore.tools.builtin.web.relevance import (
        SEARCH_POLICY_ACADEMIC_LITERATURE,
        academic_demoted,
        academic_preferred,
    )

    assert academic_preferred("https://arxiv.org/abs/2301.00001")
    assert academic_preferred("https://pubmed.ncbi.nlm.nih.gov/12345/")
    assert academic_preferred("https://doi.org/10.1000/xyz")
    assert academic_demoted("https://baike.baidu.com/item/x")
    assert academic_demoted("https://dict.youdao.com/w/foo")
    assert academic_demoted("https://www.163.com/news/x")
    # 不照搬辩论：知乎可 demote，但预印本讨论页不因 weak 被硬剔
    assert not academic_preferred("https://zhuanlan.zhihu.com/p/1")
    assert academic_demoted("https://zhuanlan.zhihu.com/p/1")

    q = "medical report generation radiology R2Gen MIMIC"
    arxiv = _r(
        "R2Gen radiology report generation",
        "https://arxiv.org/abs/2010.00001",
        "medical report generation R2Gen MIMIC radiology",
    )
    baike = _r(
        "医学报告生成 百科",
        "https://baike.baidu.com/item/医学报告",
        "medical report generation radiology R2Gen MIMIC 百科词条",
    )
    default_out = filter_results_for_injection(q, [baike, arxiv])
    assert "https://baike.baidu.com/item/医学报告" in [r.url for r in default_out.kept]

    academic_out = filter_results_for_injection(
        q, [baike, arxiv], search_policy=SEARCH_POLICY_ACADEMIC_LITERATURE
    )
    kept_urls = [r.url for r in academic_out.kept]
    assert "https://arxiv.org/abs/2010.00001" in kept_urls
    assert "https://baike.baidu.com/item/医学报告" not in kept_urls
    assert academic_out.evidence_gap is False


def test_academic_literature_junk_serp_sets_evidence_gap():
    """百科/词典/门户主导 → evidence_gap；默认姿态不戳。"""
    from agentcore.tools.builtin.web.relevance import SEARCH_POLICY_ACADEMIC_LITERATURE

    q = "医学报告生成 近三年 文献综述 radiology"
    junk = [
        _r(
            "医学报告生成百科",
            "https://baike.baidu.com/item/x",
            "医学报告生成 近三年 文献综述 radiology",
        ),
        _r(
            "report generation 词典",
            "https://dict.youdao.com/w/report",
            "医学报告生成 近三年 文献综述 radiology",
        ),
        _r(
            "医学报告门户稿",
            "https://www.163.com/dy/article/x.html",
            "医学报告生成 近三年 文献综述 radiology",
        ),
        _r(
            "知乎讨论",
            "https://zhuanlan.zhihu.com/p/99",
            "医学报告生成 近三年 文献综述 radiology",
        ),
    ]
    default_out = filter_results_for_injection(q, junk)
    assert default_out.evidence_gap is False
    assert len(default_out.kept) >= 1

    academic_out = filter_results_for_injection(
        q, junk, search_policy=SEARCH_POLICY_ACADEMIC_LITERATURE
    )
    assert academic_out.evidence_gap is True
    # 全是 demoted → 仍可残留注入，但必须标 gap
    note = relevance_note(
        dropped=academic_out.dropped,
        truncated_snippets=academic_out.truncated_snippets,
        uniformly_weak=academic_out.uniformly_weak,
        evidence_gap=academic_out.evidence_gap,
    )
    assert note is not None
    assert "证据差" in note


def test_academic_literature_uniformly_weak_sets_evidence_gap():
    from agentcore.tools.builtin.web.relevance import SEARCH_POLICY_ACADEMIC_LITERATURE

    q = "起诉第三者 立案 商标"
    results = [
        _r(f"Weather {i}", f"https://weather.com/{i}", f"forecast {i}") for i in range(4)
    ]
    out = filter_results_for_injection(
        q, results, search_policy=SEARCH_POLICY_ACADEMIC_LITERATURE
    )
    assert out.uniformly_weak is True
    assert out.kept == []
    assert out.evidence_gap is True


@pytest.mark.asyncio
async def test_web_search_academic_policy_stamps_evidence_gap(monkeypatch):
    """工具结果 metadata / payload 带 evidence_gap，供降档块消费。"""
    from dataclasses import replace

    from agentcore.tools.protocol import RetrievalBudgetState

    class _FakeBackend:
        async def search(self, query, max_results=5, on_phase=None, *, language=None):
            return [
                _r(
                    "医学报告百科",
                    "https://baike.baidu.com/item/x",
                    "医学报告生成 近三年 文献 radiology MIMIC",
                ),
                _r(
                    "词典条",
                    "https://iciba.com/word/report",
                    "医学报告生成 近三年 文献 radiology MIMIC",
                ),
                _r(
                    "门户稿",
                    "https://www.sohu.com/a/1",
                    "医学报告生成 近三年 文献 radiology MIMIC",
                ),
            ]

    monkeypatch.setattr(search_mod, "get_search_backend", lambda: _FakeBackend())
    budget = RetrievalBudgetState(limit=10)
    q = "医学报告生成 近三年 文献 radiology MIMIC"
    result = await WebSearchTool().execute(
        {"query": q},
        replace(_ctx(), search_policy="academic_literature", retrieval_budget=budget),
    )
    assert result.success is True
    assert result.metadata.get("search_policy") == "academic_literature"
    assert result.metadata.get("evidence_gap") is True
    payload = json.loads(result.output)
    assert payload.get("evidence_gap") is True
    assert "证据差" in (payload.get("note") or "")
    assert budget.evidence_gap is True


def test_parse_search_policy_recognises_academic_literature():
    from agentcore.runtime.runs.builder import _parse_search_policy

    assert _parse_search_policy("academic_literature") == "academic_literature"
    assert _parse_search_policy("debate_evidence") == "debate_evidence"
    assert _parse_search_policy("unknown") == ""
    assert _parse_search_policy(None) == ""
