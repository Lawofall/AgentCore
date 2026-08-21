import { resolveSidecarAccountAuth } from "@/services/accountToken";
import { useAuthStore } from "@/stores/auth";

/**
 * After a file-page write that changes what the next turn injects: force-refresh
 * the prepare snapshot on **already running** sidecars (ignore the 300s TTL).
 * Fire-and-forget; no toast. No live sidecar / no ticket → no-op (cloud turns
 * read the DB; they do not use this cache).
 */
export function scheduleAccountRulesMemoryRefresh(): void {
  void (async () => {
    if (!window.sidecarApi?.refreshLiveAccountRulesMemory) return;
    const accountAuth = (await resolveSidecarAccountAuth()) ?? undefined;
    if (!accountAuth) return;
    await window.sidecarApi.refreshLiveAccountRulesMemory({
      accountAuth,
      userId: useAuthStore.getState().user?.id,
    });
  })().catch(() => {
    /* best-effort; no toast */
  });
}
