import { Suspense, lazy, useEffect } from "react";

const Switch = import.meta.env.DEV
  ? lazy(() =>
      import("@/dev-schemes/DevSchemeSwitch").then((m) => ({
        default: m.DevSchemeSwitch,
      })),
    )
  : null;

/** 角落面板宿主：默认不画。命令面板「开发」或 Ctrl+. 打开。 */
const SHOW_CORNER_SWITCH = true;

/** 生产构建整段裁掉；开发态只挂宿主，关掉后面板和图标都不留。 */
export function DevSchemeGate() {
  useEffect(() => {
    if (!import.meta.env.DEV) return;
    void import("@/dev-schemes/register");
  }, []);
  if (!Switch || !SHOW_CORNER_SWITCH) return null;
  return (
    <Suspense fallback={null}>
      <Switch />
    </Suspense>
  );
}
