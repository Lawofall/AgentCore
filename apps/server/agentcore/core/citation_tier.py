"""来源域名可信度分级（平台层单源）。

``core`` 底层：web 叶工具与 ``runtime.citations`` / 证据台账共用，禁止另造第二套分级器。
名单为假设、待校准（开发期无真实分布）；校准入口见 ``tests/test_citation_quality.py``。
"""

from __future__ import annotations

from typing import Any, Literal
from urllib.parse import urlparse

# official / media / unknown / weak = 可登记可引用；blocked = 硬拦（检索出口剔除、不进 sink）。
CitationTier = Literal["official", "media", "unknown", "weak", "blocked"]

# mid-turn sink 准入（P2：仅拒硬拦）。
_POOL_ADMISSIBLE: frozenset[str] = frozenset({"official", "media", "unknown", "weak"})

_OFFICIAL_DOMAIN_SUFFIXES: frozenset[str] = frozenset(
    {
        "gov.cn",
        "gov",
        "court.gov.cn",
        "wenshu.court.gov.cn",
        "zxgk.court.gov.cn",
        "ipc.gov.cn",
        "cnipa.gov.cn",
        "samr.gov.cn",
        "nmpa.gov.cn",
        "pbc.gov.cn",
        "csrc.gov.cn",
        "cac.gov.cn",
        "spp.gov.cn",
        "moj.gov.cn",
        "mps.gov.cn",
        "mofcom.gov.cn",
        "ndrc.gov.cn",
        "miit.gov.cn",
        "stats.gov.cn",
        "customs.gov.cn",
        "chinatax.gov.cn",
        "npc.gov.cn",
        "supreme.gov",  # 部分境外法院站
    }
)

_MEDIA_DOMAINS: frozenset[str] = frozenset(
    {
        "reuters.com",
        "apnews.com",
        "bbc.com",
        "bbc.co.uk",
        "nytimes.com",
        "wsj.com",
        "ft.com",
        "bloomberg.com",
        "economist.com",
        "scmp.com",
        "nikkei.com",
        "xinhuanet.com",
        "news.cn",
        "people.com.cn",
        "cctv.com",
        "cnr.cn",
        "china.com.cn",
        "caixin.com",
        "thepaper.cn",
        "jiemian.com",
        "yicai.com",
        "cls.cn",
        "stcn.com",
        "21jingji.com",
        "nfnews.com",
        "infzm.com",
        "bjnews.com.cn",  # 新京报（生产样本曾漏判 unknown）
        "chinanews.com.cn",
        "gmw.cn",
        "youth.cn",
        "huanqiu.com",
        "southcn.com",
        "workercn.cn",
        "ce.cn",
        "chinadaily.com.cn",
        "theguardian.com",
        "washingtonpost.com",
        "latimes.com",
        "npr.org",
        "pbs.org",
        "aljazeera.com",
        "afp.com",
    }
)

# 硬拦：纯垃圾 / 零引用价值（UGC 问答、搜索引擎首页/壳、浏览器聚合页、中日文词典站）。
# 精确 host（避免 ``baidu.com`` 后缀误伤文库等子域——子域另列 weak/blocked）。
_BLOCKED_EXACT_HOSTS: frozenset[str] = frozenset(
    {
        "baidu.com",
        "bing.com",
        "google.com",
        "google.com.hk",
        "so.com",
        "sogou.com",
        "yahoo.com",
    }
)

_BLOCKED_DOMAINS: frozenset[str] = frozenset(
    {
        "zhidao.baidu.com",
        "browser.qq.com",
        "wenwen.sogou.com",
        "iask.sina.com.cn",
        "wenwen.baidu.com",
        # 中日文检索常见词典站（后缀匹配含 kanji.jitenon.jp 等子域）
        "weblio.jp",
        "kotobank.jp",
        "jitenon.jp",
    }
)

# 低质：文库 / 百家号 / 题库聚合 / 常见 UGC——可显式引用；辩论检索可硬剔，来源卡不打档位徽标。
_WEAK_DOMAINS: frozenset[str] = frozenset(
    {
        "wenku.baidu.com",
        "baijiahao.baidu.com",
        "easylearn.baidu.com",
        "baike.baidu.com",
        "tieba.baidu.com",
        "jingyan.baidu.com",
        "zhihu.com",
        "zhuanlan.zhihu.com",
        "csdn.net",
        "blog.csdn.net",
        "jianshu.com",
        "douban.com",
        "xiaohongshu.com",
        "jyeoo.com",
        "zxxk.com",
        "21cnjy.com",
        "docin.com",
        "doc88.com",
    }
)


def normalize_citation_url(url: str) -> str:
    """URL 去重键：去掉 ``#fragment`` 与结尾 ``/``（与 :func:`merge_citations` 同口径）。"""
    return (url or "").split("#", 1)[0].rstrip("/")


def _host_of(url: str) -> str:
    host = urlparse(url if "://" in url else f"https://{url}").netloc.casefold()
    return host.removeprefix("www.")


def _domain_matches(host: str, domain: str) -> bool:
    """host 等于 domain，或为其子域。"""
    d = domain.casefold()
    return host == d or host.endswith("." + d)


def citation_tier_for_url(url: str) -> CitationTier:
    """规则表：official / media / weak / blocked / unknown。空 URL → unknown。

    判定序：硬拦 → 官方 → 媒体 → 低质 → unknown。名单为假设、待校准。
    """
    norm = normalize_citation_url(url)
    if not norm:
        return "unknown"
    host = _host_of(norm)
    if not host:
        return "unknown"
    if host in _BLOCKED_EXACT_HOSTS:
        return "blocked"
    for d in _BLOCKED_DOMAINS:
        if _domain_matches(host, d):
            return "blocked"
    # .gov / .gov.cn 及名单内官方域 → official（优先于 media）
    if host.endswith(".gov.cn") or host.endswith(".gov") or host == "gov.cn":
        return "official"
    for d in _OFFICIAL_DOMAIN_SUFFIXES:
        if _domain_matches(host, d):
            return "official"
    for d in _MEDIA_DOMAINS:
        if _domain_matches(host, d):
            return "media"
    for d in _WEAK_DOMAINS:
        if _domain_matches(host, d):
            return "weak"
    return "unknown"


def citation_pool_admissible(tier: str) -> bool:
    """该 tier 是否允许进入 mid-turn 汇入 sink（P2：仅拒 ``blocked``）。"""
    return tier in _POOL_ADMISSIBLE


def stamp_citation_tier(citation: dict[str, Any]) -> dict[str, Any]:
    """确保 citation dict 带 ``tier``（已有非空字符串则保留，否则按 URL 判定）。"""
    existing = citation.get("tier")
    if isinstance(existing, str) and existing.strip():
        return citation
    url = citation.get("url") or ""
    out = dict(citation)
    out["tier"] = citation_tier_for_url(url)
    return out
