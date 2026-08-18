"""Workspace storage, snapshots, retention, and local-op timeouts."""

from pydantic import BaseModel


class WorkspaceSettings(BaseModel):
    data_dir: str = "./data"

    storage_backend: str = "auto"
    s3_endpoint_url: str = ""
    s3_region: str = "cn-shenzhen"
    s3_bucket: str = "agentcore-workspaces"
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""
    s3_addressing_style: str = "path"

    workspace_snapshot_enabled: bool = True
    workspace_auto_snapshot_max: int = 10
    # System snapshots (turn-baseline / handoff / export·merge labels): D+C —
    # keep newest N AND within TTL; user-named kept versions are never pruned here.
    # Open handoff Diff base + turn baseline_snapshot_id refs are pinned (skipped).
    # TTL defaults align with workspace_retention_days (soft-delete grace).
    workspace_system_baseline_snapshot_max: int = 5
    workspace_system_other_snapshot_max: int = 10
    workspace_system_snapshot_retention_days: int = 30
    # Third retention axis on the same storage key: total zip bytes (count + TTL
    # stay). Whole-tree zips have no increment/dedup — a 84MB workspace at the
    # count caps still grows without bound. 0 disables this leg. User-named kept
    # versions and open-handoff / turn-baseline pins are never evicted here;
    # oldest evictable first. A single zip over the cap is kept (do not erase
    # the restore point just written).
    workspace_snapshot_max_bytes: int = 500 * 1024 * 1024

    # Local turn baseline zips (``AgentCore/baselines/<message_id>.zip``, sidecar +
    # desktop channel): same D+C shape as the cloud system caps above, pruned right
    # after a new baseline lands. The count is looser because the disk is the user's
    # own and each kept zip is one more turn that can still roll back; it is still
    # capped, since a whole-tree zip per turn otherwise accumulates forever. Either
    # knob at ``0`` disables that leg (never "delete every restore point").
    # Desktop main mirrors both values in ``fs/constants.ts`` — it cannot read settings.
    workspace_local_baseline_max: int = 20
    workspace_local_baseline_retention_days: int = 30

    workspace_retention_enabled: bool = True
    # Soft-deleted workspace grace before hard purge. Also the open-handoff Diff
    # window (§7.6): unapplied/undiscarded cloud hosts stay until finished_at +
    # this many days, then soft-delete into the same sweep — never early-delete
    # on succeed (that would break Diff). Apply/discard soft-delete immediately.
    workspace_retention_days: int = 30
    workspace_retention_sweep_interval_seconds: int = 6 * 3600
    workspace_retention_batch_limit: int = 100

    workspace_upload_max_bytes: int = 25 * 1024 * 1024
    avatar_upload_max_bytes: int = 5 * 1024 * 1024
    workspace_clone_timeout_seconds: int = 120
    workspace_op_timeout_seconds: float = 60.0
    # Channel default when no outer tool deadline is bound (tests / handoff archive).
    # Inside ``tool_exec``, ``WorkspaceChannel`` **derives** its deadline from the
    # outer tool liveness budget (``tool_default_timeout_seconds`` − settle slack) —
    # never a second independent 60s clock. See ``runtime/tool_deadline.py``.
    # Local WorkspaceChannel only: max concurrent desktop round-trips (awaiting
    # suspend). Extra ops queue; queue wait counts on the outer tool wall clock.
    # Does not apply to cloud ServerWorkspace / workspace_lock.
    # 16: enough for a multi-worker local file wave without pretending the
    # desktop bridge is unbounded; still a backpressure valve, not a product cap.
    workspace_channel_max_inflight: int = 16
    # Prepare-phase wall-clock budget for local desktop channel IO (baseline +
    # probe_exec + exists/.git + …). Caps the sum of per-op timeouts so a silent
    # fulfiller cannot burn 60s×N before the turn aborts. Execution-phase tool
    # IO is unaffected.
    prepare_local_io_budget_seconds: float = 20.0
    workspace_execute_timeout_slack_seconds: float = 30.0
    workspace_handoff_timeout_seconds: float = 300.0
    # AI 协作白板 (AI协作白板.md §六 M2): how long the BoardChannel waits for the bound
    # desktop to apply an op batch before failing the call (so a closed canvas / dropped
    # client never hangs the turn). Same class as the workspace-op deadline above.
    board_op_timeout_seconds: float = 60.0

    # Cloud (server-location) workers: code_execute runs in the API container subprocess
    # — not a real isolation boundary. Default off; local/sidecar keeps code_execute.
    code_execute_cloud_enabled: bool = False
    # Second, deliberate acknowledgement that the cloud subprocess "sandbox" is NOT a real
    # isolation boundary (no namespace/seccomp/rlimit/egress control): enabling cloud code
    # execution gives any authenticated user full-permission RCE inside the API container.
    # ``_validate_production_security`` refuses to boot a non-debug server that turns
    # ``code_execute_cloud_enabled`` on without ALSO setting this, so the dangerous config
    # can never be reached by flipping a single flag (SEC-005).
    code_execute_cloud_unsafe_ack: bool = False

    # Cloud (server-location) workers: use gVisor (runsc) for real isolation.
    # Default ON for 内测/自托管（取消「部署时再想起开开关」）；健康探测失败仍
    # 诚实不装配执行类。紧急关闭：机上 env ``GVISOR_ENABLED=false``。
    # When true, code_execute is enabled on cloud workers without the unsafe-ack gate.
    gvisor_enabled: bool = True
    # Path to the runsc binary (default: on PATH).
    gvisor_runsc_path: str = "runsc"
    # runsc runtime state directory (containers, sandboxes). Must live on the
    # DATA_DIR volume — container /tmp overlay makes runsc mkdir fail with EINVAL.
    # Local default tracks ``data_dir``; compose sets ``/data/sandbox`` when
    # ``DATA_DIR=/data``.
    gvisor_runtime_root: str = "./data/sandbox"

    # ── gVisor 灰度护栏（部署与运维.md §云端执行灰度 / 安全权限与治理.md §五）──
    # Global cap on concurrently RUNNING cloud sandbox executions per API process
    # (single-uvicorn production ⇒ effectively per host). Sized for the 2C8G box.
    gvisor_max_concurrent_executions: int = 2
    # Bounded grace queue: how long one call may wait for a free slot before it
    # fails fast with an explainable "busy" result. code_execute stays ≤60s at the
    # tool layer; test_run (bounded verify) may use up to gvisor_timeout_max.
    gvisor_slot_wait_seconds: float = 15.0
    # Per-execution hard resource caps enforced by the OCI spec. Authoritative for
    # cloud runs: an ExecutionRequest cannot exceed them. Memory default sized for
    # document/data workloads (pandas + matplotlib comfortably above 256MB).
    gvisor_memory_limit_mb: int = 512
    # Ceiling for sandbox requests. code_execute still caps itself at 60s; raised
    # so bounded project verify (test_run outer loop) is not silently truncated on
    # cloud gVisor. Covers disaster wall (1200s) + engine slack (30s).
    gvisor_timeout_max_seconds: int = 1230
    # 产物写回 (copy-in/copy-out): the workspace is COPIED into a per-execution
    # staging dir (mounted rw at /workspace), and new/changed regular files are
    # copied back after the run. Caps bound both legs.
    gvisor_stage_max_bytes: int = 512 * 1024 * 1024
    gvisor_write_back_max_bytes: int = 128 * 1024 * 1024
    gvisor_write_back_max_files: int = 200

    # ── L3 团队浏览器 M0（内置浏览器与Agent浏览器提案.md · D9–D11）────────────
    # Session-level long-lived Chromium sandboxes (browser_* tools). Cloud-only:
    # gated by the SAME cloud-execution predicate as code_execute (needs gVisor),
    # so a plain-subprocess API container never runs a browser (no isolation).
    # Concurrency gate: process-wide cap on live browser sessions (~1GB/session per
    # PoC, so a 8GB node holds a handful). A new conversation past the cap fails
    # fast with an explainable busy result after an idle reap.
    browser_max_sessions: int = 4
    # Idle TTL: a session untouched this long is reaped (the reaper loop + lazy
    # checks). Default 10min — a research pause shouldn't hold ~1GB indefinitely.
    browser_session_idle_ttl_seconds: float = 10 * 60.0
    # Max lifetime: a session is force-recycled past this age even if active, so a
    # runaway loop cannot pin a sandbox forever. Default 2h.
    browser_session_max_lifetime_seconds: float = 2 * 3600.0
    # Reaper sweep cadence (lifespan background loop, mirrors session_retention).
    browser_reaper_interval_seconds: float = 60.0
    # Lifespan ``close_all`` wall-clock cap. Shutdown must not wait runsc's 180s
    # per-command bound, nor drain sessions serially; leftover sandboxes are left
    # for restart / the reaper.
    browser_shutdown_close_all_seconds: float = 6.0
    # Per-command RPC deadline (host waits this long for one driver response before
    # treating the driver as wedged). Navigation is the slow leg.
    browser_command_timeout_seconds: float = 60.0
    # Keyframe budget (D5 关键帧): jpeg quality / viewport width, per-turn frame
    # count cap and single-frame byte cap. Over a cap ⇒ stop capturing frames but
    # the tool keeps working (the action still runs, only the screenshot is skipped).
    browser_keyframe_jpeg_quality: int = 70
    browser_keyframe_width: int = 1280
    browser_keyframe_max_per_turn: int = 60
    browser_keyframe_max_bytes: int = 512 * 1024
    # Per-session OCI caps (PoC 差异清单：browser 会话专用，勿动 code_execute 限额).
    # ~1.5–2GB observed peak (systrap); pids/cpu raised for Chromium's many procs.
    browser_sandbox_memory_limit_mb: int = 2048
    browser_sandbox_pids_limit: int = 512
    browser_sandbox_cpu_limit: float = 2.0
    # Skip runsc OCI cgroup application. Docker's default cgroup2 mount inside the
    # api container is read-only — non-rootless browser runsc then dies at create.
    # Code auto-adds ``--ignore-cgroups`` when ``cgroup.subtree_control`` exists
    # and is not writable; this flag forces the same. Session memory/pids/cpu
    # limits then do not apply (container ``mem_limit`` still does).
    browser_sandbox_ignore_cgroups: bool = False
    # Playwright Chromium bundle location inside the runtime image (ro-bind into the
    # sandbox; the product's 5 host binds don't cover /opt, so add exactly this one).
    browser_playwright_browsers_path: str = "/opt/ms-playwright"
    # Host SSRF filter proxy: the per-session veth /24 base and listen port. Each
    # session gets 10.<base2>.<n>.0/24 (host .1 = proxy, sandbox .2); no NAT/forward
    # so the sandbox's only route out is the proxy (D10 network-layer enforcement).
    browser_proxy_port: int = 8899
    browser_veth_subnet_base: str = "10.201"

    # Packaging install egress (allowlist proxy + netns; distinct from browser SSRF).
    # Hostnames from ``ALLOWED_NPM_REGISTRIES`` + ``ALLOWED_NPM_HOSTS`` (CDN ≠ pin).
    package_egress_proxy_port: int = 8898
    package_veth_subnet_base: str = "10.202"

    # ── L3 团队浏览器 M1 直播（内置浏览器与Agent浏览器提案.md · D13–D15）─────────
    # Live screencast baseline (D14): CDP Page.startScreencast params. The gVisor gate
    # (scripts/poc_browser_gvisor/run_screencast.py) measured ~57fps @ ~14KB/frame at
    # q60/1280 — capability far exceeds need, so everyNthFrame throttles the base rate
    # (2 ⇒ ~half) while frame ack backpressure + per-viewer coalescing bound the rest.
    browser_screencast_jpeg_quality: int = 60
    browser_screencast_max_width: int = 1280
    browser_screencast_max_height: int = 800
    browser_screencast_every_nth_frame: int = 2
    # Local (Desktop Bridge) live: poll capturePage via screenshot action while watched.
    # No CDP screencast; interval bounds Bridge/CPU cost (≈4 fps). Only runs when Hub
    # has viewers (start/stop_screencast); zero overhead when nobody is watching.
    browser_local_screencast_interval_seconds: float = 0.25
    # Viewer lifecycle (D13): screencast starts on the FIRST viewer attach and stops when
    # the LAST viewer leaves — after this grace window, so a refresh / quick reconnect does
    # not thrash start/stop. A watched session is also spared idle-TTL reaping (max lifetime
    # still applies) so an open live tab does not pin a sandbox forever.
    browser_live_grace_seconds: float = 3.0
    # Per-viewer bounded frame queue (drop-oldest = latest-frame-wins): keeps a slow / stalled
    # viewer from growing memory and keeps latency low (show the newest frame, not a backlog).
    browser_live_max_queued_frames: int = 8

    # ── L3 团队浏览器 M2 接管（内置浏览器与Agent浏览器提案.md · D16–D18）──────────
    # Max input events one POST …/browser/input batch may carry (打字合批 上限). Bounds a
    # single injection round so a malformed / oversized batch is rejected (422) rather than
    # wedging the driver; the client coalesces typing into batches under this cap.
    browser_input_max_events: int = 256
