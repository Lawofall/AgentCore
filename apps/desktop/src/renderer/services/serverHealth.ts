import { logEvent } from "@/lib/log";
import {
  type ReadyzDiagnosis,
  diagnoseOutage,
  probeReadyz,
} from "@/services/auth";
import { useServerHealthStore } from "@/stores/serverHealth";
import type { LogLevel } from "@shared/log-contract";

/**
 * Ambient backend-connectivity heartbeat.
 *
 * The app used to learn the server was down only *reactively* — either at
 * startup (AuthGate bootstrap) or when a request happened to fail. So a user had
 * no way to know the backend was unreachable **before** hitting send. This
 * monitor proactively probes `/readyz` on a cadence (via {@link probeReadyz},
 * the same readiness diagnosis the AuthGate uses), tightening while offline /
 * degraded so a recovery (or a confirming outage) is picked up quickly, and also
 * probes on tab focus / browser online-offline events. It folds each verdict into
 * {@link useServerHealthStore}, which the composer's connection indicator renders.
 *
 * The probe uses a raw `fetch` (not the `api` layer), so these background
 * probes never trip the AuthGate's reactive full-screen outage takeover — the
 * ambient indicator and the hard-outage screen stay independent.
 *
 * Hysteresis (align with `deploy/scripts/healthcheck.sh` FAIL_THRESHOLD=3 and
 * common k8s probe defaults): a single flaky `/readyz` does **not** flip the UI
 * to offline. Soft failures log `server_health.probe_failed` (1st=`debug`,
 * later=`warn`, with `kind`/`duration_ms`); only after
 * {@link SERVER_HEALTH_FAILURE_THRESHOLD} consecutive failures (or an already-
 * offline refresh) do we mark offline. Recovery is eager (one success → online).
 * True browser `offline` and a mid-session API outage that `/readyz` confirms
 * still mark offline immediately — those are not single-probe noise.
 */

/** Steady-state cadence while connected and healthy. */
const ONLINE_INTERVAL_MS = 20_000;
/** Faster cadence while offline, so a recovery is reflected quickly. */
const OFFLINE_INTERVAL_MS = 5_000;
/**
 * Cadence after soft probe failures but before the UI flips offline — confirm
 * quickly without waiting a full online interval (3 × 20s would feel stuck).
 */
const DEGRADED_INTERVAL_MS = 5_000;

/**
 * Consecutive `/readyz` failures required before heartbeat marks offline.
 * Matches ops `healthcheck.sh` default; one blip must not flash the red bar.
 */
export const SERVER_HEALTH_FAILURE_THRESHOLD = 3;

let probeInFlight: Promise<boolean> | null = null;
let consecutiveFailures = 0;

/** Test-only: clear in-flight probe + failure counter between cases. */
export function resetServerHealthProbeStateForTests(): void {
  probeInFlight = null;
  consecutiveFailures = 0;
}

function currentProbeDelayMs(): number {
  const { status } = useServerHealthStore.getState();
  if (status === "offline") return OFFLINE_INTERVAL_MS;
  if (consecutiveFailures > 0) return DEGRADED_INTERVAL_MS;
  return ONLINE_INTERVAL_MS;
}

function softProbeFailedLevel(failures: number): LogLevel {
  return failures <= 1 ? "debug" : "warn";
}

function probeFailedFields(
  failures: number,
  diagnosis: Extract<ReadyzDiagnosis, { ok: false }>,
  store: { status: string; lastOkAt: number | null },
): Record<string, unknown> {
  return {
    consecutive_failures: failures,
    failure_threshold: SERVER_HEALTH_FAILURE_THRESHOLD,
    reason: diagnosis.reason,
    kind: diagnosis.kind,
    duration_ms: diagnosis.duration_ms,
    ...(diagnosis.http_status != null
      ? { http_status: diagnosis.http_status }
      : {}),
    status: store.status,
    last_ok_at: store.lastOkAt,
  };
}

/** Probe `/readyz` once (deduped) and fold the verdict into the health store. */
export async function probeServerHealth(): Promise<boolean> {
  if (probeInFlight) return probeInFlight;
  probeInFlight = (async () => {
    const diagnosis = await probeReadyz();
    const store = useServerHealthStore.getState();
    if (diagnosis.ok) {
      const wasDegraded = consecutiveFailures > 0;
      consecutiveFailures = 0;
      if (wasDegraded && store.status !== "offline") {
        // Soft failures never flipped the UI — note the clear so dogfood dumps
        // can explain a probe_failed streak that self-healed.
        logEvent("info", "server_health.probe_recovered", {
          status: store.status,
          last_ok_at: store.lastOkAt,
        });
      }
      store.markOnline();
      return true;
    }
    consecutiveFailures += 1;
    const failures = consecutiveFailures;
    if (
      failures >= SERVER_HEALTH_FAILURE_THRESHOLD ||
      store.status === "offline"
    ) {
      store.markOffline(diagnosis.reason, "heartbeat", {
        consecutive_failures: failures,
      });
      return false;
    }
    logEvent(
      softProbeFailedLevel(failures),
      "server_health.probe_failed",
      probeFailedFields(failures, diagnosis, store),
    );
    return false;
  })().finally(() => {
    probeInFlight = null;
  });
  return probeInFlight;
}

/**
 * Mid-session API transport/5xx signal: confirm via `/readyz` before flipping
 * the soft offline banner. A healthy readiness probe means the failing call was
 * transient (or a single broken route) — log and ignore so we don't flash
 * "与服务器断开连接" on every 5xx.
 */
export async function confirmMidSessionOutage(): Promise<boolean> {
  const before = useServerHealthStore.getState();
  const reason = await diagnoseOutage();
  if (reason === null) {
    logEvent("info", "server_health.api_outage_ignored", {
      status: before.status,
      last_ok_at: before.lastOkAt,
    });
    return false;
  }
  useServerHealthStore.getState().markOffline(reason, "api_outage");
  return true;
}

/**
 * Start the heartbeat. Returns a disposer that stops polling and unbinds the
 * focus / online / offline listeners. Safe to call once per authenticated
 * session (the AppShell owns its lifecycle).
 */
export function startServerHealthMonitor(): () => void {
  let timer: number | undefined;
  let stopped = false;

  const loop = async () => {
    if (stopped) return;
    await probeServerHealth();
    if (stopped) return;
    timer = window.setTimeout(() => void loop(), currentProbeDelayMs());
  };

  const probeNow = () => void probeServerHealth();
  // The browser reports a full network drop instantly — reflect it without
  // waiting for the failure threshold; the loop then confirms/recovers on its
  // cadence.
  const onOffline = () => {
    consecutiveFailures = SERVER_HEALTH_FAILURE_THRESHOLD;
    useServerHealthStore
      .getState()
      .markOffline("网络已断开，请检查网络连接", "browser_offline");
  };

  window.addEventListener("focus", probeNow);
  window.addEventListener("online", probeNow);
  window.addEventListener("offline", onOffline);

  void loop();

  return () => {
    stopped = true;
    consecutiveFailures = 0;
    if (timer) window.clearTimeout(timer);
    window.removeEventListener("focus", probeNow);
    window.removeEventListener("online", probeNow);
    window.removeEventListener("offline", onOffline);
  };
}
