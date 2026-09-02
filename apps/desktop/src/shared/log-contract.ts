/**
 * 桌面端结构化日志 IPC 契约 —— 主进程 / preload / renderer 三端共享的单一真相源。
 *
 * 渲染层在沙箱里无法直接落盘，故经此通道把结构化事件交给主进程，由主进程按 **JSON
 * Lines** 追加到 `userData/logs/desktop.jsonl`（与后端 `logs/dev.jsonl` 同为可被
 * `json.loads` / `jq` 逐行解析的产品日志）。每行由主进程自动补 `timestamp` /
 * `build`(prod|dev) / `version`——这让产品日志**真正能区分「安装版」与「开发态」**：
 * 开发态会出现 `auth.bootstrap result=dev_auto_login`（被 .env.local 自动重登掩盖的那条
 * 路径），生产态则只会是 me_ok / refreshed / logged_out / outage。
 *
 * 事件命名沿用后端「组件.动作」式（如 `auth.bootstrap`），动作/状态走 snake_case 字段。
 * 鉴权可观测：`auth.bootstrap`（冷启动）、`auth.refresh`（静默刷新失败/歧义 cookie）、
 * `auth.session_kicked`（中途踢回登录页）。
 * 后端连通性（composer 断线只读红条）：`server_health.offline`（边沿；`source`=
 * heartbeat|api_outage|browser_offline|bootstrap，`reason`/`last_ok_at`/`from`；
 * heartbeat 另可带 `consecutive_failures`）/ `server_health.online`（仅从 offline
 * 恢复；`since_offline_ms`）。心跳探活成功不打。软失败（未达阈值、UI 未翻红）：
 * `server_health.probe_failed`（第 1 次 `debug`、之后 `warn`；`consecutive_failures`/
 * `failure_threshold`/`reason`/`kind`/`duration_ms`/`http_status?`/`status`）→
 * 自愈未翻红则 `server_health.probe_recovered`。会话中 API 5xx/断传输
 * 但 `/readyz` 仍健康：`server_health.api_outage_ignored`（不标 offline）。
 * 自动更新可观测（主进程直写）：`updater.configure` / `updater.schedule_start` /
 * `updater.policy` / `updater.check_begin|end` / `updater.phase` /
 * `updater.download_begin|progress|end` / `updater.error` / `updater.open_installer`
 *（含 `durationMs` / `sinceCheckMs`，用于区分 policy / feed / 下载慢点；
 * `download_progress` 的 `bytesPerSecond`=近期窗口；`configure` 另记
 * `installerSource=github`——安装包走 GitHub，不经 electron-updater）。
 * 切对话消息窗诊断（临时）：`conversation.slice_diag`（`action`=
 * `message_end_slice_kept` / `release_drop`（仅显式 API）/ `warm_skip_reconcile`
 *（仅 generating）/ `warm_keep_anchor`（pendingFocus / ?msg=）/ `warm_snap_latest` /
 * `load_latest_window` / `open_decide` / `reject_not_resident` /
 * `reject_not_richer` / `reject_generating` / `reject_active_has_more_after` 等）。
 * 本地引擎互斥拒（横幅「turn already running」；不进云端 sync:logs）：
 * `sidecar.turn_already_running`（`op`=startTurn|resume，`turn_id` / `conversation_id` /
 * `saw_any_event`；与 sidecar 进程同名事件对偶，查 `userData/logs/desktop.jsonl`）。
 * 本地工作区通道 L3（channel dead / 多对话活性挂起）：`workspace_op.received` /
 * `workspace_op.dropped`（turnPhase 门丢掉）/ `workspace_op.ipc_begin|end`（成功多为
 * debug）/ `workspace_op.aborted`（超时 Abort · warn，含 `inflight_cid` /
 * `inflight_total` / `queue_depth` / `duration_ms`）/ `workspace_op.fulfill_begin`
 *（debug）/ `workspace_op.resolve`（`outcome`=ok|stale_404|fail；可含
 * `resolve_attempts` / `resolve_ms`）/ `workspace_op.resolve_retry` /
 * 回合掉线重连（GET attach，禁止 POST 重发）：`conversation.rejoin_retry` /
 * `conversation.rejoin_closed`。对话级跟播：`conversation.follow_open` /
 * `conversation.follow_closed`（切走 / 卸订）/ `conversation.follow_muted` /
 * `conversation.follow_unmuted`（本端自有连接占用时静音不断连，`reason`=
 * `local_stream_handoff`）。旧日志里让位曾 abort 成 `follow_closed` 同 reason。
 * `workspace_op.settle_exhausted`（`stream_nudged`）/ `sse.idle_stall`（泵空闲 60s）/
 * `sse.forced_transport_drop`（settle 耗尽后踢泵 → rejoin）。字段对齐服务端
 * `workspace.op_timeout`：`conversation_id` / `request_id` / `op`。
 * 主进程墙钟 + 物理并发闸（对齐 desktop.jsonl）：`workspace_op.queued`（debug；
 * CAP 满入队）/ `workspace_op.admitted`（debug；获物理槽，含 `queue_wait_ms`）/
 * `workspace_op.main_begin`（debug）/ `workspace_op.main_end`（成功 debug / 失败
 * warn）/ `workspace_op.main_timeout`（warn；活性；含 inflight / physical_running /
 * zombie_count / queue_depth / duration）/ `workspace_op.zombie_enter`（warn；超时
 * leave-once 后底层仍占物理槽）/ `workspace_op.zombie_end`（debug；底层 finally）/
 * `workspace_op.rejected_capacity`（warn；排队耗尽 deadline，detail ≠ 活性挂起 /
 * timed out）。字段另含 `physical_running` / `zombie_count` / `cap`；`queue_depth`
 * = 真实排队等待者数（非 leave-once 伪争用）。可选 conversation_id / request_id。
 * 铁律：禁止把 token / 密码 / 消息正文放进 `fields`（只记可观测信号，不记机密与正文）。
 *
 * 与 ipc-contract（文件系统）/ sidecar-contract（本地引擎）/ updater-contract（自动更新）
 * 分文件：各自独立的主进程能力，刻意不混在一个契约里。
 */

export type LogLevel = "debug" | "info" | "warn" | "error";

/** renderer 发往主进程的一条日志（fire-and-forget，不等回执）。 */
export interface LogEntry {
  level: LogLevel;
  /** 组件.动作，如 `auth.bootstrap`。 */
  event: string;
  /** 结构化字段（已脱敏：禁止放 token / 密码 / 消息正文）。 */
  fields?: Record<string, unknown>;
}

/** 主进程落盘后每行的最终形状（在 {@link LogEntry} 基础上补运行环境元数据）。 */
export interface LogRecord extends LogEntry {
  /** ISO 8601 UTC 时间戳。 */
  timestamp: string;
  /** 安装版（打包）= "prod"；dev / 未打包 = "dev"——产品日志据此区分本机与开发。 */
  build: "prod" | "dev";
  /** 应用版本（`app.getVersion()`）。 */
  version: string;
}

/** IPC 通道名 —— 主进程与 preload 共用，避免硬编码漂移（对齐 fs/sidecar/updater 契约写法）。 */
export const LOG_CHANNELS = {
  /** renderer → main，单向 send（fire-and-forget，日志失败绝不回灌阻塞 UI）。 */
  write: "app:log",
  /**
   * renderer → main：读 ``desktop.jsonl`` 尾部并返回**已脱敏**的 JSON 行
   * （只含连通性 / 重连诊断事件与允许字段；无正文 / token / 文件内容）。
   */
  readTail: "app:log:readTail",
} as const;

/** 暴露在 `window.logApi` 上的 renderer 端 API 面。 */
export interface LogApi {
  /** 记一条结构化日志到产品日志文件（fire-and-forget；失败静默吞掉）。 */
  write(entry: LogEntry): void;
  /**
   * 排查包用：本机 ``desktop.jsonl`` 尾部的脱敏行。纯浏览器 / 单测可缺失。
   * 失败时返回空数组，绝不抛到 UI。
   */
  readTail(): Promise<string[]>;
}
