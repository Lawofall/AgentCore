// Dev-only 协作图缺节点探针：区分「投影 !pos 丢节点」vs「RF 有节点但被裁切」。
//
// 开：DevTools 执行 `__graphTrace()`（刷新后仍开，经 uiStorage）
// 关：`__graphTrace(false)`
// dump：`__graphTrace.dump()` → 环形缓冲事件（供 Playwright / 假跑收集）
//
// 纯诊断、零生产副作用。

import { uiGet, uiSet } from "@/lib/uiStorage";

const GRAPH_TRACE_KEY = "graphTrace";

export type GraphTraceKind =
  | "structure"
  | "layout_ok"
  | "projection"
  | "viewport"
  | "dom_clip";

export interface GraphTraceEvent {
  t: number;
  kind: GraphTraceKind;
  detail: Record<string, unknown>;
}

const RING_MAX = 200;
const ring: GraphTraceEvent[] = [];
const start = performance.now();

let _on = false;

declare global {
  interface Window {
    /** Dev 协作图缺节点探针。`__graphTrace()` 开、`__graphTrace(false)` 关；`.dump()` 取缓冲。 */
    __graphTrace?: ((on?: boolean) => boolean) & {
      dump?: () => GraphTraceEvent[];
      clear?: () => void;
    };
  }
}

if (import.meta.env.DEV && typeof window !== "undefined") {
  _on = uiGet<boolean>(GRAPH_TRACE_KEY) === true;
  const api = ((on = true): boolean => {
    _on = on;
    uiSet(GRAPH_TRACE_KEY, on ? true : undefined);
    console.info(
      `[graph-trace] ${on ? "ON — 看 projection/layout/dom_clip 缺节点" : "off"}`,
    );
    return _on;
  }) as NonNullable<Window["__graphTrace"]>;
  api.dump = (): GraphTraceEvent[] => ring.slice();
  api.clear = (): void => {
    ring.length = 0;
  };
  window.__graphTrace = api;
}

function enabled(): boolean {
  return import.meta.env.DEV && _on;
}

function push(kind: GraphTraceKind, detail: Record<string, unknown>): void {
  if (!enabled()) return;
  const ev: GraphTraceEvent = {
    t: Math.round(performance.now() - start),
    kind,
    detail,
  };
  ring.push(ev);
  if (ring.length > RING_MAX) ring.shift();
  const anomaly =
    detail.missingPosIds != null ||
    detail.clippedIds != null ||
    detail.gap != null ||
    detail.anomaly === true;
  const line = `[graph-trace] +${ev.t}ms ${kind} ${JSON.stringify(detail)}`;
  if (anomaly) console.warn(line);
  else console.info(line);
}

/** 结构指纹变更 / 清空。 */
export function traceGraphStructure(detail: Record<string, unknown>): void {
  push("structure", detail);
}

/** ELK onOk（结构或测高）写入的 positions 键集。 */
export function traceGraphLayoutOk(detail: Record<string, unknown>): void {
  const posIds = detail.posIds;
  const sceneIds = detail.sceneIds;
  if (
    Array.isArray(posIds) &&
    Array.isArray(sceneIds) &&
    sceneIds.some((id) => typeof id === "string" && !posIds.includes(id))
  ) {
    push("layout_ok", {
      ...detail,
      anomaly: true,
      missingPosIds: sceneIds.filter(
        (id) => typeof id === "string" && !posIds.includes(id),
      ),
    });
    return;
  }
  push("layout_ok", detail);
}

/**
 * 投影结果：execution worker runs vs 实际 agent 节点。
 * `missingPosIds` = 因无坐标被跳过；`foldedIds` = 折叠/辩论折列故意不渲。
 */
export function traceGraphProjection(detail: {
  runIds: string[];
  agentNodeIds: string[];
  missingPosIds: string[];
  foldedIds: string[];
  posKeyCount: number;
  layoutReady: boolean;
}): void {
  const expected = detail.runIds.filter((id) => !detail.foldedIds.includes(id));
  const missing = expected.filter((id) => !detail.agentNodeIds.includes(id));
  const extra = detail.agentNodeIds.filter((id) => !expected.includes(id));
  const gap = missing.length > 0 || detail.missingPosIds.length > 0;
  push("projection", {
    ...detail,
    expectedCount: expected.length,
    agentCount: detail.agentNodeIds.length,
    missing,
    extra,
    gap: gap || undefined,
    anomaly: gap || undefined,
  });
}

/** fit-width overflow 标志变化。 */
export function traceGraphViewport(detail: Record<string, unknown>): void {
  push("viewport", detail);
}

/** DOM：RF 节点相对容器裁切。 */
export function traceGraphDomClip(detail: {
  rfNodeIds: string[];
  clippedIds: string[];
  fullyInsideCount: number;
  containerH: number;
}): void {
  push("dom_clip", {
    ...detail,
    anomaly: detail.clippedIds.length > 0 || undefined,
  });
}

export function isGraphTraceEnabled(): boolean {
  return enabled();
}
