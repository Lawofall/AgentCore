import { logEvent } from "@/lib/log";
import { create } from "zustand";

/**
 * Ambient backend connectivity, sampled by a lightweight `/readyz` heartbeat
 * (see `services/serverHealth`). This is the "can I reach the server *right now*"
 * signal the composer shows so the user knows connectivity **before** sending —
 * distinct from the auth store's `unavailable`, which is the hard, full-screen
 * outage takeover the AuthGate raises reactively on a failed request.
 *
 * - `checking`: the first probe hasn't resolved yet (startup) — never flashed as
 *   "offline" so a healthy session doesn't blink red on load.
 * - `online` / `offline`: the last probe's verdict.
 *
 * Edge-only product logs (`server_health.offline` / `server_health.online`) land in
 * `desktop.jsonl` so dogfood dumps can explain the composer's disconnect banner.
 * Soft probe misses before the failure threshold log `server_health.probe_failed`
 * (1st=`debug`, later=`warn`; see `services/serverHealth`); mid-session API blips that `/readyz` rejects as
 * outages log `server_health.api_outage_ignored`. First `checking → online` is
 * silent (cold-start noise); only offline edges and recoveries are recorded on
 * the store itself.
 */

export type ServerConn = "checking" | "online" | "offline";

/** Why the store entered offline — for desktop.jsonl attribution. */
export type ServerHealthOfflineSource =
  | "heartbeat"
  | "api_outage"
  | "browser_offline"
  | "bootstrap";

/** Optional fields folded into the offline-edge log (heartbeat hysteresis). */
export type ServerHealthOfflineMeta = {
  consecutive_failures?: number;
};

interface ServerHealthState {
  status: ServerConn;
  /** Epoch ms of the last successful probe, or null if never reached. */
  lastOkAt: number | null;
  /** User-facing reason while offline (from `diagnoseOutage`), else null. */
  reason: string | null;
  /** True briefly right after recovering, so the chip can flash "已恢复连接". */
  justRecovered: boolean;
  /** Epoch ms when we last entered offline (for recovery duration); else null. */
  offlineSince: number | null;
  markOnline: () => void;
  markOffline: (
    reason: string | null,
    source: ServerHealthOfflineSource,
    meta?: ServerHealthOfflineMeta,
  ) => void;
  clearRecovered: () => void;
}

/**
 * Fired on the offline → online edge so turn rejoin can clear the reconnect
 * banner and skip remaining backoff. Registered from ``turns/recovery`` —
 * this store must not import that service (cycle).
 */
let recoveredHandler: (() => void) | null = null;

export function setServerHealthRecoveredHandler(fn: (() => void) | null): void {
  recoveredHandler = fn;
}

export const useServerHealthStore = create<ServerHealthState>((set, get) => ({
  status: "checking",
  lastOkAt: null,
  reason: null,
  justRecovered: false,
  offlineSince: null,
  markOnline: () => {
    const prev = get();
    const recovered = prev.status === "offline";
    const sinceOfflineMs =
      recovered && prev.offlineSince != null
        ? Date.now() - prev.offlineSince
        : null;
    set({
      status: "online",
      lastOkAt: Date.now(),
      reason: null,
      offlineSince: null,
      // Only celebrate a recovery if we were actually offline before.
      justRecovered: recovered,
    });
    if (recovered) {
      logEvent("info", "server_health.online", {
        since_offline_ms: sinceOfflineMs,
        last_ok_at: prev.lastOkAt,
      });
      recoveredHandler?.();
    }
  },
  markOffline: (reason, source, meta) => {
    const prev = get();
    if (prev.status === "offline") {
      // Already offline — keep first edge; refresh reason if the probe refined it.
      if (reason !== prev.reason) set({ reason });
      return;
    }
    const offlineSince = Date.now();
    set({
      status: "offline",
      reason,
      offlineSince,
      justRecovered: false,
    });
    logEvent("warn", "server_health.offline", {
      source,
      reason,
      last_ok_at: prev.lastOkAt,
      from: prev.status,
      ...(meta?.consecutive_failures != null
        ? { consecutive_failures: meta.consecutive_failures }
        : {}),
    });
  },
  clearRecovered: () => set({ justRecovered: false }),
}));
