"""探针：真跑「项目内 ask_user 挂起 → 断线 → POST /resume」，验证 resume 后 consult_memory
命中【项目作用域】主题（Agent记忆与知识系统 §二 / resume folder_id+memory_enabled 缺口的端到端活体验证）。

全走正规 HTTP（dev 账号 BYOK），步骤：
1. 登录（记忆产品层恒开，无需再拧开关）。
2. 建项目文件夹 + 绑定会话（POST /folders；POST /conversations{folder_id}）。
3. 直写一条【项目作用域】主题笔记 + 一条【全局】同名主题（证明 project-first）。
   consult_memory 读的是 主题/<slug>.md，无写 API（offline consolidation 才生成），故探针经
   default_memory_store() 直写到后端同一 data 目录。
4. 发一条产出类请求 → CEO 走「发问门」ask_user 挂起（持久化帧）→ 读到 checkpoint_required 即断线。
   发问门是判断式触发，故最多在 4 个新会话间重试，直到某轮真的挂起。
5. GET /recovery 确认帧已落库（读 paused 数组）。断线后在线 run 仍阻塞在 ask_user、握着 folder 级 workspace_lock，但探针
   直接走 durable /resume —— 服务端 resume_message 会先 stop_and_drain 掉该在线 run（释放锁、留帧）再
   续跑，这正是「断线未重启 → fallback durable resume」的真实路径（兼回归该服务端防线）。
6. POST /resume（note 明确要求先 consult_memory 查『部署流程』再答）。
7. 读 logs/dev.jsonl，确认【本会话且 resume 之后】出现 consult_memory.hit scope=project。

从 apps/server 跑::

    uv run python scripts/archive/probe_resume_memory.py

凭据默认 dev/devpassword、http://localhost:8000，可用 DEV_USERNAME/DEV_PASSWORD/PROBE_BASE_URL 覆盖。
仅 dev 探针，无旁路；会花真实 DeepSeek token。
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from agentcore.memory.store import default_memory_store, topic_path

REPO_ROOT = Path(__file__).resolve().parents[3]
LOG_FILE = REPO_ROOT / "logs" / "dev.jsonl"

DEFAULT_BASE_URL = os.environ.get("PROBE_BASE_URL", "http://localhost:8000")
DEFAULT_USERNAME = os.environ.get("DEV_USERNAME", "dev")
DEFAULT_PASSWORD = os.environ.get("DEV_PASSWORD", "devpassword")

TOPIC = "部署流程"
PROJECT_BODY = (
    "## 本项目部署流程（项目作用域）\n"
    "- 步骤一：`pnpm deploy:backend <short-sha>`（生产机构建镜像）\n"
    "- 步骤二：`pnpm -C apps/website deploy:pages`\n"
    "- 校验：`curl.exe https://app.example/api/version` 看 git_sha\n"
    "- 标记：本条来自【项目层】记忆，PROBE_PROJECT_MARKER_7Q\n"
)
GLOBAL_BODY = (
    "## 通用部署（全局作用域）\n"
    "- 通用 CI 流程，不含本项目专属步骤\n"
    "- 标记：本条来自【全局】记忆，PROBE_GLOBAL_MARKER_3Z\n"
)

PAUSE_EVENTS = {"checkpoint_required", "plan_review_required"}


def _user_id_from_jwt(token: str) -> str:
    """The ``sub`` claim (= user_id) from the access token, decoded WITHOUT verification
    (probe-only: we just need the id to address the on-disk memory store the server reads)."""
    payload_b64 = token.split(".")[1]
    payload_b64 += "=" * (-len(payload_b64) % 4)  # restore base64 padding
    payload = json.loads(base64.urlsafe_b64decode(payload_b64))
    return payload.get("sub") or payload.get("user_id") or ""


async def _login(client: httpx.AsyncClient, base: str, user: str, pw: str) -> str:
    r = await client.post(f"{base}/v1/auth/token", json={"username": user, "password": pw})
    if r.status_code == 401:
        raise SystemExit("登录失败 (401)。先建 dev 账号：uv run python scripts/seed_dev_user.py")
    r.raise_for_status()
    return r.json()["access_token"]


async def _stream_events(
    client: httpx.AsyncClient,
    url: str,
    headers: dict[str, str],
    body: dict[str, Any],
    *,
    stop_on: set[str],
    label: str,
    max_seconds: float = 240.0,
) -> tuple[list[dict[str, Any]], str | None]:
    """POST an SSE turn and collect events until ``stop_on`` (or message_end/error).

    Returns (events, message_id). ``stop_on`` lets the caller cut the stream at the pause
    (simulating a client disconnect) so the durable frame is left for ``/resume``.
    """
    events: list[dict[str, Any]] = []
    message_id: str | None = None
    start = time.monotonic()
    async with client.stream("POST", url, headers=headers, json=body) as resp:
        if resp.status_code != 200:
            raw = (await resp.aread()).decode("utf-8", "replace")
            raise SystemExit(f"[{label}] 发送失败 {resp.status_code}: {raw}")
        async for line in resp.aiter_lines():
            if (time.monotonic() - start) > max_seconds:
                print(f"  [{label}] 超过 {max_seconds}s，停止跟读")
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
            etype = ev.get("type", "?")
            payload = ev.get("payload") or {}
            events.append({"type": etype, "payload": payload})
            if etype == "message_start" and not message_id:
                message_id = payload.get("message_id")
            if etype == "tool_use_start":
                print(f"  [{label}] tool> {payload.get('tool_name')}")
            if etype == "tool_use_end":
                print(f"  [{label}] tool< {payload.get('tool_name')} ({payload.get('status')})")
            if etype in PAUSE_EVENTS:
                print(f"  [{label}] PAUSE «{etype}»  → 断线")
            if etype == "error":
                print(f"  [{label}] error {payload.get('code')}: {payload.get('message')}")
            if etype in stop_on or etype in {"message_end", "error"}:
                break
    return events, message_id


async def _seed_topics(user_id: str, folder_id: str) -> None:
    store = default_memory_store()
    # SAME topic name in both scopes, different bodies → a project-first hit must
    # return the PROJECT body, proving scope precedence end-to-end.
    await store.save(user_id, topic_path(TOPIC), GLOBAL_BODY)
    await store.save(user_id, topic_path(TOPIC), PROJECT_BODY, scope=folder_id)


def _log_epoch(ts: str | None) -> float:
    """Parse a structlog ISO8601 ``timestamp`` (``...Z``) to epoch seconds; 0.0 if absent."""
    if not ts:
        return 0.0
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _read_consult_logs_for(
    conversation_id: str, since_epoch: float, tail: int = 4000
) -> list[dict[str, Any]]:
    """``consult_memory.*`` 日志，**仅限本次探针的会话且发生在 ``since_epoch`` 之后**。

    旧版只读末尾若干行、不按会话/时间过滤——会命中其它并行回合或上一次探针的陈旧 hit，
    把判定钉死成假阳。这里用 conversation_id（每次探针新建，故唯一标识本次运行）+ resume
    起始时刻双重过滤，确保 scope=project 的命中确实来自这次 resume，而非 send 阶段或历史。
    """
    if not LOG_FILE.exists():
        return []
    lines = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
    hits: list[dict[str, Any]] = []
    for line in lines[-tail:]:
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        ev = rec.get("event", "")
        if not (isinstance(ev, str) and ev.startswith("consult_memory")):
            continue
        if rec.get("conversation_id") != conversation_id:
            continue
        if _log_epoch(rec.get("timestamp")) < since_epoch:
            continue
        hits.append(rec)
    return hits


async def run(args: argparse.Namespace) -> int:
    base = args.base_url.rstrip("/")
    async with httpx.AsyncClient(timeout=None) as client:
        token = await _login(client, base, args.user, args.password)
        headers = {"Authorization": f"Bearer {token}"}
        user_id = _user_id_from_jwt(token)
        print(f"登录 OK  user_id={user_id}")

        # 1) 项目文件夹 + 绑定会话
        fr = await client.post(
            f"{base}/v1/folders", headers=headers, json={"name": "记忆resume探针", "mode": "cloud"}
        )
        fr.raise_for_status()
        folder_id = fr.json()["id"]
        cr = await client.post(
            f"{base}/v1/conversations",
            headers=headers,
            json={"title": "记忆resume探针会话", "folder_id": folder_id},
        )
        cr.raise_for_status()
        conv_id = cr.json()["id"]
        print(f"项目 folder_id={folder_id}\n会话 conversation_id={conv_id}")

        # 2) 直写项目+全局同名主题
        await _seed_topics(user_id, folder_id)
        print(f"已写主题「{TOPIC}」：项目作用域(scope={folder_id}) + 全局各一份")

        # 3) 产出类请求 → ask_user 挂起 → 断线。发问门是判断式触发（非确定性，实测同一句话可能这次
        #    挂起、下次直答）；本探针只需要可靠到达「挂起」态以验 resume，故用一句明确把关键决策权交还
        #    用户、并请其先发问的产出类请求，最大化走发问门概率，再在最多 ATTEMPTS 个新会话间重试。
        msg = (
            "我想做一个网站，但风格、页面结构、要放的内容我都还没想好。"
            "你先别急着动手，挑几个最关键的问题来问我，等我确认方向后再开始。"
        )
        attempts = 4
        paused_conv: str | None = None
        for attempt in range(1, attempts + 1):
            if attempt == 1:
                attempt_conv = conv_id
            else:
                cr2 = await client.post(
                    f"{base}/v1/conversations",
                    headers=headers,
                    json={"title": f"记忆resume探针会话#{attempt}", "folder_id": folder_id},
                )
                cr2.raise_for_status()
                attempt_conv = cr2.json()["id"]
            print(f"\n[send#{attempt}] conv={attempt_conv}\n  发送笼统请求：{msg!r}")
            send_url = f"{base}/v1/conversations/{attempt_conv}/messages"
            events, _ = await _stream_events(
                client, send_url, headers, {"content": msg},
                stop_on=PAUSE_EVENTS, label=f"send#{attempt}",
            )
            if any(e["type"] in PAUSE_EVENTS for e in events):
                paused_conv = attempt_conv
                break
            last = events[-1]["type"] if events else "(无事件)"
            print(f"[send#{attempt}] 未触发挂起（末事件={last}）。")
        if paused_conv is None:
            print(f"[send] {attempts} 次均未走发问门挂起——CEO 这几轮都直答了，换更笼统的话再试。")
            return 2
        conv_id = paused_conv

        # 4) 确认持久化帧已落库。断线后在线 run 仍阻塞在 ask_user、握着 folder 级 workspace_lock——
        #    但探针不再客户端 /stop，直接走 durable /resume：服务端 resume_message 会先
        #    stop_and_drain 掉这个在线 run（cancel 释放锁、留帧），再用帧续跑。这正是「断线未重启 →
        #    fallback durable resume」的真实路径，同时回归验证该服务端自洽防线。
        await asyncio.sleep(0.5)  # 给 suspension_saver 落帧一点时间
        pl = await client.get(
            f"{base}/v1/conversations/{conv_id}/recovery", headers=headers
        )
        pl.raise_for_status()
        frames = pl.json().get("paused", [])
        if not frames:
            print("[paused] 没有挂起帧——可能帧尚未落库或已被消费。")
            return 3
        frame = frames[0]
        paused_mid = frame["message_id"]
        print(f"[paused] 帧 kind={frame['kind']} message_id={paused_mid} question={frame.get('question','')[:40]!r}")

        # 5) durable /resume：服务端先 stop_and_drain 在线 run → claim 帧 → 全新 run → 从帧里的
        #    folder_id+memory_enabled 重建工具集 → consult_memory 必须仍命中【项目作用域】。
        note = (
            f"请先调用 consult_memory 查阅本项目的『{TOPIC}』记忆主题，把它的全文读出来，"
            "然后严格按其中列出的步骤，给出本项目的部署方案。"
        )
        # ask_user 帧只收 CONTINUE（带 note 作回答）；ADJUST 是 plan_review 专用语义。
        print(f"[resume] decision=continue note={note[:50]!r}")
        resume_started = time.time()  # 判定只认这一刻之后、本会话的 consult_memory.hit
        resume_url = f"{base}/v1/conversations/{conv_id}/messages/{paused_mid}/resume"
        r_events, _ = await _stream_events(
            client,
            resume_url,
            headers,
            {"decision": "continue", "note": note, "selected": []},
            stop_on=set(),
            label="resume",
        )
        consulted = any(
            e["type"] in {"tool_use_start", "tool_use_end"}
            and (e["payload"].get("tool_name") == "consult_memory")
            for e in r_events
        )
        final = "".join(
            e["payload"].get("delta", "")
            for e in r_events
            if e["type"] == "content_delta"
        )
        print(f"[resume] consult_memory 被调用={consulted}  最终答复前120字：{final[:120]!r}")

    # 6) 读日志验证 scope=project（仅本会话、仅 resume 之后——杜绝陈旧/send 阶段命中冒充）
    await asyncio.sleep(0.4)
    logs = _read_consult_logs_for(conv_id, resume_started)
    print("\n── logs/dev.jsonl 里的 consult_memory 事件（本次探针）──")
    if not logs:
        print("  (未发现 consult_memory.* 日志——模型可能没真正调用该工具)")
    for rec in logs[-6:]:
        print(
            f"  {rec.get('timestamp','')}  {rec.get('event')}  "
            f"name={rec.get('name')}  scope={rec.get('scope')}  project_id={rec.get('project_id')}"
        )
    hit_project = any(
        r.get("event") == "consult_memory.hit" and r.get("scope") == "project" for r in logs
    )
    project_marker = "PROBE_PROJECT_MARKER_7Q" in final
    print("\n══ 判定 ══")
    print(f"  consult_memory.hit scope=project 出现日志：{hit_project}")
    print(f"  最终答复含【项目层】标记(PROBE_PROJECT_MARKER_7Q)：{project_marker}")
    if hit_project:
        print("  ✅ resume 后 consult_memory 命中项目作用域主题——端到端活体验证通过。")
        return 0
    print("  ⚠ 未在日志中确认 scope=project（见上方诊断）。")
    return 1


def main() -> None:
    p = argparse.ArgumentParser(description="活体探针：项目内 ask_user 挂起→resume→consult_memory 命中项目主题")
    p.add_argument("--base-url", default=DEFAULT_BASE_URL)
    p.add_argument("--user", default=DEFAULT_USERNAME)
    p.add_argument("--password", default=DEFAULT_PASSWORD)
    raise SystemExit(asyncio.run(run(p.parse_args())))


if __name__ == "__main__":
    main()
