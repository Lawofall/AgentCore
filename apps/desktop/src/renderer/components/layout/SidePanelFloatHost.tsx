import { DesktopFloatWindowBridge } from "@/components/layout/DesktopFloatWindowBridge";
import {
  type FloatingPanelEntry,
  FloatingPanelHost,
} from "@/components/layout/FloatingPanelHost";
import {
  SidePanelSurfaceBody,
  sidePanelFloatTitle,
} from "@/components/layout/SidePanelSurfaceBody";
import { canUseOsFloatWindow } from "@/lib/floatWindowApi";
import { WORKSPACE_TAB_ID, useSidePanelStore } from "@/stores/sidePanel";
import { useCallback, useMemo } from "react";

/**
 * Shell-level float layer bound to {@link useSidePanelStore} (UX §十).
 * Mounted from {@link AppShell} (not conversation pages), outside `open` —
 * closing the dock must not unmount floats; desktop bridge shares shell lifetime.
 * Desktop Electron → 真 OS 窗（{@link DesktopFloatWindowBridge}）；Web → 应用内 B 浮窗.
 */
export function SidePanelFloatHost() {
  const osFloat = canUseOsFloatWindow();
  const floats = useSidePanelStore((s) => s.floats);
  const tabs = useSidePanelStore((s) => s.tabs);
  const focusSurface = useSidePanelStore((s) => s.focusSurface);
  const dockTab = useSidePanelStore((s) => s.dockTab);
  const destroyFloat = useSidePanelStore((s) => s.destroyFloat);
  const focusFloat = useSidePanelStore((s) => s.focusFloat);
  const setFloatLayout = useSidePanelStore((s) => s.setFloatLayout);

  const panels: FloatingPanelEntry[] = useMemo(
    () =>
      floats.map((f) => ({
        id: f.tabId,
        title: sidePanelFloatTitle(f.tabId, tabs),
        rect: {
          x: f.layout.x,
          y: f.layout.y,
          width: f.layout.width,
          height: f.layout.height,
        },
        zIndex: f.layout.zIndex,
        closable: f.tabId !== WORKSPACE_TAB_ID,
        focused:
          focusSurface.type === "float" && focusSurface.tabId === f.tabId,
      })),
    [floats, tabs, focusSurface],
  );

  const onDock = useCallback((id: string) => dockTab(id), [dockTab]);
  const onClose = useCallback(
    (id: string) => {
      // Closable kinds destroy; fixed homes only dock (destroyFloat rejects them).
      if (!destroyFloat(id)) dockTab(id);
    },
    [destroyFloat, dockTab],
  );
  const onFocus = useCallback((id: string) => focusFloat(id), [focusFloat]);
  const onRectChange = useCallback(
    (id: string, rect: FloatingPanelEntry["rect"]) => {
      setFloatLayout(id, rect);
    },
    [setFloatLayout],
  );

  // 桌面真窗：placement 仍记 floating，但主窗不再挂应用内壳（避免双开）。
  if (osFloat) {
    return <DesktopFloatWindowBridge />;
  }

  return (
    <FloatingPanelHost
      panels={panels}
      demo={false}
      onDock={onDock}
      onClose={onClose}
      onFocus={onFocus}
      onRectChange={onRectChange}
      renderBody={(panel) => (
        <SidePanelSurfaceBody tabId={panel.id} showApprovals />
      )}
    />
  );
}
