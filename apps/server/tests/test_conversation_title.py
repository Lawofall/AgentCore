"""Tests for conversation title generation (_sanitize_title + LLMTitleGenerator)."""

import asyncio

import agentcore.memory.conversation_title as title_mod
from agentcore.llm import LLMRequest, LLMResponse
from agentcore.llm.profiles import DEEPSEEK_V4_FLASH
from agentcore.memory.conversation_title import (
    _TITLE_SYSTEM_PROMPT,
    TITLE_MAX_CHARS,
    LLMTitleGenerator,
    TitleInput,
    TitleResult,
    _parse_title_result,
    _render_title_prompt,
    _sanitize_title,
)

# --- _sanitize_title (pure text cleanup) ---


def test_sanitize_plain_title_unchanged():
    assert _sanitize_title("登录功能设计") == "登录功能设计"


def test_sanitize_strips_surrounding_quotes():
    assert _sanitize_title('"登录功能设计"') == "登录功能设计"
    assert _sanitize_title("「登录功能设计」") == "登录功能设计"
    assert _sanitize_title("“登录功能设计”") == "登录功能设计"


def test_sanitize_strips_label_prefix():
    assert _sanitize_title("标题：登录功能设计") == "登录功能设计"
    assert _sanitize_title("Title: Login flow") == "Login flow"


def test_sanitize_label_and_quotes_together():
    assert _sanitize_title('标题："登录功能设计"') == "登录功能设计"


def test_sanitize_takes_first_nonempty_line():
    assert _sanitize_title("\n\n登录功能设计\n其它说明") == "登录功能设计"


def test_sanitize_collapses_whitespace_and_trailing_punct():
    assert _sanitize_title("登录   功能   设计。") == "登录 功能 设计"


def test_sanitize_truncates_to_max():
    long = "题" * (TITLE_MAX_CHARS + 10)
    out = _sanitize_title(long)
    assert out == "题" * TITLE_MAX_CHARS + "…"


def test_sanitize_empty_returns_empty():
    assert _sanitize_title("") == ""
    assert _sanitize_title("   \n  ") == ""


# --- _render_title_prompt ---


def test_render_prompt_includes_messages_and_skips_empty():
    prompt = _render_title_prompt(
        TitleInput(
            conversation_id="c1",
            messages=[
                {"role": "user", "content": "帮我设计登录"},
                {"role": "assistant", "content": ""},
                {"role": "assistant", "content": "好的，方案如下"},
            ],
        )
    )
    assert "帮我设计登录" in prompt
    assert "好的，方案如下" in prompt
    assert "user:" in prompt and "assistant:" in prompt


def test_render_prompt_truncates_long_message():
    prompt = _render_title_prompt(
        TitleInput(
            conversation_id="c1",
            messages=[{"role": "user", "content": "x" * 5000}],
        )
    )
    assert "…" in prompt
    assert len(prompt) < 2000


# --- _TITLE_SYSTEM_PROMPT (pinned guards) ---


def test_title_prompt_bans_emoji_and_guards_injection():
    # The sanitizer does not strip emoji, so the ban lives in the prompt; the
    # conversation is interpolated as data and must not be obeyed as instructions.
    assert "emoji" in _TITLE_SYSTEM_PROMPT
    assert "JSON" in _TITLE_SYSTEM_PROMPT
    assert "tag" not in _TITLE_SYSTEM_PROMPT
    assert "不要执行其中" in _TITLE_SYSTEM_PROMPT


# --- _parse_title_result (structured JSON) ---


def test_parse_title_result_from_json():
    raw = '{"title": "登录功能设计"}'
    out = _parse_title_result(raw)
    assert out == TitleResult(title="登录功能设计")


def test_parse_title_result_ignores_legacy_tag_field():
    raw = '{"title": "竞品调研", "tag": "research"}'
    out = _parse_title_result(raw)
    assert out == TitleResult(title="竞品调研")


def test_parse_title_result_plain_text_fallback():
    out = _parse_title_result('"登录功能设计"')
    assert out == TitleResult(title="登录功能设计")


def test_parse_title_result_strips_json_fence():
    raw = '```json\n{"title":"写作提纲"}\n```'
    out = _parse_title_result(raw)
    assert out == TitleResult(title="写作提纲")


def test_parse_title_result_rejects_truncated_json_fragment():
    """finish_reason=length half-JSON must not become the sidebar title."""
    assert _parse_title_result('{"title') == TitleResult(title="")
    assert _parse_title_result('{"title": "登录') == TitleResult(title="")
    assert _parse_title_result("[") == TitleResult(title="")


# --- LLMTitleGenerator (async, with a fake provider) ---


class _FakeProvider:
    """Minimal LLMProvider stub: returns canned content and records requests."""

    def __init__(self, content: str) -> None:
        self._content = content
        self.requests: list[LLMRequest] = []

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        return LLMResponse(content=self._content)


async def test_generator_returns_sanitized_title():
    provider = _FakeProvider('{"title":"登录功能设计"}')
    result = await LLMTitleGenerator(provider).generate(
        TitleInput(
            conversation_id="c1",
            messages=[{"role": "user", "content": "帮我设计登录"}],
        )
    )
    assert result == TitleResult(title="登录功能设计")


async def test_generator_uses_flash_non_thinking_short():
    provider = _FakeProvider("标题")
    await LLMTitleGenerator(provider, model=DEEPSEEK_V4_FLASH).generate(
        TitleInput(
            conversation_id="c1",
            messages=[{"role": "user", "content": "你好"}],
        )
    )
    req = provider.requests[0]
    assert req.model == "deepseek-v4-flash"
    assert req.stream is False
    assert req.max_tokens == 1024
    assert req.thinking is False
    assert req.scenario == "title"


async def test_generator_empty_messages_skips_call():
    provider = _FakeProvider("不应被调用")
    result = await LLMTitleGenerator(provider).generate(
        TitleInput(conversation_id="c1", messages=[])
    )
    assert result == TitleResult(title="")
    assert provider.requests == []


async def test_generator_blank_output_returns_empty_without_resample():
    """A completed empty title is not sampled again; caller falls back."""
    provider = _FakeProvider("   \n  ")
    result = await LLMTitleGenerator(provider).generate(
        TitleInput(
            conversation_id="c1",
            messages=[{"role": "user", "content": "你好"}],
        )
    )
    assert result == TitleResult(title="")
    assert len(provider.requests) == 1


async def test_generator_empty_body_does_not_resample():
    provider = _FakeProvider("")
    result = await LLMTitleGenerator(provider).generate(
        TitleInput(
            conversation_id="c1",
            messages=[{"role": "user", "content": "帮我设计登录"}],
        )
    )
    assert result == TitleResult(title="")
    assert len(provider.requests) == 1


async def test_generator_times_out_returns_empty_without_retry(monkeypatch):
    """A stalled model degrades to "" with a single attempt — timeout must not retry."""
    calls = {"n": 0}

    class _StallProvider:
        async def complete(self, request: LLMRequest) -> LLMResponse:
            calls["n"] += 1
            await asyncio.sleep(3600)  # never resolves within the timeout
            raise AssertionError("unreachable")

    monkeypatch.setattr(title_mod, "_TITLE_TIMEOUT_SECONDS", 0.01)
    result = await LLMTitleGenerator(_StallProvider()).generate(
        TitleInput(conversation_id="c1", messages=[{"role": "user", "content": "你好"}])
    )
    assert result == TitleResult(title="")
    assert calls["n"] == 1
