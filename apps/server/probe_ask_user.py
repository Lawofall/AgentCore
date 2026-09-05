"""可复用探针：CEO 在「该发问」时刻的 ask_user 触发率 (实测案例复盘.md · 探针法)

开发期没有自然流量，无法靠 logs/dev.jsonl 量化触发率——本脚本是它的替代品，且支持
**多次重复**把单次轶事变成触发率分布。把一组自造代表性 prompt 直接喂进真实聊天管线
（真实 LLM、进程内、不经 HTTP），每条跑 REPEATS 次，观察 CEO 的首个决策
（ASK / DELEGATE / ANSWER），最后打印触发率表供人判读。

策略基线（及格线）：对「能做但没说全的产出类」请求，期望默认 **开提案卡 (ASK)**（甲案）。
产出类用例测 ASK 触发率；对照组（需求已全→委派 / 问答→直答）守住不过度发问。

为何能跑：ask_user 默认装配（checkpoint_gate_enabled 默认 True、交互回合 approvals_enabled
默认 True）；事件流里 checkpoint_required=发问、run_plan=委派、
content_delta=直答，足以判别首个决策。

清理（绕开上一版的坑）：取消 stream_chat 任务会污染 asyncpg 连接池，紧接着复用会失败。
故本版**先记录所有真实会话 id、全部跑完后 engine.dispose() 丢弃连接池、再用全新连接按
id 精确删除**（带重试），不在取消后立即清理。

运行（在 apps/server 下）：  uv run python probe_ask_user.py
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
import time
from collections import Counter
from dataclasses import dataclass, field

from sqlalchemy import delete, func, select

from agentcore.config import settings
from agentcore.conversation.service import stream_chat
from agentcore.db.base import async_session_factory, engine
from agentcore.db.models import Conversation, PausedTurnRow, RunSessionRow, UserLlmProvider
from agentcore.db.repositories import ConversationRepository
from agentcore.llm.resolve import resolve_user_llm_credentials
from agentcore.runtime.events import EventSink, EventType, SSEEvent

INVESTIGATION = {"file_read", "file_list", "grep", "web_search", "web_fetch"}
CASE_TIMEOUT = 150.0
REPEATS = 4


@dataclass
class Case:
    id: str
    prompt: str
    expect: str  # 该用例期望的首个决策: ASK | DELEGATE | ANSWER
    note: str


# 产出类（甲案 → 期望 ASK，测触发率）：能做但都没说全。
PRODUCE: list[Case] = [
    Case(
        "P1-web",
        "帮我做一个个人作品集网站。",
        "ASK",
        "经典产出类，多个未言明决策（风格/页面/技术栈）。",
    ),
    Case(
        "P2-doc",
        "帮我写一份面向初学者的 Python 入门教程文档。",
        "ASK",
        "成篇文档；上一轮单次跑出过 DELEGATE。",
    ),
    Case(
        "P3-ppt",
        "帮我做一份面向投资人的产品介绍 PPT。",
        "ASK",
        "高风险受众，篇幅/侧重/数据都未定。",
    ),
    Case("P4-app", "帮我设计一个手机记账 App。", "ASK", "产品设计，功能范围/平台/风格未定。"),
    Case(
        "P5-event", "帮我策划一场 200 人的公司年会。", "ASK", "非软件产出类，预算/主题/流程未定。"
    ),
]

# 对照组（守住不过度发问）。
CONTROL: list[Case] = [
    Case(
        "C1-clear",
        "用 Python 写一个脚本：读取指定 CSV，按某列分组求和，输出到新的 CSV 文件并保存到工作区。"
        "需求已明确，直接开工。",
        "DELEGATE",
        "需求说全 + 明确「直接开工」→ 不该再 ask。",
    ),
    Case("C2-explain", "什么是向量数据库？请简要解释。", "ANSWER", "纯问答 → 直答。"),
    Case("C3-chitchat", "你好，简单介绍一下你自己。", "ANSWER", "闲聊 → 直答。"),
]

CASES = PRODUCE + CONTROL


class RecordingSink(EventSink):
    def __init__(self) -> None:
        super().__init__()
        self.events: list[tuple[str, dict]] = []
        self.decisive = asyncio.Event()

    def emit(self, event: SSEEvent) -> None:
        super().emit(event)
        self.events.append((event.type.value, event.payload))
        if event.type in (
            EventType.CHECKPOINT_REQUIRED,
            EventType.RUN_PLAN,
            EventType.MESSAGE_END,
        ):
            self.decisive.set()


@dataclass
class Obs:
    outcome: str = "UNKNOWN"  # ASK | DELEGATE | ANSWER | UNKNOWN
    opening: bool = False  # ASK 时：是否开场提案味（有起步计划/风格/问题全带 default）
    wall: bool = False  # ASK 时：是否问题墙（多问且缺 default）
    n_questions: int = 0
    n_assumptions: int = 0
    agents: int = 0  # DELEGATE 时委派的 worker 数
    elapsed: float = 0.0
    investigation: list[str] = field(default_factory=list)


def classify(events: list[tuple[str, dict]]) -> Obs:
    tools = [p.get("tool_name") for t, p in events if t == "tool_use_start"]
    investigation = [n for n in tools if n in INVESTIGATION]
    ask = next((p for t, p in events if t == "checkpoint_required"), None)
    delegated = "run_plan" in {t for t, _ in events} or "delegate" in tools
    answered = any(t == "content_delta" and (p.get("delta") or "").strip() for t, p in events)

    if ask is not None:
        assumptions = ask.get("assumptions") or []
        questions = ask.get("questions") or []
        styles = ask.get("style_options") or []
        n_no_default = sum(1 for q in questions if not (q.get("default") or "").strip())
        all_default = bool(questions) and n_no_default == 0
        opening = bool(assumptions or styles or all_default)
        wall = (not opening) and len(questions) >= 3
        return Obs(
            "ASK", opening, wall, len(questions), len(assumptions), investigation=investigation
        )
    if delegated:
        plan = next((p for t, p in events if t == "run_plan"), None)
        agents = len(plan.get("agents") or plan.get("runs") or []) if plan else 0
        return Obs("DELEGATE", agents=agents, investigation=investigation)
    if answered:
        return Obs("ANSWER", investigation=investigation)
    return Obs("UNKNOWN", investigation=investigation)


async def _find_key_user() -> tuple[str | None, object]:
    async with async_session_factory() as session:
        keys = (
            (
                await session.execute(
                    select(UserLlmProvider).where(UserLlmProvider.api_key_enc.isnot(None))
                )
            )
            .scalars()
            .all()
        )
        for row in keys:
            creds = await resolve_user_llm_credentials(session, row.user_id)
            if creds is not None:
                return row.user_id, creds
    return None, None


async def _new_conversation(user_id: str) -> str:
    async with async_session_factory() as session:
        conv = await ConversationRepository(session).create(
            user_id=user_id, title="[probe] ask_user 触发探针"
        )
        return conv.id


async def _run_once(user_id: str, creds: object, prompt: str, created: list[str]) -> Obs:
    conversation_id = await _new_conversation(user_id)
    created.append(conversation_id)
    sink = RecordingSink()
    started = time.monotonic()
    task = asyncio.create_task(
        stream_chat(
            conversation_id=conversation_id,
            user_message=prompt,
            user_id=user_id,
            sink=sink,
            llm_credentials=creds,  # type: ignore[arg-type]
        )
    )
    waiter = asyncio.create_task(sink.decisive.wait())
    try:
        await asyncio.wait(
            {task, waiter}, timeout=CASE_TIMEOUT, return_when=asyncio.FIRST_COMPLETED
        )
    finally:
        waiter.cancel()
        if not task.done():
            task.cancel()
        with contextlib.suppress(BaseException):
            await task
    obs = classify(list(sink.events))
    obs.elapsed = time.monotonic() - started
    return obs


async def _cleanup(ids: list[str]) -> list[str]:
    # Cancelling stream_chat mid-flight can leave a poisoned pooled connection; drop the
    # whole pool first, then delete on fresh connections (with a small retry).
    await engine.dispose()
    failed: list[str] = []
    for cid in ids:
        ok = False
        for _ in range(3):
            try:
                async with async_session_factory() as s:
                    await ConversationRepository(s).hard_delete(cid)
                async with async_session_factory() as s:
                    await s.execute(
                        delete(PausedTurnRow).where(PausedTurnRow.conversation_id == cid)
                    )
                    await s.execute(
                        delete(RunSessionRow).where(RunSessionRow.conversation_id == cid)
                    )
                    await s.commit()
                ok = True
                break
            except Exception:
                await asyncio.sleep(0.5)
        if not ok:
            failed.append(cid)
    return failed


def _rate_line(case: Case, counts: Counter, asks: list[Obs]) -> str:
    n = sum(counts.values())
    hit = counts.get(case.expect, 0)
    dist = " ".join(
        f"{k}:{counts.get(k, 0)}" for k in ("ASK", "DELEGATE", "ANSWER", "UNKNOWN") if counts.get(k)
    )
    extra = ""
    if case.expect == "ASK" and asks:
        opening = sum(1 for o in asks if o.opening)
        wall = sum(1 for o in asks if o.wall)
        extra = f"  [开场提案味 {opening}/{len(asks)}, 问题墙 {wall}]"
    verdict = "✓" if hit == n else ("△" if hit else "✗")
    return f"  {verdict} [{case.id}] 期望 {case.expect} → 命中 {hit}/{n}（{dist}）{extra}"


async def main() -> None:
    with contextlib.suppress(Exception):
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]

    print("=" * 84)
    print(f"ask_user 触发率探针 · 每条 ×{REPEATS} · 策略基线=甲(产出类→应开提案卡)")
    print(
        f"billing_mode={settings.billing_mode} · "
        f"checkpoint_gate_enabled={settings.checkpoint_gate_enabled}"
    )
    print("=" * 84)
    if not settings.checkpoint_gate_enabled:
        print("✗ checkpoint_gate_enabled=False → ask_user 不装配，先开启。")
        return
    user_id, creds = await _find_key_user()
    if user_id is None:
        print("✗ 没有可用 BYOK key 的用户 → 无法跑真实回合。")
        return
    print(f"运行身份 user_id={user_id}\n")

    created: list[str] = []
    per_case: dict[str, tuple[Case, Counter, list[Obs]]] = {}
    for case in CASES:
        counts: Counter = Counter()
        asks: list[Obs] = []
        marks: list[str] = []
        for _r in range(REPEATS):
            try:
                obs = await _run_once(user_id, creds, case.prompt, created)
            except Exception as e:
                counts["UNKNOWN"] += 1
                marks.append(f"err({e!r})")
                continue
            counts[obs.outcome] += 1
            if obs.outcome == "ASK":
                asks.append(obs)
            marks.append(f"{obs.outcome}{'·提案' if obs.opening else ''}[{obs.elapsed:.0f}s]")
        per_case[case.id] = (case, counts, asks)
        print(f"▶ [{case.id}] {case.prompt[:34]}")
        print(f"    {'  '.join(marks)}")

    print("\n" + "=" * 84)
    print("触发率表")
    print("-" * 84)
    print("产出类（期望 ASK）：")
    for case in PRODUCE:
        c, counts, asks = per_case[case.id]
        print(_rate_line(c, counts, asks))
    print("对照组：")
    for case in CONTROL:
        c, counts, asks = per_case[case.id]
        print(_rate_line(c, counts, asks))
    prod_ask = sum(per_case[c.id][1].get("ASK", 0) for c in PRODUCE)
    prod_total = sum(sum(per_case[c.id][1].values()) for c in PRODUCE)
    print("-" * 84)
    print(f"产出类整体 ASK 触发率：{prod_ask}/{prod_total} = {prod_ask / max(prod_total, 1):.0%}")
    print("=" * 84)

    print(f"\n清理 {len(created)} 个临时会话…")
    failed = await _cleanup(created)
    async with async_session_factory() as s:
        left = (
            await s.execute(
                select(func.count()).select_from(Conversation).where(Conversation.id.in_(created))
            )
        ).scalar()
    print(f"清理完成：残留 {left} 个（清理失败 id：{failed or '无'}）")


if __name__ == "__main__":
    asyncio.run(main())
