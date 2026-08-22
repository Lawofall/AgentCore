"""S1 harness: drive Desktop-equivalent sidecar JSON-RPC against hello-cli-s1.

Same engine path as Desktop (python -m agentcore.sidecar + inference proxy + local disk).
Does not modify apps/**. Evidence → logs/probes/code_cap_s1_*.json
"""

from __future__ import annotations

import codecs
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

REPO = Path(__file__).resolve().parents[2]
SERVER = REPO / "apps" / "server"
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))

from agentcore.sidecar.line_buffer import append_stdout_chunk  # noqa: E402
WS = REPO / "evals" / "code-capability" / "workspaces" / "hello-cli-s1"
OUT_DIR = REPO / "logs" / "probes"
FS_ROOTS = Path(os.environ.get("APPDATA", "")) / "agentcore-desktop" / "fs-roots.json"
DATA_DIR = Path(os.environ.get("APPDATA", "")) / "agentcore-desktop" / "sidecar"
BASE = os.environ.get("PROBE_BASE_URL", "http://127.0.0.1:8000")
USER = os.environ.get("DEV_USERNAME", "dev")
PASSWORD = os.environ.get("DEV_PASSWORD", "devpassword")
ROOT_NAME = "hello-cli-s1"
# Former sidecar ``permissionPreset: full_trust`` → managed recipe axes.
_PERMISSION_AXES_MANAGED: dict[str, str] = {
    "file_write": "session",
    "command": "auto",
    "host": "session",
}


def _venv_python() -> list[str]:
    win = SERVER / ".venv" / "Scripts" / "python.exe"
    if win.is_file():
        return [str(win)]
    return ["uv", "run", "python"]


def auth(client: httpx.Client) -> tuple[str, str]:
    r = client.post(
        f"{BASE}/v1/auth/token",
        json={"username": USER, "password": PASSWORD},
    )
    r.raise_for_status()
    token = r.json()["access_token"]
    me = client.get(f"{BASE}/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    me.raise_for_status()
    return token, me.json()["id"]


def ensure_fs_root(abs_path: Path) -> str:
    abs_s = str(abs_path.resolve())
    roots: list[dict[str, Any]] = []
    if FS_ROOTS.is_file():
        roots = json.loads(FS_ROOTS.read_text(encoding="utf-8"))
    for r in roots:
        if str(Path(r.get("absPath", "")).resolve()) == abs_s:
            return str(r["id"])
    rid = str(uuid.uuid4())
    roots.append({"id": rid, "name": ROOT_NAME, "absPath": abs_s})
    FS_ROOTS.parent.mkdir(parents=True, exist_ok=True)
    FS_ROOTS.write_text(json.dumps(roots, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return rid


def mint_inference(client: httpx.Client, token: str) -> dict[str, str]:
    r = client.post(
        f"{BASE}/v1/inference/token",
        headers={"Authorization": f"Bearer {token}"},
    )
    r.raise_for_status()
    body = r.json()
    return {
        "baseUrl": f"{BASE}/v1/inference/v1",
        "apiKey": body["token"],
        "model": body["model"],
    }


class SidecarProc:
    def __init__(self) -> None:
        self._proc = subprocess.Popen(
            [*_venv_python(), "-m", "agentcore.sidecar"],
            cwd=str(SERVER),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            # Binary pipes — see probe_sidecar_turn.SidecarClient (Windows TextIO race).
            bufsize=0,
        )
        assert self._proc.stdin and self._proc.stdout and self._proc.stderr
        self._stdin = self._proc.stdin
        self._stdout = self._proc.stdout
        self._lock = threading.Lock()
        self._pending: dict[Any, dict[str, Any] | None] = {}
        self._events: list[dict[str, Any]] = []
        self._cv = threading.Condition(self._lock)
        self._next_id = 1
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        self._err_reader = threading.Thread(target=self._stderr_loop, daemon=True)
        self._err_reader.start()
        self.stderr_tail: list[str] = []

    def _stderr_loop(self) -> None:
        assert self._proc.stderr
        dec = codecs.getincrementaldecoder("utf-8")("replace")
        buf = ""
        while True:
            raw = self._proc.stderr.read(4096)
            if not raw:
                return
            buf += dec.decode(raw)
            while "\n" in buf:
                line, buf = buf.split("\n", 1)
                line = line.rstrip("\r")
                self.stderr_tail.append(line)
                if len(self.stderr_tail) > 200:
                    self.stderr_tail = self.stderr_tail[-200:]
                print(f"[sidecar.err] {line}", flush=True)

    def _handle_line(self, line: str) -> None:
        line = line.strip()
        if not line:
            return
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            print(f"[sidecar.bad] {line[:200]}", flush=True)
            return
        with self._cv:
            if "id" in msg and ("result" in msg or "error" in msg):
                self._pending[msg["id"]] = msg
                self._cv.notify_all()
            elif msg.get("method") == "turn/event":
                self._events.append(msg.get("params") or {})
                ev = (msg.get("params") or {}).get("event") or {}
                et = ev.get("type")
                if et and et not in {"content_delta", "reasoning_delta"}:
                    tool = (ev.get("payload") or {}).get("tool_name")
                    print(f"[event] {et}" + (f" {tool}" if tool else ""), flush=True)
                self._cv.notify_all()

    def _read_loop(self) -> None:
        # Desktop-homologous chunked framing — do **not** iterate lines / readline.
        # Large run_context lines mid-write deadlock Windows pipes if the reader
        # waits for \\n before draining (see agentcore.sidecar.line_buffer).
        # Binary stdout.read short-reads; not TextIOWrapper.read(n) / readline.
        assert self._stdout
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        buffer = ""
        while True:
            raw = self._stdout.read(65536)
            if not raw:
                chunk = decoder.decode(b"", final=True)
                if buffer.strip() or chunk.strip():
                    self._handle_line(buffer + chunk)
                return
            chunk = decoder.decode(raw)
            buffer, lines = append_stdout_chunk(buffer, chunk)
            for line in lines:
                self._handle_line(line)

    def request(self, method: str, params: dict[str, Any], timeout: float = 900.0) -> dict[str, Any]:
        with self._lock:
            rid = self._next_id
            self._next_id += 1
            self._pending[rid] = None
        line = json.dumps(
            {"jsonrpc": "2.0", "id": rid, "method": method, "params": params},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        self._stdin.write((line + "\n").encode("utf-8"))
        self._stdin.flush()
        deadline = time.time() + timeout
        with self._cv:
            while self._pending.get(rid) is None:
                remaining = deadline - time.time()
                if remaining <= 0:
                    raise TimeoutError(f"{method} timed out after {timeout}s")
                self._cv.wait(timeout=min(remaining, 1.0))
                if self._proc.poll() is not None:
                    raise RuntimeError(
                        f"sidecar exited code={self._proc.returncode}; "
                        f"stderr_tail={self.stderr_tail[-20:]}"
                    )
            msg = self._pending.pop(rid)
        assert msg is not None
        if "error" in msg:
            raise RuntimeError(f"{method} error: {msg['error']}")
        return msg["result"]

    def events(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._events)

    def close(self) -> None:
        try:
            self.request("shutdown", {}, timeout=10)
        except Exception:
            pass
        with self._lock:
            try:
                self._stdin.close()
            except Exception:
                pass
        try:
            self._proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._proc.kill()


def run_golden(ws: Path) -> dict[str, Any]:
    checks: dict[str, Any] = {}
    cmds = [
        ("help", [sys.executable, "-m", "hello_cli", "--help"]),
        ("greet", [sys.executable, "-m", "hello_cli", "greet", "Ada"]),
        ("add", [sys.executable, "-m", "hello_cli", "add", "2", "3"]),
        ("pytest", [sys.executable, "-m", "pytest", "-q"]),
    ]
    # Prefer package entry; fall back to main.py --help only for help discovery.
    for name, cmd in cmds:
        try:
            p = subprocess.run(
                cmd,
                cwd=str(ws),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
            )
            checks[name] = {
                "cmd": cmd,
                "exit": p.returncode,
                "stdout": (p.stdout or "")[-2000:],
                "stderr": (p.stderr or "")[-1000:],
            }
        except Exception as e:
            checks[name] = {"cmd": cmd, "error": str(e)}
    entry = None
    if (ws / "hello_cli" / "__main__.py").is_file():
        entry = "hello_cli"
    elif (ws / "main.py").is_file():
        entry = "main.py"
    checks["entry"] = entry
    checks["files"] = sorted(
        str(p.relative_to(ws)).replace("\\", "/")
        for p in ws.rglob("*")
        if p.is_file()
        and "__pycache__" not in p.parts
        and not (
            len(p.parts) >= len(ws.parts) + 2
            and p.parts[len(ws.parts)] == "AgentCore"
            and p.parts[len(ws.parts) + 1] in ("index", "trash", "baselines")
        )
    )
    help_ok = checks.get("help", {}).get("exit") == 0
    greet_out = checks.get("greet", {}).get("stdout") or ""
    greet_ok = checks.get("greet", {}).get("exit") == 0 and "Hello, Ada" in greet_out
    add_out = (checks.get("add", {}).get("stdout") or "").strip()
    add_ok = checks.get("add", {}).get("exit") == 0 and (
        add_out.splitlines()[-1:] == ["5"] or "5" in add_out
    )
    pytest_ok = checks.get("pytest", {}).get("exit") == 0
    checks["pass"] = bool(entry and help_ok and greet_ok and add_ok and pytest_ok)
    return checks


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if not WS.is_dir():
        raise SystemExit(f"missing workspace {WS}")
    prompt = (WS / "PROMPT.md").read_text(encoding="utf-8").strip()
    # Strip markdown heading noise for the user message — send body after first blank line if present.
    lines = prompt.splitlines()
    if lines and lines[0].startswith("#"):
        body = "\n".join(lines[1:]).strip()
        prompt_text = body or prompt
    else:
        prompt_text = prompt

    root_id = ensure_fs_root(WS)
    print(f"workspace={WS}")
    print(f"root_id={root_id}")

    with httpx.Client(timeout=60.0) as client:
        token, user_id = auth(client)
        headers = {"Authorization": f"Bearer {token}"}
        conv = client.post(
            f"{BASE}/v1/conversations",
            headers=headers,
            json={"title": "code-cap-S1-hello-cli"},
        )
        conv.raise_for_status()
        conversation_id = conv.json()["id"]
        print(f"conversation_id={conversation_id}")

        bind = client.put(
            f"{BASE}/v1/conversations/{conversation_id}/workspace/binding",
            headers=headers,
            json={"root_id": root_id},
        )
        print(f"binding_status={bind.status_code} body={bind.text[:300]}")
        if bind.status_code >= 400:
            # Binding may 4xx if root unknown server-side — still run sidecar on abs path.
            print("WARN: binding failed; continuing sidecar with abs workspaceRoot")

        inference = mint_inference(client, token)
        print(f"inference_model={inference['model']}")

    turn_id = str(uuid.uuid4())
    trace_id = uuid.uuid4().hex
    user_message_id = str(uuid.uuid4())
    print(f"trace_id={trace_id}")
    print(f"turn_id={turn_id}")

    sc = SidecarProc()
    evidence: dict[str, Any] = {
        "scenario": "S1",
        "workspace": str(WS),
        "root_id": root_id,
        "conversation_id": conversation_id,
        "trace_id": trace_id,
        "turn_id": turn_id,
        "user_message_id": user_message_id,
        "binding_status": bind.status_code,
        "binding_body": bind.text[:500],
    }
    try:
        init = sc.request(
            "initialize",
            {
                "userId": user_id,
                "workspaceRoot": str(WS.resolve()),
                "approvalsEnabled": False,
                "permissionAxes": _PERMISSION_AXES_MANAGED,
                "dataDir": str(DATA_DIR),
                "inference": inference,
            },
            timeout=60,
        )
        print(f"initialize={init}")
        evidence["initialize"] = init

        result = sc.request(
            "startTurn",
            {
                "turnId": turn_id,
                "conversationId": conversation_id,
                "traceId": trace_id,
                "userMessageId": user_message_id,
                "userMessage": prompt_text,
                "history": [],
                "inference": inference,
                "permissionAxes": _PERMISSION_AXES_MANAGED,
            },
            timeout=900,
        )
        print(
            f"finishReason={result.get('finishReason')} "
            f"messageId={result.get('messageId')} "
            f"rounds={result.get('rounds')}"
        )
        evidence["startTurn"] = {
            k: result.get(k)
            for k in (
                "turnId",
                "messageId",
                "finishReason",
                "model",
                "rounds",
                "error",
                "content",
            )
        }
        evidence["content_preview"] = (result.get("content") or "")[:2000]

        # Auto-resume durable pause (ask_user / plan_review) once if needed.
        fr = str(result.get("finishReason") or "")
        if fr in {"paused", "awaiting_input", "ask_user", "plan_review"} or (
            result.get("messageId") and "paused" in fr.lower()
        ):
            mid = result["messageId"]
            print(f"resuming paused turn messageId={mid}")
            resume = sc.request(
                "resume",
                {
                    "conversationId": conversation_id,
                    "messageId": mid,
                    "traceId": uuid.uuid4().hex,
                    "userMessageId": user_message_id,
                    "decision": "continue",
                    "note": "Proceed with the hello-cli implementation as specified.",
                    "inference": inference,
                    "permissionAxes": _PERMISSION_AXES_MANAGED,
                },
                timeout=900,
            )
            evidence["resume"] = {
                k: resume.get(k)
                for k in (
                    "turnId",
                    "messageId",
                    "finishReason",
                    "model",
                    "rounds",
                    "error",
                    "content",
                )
            }
            result = resume

        # Second-chance resume if still paused after first continue.
        fr2 = str(result.get("finishReason") or "")
        if "paus" in fr2.lower() or fr2 in {"ask_user", "plan_review"}:
            mid = result.get("messageId")
            if mid:
                resume2 = sc.request(
                    "resume",
                    {
                        "conversationId": conversation_id,
                        "messageId": mid,
                        "traceId": uuid.uuid4().hex,
                        "userMessageId": user_message_id,
                        "decision": "continue",
                        "note": "Approved — implement and finish.",
                        "selected": [],
                        "inference": inference,
                        "permissionAxes": _PERMISSION_AXES_MANAGED,
                    },
                    timeout=900,
                )
                evidence["resume2"] = {
                    k: resume2.get(k)
                    for k in (
                        "messageId",
                        "finishReason",
                        "rounds",
                        "error",
                        "content",
                    )
                }
                result = resume2

        evidence["final_finishReason"] = result.get("finishReason")
        evidence["final_messageId"] = result.get("messageId")
        evidence["tool_events"] = [
            {
                "type": (e.get("event") or {}).get("type"),
                "tool": ((e.get("event") or {}).get("payload") or {}).get("tool_name"),
            }
            for e in sc.events()
            if (e.get("event") or {}).get("type")
            in {"tool_use_start", "tool_use_end", "error", "message_end"}
        ]
    finally:
        sc.close()

    golden = run_golden(WS)
    evidence["golden"] = golden
    evidence["verdict"] = "Pass" if golden.get("pass") else "Fail"

    ts = time.strftime("%Y%m%d_%H%M%S")
    out = OUT_DIR / f"code_cap_s1_{ts}.json"
    out.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"evidence={out}")
    print(f"verdict={evidence['verdict']}")
    print(f"entry={golden.get('entry')}")
    print(f"files={golden.get('files')}")
    return 0 if golden.get("pass") else 1


if __name__ == "__main__":
    raise SystemExit(main())
