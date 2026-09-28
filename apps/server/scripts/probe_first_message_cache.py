"""首次发消息的前缀缓存：同一段正文，换不换对话 id。

DeepSeek 要整段对齐已落盘的前缀单位。公共前缀通常要先被看见两次，第三次才命中。
本脚本用 dev 账号自己的 Key（拒绝平台池）打两串，每串三枪，枪与枪之间等缓存落盘：

1. ``same-session``：三枪共用一个 ``conversation_id``（OpenCode 会把它写成 ``x-opencode-session``）。
2. ``new-chat``：三枪各一个新对话 id，工具表和 system 与上一串逐字节相同，只换用户句。

若同一会话的第三枪命中很高、新对话仍接近 0，会话头把缓存隔开了。
若新对话第一枪就吃到同一段前缀，会话头没有隔开磁盘缓存。

从 ``apps/server`` 跑::

    uv run python scripts/probe_first_message_cache.py
    uv run python scripts/probe_first_message_cache.py --official

``--official`` 选用 dev 账号里 ``api.deepseek.com`` 那一条，不走 OpenCode。
无 Key 则退出。不读 ``PLATFORM_API_KEY``。
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import uuid
from dataclasses import dataclass
from urllib.parse import urlparse

from agentcore.core.errors import LLMError
from agentcore.core.log_context import log_context, new_trace_id
from agentcore.evals.harness import (
    _credentials_from_dev_byok,
    _credentials_from_eval_env,
)
from agentcore.llm.byok_provider_presets import (
    is_opencode_byok_endpoint,
    match_byok_provider_preset,
)
from agentcore.llm.factory import build_provider
from agentcore.llm.provider.protocol import LLMMessage, LLMRequest, TokenUsage

# 长到越过常见缓存粒度；两串必须逐字节相同。
_STABLE_SYSTEM = ("Stable prefix block for first-message cache probe. " * 160).strip()
_PAUSE_S = 8.0

_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "alpha_probe",
            "description": "No-op probe. Do not call.",
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "beta_probe",
            "description": "Second no-op probe. Do not call.",
            "parameters": {
                "type": "object",
                "properties": {
                    "note": {"type": "string", "description": "Ignored."},
                },
                "additionalProperties": False,
            },
        },
    },
]


@dataclass(frozen=True)
class Hop:
    series: str
    index: int
    conversation_id: str
    usage: TokenUsage
    latency_ms: int


def _tools_fingerprint(tools: list[dict]) -> str:
    raw = json.dumps(tools, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def _host(base_url: str) -> str:
    parsed = urlparse(base_url)
    return parsed.netloc or base_url


def _ratio(usage: TokenUsage) -> float | None:
    total = int(usage.input_tokens or 0)
    if total <= 0:
        return None
    return int(usage.cache_hit_tokens or 0) / total


def _reported(usage: TokenUsage) -> bool:
    return int(usage.cache_hit_tokens or 0) > 0 or int(usage.cache_miss_tokens or 0) > 0


async def _credentials():
    env_creds = _credentials_from_eval_env()
    if env_creds is not None:
        return env_creds
    byok = await _credentials_from_dev_byok()
    if byok is None:
        raise SystemExit(
            "没有 EVAL_DEEPSEEK_API_KEY，也没有 dev 账号自己的 Key。"
            "拒绝 PLATFORM_API_KEY。"
        )
    return byok


async def _official_credentials():
    """dev 账号里预设 id 为 ``deepseek`` 的那一条（``api.deepseek.com``）。"""
    from agentcore.db.base import async_session_factory
    from agentcore.db.repositories import UserRepository
    from agentcore.llm.resolve import list_user_providers, resolve_provider_credentials

    username = (os.environ.get("DEV_USERNAME") or "dev").strip() or "dev"
    async with async_session_factory() as session:
        user = await UserRepository(session).get_by_username(username)
        if user is None:
            raise SystemExit(f"没有用户 {username}")
        rows = await list_user_providers(session, user.user_id)
        for row in rows:
            preset = match_byok_provider_preset(row.base_url or "")
            if preset is None or preset.id != "deepseek":
                continue
            creds = await resolve_provider_credentials(session, user.user_id, row.id)
            if creds is not None:
                return creds
    raise SystemExit("dev 账号没有官方 DeepSeek（api.deepseek.com）。拒绝平台池。")


def _request(messages: list[LLMMessage], *, model: str) -> LLMRequest:
    return LLMRequest(
        messages=messages,
        model=model,
        temperature=0.0,
        max_tokens=16,
        tools=_TOOLS,
        tool_choice="none",
        stream=False,
        scenario="title",
        thinking=False,
        retry_patience_seconds=0.0,
    )


async def _hop(
    provider,
    *,
    series: str,
    index: int,
    conversation_id: str,
    model: str,
    user_text: str,
) -> Hop:
    messages = [
        LLMMessage(role="system", content=_STABLE_SYSTEM),
        LLMMessage(role="user", content=user_text),
    ]
    with log_context(conversation_id=conversation_id, trace_id=new_trace_id()):
        response = await provider.complete(_request(messages, model=model))
    return Hop(
        series=series,
        index=index,
        conversation_id=conversation_id,
        usage=response.usage,
        latency_ms=response.latency_ms,
    )


def _print_hop(hop: Hop) -> None:
    usage = hop.usage
    ratio = _ratio(usage)
    ratio_s = "n/a" if ratio is None else f"{ratio:.1%}"
    reported = "yes" if _reported(usage) else "no"
    print(
        f"{hop.series} #{hop.index}  "
        f"cid={hop.conversation_id[-8:]}  "
        f"input={usage.input_tokens}  "
        f"hit={usage.cache_hit_tokens}  "
        f"miss={usage.cache_miss_tokens}  "
        f"ratio={ratio_s}  "
        f"reported={reported}  "
        f"latency_ms={hop.latency_ms}",
        flush=True,
    )


async def _series(
    provider,
    *,
    series: str,
    conversation_ids: list[str],
    model: str,
) -> list[Hop]:
    hops: list[Hop] = []
    for index, cid in enumerate(conversation_ids, start=1):
        user_text = f"Reply with the single word hop{index}."
        try:
            hop = await _hop(
                provider,
                series=series,
                index=index,
                conversation_id=cid,
                model=model,
                user_text=user_text,
            )
        except LLMError as exc:
            print(f"{series} #{index} 上游失败：{type(exc).__name__}: {exc}", flush=True)
            break
        hops.append(hop)
        _print_hop(hop)
        if index < len(conversation_ids):
            await asyncio.sleep(_PAUSE_S)
    return hops


def _reading(same: list[Hop], fresh: list[Hop]) -> str:
    if len(same) < 3 or len(fresh) < 1:
        return "枪数不够，不能判断会话头有没有隔开缓存。"
    same_ratio = _ratio(same[-1].usage)
    fresh_ratio = _ratio(fresh[0].usage)
    if same_ratio is None or fresh_ratio is None:
        return "没有 input token，不能算命中率。"
    if not _reported(same[-1].usage) and not _reported(fresh[0].usage):
        return "上游没有回报缓存字段。这不是 0% 命中。"
    if same_ratio >= 0.5 and fresh_ratio < 0.2:
        return (
            "同一会话第三枪命中高，新对话第一枪仍然很低。"
            "这段前缀没有跨过新的 conversation_id。"
        )
    if fresh_ratio >= 0.5:
        return (
            "新对话第一枪已经命中同一段前缀。"
            "会话头没有把磁盘缓存隔开；首次偏低要另看工具表或信封是否逐字节相同。"
        )
    if same_ratio < 0.2:
        return (
            "同一会话第三枪也没有明显命中。"
            "这条路线上，重复前缀没有在本次等待里落成可命中的单位。"
        )
    return (
        f"同一会话第三枪 {same_ratio:.1%}，新对话第一枪 {fresh_ratio:.1%}。"
        "介于两边阈值之间，看表里的 hit/input，不要当成定论。"
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="首次发消息前缀缓存探针")
    parser.add_argument(
        "--official",
        action="store_true",
        help="打 dev 账号的 api.deepseek.com，不走 OpenCode",
    )
    return parser.parse_args()


async def main() -> None:
    args = _parse_args()
    creds = await _official_credentials() if args.official else await _credentials()
    model = (creds.default_model or "").strip()
    if not model:
        raise SystemExit("凭据没有 default_model")
    base_url = (creds.base_url or "").strip()
    session = (
        "x-opencode-session=conversation_id"
        if is_opencode_byok_endpoint(base_url)
        else "无 OpenCode 会话头"
    )
    fingerprint = _tools_fingerprint(_TOOLS)
    print(
        f"model={model}  host={_host(base_url)}  {session}\n"
        f"system_chars={len(_STABLE_SYSTEM)}  tools_sha={fingerprint}  "
        f"pause_s={_PAUSE_S:.0f}",
        flush=True,
    )
    provider = build_provider(creds)
    shared = f"probe-cache-{uuid.uuid4().hex}"
    print("--- same-session ---", flush=True)
    same = await _series(
        provider,
        series="same-session",
        conversation_ids=[shared, shared, shared],
        model=model,
    )
    if len(same) < 3:
        raise SystemExit("同一会话那一串没有打完")
    await asyncio.sleep(_PAUSE_S)
    fresh_ids = [f"probe-cache-{uuid.uuid4().hex}" for _ in range(3)]
    print("--- new-chat ---", flush=True)
    fresh = await _series(
        provider,
        series="new-chat",
        conversation_ids=fresh_ids,
        model=model,
    )
    print(_reading(same, fresh), flush=True)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(130)
