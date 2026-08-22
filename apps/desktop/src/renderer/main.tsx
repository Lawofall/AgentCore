import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { isNativeRuntime } from "./lib/capabilities";
import { initScrollReveal } from "./lib/scrollReveal";
import { applyTheme } from "./lib/theme";
import { readNarrowViewport } from "./lib/useNarrowLayout";
import { installAccountStateIngress } from "./services/accountStateIngress";
import { installClientToolIngress } from "./services/clientToolIngress";
import { startOutboxReconcile } from "./services/outboxReconcile";
import { installSidecarEventPump } from "./services/sidecarEventPump";
import { installSidecarHarvestClaim } from "./services/sidecarHarvestClaim";
import { installSidecarStatusListener } from "./services/sidecarStatus";
import { liveStagingIds } from "./stores/composer";
import { useUIStore } from "./stores/ui";
import "./styles/globals.css";

initScrollReveal();
// Consume the sidecar lifecycle/diagnostic channel so a local-engine spawn/exit
// failure surfaces its real reason on a failed turn, not a generic "network"
// banner (no-op outside the desktop shell). See services/sidecarStatus.
installSidecarStatusListener();
// Single App-lifetime `sidecar:event` subscription; turns claim sinks (叠字根因：
// 多 onEvent listener). See services/sidecarEventPump.
installSidecarEventPump();
// 自发 harvest `turn/event`（新 turnId）认领进当前对话；本机流占用时不抢。
installSidecarHarvestClaim();
// Fulfill channels (云 device stream + 本机 sidecar push) → CLIENT_TOOL
// perform/settle; the cloud transport itself is started in AppShell.
installClientToolIngress();
// 同一条设备流还捎账号级状态（队列快照 / 挂起卡结算）——它们不属于任何一个对话。
installAccountStateIngress();
// Main-process outbox sync acks + exit flush (as-built: 前端技术 §7.2).
startOutboxReconcile();
// Reap attach-staging left by earlier sessions: drafts are capped, so an evicted
// draft's staged bytes are unreachable and would otherwise never be freed.
void window.fsApi?.sweepStagingOrphans?.(liveStagingIds());
// Apply the persisted theme before the first paint to avoid a light→dark flash
// (the store reads the saved choice from localStorage on creation).
applyTheme(
  isNativeRuntime() || readNarrowViewport()
    ? "light"
    : useUIStore.getState().theme,
);

const rootElement = document.getElementById("root");
if (!rootElement) throw new Error("Root element #root not found");

createRoot(rootElement).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
