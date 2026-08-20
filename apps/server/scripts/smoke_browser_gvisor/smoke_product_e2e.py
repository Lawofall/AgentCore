"""L3 团队浏览器 M0+M1+M2 —— 真机 gVisor 产品模块端到端冒烟。

与 ``scripts/poc_browser_gvisor``（探路用的同形副本）不同，本脚本直接驱动**产品模块**：

    tools/builtin/browser.py（单一 browser + action）
      → runtime/browser/registry.py（BrowserSessionRegistry.acquire）
        → tools/sandbox/browser/gvisor_session.py（open_gvisor_browser_session）
          → netns.py（真 netns+veth）+ proxy.py（真 SSRF 过滤代理，复用 core/net.py）
            + oci.py（会话 OCI）+ runsc（--platform=systrap --network=sandbox）
              + driver.py（沙箱内长驻 async Playwright Chromium）
    runtime/browser/live.py（BrowserLiveHub，M1 直播帧扇出）
    driver 的 CDP Input 注入（M2 接管，经 browser/input 端点同一 send 路径）

必须在 ``--privileged`` 容器内、产品镜像（``INSTALL_BROWSER=1`` 构建）上跑。宿主 Windows +
Docker Desktop 的运行方式见本目录 README。

逐条验收 7 断言并打印 ``SMOKE_METRICS_JSON=...`` + ``SMOKE_OK=True/False``；关键帧 / 直播样帧
落到 ``/out`` 便于目视。任何架构级失败（netstack→proxy 不通、握手卡死）都会以 SMOKE_OK=False +
诊断出现——即「停下回报」的证据。

环境变量（docker run -e）：GVISOR_ENABLED=true、BROWSER_SANDBOX_IGNORE_CGROUPS=true
（Docker Desktop 嵌套 cgroup v1，见 PoC finding #6）；read_url_allow_fake_ip_proxy 默认 True
（dev fake-IP，PoC finding #5）。
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import os
import subprocess
import time
import traceback
from pathlib import Path
from unittest.mock import MagicMock

# --- product modules under test (real, not PoC copies) -----------------------
from agentcore.config import settings
from agentcore.runtime.browser.live import default_browser_live_hub
from agentcore.runtime.browser.registry import (
    default_browser_session_registry,
    shutdown_browser_sessions,
)
from agentcore.runtime.events import EventType
from agentcore.tools.builtin.browser import BrowserTool
from agentcore.tools.protocol import ToolContext
from agentcore.tools.sandbox.browser import proxy as proxy_mod
from agentcore.tools.sandbox.browser.protocol import BrowserCommand, BrowserSessionRequest

OUT = os.environ.get("SMOKE_OUT", "/out")
WS_ROOT = Path(OUT) / "smoke-workspace"
CID = "smoke-conv-e2e-1"

# A public page (proves the sandbox → netns → filter proxy → internet path, incl. the dev
# Clash fake-IP allowance) and two offline data: pages (deterministic click/type/scroll +
# takeover injection, no network flake).
PUBLIC_URL = os.environ.get("SMOKE_PUBLIC_URL", "https://example.com")

# Form page (six-tool click/type/scroll): input has NO placeholder/aria so its snapshot name
# falls through to el.value — lets us verify typed text via a plain snapshot substring.
FORM_HTML = (
    "<!doctype html><html><head><meta charset=utf-8><title>Smoke Form</title></head>"
    "<body style='margin:0;font-family:sans-serif'>"
    "<input id=q style='position:absolute;left:20px;top:20px;width:320px;height:40px;font-size:20px'>"
    "<button id=b type=button onclick=\"this.textContent='BTN_CLICKED'\" "
    "style='position:absolute;left:20px;top:90px;width:220px;height:44px'>submitme</button>"
    "<div style='height:2200px;background:linear-gradient(#fff,#ccc)'></div>"
    "</body></html>"
)
FORM_URL = "data:text/html;base64," + base64.b64encode(FORM_HTML.encode()).decode()

# Takeover page (M2 injection): fixed-coordinate input + a button whose onclick mutates its
# own text, so both mouse and keyboard injection produce a snapshot-observable change.
TAKEOVER_HTML = (
    "<!doctype html><html><head><meta charset=utf-8><title>Takeover</title></head>"
    "<body style='margin:0;font-family:sans-serif'>"
    "<input id=inp style='position:absolute;left:20px;top:20px;width:300px;height:44px;font-size:22px'>"
    "<button id=btn onclick=\"this.textContent='CLICKED_OK'\" "
    "style='position:absolute;left:20px;top:100px;width:260px;height:50px'>clickme</button>"
    "</body></html>"
)
TAKEOVER_URL = "data:text/html;base64," + base64.b64encode(TAKEOVER_HTML.encode()).decode()

# Animated page (M1 live): continuous repaint so the CDP screencast keeps pushing frames
# (a fully static page would emit ~1 frame then go quiet). Same shape as the PoC gate page.
ANIMATED_HTML = (
    "<!doctype html><html><head><meta charset=utf-8>"
    "<style>html,body{margin:0;height:100%;background:#101827}"
    "#b{position:absolute;width:150px;height:150px;background:#38bdf8;border-radius:20px}</style>"
    "</head><body><div id=b></div>"
    "<script>const b=document.getElementById('b');"
    "function t(x){b.style.left=(Math.cos(x/400)*300+400)+'px';"
    "b.style.top=(Math.sin(x/400)*200+250)+'px';requestAnimationFrame(t);}"
    "requestAnimationFrame(t);</script></body></html>"
)
ANIMATED_URL = "data:text/html;base64," + base64.b64encode(ANIMATED_HTML.encode()).decode()


class _OutBackend:
    """Minimal WorkspaceBackend for the browser tools — only ``write_bytes`` is exercised.

    The six tools persist each keyframe via ``context.backend.write_bytes(path, frame)``; a
    real jpeg landing on a real disk (mounted /out) is exactly the「关键帧真实落盘且非空」
    proof. The workspace backend itself (ServerWorkspace) has its own tests and is out of
    scope for this browser-stack smoke.
    """

    location = "server"
    root_label = "smoke"
    dirty = False

    def __init__(self, root: Path) -> None:
        self._root = root

    async def write_bytes(self, path: str, data: bytes) -> int:
        p = self._root / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return len(data)


def _tool_ctx() -> ToolContext:
    return ToolContext.create(
        execution_id="smoke-exec",
        run_id="smoke-run",
        agent_id="smoke-agent",
        backend=_OutBackend(WS_ROOT),
        user_id="smoke-user",
        conversation_id=CID,
        escalation=MagicMock(),
    )


def _sh(cmd: list[str]) -> str:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        return p.stdout + p.stderr
    except Exception as exc:  # noqa: BLE001 - best-effort host probe
        return f"<{type(exc).__name__}: {exc}>"


def host_state() -> dict:
    """Enumerate this session's kernel resources by product naming (netns/veth) + runsc."""
    netns = [ln for ln in _sh(["ip", "netns", "list"]).splitlines() if "acbrw" in ln]
    veth = [ln for ln in _sh(["ip", "-o", "link", "show"]).splitlines() if "acbrwh" in ln]
    runsc = [
        ln
        for ln in _sh(["runsc", f"--root={settings.gvisor_runtime_root}", "list"]).splitlines()
        if "agentcore-browser-" in ln
    ]
    return {"netns": netns, "veth": veth, "runsc_containers": runsc}


def _jpeg_ok(raw: bytes) -> bool:
    return len(raw) > 0 and raw[:2] == b"\xff\xd8"


async def _tool(tool, args: dict, ctx: ToolContext):
    """Run one product tool; return (result, parsed_output_dict)."""
    res = await tool.execute(args, ctx)
    data: dict = {}
    if res.output:
        try:
            data = json.loads(res.output)
        except json.JSONDecodeError:
            data = {}
    return res, data


def _find_ref(elements: str, role: str) -> str | None:
    import re

    m = re.search(r"\[(\w+)\]\s+" + re.escape(role) + r"\b", elements or "")
    return m.group(1) if m else None


async def main() -> int:
    os.makedirs(OUT, exist_ok=True)
    WS_ROOT.mkdir(parents=True, exist_ok=True)

    metrics: dict = {
        "settings": {
            "gvisor_enabled": settings.gvisor_enabled,
            "runsc_path": settings.gvisor_runsc_path,
            "runtime_root": settings.gvisor_runtime_root,
            "ignore_cgroups": settings.browser_sandbox_ignore_cgroups,
            "proxy_port": settings.browser_proxy_port,
            "veth_subnet_base": settings.browser_veth_subnet_base,
            "allow_fake_ip_proxy": settings.read_url_allow_fake_ip_proxy,
            "screencast_every_nth_frame": settings.browser_screencast_every_nth_frame,
        }
    }
    checks: dict[str, bool] = {}

    # SSRF decision recorder: pre-seed the process-wide proxy singleton with the product
    # proxy class + an on_decision hook BEFORE anything triggers ensure_browser_proxy().
    decisions: list[dict] = []

    def _rec(method: str, host: str, allowed: bool, reason: str) -> None:
        decisions.append({"method": method, "host": host, "allowed": allowed, "reason": reason})

    proxy_mod._proxy = proxy_mod.BrowserFilterProxy(on_decision=_rec)

    registry = default_browser_session_registry()
    hub = default_browser_live_hub()  # observer wired to the default registry
    ctx = _tool_ctx()
    session = None

    _run_t0 = time.monotonic()

    def _mark(phase: str) -> None:
        print(f"[smoke] +{time.monotonic() - _run_t0:6.1f}s  {phase}", flush=True)

    _mark("start")

    try:
        # === 断言 2：BrowserSessionRegistry.acquire → 真 netns+veth+代理+runsc（冷启耗时）===
        req = BrowserSessionRequest(
            conversation_id=CID,
            viewport_width=int(settings.browser_keyframe_width),
            viewport_height=800,
            jpeg_quality=int(settings.browser_keyframe_jpeg_quality),
        )
        t0 = time.monotonic()
        session, _keyframes = await registry.acquire(req)
        cold = time.monotonic() - t0
        metrics["cold_start_seconds"] = round(cold, 2)
        after_acquire = host_state()
        metrics["host_state_after_acquire"] = after_acquire
        checks["a2_acquire_session_alive"] = bool(session is not None and session.alive)
        checks["a2_netns_created"] = len(after_acquire["netns"]) >= 1
        checks["a2_veth_created"] = len(after_acquire["veth"]) >= 1
        checks["a2_runsc_running"] = len(after_acquire["runsc_containers"]) >= 1
        print(f"[smoke] session acquired in {cold:.2f}s; host={after_acquire}", flush=True)

        # === 断言 3：browser 真跑（navigate 公网 → snapshot(a11y 非空) → click/type/scroll → screenshot）===
        tool = BrowserTool(registry=registry)

        # navigate 公网页面
        res, data = await _tool(tool, {"action": "navigate", "url": PUBLIC_URL}, ctx)
        metrics["navigate_public"] = {
            "success": res.success,
            "http_status": data.get("http_status"),
            "final_url": data.get("final_url"),
            "error": res.error,
        }
        checks["a3_navigate_public"] = bool(res.success) and data.get("http_status") == 200

        # snapshot（a11y 树非空）on the public page
        res, data = await _tool(tool, {"action": "snapshot"}, ctx)
        uwc = data.get("untrusted_web_content") or {}
        elements = uwc.get("elements") or ""
        aria = uwc.get("accessibility_tree") or ""
        metrics["snapshot_public"] = {
            "success": res.success,
            "version": data.get("snapshot_version"),
            "elements_len": len(elements),
            "aria_len": len(aria),
        }
        checks["a3_snapshot_a11y_nonempty"] = bool(res.success and (elements or aria))

        # screenshot
        res, data = await _tool(tool, {"action": "screenshot"}, ctx)
        checks["a3_screenshot"] = bool(res.success and data.get("keyframe"))

        # navigate to the offline form (deterministic click/type/scroll)
        res, _ = await _tool(tool, {"action": "navigate", "url": FORM_URL}, ctx)
        checks["a3_navigate_form"] = bool(res.success)

        # snapshot → input(e?)/button(e?) refs + version
        res, data = await _tool(tool, {"action": "snapshot"}, ctx)
        elements = (data.get("untrusted_web_content") or {}).get("elements") or ""
        v_a = data.get("snapshot_version")
        input_ref = _find_ref(elements, "input")
        button_ref = _find_ref(elements, "button")
        metrics["form_refs"] = {"input": input_ref, "button": button_ref, "version": v_a}

        # type into the input
        res, _ = await _tool(
            tool,
            {
                "action": "type",
                "ref": input_ref,
                "text": "smoke-typed-123",
                "snapshot_version": v_a,
            },
            ctx,
        )
        checks["a3_type"] = bool(res.success)

        # re-snapshot (type bumped the driver version) then click the button
        res, data = await _tool(tool, {"action": "snapshot"}, ctx)
        v_b = data.get("snapshot_version")
        res, _ = await _tool(
            tool, {"action": "click", "ref": button_ref, "snapshot_version": v_b}, ctx
        )
        checks["a3_click"] = bool(res.success)

        # scroll the tall page
        res, _ = await _tool(tool, {"action": "scroll", "dy": 600}, ctx)
        checks["a3_scroll"] = bool(res.success)

        # verify type + click actually took effect (snapshot substring)
        res, data = await _tool(tool, {"action": "snapshot"}, ctx)
        elements = (data.get("untrusted_web_content") or {}).get("elements") or ""
        checks["a3_type_effect"] = "smoke-typed-123" in elements
        checks["a3_click_effect"] = "BTN_CLICKED" in elements

        # keyframes truly on disk + non-empty jpeg
        frame_files = sorted((WS_ROOT / "browser").glob("*.jpg"))
        frame_report = [
            {"name": p.name, "bytes": p.stat().st_size, "jpeg": _jpeg_ok(p.read_bytes())}
            for p in frame_files
        ]
        metrics["keyframes_on_disk"] = frame_report
        checks["a3_keyframes_on_disk"] = len(frame_files) >= 3 and all(
            f["jpeg"] and f["bytes"] > 0 for f in frame_report
        )

        _mark("six tools done")

        # === 断言 4：M1 直播（live hub 收到连续帧，报帧率与单帧体积；stop 后停帧）===
        await session.send(BrowserCommand(action="navigate", args={"url": ANIMATED_URL, "capture": False}))
        viewer = await hub.attach(CID)
        frames: list[dict] = []
        statuses: list[str] = []
        window = 4.0
        t_end = time.monotonic() + window
        while time.monotonic() < t_end:
            try:
                ev = await asyncio.wait_for(
                    viewer.get(), timeout=max(0.05, t_end - time.monotonic())
                )
            except TimeoutError:
                break
            if ev is None:
                break
            if ev.type == EventType.BROWSER_LIVE_FRAME:
                frames.append(ev.payload)
            elif ev.type == EventType.BROWSER_LIVE_STATUS:
                statuses.append(ev.payload.get("state"))
        fps = round(len(frames) / window, 1)
        frame_kb = 0.0
        if frames:
            raw = base64.b64decode(frames[-1].get("frame_b64") or "")
            frame_kb = round(len(raw) / 1024, 1)
            with open(f"{OUT}/live-sample.jpg", "wb") as fh:
                fh.write(raw)
            checks["a4_frame_is_jpeg"] = _jpeg_ok(raw)
        metrics["live"] = {
            "statuses": statuses,
            "frames_in_window": len(frames),
            "fps": fps,
            "frame_kb": frame_kb,
            "window_s": window,
        }
        checks["a4_continuous_frames"] = len(frames) >= 10  # >2.5 fps over 4s

        # detach → after grace, screencast must stop (no more frames)
        await hub.detach(CID, viewer)
        await asyncio.sleep(float(settings.browser_live_grace_seconds) + 2.0)
        metrics["live"]["screencast_on_after_stop"] = bool(getattr(session, "_screencast_on", True))
        checks["a4_stop_halts"] = getattr(session, "_screencast_on", True) is False

        _mark("live done")

        # === 断言 5：M2 接管 input 注入鼠标点击 + 键盘输入并生效 ===
        await session.send(BrowserCommand(action="navigate", args={"url": TAKEOVER_URL, "capture": False}))
        # mouse click on the button (its onclick sets text → snapshot-observable)
        mouse_events = [
            {"kind": "mouse", "type": "move", "x": 150, "y": 125},
            {"kind": "mouse", "type": "down", "x": 150, "y": 125, "button": "left", "click_count": 1},
            {"kind": "mouse", "type": "up", "x": 150, "y": 125, "button": "left", "click_count": 1},
        ]
        r_mouse = await session.send(
            BrowserCommand(
                action="input",
                args={"events": mouse_events, "frame_width": 1280, "frame_height": 800},
            )
        )
        r_snap = await session.send(BrowserCommand(action="snapshot"))
        btn_effect = "CLICKED_OK" in (r_snap.data.get("elements") or "")
        # keyboard: focus the input by clicking it, then insert text
        key_events = [
            {"kind": "mouse", "type": "down", "x": 170, "y": 42, "button": "left", "click_count": 1},
            {"kind": "mouse", "type": "up", "x": 170, "y": 42, "button": "left", "click_count": 1},
            {"kind": "text", "text": "hi-takeover-9"},
        ]
        r_key = await session.send(
            BrowserCommand(
                action="input",
                args={"events": key_events, "frame_width": 1280, "frame_height": 800},
            )
        )
        r_snap2 = await session.send(BrowserCommand(action="snapshot"))
        key_effect = "hi-takeover-9" in (r_snap2.data.get("elements") or "")
        metrics["takeover_input"] = {
            "mouse_injected": r_mouse.data.get("injected"),
            "mouse_effect_clicked": btn_effect,
            "key_injected": r_key.data.get("injected"),
            "key_effect_text": key_effect,
        }
        checks["a5_mouse_inject"] = bool(r_mouse.ok) and int(r_mouse.data.get("injected") or 0) >= 3
        checks["a5_mouse_effect"] = btn_effect
        checks["a5_key_inject"] = bool(r_key.ok) and int(r_key.data.get("injected") or 0) >= 3
        checks["a5_key_effect"] = key_effect
        _mark("takeover done")

        # === 断言 6：SSRF 负面（沙箱内经代理访问元数据 + 私网被拒）===
        # metadata.google.internal + a private literal IP are NORMAL http targets Chromium
        # DOES route via --proxy-server, so both hit the real sandbox → proxy chokepoint and
        # get refused there (recorded via the product proxy's on_decision hook).
        _t = time.monotonic()
        r_meta = await session.send(
            BrowserCommand(
                action="navigate",
                args={"url": "http://metadata.google.internal/computeMetadata/v1/", "capture": False},
            )
        )
        meta_nav_s = round(time.monotonic() - _t, 1)
        _t = time.monotonic()
        r_priv = await session.send(
            BrowserCommand(
                action="navigate", args={"url": "http://10.199.0.9/", "capture": False}
            )
        )
        priv_nav_s = round(time.monotonic() - _t, 1)
        _mark(f"ssrf navigates done (meta={meta_nav_s}s priv={priv_nav_s}s)")
        blocked = [d for d in decisions if not d["allowed"]]
        meta_blocked = any("metadata.google.internal" in d["host"] for d in blocked)
        priv_blocked = any(d["host"] == "10.199.0.9" for d in blocked)
        # The cloud-metadata IP literal (link-local) — Chromium bypasses the proxy for
        # link-local (PoC finding #4), so prove the chokepoint itself refuses it via the
        # SAME product function the proxy runs per request.
        ip_pinned, ip_reason = await proxy_mod.resolve_dial_target(
            "169.254.169.254", 80, scheme="http"
        )
        metrics["ssrf"] = {
            "blocked_decisions": blocked[-8:],
            "metadata_hostname_blocked": meta_blocked,
            "private_ip_blocked": priv_blocked,
            "metadata_ip_resolve": {"pinned": ip_pinned, "reason": ip_reason},
            "metadata_navigate_s": meta_nav_s,
            "private_navigate_s": priv_nav_s,
            "metadata_navigate_result": {"ok": r_meta.ok, "http_status": r_meta.data.get("http_status")},
            "private_navigate_result": {"ok": r_priv.ok, "http_status": r_priv.data.get("http_status")},
        }
        checks["a6_metadata_blocked"] = meta_blocked
        checks["a6_private_blocked"] = priv_blocked
        checks["a6_metadata_ip_refused"] = ip_pinned is None

    except Exception as exc:  # noqa: BLE001 - failure IS the evidence
        metrics["error"] = f"{type(exc).__name__}: {exc}"
        metrics["traceback"] = traceback.format_exc()
        print("[smoke] EXCEPTION:\n" + metrics["traceback"], flush=True)

    # === 断言 7：干净拆除（registry.close 后 runsc/netns/veth 全消失）===
    try:
        _mark("teardown start")
        # Test-only timing wrapper on the session's runsc helper (no product change) to
        # attribute the teardown cost precisely.
        if session is not None and hasattr(session, "_runsc_cmd"):
            _orig_runsc = session._runsc_cmd

            async def _timed_runsc(*a):  # noqa: ANN001
                _s = time.monotonic()
                await _orig_runsc(*a)
                print(f"[smoke]   runsc {' '.join(a)} took {time.monotonic() - _s:.1f}s", flush=True)

            session._runsc_cmd = _timed_runsc
        pre = host_state()
        _tc = time.monotonic()
        await registry.close(CID)
        metrics["teardown_close_s"] = round(time.monotonic() - _tc, 1)
        await asyncio.sleep(3.0)
        post = host_state()
        _mark("teardown done")
        metrics["teardown"] = {"pre": pre, "post": post}
        had_resources = (
            len(pre["netns"]) + len(pre["veth"]) + len(pre["runsc_containers"])
        ) > 0
        clean = (
            len(post["netns"]) == 0
            and len(post["veth"]) == 0
            and len(post["runsc_containers"]) == 0
        )
        checks["a7_teardown_clean"] = bool(had_resources and clean)
    except Exception as exc:  # noqa: BLE001
        metrics["teardown_error"] = f"{type(exc).__name__}: {exc}"
        checks["a7_teardown_clean"] = False
    finally:
        with contextlib.suppress(Exception):
            await shutdown_browser_sessions()

    metrics["checks"] = checks
    ok = all(
        checks.get(k, False)
        for k in (
            "a2_acquire_session_alive", "a2_netns_created", "a2_veth_created", "a2_runsc_running",
            "a3_navigate_public", "a3_snapshot_a11y_nonempty", "a3_screenshot",
            "a3_navigate_form", "a3_type", "a3_click", "a3_scroll",
            "a3_type_effect", "a3_click_effect", "a3_keyframes_on_disk",
            "a4_continuous_frames", "a4_frame_is_jpeg", "a4_stop_halts",
            "a5_mouse_inject", "a5_mouse_effect", "a5_key_inject", "a5_key_effect",
            "a6_metadata_blocked", "a6_private_blocked", "a6_metadata_ip_refused",
            "a7_teardown_clean",
        )
    )
    print("SMOKE_METRICS_JSON=" + json.dumps(metrics, ensure_ascii=False), flush=True)
    print(f"SMOKE_OK={ok}", flush=True)
    return 0 if ok else 5


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
