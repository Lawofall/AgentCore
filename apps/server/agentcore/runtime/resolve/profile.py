"""Prompt 变体注入（方向①）：eval 可 A/B 提示词片段，生产恒等、前缀缓存零影响.

**机制**：一个进程级 :class:`~contextvars.ContextVar`。``assemble_system_prompt`` /
``compose_ceo_chat_prompt`` 装配每个**具名静态片段**时经 :func:`resolve` 取文案——未设
profile（生产恒如此）→ 返回原常量 → **逐字节不变**，故 DeepSeek 前缀缓存不受任何影响。
eval harness 在 ``run_case`` 外层用 :func:`use_profile` 注入变体（读 ``EvalCase.prompt_profile``）；
只覆盖 profile 声明的 key，其余片段仍走原常量。

**为何 contextvar 而非穿参**：装配只发生在 ``run_chat_pipeline`` 内部（``pipeline/run.py``），
而 eval harness 的铁律是**零侵入**——只消费其返回值、不改其签名一行。contextvar 让装配函数
就地咨询「当前活跃变体」，无需把 profile 线穿过 pipeline / engine 的签名；contextvar 又是
async-task 隔离的，并发用例互不串味。

**确定性边界**：本机制纯确定性、可零 LLM 单测（覆盖 / 恒等 / 嵌套重置 / 逐字节守恒）；但要
**比出哪个变体更好**，需两个 profile 各跑真模型 CEO 回合 + 裁判 / 指标对比，属
真跑评测主线（详细提案不在公开仓；现状见 docs/02-架构/后端架构.md §五）。

**粒度（v1 整块）**：可覆盖的是下面 :data:`OVERRIDABLE_KEYS` 这几个**静态文案常量**；动态片段
（日期 / 能力目录 / 记忆 / 附件）随回合或用户而变，A/B 一份静态文案无意义，故不开放覆盖。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

# 可被变体覆盖的静态片段 key（值 = 装配点的 key，单一真相）。覆盖 BASE 同时作用于 worker
# 基底与 CEO 基底（CEO base_prompt 即 assemble 结果）；其余仅在 CEO 装配里出现。
FRAGMENT_BASE = "base"
FRAGMENT_CEO_CORE = "ceo_core"

OVERRIDABLE_KEYS: frozenset[str] = frozenset({FRAGMENT_BASE, FRAGMENT_CEO_CORE})


@dataclass(frozen=True)
class PromptProfile:
    """一个提示词变体：把若干具名片段替换成实验文案（其余片段保持原常量）。

    ``overrides`` 的 key 应取自 :data:`OVERRIDABLE_KEYS`；未列出的片段走默认（恒等）。
    用空串覆盖某 key 即做**消融**（assembler 跳过 falsy 片段→该块整段移除），是「这段文案
    值不值这些 token」的最廉价实验，不需编造任何新文案。
    """

    name: str
    overrides: dict[str, str] = field(default_factory=dict)

    def resolve(self, key: str, default: str) -> str:
        """取片段 ``key`` 的文案：本变体覆盖了它→用覆盖文案，否则原 ``default``。"""
        return self.overrides.get(key, default)


# 进程级活跃变体；默认 None = 无变体 = 恒等（生产路径永远是这个状态）。
_ACTIVE: ContextVar[PromptProfile | None] = ContextVar("agentcore_prompt_profile", default=None)


def active_profile() -> PromptProfile | None:
    """当前作用域的活跃变体（无则 None）。"""
    return _ACTIVE.get()


def resolve(key: str, default: str) -> str:
    """装配点调用：有活跃变体且覆盖了 ``key`` → 返回覆盖文案，否则 ``default``（恒等）。

    生产无人 :func:`use_profile` → ``_ACTIVE`` 恒为 None → 永远返回 ``default``，与未引入
    本机制时逐字节一致。
    """
    profile = _ACTIVE.get()
    return profile.resolve(key, default) if profile is not None else default


@contextmanager
def use_profile(profile: PromptProfile | None) -> Iterator[None]:
    """在 ``with`` 作用域内激活一个变体（``None`` = 显式恒等）；退出**必复位**（token reset）.

    用 :class:`~contextvars.ContextVar` 的 token 复位，故嵌套 / 异常都能精确还原上一层状态，
    不会把变体泄漏到作用域之外。
    """
    token = _ACTIVE.set(profile)
    try:
        yield
    finally:
        _ACTIVE.reset(token)
