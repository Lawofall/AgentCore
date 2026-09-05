"""One-off probe: run a deliverable-grade, multi-angle research request through the
REAL chat pipeline (same ``stream_chat`` the HTTP endpoint calls) and observe
whether the CEO fans the research out into parallel delegate workers.

This validates the A/B/C prompt levers (双向判据 / 披露解锁 / 调研并行委派) end to
end against the running prompt code — not a unit test of the wording, but the
actual model behaviour on a request that *should* trigger parallel fan-out.

Run from ``apps/server`` (uses the same DB + a user's own BYOK key)::

    uv run python scripts/archive/run_research_probe.py
    uv run python scripts/archive/run_research_probe.py --message "..."   # custom prompt

It picks the first user that has a usable BYOK key, creates a throwaway
conversation, streams one turn, and prints the team-graph shape (delegate
``run_plan`` width + each worker's role/depends_on) plus a terminal summary.
The authoritative fan-out metrics are also in ``logs/dev.jsonl``
(``delegate.started nodes=`` / ``chat.turn_complete workers=``) keyed by the
printed ``conversation_id``.
"""

from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import select

from agentcore.conversation.service import stream_chat
from agentcore.db import async_session_factory
from agentcore.db.models import UserLlmProvider
from agentcore.db.repositories import ConversationRepository, UserRepository
from agentcore.llm.resolve import resolve_user_llm_credentials
from agentcore.runtime.events import EventSink, EventType

# A deliverable-grade request with FOUR naturally-independent research angles, each
# needing its own retrieval — the textbook trigger for "调研并行委派" (lever C). The
# explicit "直接开工、不必反问" discourages an ask_user clarification pause so the
# turn actually runs the team instead of suspending.
DEFAULT_MESSAGE = (
    "请系统调研并产出一份《建设工程法律实务》研究报告。请覆盖以下相互独立的方面，"
    "每个方面都要有充分的联网检索支撑："
    "① 现行法律法规与司法解释框架；"
    "② 典型裁判案例与裁判规则；"
    "③ 工程合同全周期的法律风险点与防范；"
    "④ 工程纠纷的争议解决路径（诉讼 / 仲裁 / 调解）对比。"
    "最后整合成一份结构化的报告文档。需求已明确，请直接开工、不必反问。"
)

# A research run with several workers (each doing multiple web_search + web_fetch +
# a writing pass) can take minutes. Generous ceiling; the probe exits earlier on a
# normal message_end or a checkpoint pause.
TIMEOUT_S = 600.0

PAUSE_EVENTS = {
    EventType.CHECKPOINT_REQUIRED,
    EventType.APPROVAL_REQUIRED,
    EventType.PLAN_REVIEW_REQUIRED,
    EventType.WORKSPACE_OP_REQUIRED,
}


async def _pick_byok_user() -> tuple[str, object]:
    """Return ``(user_id, credentials)`` for the first user with a usable BYOK key."""
    async with async_session_factory() as session:
        row = (
            await session.execute(
                select(UserLlmProvider)
                .where(UserLlmProvider.api_key_enc.isnot(None))
                .limit(1)
            )
        ).scalar_one_or_none()
        if row is None:
            raise SystemExit(
                "No user has a BYOK key configured — set a DeepSeek key in 设置·模型配置 first."
            )
        user_id = row.user_id
        creds = await resolve_user_llm_credentials(session, user_id)
        if creds is None:
            raise SystemExit(
                f"User {user_id} has a key row but it could not be decrypted "
                "(missing/rotated ENCRYPTION_KEY?)."
            )
        user = await UserRepository(session).get_by_id(user_id)
        label = getattr(user, "username", user_id)
    print(f"[user] using BYOK user {label!r} (id={user_id})")
    return user_id, creds


def _describe_run(run: dict) -> str:
    rid = run.get("run_id") or run.get("id") or "?"
    agent = run.get("agent_id") or run.get("role") or run.get("name") or "?"
    deps = run.get("depends_on") or run.get("deps") or []
    dep_str = f" depends_on={deps}" if deps else " (no deps → parallel)"
    return f"    - run {rid} · agent={agent}{dep_str}"


async def _drain(sink: EventSink, producer: asyncio.Task) -> dict:
    """Consume events live, printing the team-graph shape. Returns a summary dict."""
    summary = {
        "run_plan": None,
        "workers_started": 0,
        "ceo_tools": [],
        "paused": None,
        "finish_reason": None,
        "error": None,
    }
    async for ev in sink:
        t = ev.type
        if t == EventType.RUN_PLAN:
            agents = ev.payload.get("agents") or []
            runs = ev.payload.get("runs") or []
            summary["run_plan"] = {
                "plan_type": ev.payload.get("plan_type"),
                "agents": len(agents),
                "runs": len(runs),
            }
            print(
                f"[run_plan] plan_type={ev.payload.get('plan_type')} "
                f"agents={len(agents)} runs={len(runs)}"
            )
            print(f"    task_summary: {ev.payload.get('task_summary', '')[:160]}")
            for run in runs:
                print(_describe_run(run))
        elif t == EventType.RUN_STARTED:
            summary["workers_started"] += 1
            print(
                f"[run_started #{summary['workers_started']}] "
                f"agent={ev.payload.get('agent_id')} kind={ev.payload.get('kind')}"
            )
        elif t == EventType.TOOL_USE_START:
            name = ev.payload.get("tool_name", "?")
            summary["ceo_tools"].append(name)
            print(f"[ceo_tool] {name}")
        elif t in PAUSE_EVENTS:
            summary["paused"] = t.value
            q = ev.payload.get("question") or ev.payload.get("message") or ""
            print(f"[PAUSED · {t.value}] {q[:200]}")
            print("    → turn suspended awaiting user input; cancelling probe.")
            producer.cancel()
            break
        elif t == EventType.ERROR:
            summary["error"] = ev.payload
            print(f"[error] {ev.payload}")
        elif t == EventType.MESSAGE_END:
            summary["finish_reason"] = ev.payload.get("finish_reason")
            print(f"[message_end] finish_reason={ev.payload.get('finish_reason')}")
    return summary


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--message", default=DEFAULT_MESSAGE)
    args = parser.parse_args()

    user_id, creds = await _pick_byok_user()

    async with async_session_factory() as session:
        conv = await ConversationRepository(session).create(
            user_id=user_id, title="[probe] 建设工程法律实务调研"
        )
    print(f"[conversation] created {conv.id}")
    print(f"[message] {args.message[:120]}…\n")

    sink = EventSink()
    producer = asyncio.create_task(
        stream_chat(
            conversation_id=conv.id,
            user_message=args.message,
            user_id=user_id,
            sink=sink,
            llm_credentials=creds,
        )
    )

    try:
        summary = await asyncio.wait_for(_drain(sink, producer), timeout=TIMEOUT_S)
    except TimeoutError:
        producer.cancel()
        print(f"\n[timeout] exceeded {TIMEOUT_S:.0f}s — cancelling.")
        summary = {"timeout": True}

    try:
        await producer
    except (asyncio.CancelledError, Exception) as e:  # noqa: BLE001 - probe, report only
        print(f"[producer] ended: {type(e).__name__}: {e}")

    print("\n==== SUMMARY ====")
    rp = summary.get("run_plan")
    if rp:
        print(
            f"delegate fan-out: plan_type={rp['plan_type']} agents={rp['agents']} runs={rp['runs']}"
        )
    else:
        print("delegate fan-out: NONE (CEO did not call delegate / no run_plan)")
    print(f"workers started:  {summary.get('workers_started', 0)}")
    print(f"CEO own tools:    {summary.get('ceo_tools')}")
    print(f"paused:           {summary.get('paused')}")
    print(f"finish_reason:    {summary.get('finish_reason')}")
    print(f"error:            {summary.get('error')}")
    print(f"\nconversation_id = {conv.id}")
    print("Inspect logs:  uv run python scripts/log_timeline.py", conv.id)


if __name__ == "__main__":
    asyncio.run(main())
