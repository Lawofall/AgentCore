"""探针：发一条真实回合，dump SSE 事件流的「到达顺序 + 折叠时间线」。

排查「AI 对话前端时序」用——`LOG_LEVEL=info` 的运行时日志只到「轮」级
(`react.round_end` / `tool.execute_end` / `llm.call`)，**看不到** reasoning↔content↔tool
的逐事件发射顺序，而「输出完成后乱序」恰恰活在这个 token 级。本脚本直连运行中的后端
(默认 http://localhost:8000)，用 dev 账号登录、建/复用会话、发一条消息，然后：

1. 按**到达顺序**打印每个 SSE 事件 (delta 默认合批成段，`--raw` 则逐个打)；
2. 按后端 ``EventSink._accumulate_process`` 同款规则折出「思考·正文·工具」时间线——
   一眼判断是「后端发射就乱」还是「前端 fold 才乱」；
3. 把完整原始事件序列 (带 ``t_ms`` 到达时刻) 另存 ``logs/probes/probe_<ts>.json``，供后续 AI 用
   Read 直接读分析。

从 ``apps/server`` 跑::

    uv run python scripts/archive/probe_turn.py "联网搜一下 Python 3.13 新特性，简短三点"
    uv run python scripts/archive/probe_turn.py --raw "..."               # 连每个 delta 都打
    uv run python scripts/archive/probe_turn.py --conversation <id> "..."  # 复用已有会话

凭据默认 ``dev`` / ``devpassword`` (见 ``seed_dev_user.py``)，可用 ``DEV_USERNAME`` /
``DEV_PASSWORD`` 环境变量或 ``--user`` / ``--password`` 覆盖；后端地址用 ``--base-url`` 或
``PROBE_BASE_URL``。仅 dev 便利工具，走正常 ``/auth/token`` + ``/messages`` 流程，无任何旁路。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

import httpx

REPO_ROOT = Path(__file__).resolve().parents[3]
OUT_DIR = REPO_ROOT / "logs" / "probes"

DEFAULT_BASE_URL = os.environ.get("PROBE_BASE_URL", "http://localhost:8000")
DEFAULT_USERNAME = os.environ.get("DEV_USERNAME", "dev")
DEFAULT_PASSWORD = os.environ.get("DEV_PASSWORD", "devpassword")

# Streamed text-delta events coalesced into one segment for the readable trace.
_DELTA_TYPES = {"content_delta", "reasoning_delta"}


def _preview(text: str, limit: int = 70) -> str:
    flat = text.replace("\n", "\\n")
    return flat if len(flat) <= limit else flat[:limit] + "…"


class TraceCoalescer:
    """Print the live event trace, folding consecutive same-type deltas into one line.

    A run of ``content_delta`` (or ``reasoning_delta``) prints as a single
    ``content ×N  C chars`` line so the trace reads as segments, not thousands of
    tokens. ``--raw`` disables the folding (every delta prints). Non-delta events
    always print on their own line at their arrival ``t_ms``.
    """

    def __init__(self, raw: bool) -> None:
        self.raw = raw
        self._kind: str | None = None
        self._t0_ms: int = 0
        self._chars: int = 0
        self._count: int = 0

    def feed(self, t_ms: int, ev_type: str, payload: dict[str, Any]) -> None:
        if ev_type in _DELTA_TYPES and not self.raw:
            delta = payload.get("delta") or ""
            if self._kind == ev_type:
                self._chars += len(delta)
                self._count += 1
            else:
                self.flush()
                self._kind = ev_type
                self._t0_ms = t_ms
                self._chars = len(delta)
                self._count = 1
            return
        self.flush()
        print(_format_event(t_ms, ev_type, payload, raw=self.raw))

    def flush(self) -> None:
        if self._kind is None:
            return
        label = "reasoning" if self._kind == "reasoning_delta" else "content"
        print(f"  {self._t0_ms:>7}ms  ~ {label:<14} x{self._count:<4} {self._chars} chars")
        self._kind = None
        self._chars = 0
        self._count = 0


def _format_event(t_ms: int, ev_type: str, payload: dict[str, Any], *, raw: bool) -> str:
    head = f"  {t_ms:>7}ms"
    if ev_type == "message_start":
        return f"{head}  >> message_start"
    if ev_type in _DELTA_TYPES:  # only reached in --raw mode
        mark = "reasoning" if ev_type == "reasoning_delta" else "content"
        return f'{head}  ~ {mark:<14} "{_preview(payload.get("delta") or "")}"'
    if ev_type == "content_reset":
        return f"{head}  !! content_reset (drop streamed draft)"
    if ev_type == "tool_use_start":
        return f"{head}  [tool>] {payload.get('tool_name', '?')}"
    if ev_type == "tool_use_end":
        return f"{head}  [tool<] {payload.get('tool_name', '?')} ({payload.get('status', '?')})"
    if ev_type == "tool_progress":
        name = payload.get("tool_name", "?")
        return f"{head}  .. composing {name} ({payload.get('chars', 0)} chars)"
    if ev_type == "run_plan":
        return (
            f"{head}  [TEAM] run_plan type={payload.get('plan_type')}"
            f" agents={len(payload.get('agents') or [])}"
            f" runs={len(payload.get('runs') or [])}"
        )
    if ev_type in {"run_started", "run_completed", "run_failed", "run_context"}:
        return f"{head}  [run] {ev_type} agent={payload.get('agent_id', '?')}"
    if ev_type == "citations":
        return f"{head}  [cite] citations ({len(payload.get('citations') or [])})"
    if ev_type in {"checkpoint_required", "plan_review_required"}:
        return f"{head}  [PAUSE] {ev_type}"
    if ev_type == "message_end":
        return f"{head}  == message_end (finish={payload.get('finish_reason')})"
    if ev_type == "error":
        return f"{head}  XX error {payload.get('code')}: {payload.get('message')}"
    return f"{head}  . {ev_type}"


def fold_process(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Reconstruct the single-agent process timeline the SAME way the backend does.

    Mirrors ``EventSink._accumulate_process`` (runtime/events.py): coalesce reasoning
    deltas / content deltas into trailing steps, append a tool step per call (resolved
    by its end), drop trailing content on a reset. The resulting step order is exactly
    what the frontend folds + renders, so reading it tells us whether the order itself
    is chronologically sane.
    """
    process: list[dict[str, Any]] = []
    for ev in events:
        t = ev["type"]
        p = ev.get("payload") or {}
        if t == "reasoning_delta":
            d = p.get("delta") or ""
            if not d:
                continue
            if process and process[-1]["kind"] == "reasoning":
                process[-1]["text"] += d
            else:
                process.append({"kind": "reasoning", "text": d})
        elif t == "content_delta":
            d = p.get("delta") or ""
            if not d:
                continue
            if process and process[-1]["kind"] == "content":
                process[-1]["text"] += d
            else:
                process.append({"kind": "content", "text": d})
        elif t == "content_reset":
            while process and process[-1]["kind"] == "content":
                process.pop()
        elif t == "tool_use_start":
            process.append(
                {
                    "kind": "tool",
                    "id": p.get("tool_call_id", ""),
                    "tool_name": p.get("tool_name", ""),
                    "status": "running",
                }
            )
        elif t == "tool_use_end":
            cid = p.get("tool_call_id", "")
            for step in reversed(process):
                if step["kind"] == "tool" and step.get("id") == cid:
                    step["status"] = p.get("status", "success")
                    break
    return process


def print_folded_timeline(process: list[dict[str, Any]], *, multi_agent: bool) -> None:
    print("\n── 折叠后的「思考·正文·工具」时间线 (后端 _accumulate_process 同款) ──")
    if multi_agent:
        print(
            "  ⚠ 多 Agent 回合：真实渲染走团队图、会丢弃此 process(且 tool_use_* 无 run 归属，"
            "下面把 CEO + 队员的工具混在一条道里)。看 CEO 自身时序请只读 reasoning/content 段 +"
            " 上面里程碑的 run_plan 位置。"
        )
    if not process:
        print("  (空：无 reasoning / content / tool 步——多半是纯委派回合，正文在团队图下)")
        return
    for i, step in enumerate(process):
        kind = step["kind"]
        if kind == "tool":
            print(f"  {i:>2}. [tool] {step['tool_name']} ({step['status']})")
        else:
            label = "思考 reasoning" if kind == "reasoning" else "正文 content"
            print(f"  {i:>2}. {label}  ({len(step['text'])} chars)  {_preview(step['text'], 50)}")
    tail = process[-1]
    note = "正文 content" if tail["kind"] == "content" else f"{tail['kind']} (非 content!)"
    print(f"  末段 = {note}  →  设计上末段 content 即最终答案")


async def _login(client: httpx.AsyncClient, base_url: str, user: str, pw: str) -> str:
    r = await client.post(
        f"{base_url}/v1/auth/token",
        headers={"X-Client-Platform": "desktop"},
        json={"username": user, "password": pw},
    )
    if r.status_code == 401:
        raise SystemExit(
            "登录失败 (401)。先建 dev 账号：uv run python scripts/seed_dev_user.py"
            f"\n或用 --user/--password 指定一个已存在账号 (当前试的是 {user!r})。"
        )
    r.raise_for_status()
    return r.json()["access_token"]


async def _create_conversation(
    client: httpx.AsyncClient, base_url: str, headers: dict[str, str]
) -> str:
    r = await client.post(
        f"{base_url}/v1/conversations", headers=headers, json={"title": "时序探针"}
    )
    r.raise_for_status()
    return r.json()["id"]


async def probe(args: argparse.Namespace) -> None:
    base_url = args.base_url.rstrip("/")
    async with httpx.AsyncClient(timeout=None) as client:
        token = await _login(client, base_url, args.user, args.password)
        headers = {
            "Authorization": f"Bearer {token}",
            "X-Client-Platform": "desktop",
        }

        conv_id = args.conversation or await _create_conversation(client, base_url, headers)
        print(f"会话: {conv_id}")
        print(f"消息: {args.message!r}")
        print("─" * 70)
        print("SSE 事件 (按到达顺序, t = 自请求起的毫秒)：")

        events: list[dict[str, Any]] = []
        coalescer = TraceCoalescer(args.raw)
        start = time.monotonic()
        url = f"{base_url}/v1/conversations/{conv_id}/messages"
        async with client.stream(
            "POST",
            url,
            headers=headers,
            json={"content": args.message, "delivery": "steer"},
        ) as resp:
            if resp.status_code != 200:
                body = (await resp.aread()).decode("utf-8", "replace")
                raise SystemExit(f"发送失败 {resp.status_code}: {body}")
            async for line in resp.aiter_lines():
                if (time.monotonic() - start) > args.max_seconds:
                    print(f"  ... 超过 {args.max_seconds}s 上限，停止跟读 (回合仍在后端跑)")
                    break
                if not line.startswith("data:"):
                    continue
                raw = line[5:].strip()
                if not raw:
                    continue
                try:
                    ev = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                t_ms = int((time.monotonic() - start) * 1000)
                rec = {
                    "t_ms": t_ms,
                    "type": ev.get("type", "?"),
                    "payload": ev.get("payload") or {},
                    "ts": ev.get("timestamp"),
                }
                events.append(rec)
                coalescer.feed(t_ms, rec["type"], rec["payload"])
                if rec["type"] in {"message_end", "error"}:
                    break
        coalescer.flush()

    _summarize(events)
    multi_agent = any(ev["type"] == "run_plan" for ev in events)
    print_folded_timeline(fold_process(events), multi_agent=multi_agent)
    out = _save(events, conv_id, args.message)
    print(f"\n完整原始事件序列已存: {out}")
    print("  (后续用 Read 读这个文件，逐事件含 t_ms / type / payload)")


def _summarize(events: list[dict[str, Any]]) -> None:
    counts: dict[str, int] = {}
    for ev in events:
        counts[ev["type"]] = counts.get(ev["type"], 0) + 1
    multi = "run_plan" in counts
    print("─" * 70)
    print(f"共 {len(events)} 个事件；回合类型: {'多 Agent (有 run_plan)' if multi else '单 Agent'}")
    order = ", ".join(f"{k}×{v}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1]))
    print(f"事件计数: {order}")

    def _first(kind: str) -> str:
        for ev in events:
            if ev["type"] == kind:
                return f"{ev['t_ms']}ms"
        return "—"

    print(
        "关键里程碑到达时刻: "
        f"首 reasoning={_first('reasoning_delta')}  "
        f"首 content={_first('content_delta')}  "
        f"首 tool={_first('tool_use_start')}  "
        f"run_plan={_first('run_plan')}  "
        f"message_end={_first('message_end')}"
    )


def _save(events: list[dict[str, Any]], conv_id: str, message: str) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = OUT_DIR / f"probe_{stamp}.json"
    out.write_text(
        json.dumps(
            {"conversation_id": conv_id, "message": message, "events": events},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="发一条真实回合并 dump SSE 时序")
    parser.add_argument("message", help="要发送的用户消息")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--user", default=DEFAULT_USERNAME)
    parser.add_argument("--password", default=DEFAULT_PASSWORD)
    parser.add_argument("--conversation", default=None, help="复用已有会话 id (默认新建)")
    parser.add_argument("--raw", action="store_true", help="逐个打印 delta，不合批")
    parser.add_argument("--max-seconds", type=float, default=300.0, help="最长跟读秒数 (默认 300)")
    args = parser.parse_args()
    asyncio.run(probe(args))


if __name__ == "__main__":
    main()
