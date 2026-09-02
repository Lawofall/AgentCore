import type { SidecarTarget } from "@/services/sidecarRouting";
import {
  setSidecarSpawnedHandler,
  takeRecentSidecarFailure,
} from "@/services/sidecarStatus";

/**
 * 本地引擎（sidecar）会话级健康缓存 + 主动探活。
 *
 * 双模式工作区 §7.2 · 探活增强。本机传统新开回合**默认同侧** sidecar（`resolveSidecarRoot`：
 * unset 不挡；仅 `sidecarPreference==="off"` 强制云）。若用户机器环境起不来（杀软 / 缺组件 /
 * venv 损坏…），没有探活则每个回合都「试 startTurn → 启动失败 → 降级过桥」，反复打扰。
 *
 * 本模块把「首轮失败」前移成一次**主动探活**，并按 `root + subpath` 记住结果（app 进程内、
 * 会话级，不持久化）：
 *   - {@link probeSidecar}：首次走 sidecar 前调；未知则拉起进程 + initialize 握手（不跑回合）验
 *     证环境，成功标 `ok` / 失败标 `bad`（诊断取自 `sidecarStatus`）。已有结论则直接返回、不重探；
 *     `bad` 带 TTL——过期后允许再探，避免整会话无限静默走云。返回的 `probed` 区分「本次真探活」
 *     与「命中缓存」，便于日志 / 诊断。
 *   - {@link getSidecarHealth}：查询本会话对某根的健康结论（`ok`/`bad`/`unknown`），供测试断言 /
 *     UI 状态展示。路由判定（`resolveSidecarRoot`）**不**看它——健康收敛由各调用方按语义处理
 *     （`sendTurn` 探活失败走云、`runResume` 探活失败保留帧），见各处。
 *   - {@link markSidecarUnhealthy}：阶段二降级（探活通过、但回合启动期仍失败的边缘）也标 `bad`，
 *     与探活**共用**同一「记坏 → 命中缓存」出口，不形成第二条降级路径。
 *   - {@link clearSidecarHealth}：用户在设置里重新开启本地引擎时清空，给「修好环境后重试」机会。
 *
 * 过桥可感知面 = CloudBridgeHint（executionVia → 最新助手泡脚注），无 toast 节流槽。
 *
 * 探活成功留存的进程正好被随后的首个回合复用（主进程 `ensure` 命中缓存），故探活不浪费拉起。
 */

type Health = "ok" | "bad";

type HealthEntry = { health: Health; at: number; detail: string | null };

/** `bad` 缓存 TTL：过期后 `probeSidecar` 再探一次（修好环境 / 偶发失败可恢复）。 */
export const BAD_HEALTH_TTL_MS = 5 * 60 * 1000;

/** `rootId::subpath` → 本会话探明的健康（与主进程 `entryKey` 同构）。无项 = 未探活（unknown）。 */
const health = new Map<string, HealthEntry>();

function keyOf(target: SidecarTarget): string {
  return `${target.rootId}::${target.subpath}`;
}

function nowMs(): number {
  return Date.now();
}

/** 本会话对某 sidecar 目标的健康结论：`ok` 可走 / `bad` 跳过走云 / `unknown` 尚未探活。 */
export function getSidecarHealth(target: SidecarTarget): Health | "unknown" {
  const entry = health.get(keyOf(target));
  if (!entry) return "unknown";
  if (entry.health === "bad" && nowMs() - entry.at >= BAD_HEALTH_TTL_MS) {
    return "unknown";
  }
  return entry.health;
}

/**
 * 标记某 sidecar 目标本会话「环境起不来」（探活失败、或阶段二降级的边缘失败）。
 *
 * 标记后 `probeSidecar` 对该根在 TTL 内命中 `bad` 缓存（`probed:false`）；过期后允许再探。
 * `detail` 留在缓存里，命中续云时仍能打出可读诊断（不必等 TTL 再探）。
 */
export function markSidecarUnhealthy(
  target: SidecarTarget,
  detail: string | null = null,
): void {
  health.set(keyOf(target), { health: "bad", at: nowMs(), detail });
}

/**
 * 主进程推送 `spawned` 时清掉该根下所有 `bad`（进程已起来，勿再等满 TTL 才放行）。
 * 同 root 不同 subpath 一并清——拉起成功说明本机环境可用。
 */
export function noteSidecarSpawned(rootId: string): void {
  const prefix = `${rootId}::`;
  for (const [k, entry] of health) {
    if (entry.health === "bad" && k.startsWith(prefix)) {
      health.delete(k);
    }
  }
}

/** 清空全部健康结论（用户在设置里重新开启本地引擎时调）。 */
export function clearSidecarHealth(): void {
  health.clear();
}

/** 一次探活的结论：是否健康 + 本次是否真探活（区分首探 / 命中缓存）+ （失败时）可读诊断。 */
export interface SidecarProbeOutcome {
  healthy: boolean;
  /** 本次是否真执行了探活（true = 首探或 TTL 后再探；false = 命中 `ok`/`bad` 缓存或非桌面）。 */
  probed: boolean;
  detail: string | null;
}

/**
 * 探活一个 sidecar 目标（带会话级缓存）：未知则主动拉起 + 握手验证环境，已有结论则直接返回。
 *
 * 成功 → 标 `ok`、返回 healthy（留存的进程被随后首个回合复用）；失败 → 标 `bad`、返回带诊断的
 * unhealthy（诊断取自 `sidecarStatus` 的 onStatus 推送）。非桌面 / 未注入 `sidecarApi` 时视作
 * 不健康（调用方退回云链路）。`bad` 超过 {@link BAD_HEALTH_TTL_MS} 后视为未知并再探。
 */
export async function probeSidecar(
  target: SidecarTarget,
): Promise<SidecarProbeOutcome> {
  const key = keyOf(target);
  const cached = health.get(key);
  if (cached?.health === "ok") {
    return { healthy: true, probed: false, detail: null };
  }
  if (cached?.health === "bad") {
    if (nowMs() - cached.at < BAD_HEALTH_TTL_MS) {
      // 命中缓存仍带回上次诊断，便于 desktop.jsonl 对照 via=cloud。
      return {
        healthy: false,
        probed: false,
        detail: cached.detail,
      };
    }
    health.delete(key);
  }

  if (typeof window === "undefined" || !window.sidecarApi) {
    // 非桌面：调用方应已因 isSidecarEnabled 拿不到 target。此处诚实不健康，勿假装可走。
    return {
      healthy: false,
      probed: false,
      detail: "当前环境无本地引擎",
    };
  }
  try {
    await window.sidecarApi.probe({
      rootId: target.rootId,
      subpath: target.subpath,
    });
    health.set(key, { health: "ok", at: nowMs(), detail: null });
    return { healthy: true, probed: true, detail: null };
  } catch {
    // 诊断由主进程 onStatus(error) 推入 sidecarStatus；取走它换出针对性提示（取不到则 null，
    // 由调用方退回通用兜底文案）。写入 bad 缓存，TTL 内续云仍可带出同一 detail。
    const detail = takeRecentSidecarFailure(target.rootId);
    health.set(key, { health: "bad", at: nowMs(), detail });
    return {
      healthy: false,
      probed: true,
      detail,
    };
  }
}

// 进程真正拉起成功 → 提前作废该根 bad，避免 DEV 下偶发首探失败后整段静默走云。
setSidecarSpawnedHandler(noteSidecarSpawned);
