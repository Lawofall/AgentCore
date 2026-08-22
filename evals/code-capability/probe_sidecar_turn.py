"""Drive one real sidecar turn against a local workspace (Desktop+sidecar path).

Mirrors Desktop's JSON-RPC contract (initialize / startTurn / resume) without
touching apps/**. Used by code-capability eval scenarios (e.g. S5).

From repo root or apps/server::

    uv run python ../../evals/code-capability/probe_sidecar_turn.py \\
      --workspace <abs-or-rel> --title code-cap-S5 --prompt-file PROMPT.md

Artifacts land in logs/probes/sidecar_<ts>.json
"""

from __future__ import annotations

import argparse
import asyncio
import codecs
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

# Prefer apps/server on path so ``agentcore.sidecar.line_buffer`` resolves when
# launched via ``uv run`` from that cwd (r_llm_smoke / probe CLI).
_SERVER_ROOT = Path(__file__).resolve().parents[2] / "apps" / "server"
if str(_SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(_SERVER_ROOT))

from agentcore.sidecar.line_buffer import append_stdout_chunk  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = REPO_ROOT / "logs" / "probes"
DEFAULT_BASE_URL = os.environ.get("PROBE_BASE_URL", "http://127.0.0.1:8000")
DEFAULT_USERNAME = os.environ.get("DEV_USERNAME", "dev")
DEFAULT_PASSWORD = os.environ.get("DEV_PASSWORD", "devpassword")
AGENTCORE_ROOT_ID = "24407ff4-5703-4904-8f10-68314f673384"  # fs-roots: C:\\Project\\AgentCore
# Former sidecar ``permissionPreset: full_trust`` → managed recipe axes.
_PERMISSION_AXES_MANAGED: dict[str, str] = {
    "file_write": "session",
    "command": "auto",
    "host": "session",
}
# Unpackaged Electron dumps loopback Bridge creds here (see main/browser/bridge.ts).
_DEFAULT_DEV_BRIDGE = (
    Path(os.environ.get("APPDATA", "")) / "agentcore-desktop" / "browser-bridge.dev.json"
)
DEV_BRIDGE_FILE = Path(os.environ.get("AGENTCORE_DEV_BRIDGE_FILE", str(_DEFAULT_DEV_BRIDGE)))


def _load_desktop_bridge() -> dict[str, str] | None:
    """Read unpackaged Electron ``browser-bridge.dev.json`` → ``{baseUrl, token}``."""
    if not DEV_BRIDGE_FILE.is_file():
        return None
    try:
        data = json.loads(DEV_BRIDGE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    url = (data.get("baseUrl") or "").strip().rstrip("/")
    token = (data.get("token") or "").strip()
    if not url or not token:
        return None
    return {"baseUrl": url, "token": token}


def _inject_desktop_bridge_env(env: dict[str, str]) -> str | None:
    """Legacy env fallback; prefer per-turn ``browserBridge`` RPC (B-Arch)."""
    if env.get("AGENTCORE_BROWSER_BRIDGE_URL") and env.get("AGENTCORE_BROWSER_BRIDGE_TOKEN"):
        return f"env already set ({env['AGENTCORE_BROWSER_BRIDGE_URL']})"
    creds = _load_desktop_bridge()
    if not creds:
        if not DEV_BRIDGE_FILE.is_file():
            return None
        return f"dev bridge file unusable: {DEV_BRIDGE_FILE}"
    env["AGENTCORE_BROWSER_BRIDGE_URL"] = creds["baseUrl"]
    env["AGENTCORE_BROWSER_BRIDGE_TOKEN"] = creds["token"]
    return f"loaded {DEV_BRIDGE_FILE} → {creds['baseUrl']}"


def _new_id() -> str:
    return str(uuid.uuid4())


async def _auth(client: httpx.AsyncClient, base: str, user: str, password: str) -> tuple[str, str]:
    r = await client.post(f"{base}/v1/auth/token", json={"username": user, "password": password})
    r.raise_for_status()
    token = r.json()["access_token"]
    me = await client.get(f"{base}/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    me.raise_for_status()
    return token, me.json()["id"]


async def _mint_inference(client: httpx.AsyncClient, base: str, token: str) -> dict[str, str]:
    r = await client.post(f"{base}/v1/inference/token", headers={"Authorization": f"Bearer {token}"})
    r.raise_for_status()
    body = r.json()
    return {
        "baseUrl": f"{base}/v1/inference/v1",
        "apiKey": body["token"],
        "model": body["model"],
    }


async def _setup_conversation(
    client: httpx.AsyncClient,
    *,
    base: str,
    token: str,
    title: str,
    root_id: str,
    local_subpath: str | None,
) -> tuple[str, str | None]:
    headers = {"Authorization": f"Bearer {token}"}
    folder_id: str | None = None
    if local_subpath:
        fr = await client.post(
            f"{base}/v1/folders",
            headers=headers,
            json={
                "name": title,
                "mode": "local",
                "local_root_id": root_id,
                "local_subpath": local_subpath.replace("\\", "/"),
            },
        )
        fr.raise_for_status()
        folder_id = fr.json()["id"]
        cr = await client.post(
            f"{base}/v1/conversations",
            headers=headers,
            json={"title": title, "folder_id": folder_id},
        )
        cr.raise_for_status()
        return cr.json()["id"], folder_id

    cr = await client.post(
        f"{base}/v1/conversations",
        headers=headers,
        json={"title": title},
    )
    cr.raise_for_status()
    conv_id = cr.json()["id"]
    br = await client.put(
        f"{base}/v1/conversations/{conv_id}/workspace/binding",
        headers=headers,
        json={"root_id": root_id},
    )
    br.raise_for_status()
    return conv_id, None


class SidecarClient:
    """Line-delimited JSON-RPC over a spawned ``python -m agentcore.sidecar``."""

    def __init__(self, workspace: Path, data_dir: Path) -> None:
        self.workspace = workspace
        self.data_dir = data_dir
        self.proc: subprocess.Popen[str] | None = None
        self._reader: asyncio.Task[None] | None = None
        self._pending: dict[Any, asyncio.Future[dict[str, Any]]] = {}
        self.events: list[dict[str, Any]] = []
        self._lines: list[dict[str, Any]] = []
        self._id = 0

    async def start(self) -> None:
        env = os.environ.copy()
        bridge_note = _inject_desktop_bridge_env(env)
        if bridge_note:
            print(f"bridge: {bridge_note}", flush=True)
        else:
            print(
                f"bridge: no AGENTCORE_BROWSER_BRIDGE_* and no {DEV_BRIDGE_FILE} "
                "(start unpackaged desktop first for Local browser_*)",
                flush=True,
            )
        # Sidecar→localhost inference SSE stalls (0 chunks); unary completes.
        env.setdefault("AGENTCORE_INFERENCE_UNARY", "1")
        # Prefer apps/server venv python via `uv run` from that cwd.
        server_cwd = REPO_ROOT / "apps" / "server"
        self.proc = subprocess.Popen(
            ["uv", "run", "python", "-m", "agentcore.sidecar"],
            cwd=str(server_cwd),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            # Windows pipe backpressure can stall the sidecar event loop mid-LLM
            # stream (looks like hang after proxy_spend_enqueued). Discard unless
            # AGENTCORE_SIDECAR_STDERR=1 for debugging.
            stderr=(
                None
                if os.environ.get("AGENTCORE_SIDECAR_STDERR", "").strip() in {"1", "true", "yes"}
                else subprocess.DEVNULL
            ),
            # Binary pipes: text=True + os.read(fileno) races TextIOWrapper's buffer
            # on Windows (response lands in BufferedReader, fileno read blocks forever).
            # Desktop also treats stdout as a byte stream + decode.
            bufsize=0,
            env=env,
        )
        assert self.proc.stdin and self.proc.stdout
        self._reader = asyncio.create_task(self._read_loop())
        if self.proc.stderr is not None:
            asyncio.create_task(self._drain_stderr())

    async def _drain_stderr(self) -> None:
        assert self.proc and self.proc.stderr
        loop = asyncio.get_running_loop()
        while True:
            raw = await loop.run_in_executor(None, self.proc.stderr.read, 4096)
            if not raw:
                return
            sys.stderr.write(f"[sidecar.err] {raw.decode('utf-8', errors='replace')}")

    def _dispatch_line(self, line: str) -> None:
        line = line.strip()
        if not line:
            return
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            sys.stderr.write(f"[sidecar.bad] {line[:200]}\n")
            return
        self._lines.append(msg)
        if "id" in msg and ("result" in msg or "error" in msg):
            fut = self._pending.pop(msg["id"], None)
            if fut and not fut.done():
                fut.set_result(msg)
        elif msg.get("method") == "turn/event":
            params = msg.get("params") or {}
            self.events.append(params)
            inner = params.get("event")
            if isinstance(inner, dict):
                et = inner.get("type") or "?"
                payload = inner.get("payload") or {}
            else:
                et = params.get("type") or "?"
                payload = params.get("payload") or {}
            label = et if isinstance(et, str) else "?"
            if label == "tool_use_start":
                label = f"tool> {payload.get('tool_name')}"
            elif label == "tool_use_end":
                label = f"tool< {payload.get('tool_name')} ({payload.get('status')})"
            elif label == "run_plan":
                agents = payload.get("agents") or []
                label = f"TEAM run_plan agents={len(agents)}"
            elif label in {"content_delta", "reasoning_delta"}:
                return  # noisy
            print(f"  evt {label}", flush=True)

    async def _read_loop(self) -> None:
        """Desktop-homologous chunked framing — do **not** use ``readline()``.

        ``readline`` waits for a full ``\\n``. A large ``run_context`` line written
        halfway fills the Windows pipe; the probe blocks in readline while the
        sidecar blocks in write → deadlock. Desktop uses ``data`` chunks + buffer;
        we ``read(chunk)`` on a binary pipe (short reads) the same way.

        Avoid ``TextIOWrapper.read(n)`` — it keeps reading until *n* chars arrive.
        """
        assert self.proc and self.proc.stdout
        loop = asyncio.get_running_loop()
        stdout = self.proc.stdout
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        buffer = ""

        def _read_chunk() -> str:
            raw = stdout.read(65536)
            if not raw:
                return decoder.decode(b"", final=True)
            return decoder.decode(raw)

        while True:
            try:
                chunk = await loop.run_in_executor(None, _read_chunk)
            except Exception as exc:
                sys.stderr.write(f"[sidecar.read] {exc!r}\n")
                for fut in self._pending.values():
                    if not fut.done():
                        fut.set_exception(exc)
                return
            if not chunk:
                if buffer.strip():
                    try:
                        self._dispatch_line(buffer)
                    except Exception as exc:
                        sys.stderr.write(f"[sidecar.dispatch] {exc!r}\n")
                for fut in self._pending.values():
                    if not fut.done():
                        fut.set_exception(RuntimeError("sidecar stdout closed"))
                return
            buffer, lines = append_stdout_chunk(buffer, chunk)
            for line in lines:
                try:
                    self._dispatch_line(line)
                except Exception as exc:
                    sys.stderr.write(f"[sidecar.dispatch] {exc!r}\n")

    def _next_id(self) -> int:
        self._id += 1
        return self._id

    async def request(self, method: str, params: dict[str, Any], *, timeout: float) -> dict[str, Any]:
        assert self.proc and self.proc.stdin
        req_id = self._next_id()
        fut: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[req_id] = fut
        msg = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
        payload = (json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8")
        stdin = self.proc.stdin

        def _write_req() -> None:
            stdin.write(payload)
            stdin.flush()

        await asyncio.to_thread(_write_req)
        return await asyncio.wait_for(fut, timeout=timeout)

    async def initialize(
        self,
        *,
        user_id: str,
        inference: dict[str, str] | None,
        browser_bridge: dict[str, str] | None = None,
        permission_axes: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "workspaceRoot": str(self.workspace),
            "userId": user_id,
            "approvalsEnabled": False,
            "permissionAxes": permission_axes or _PERMISSION_AXES_MANAGED,
            "dataDir": str(self.data_dir),
        }
        if inference:
            params["inference"] = inference
        if browser_bridge is not None:
            params["browserBridge"] = browser_bridge
        return await self.request("initialize", params, timeout=120)

    async def start_turn(
        self,
        *,
        turn_id: str,
        conversation_id: str,
        user_message: str,
        trace_id: str,
        user_message_id: str,
        inference: dict[str, str] | None,
        browser_bridge: dict[str, str] | None = None,
        permission_axes: dict[str, str] | None = None,
        timeout: float,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "turnId": turn_id,
            "conversationId": conversation_id,
            "userMessage": user_message,
            "history": [],
            "traceId": trace_id,
            "userMessageId": user_message_id,
            "permissionAxes": permission_axes or _PERMISSION_AXES_MANAGED,
        }
        if inference:
            params["inference"] = inference
        if browser_bridge is not None:
            params["browserBridge"] = browser_bridge
        return await self.request("startTurn", params, timeout=timeout)

    async def resume(
        self,
        *,
        message_id: str,
        conversation_id: str,
        trace_id: str,
        decision: str = "continue",
        note: str = "",
        inference: dict[str, str] | None,
        browser_bridge: dict[str, str] | None = None,
        timeout: float,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "messageId": message_id,
            "conversationId": conversation_id,
            "traceId": trace_id,
            "decision": decision,
            "note": note,
            "selected": [],
            "permissionAxes": _PERMISSION_AXES_MANAGED,
        }
        if inference:
            params["inference"] = inference
        if browser_bridge is not None:
            params["browserBridge"] = browser_bridge
        return await self.request("resume", params, timeout=timeout)

    async def close(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        if self._reader:
            self._reader.cancel()


def _event_types(events: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for e in events:
        t = e.get("type") or e.get("event")
        if isinstance(t, str):
            out.append(t)
    return out


def _has_run_plan(events: list[dict[str, Any]]) -> bool:
    return "run_plan" in _event_types(events)


async def main_async(args: argparse.Namespace) -> int:
    workspace = Path(args.workspace).resolve()
    if not workspace.is_dir():
        print(f"FAIL: workspace missing: {workspace}", file=sys.stderr)
        return 2

    prompt = args.prompt
    if args.prompt_file:
        prompt = Path(args.prompt_file).read_text(encoding="utf-8")
    if args.prompt_prefix:
        prompt = args.prompt_prefix.rstrip() + "\n\n" + prompt

    # Relative subpath under AgentCore root (for local folder binding).
    try:
        subpath = str(workspace.relative_to(REPO_ROOT)).replace("\\", "/")
    except ValueError:
        subpath = None

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    data_dir = OUT_DIR / f"sidecar_data_{ts}"
    data_dir.mkdir(parents=True, exist_ok=True)

    async with httpx.AsyncClient(timeout=60.0) as client:
        token, user_id = await _auth(client, args.base_url, args.user, args.password)
        print(f"auth ok user_id={user_id}")
        inference = await _mint_inference(client, args.base_url, token)
        print(f"inference model={inference['model']}")
        conv_id, folder_id = await _setup_conversation(
            client,
            base=args.base_url,
            token=token,
            title=args.title,
            root_id=args.root_id,
            local_subpath=subpath if args.bind_folder else None,
        )
        print(f"conversation_id={conv_id} folder_id={folder_id} subpath={subpath}")

    trace_id = uuid.uuid4().hex
    turn_id = _new_id()
    user_message_id = _new_id()
    print(f"trace_id={trace_id}")
    print(f"workspace={workspace}")

    sc = SidecarClient(workspace, data_dir)
    t0 = time.time()
    result: dict[str, Any] | None = None
    error: str | None = None
    browser_bridge = _load_desktop_bridge()
    if browser_bridge:
        print(f"browserBridge RPC: {browser_bridge['baseUrl']}", flush=True)
    else:
        print("browserBridge RPC: none (env fallback only)", flush=True)
    try:
        await sc.start()
        init = await sc.initialize(
            user_id=user_id, inference=inference, browser_bridge=browser_bridge
        )
        print(f"initialize: {json.dumps(init.get('result') or init.get('error'), ensure_ascii=False)}")
        if "error" in init:
            error = str(init["error"])
            raise RuntimeError(error)

        print("--- startTurn ---")
        resp = await sc.start_turn(
            turn_id=turn_id,
            conversation_id=conv_id,
            user_message=prompt,
            trace_id=trace_id,
            user_message_id=user_message_id,
            inference=inference,
            browser_bridge=browser_bridge,
            timeout=args.timeout,
        )
        if "error" in resp:
            error = str(resp["error"])
            raise RuntimeError(error)
        result = resp.get("result") or {}
        finish = result.get("finishReason") or result.get("finish_reason")
        print(f"startTurn finish={finish} messageId={result.get('messageId')}")

        # Auto-resume ask_user / plan_review once (continue).
        resumes = 0
        while (
            resumes < args.max_resumes
            and isinstance(finish, str)
            and finish.lower() in {"ask_user", "plan_review", "paused", "needs_input", "suspended"}
        ):
            mid = result.get("messageId") or result.get("message_id")
            if not mid:
                break
            resumes += 1
            print(f"--- resume #{resumes} decision=continue messageId={mid} ---")
            # Fresh inference token in case TTL mattered.
            async with httpx.AsyncClient(timeout=60.0) as client:
                token, _ = await _auth(client, args.base_url, args.user, args.password)
                inference = await _mint_inference(client, args.base_url, token)
            resp = await sc.resume(
                message_id=str(mid),
                conversation_id=conv_id,
                trace_id=trace_id,
                decision="continue",
                note="S5 eval: continue",
                inference=inference,
                browser_bridge=browser_bridge,
                timeout=args.timeout,
            )
            if "error" in resp:
                error = str(resp["error"])
                print(f"resume error: {error}")
                break
            result = resp.get("result") or {}
            finish = result.get("finishReason") or result.get("finish_reason")
            print(f"resume finish={finish}")
    except Exception as e:
        error = str(e)
        print(f"ERROR: {e}", file=sys.stderr)
    finally:
        elapsed = time.time() - t0
        await sc.close()

    types = _event_types(sc.events)
    team = _has_run_plan(sc.events)
    artifact = {
        "scenario": args.title,
        "workspace": str(workspace),
        "conversation_id": conv_id,
        "folder_id": folder_id,
        "trace_id": trace_id,
        "turn_id": turn_id,
        "user_message_id": user_message_id,
        "elapsed_sec": round(elapsed, 1),
        "run_plan": team,
        "event_types": types,
        "tool_names": [
            (e.get("payload") or {}).get("tool_name")
            for e in sc.events
            if (e.get("type") == "tool_use_start")
        ],
        "result": result,
        "error": error,
        "prompt_preview": prompt[:400],
    }
    out_path = OUT_DIR / f"sidecar_{ts}.json"
    out_path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"artifact={out_path}")
    print(f"run_plan={team} events={len(sc.events)} error={error!r}")
    return 0 if error is None else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--workspace", required=True, help="Absolute/relative local workspace root")
    p.add_argument("--title", default="code-cap-S5")
    p.add_argument("--prompt", default="", help="Inline prompt text")
    p.add_argument("--prompt-file", default="", help="Read prompt from file")
    p.add_argument(
        "--prompt-prefix",
        default="",
        help="Optional prefix (e.g. team-formation instruction for S5)",
    )
    p.add_argument("--base-url", default=DEFAULT_BASE_URL)
    p.add_argument("--user", default=DEFAULT_USERNAME)
    p.add_argument("--password", default=DEFAULT_PASSWORD)
    p.add_argument("--root-id", default=AGENTCORE_ROOT_ID)
    p.add_argument("--bind-folder", action="store_true", default=True)
    p.add_argument("--no-bind-folder", action="store_false", dest="bind_folder")
    p.add_argument("--timeout", type=float, default=1200.0)
    p.add_argument("--max-resumes", type=int, default=3)
    return p


def main() -> None:
    args = build_parser().parse_args()
    if not args.prompt and not args.prompt_file:
        print("Need --prompt or --prompt-file", file=sys.stderr)
        sys.exit(2)
    raise SystemExit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()
