/**
 * 协作图嵌入宿主——聊天内嵌 / 全屏放大 两入口共用。
 *
 * RF StoreUpdater 契约（Provider / 禁止 fitView 属性 / 稳定 proOptions）在
 * `components/xyflow/host.tsx`。本文只留协作图嵌入差异：
 *
 * 1. 尺寸：容器 ResizeObserver 测宽（width）或由外层定高；bbox 来自
 *    computeLayout 的结构包围盒（固定 NODE_* footprint；原点钉在 padding）。
 * 2. Fit：必须按结构 bbox，不得因测高 / soft-center / originY / 宿主回读高度伺服相机。
 *    - view：bbox 就绪 / 容器改宽时调 instance.fitView（一次）。
 *    - width：fit-to-width；zoom/目标高只由结构 bbox × 列宽（fitWidthBox）决定，
 *      onMeasure 写出宿主高度，禁止再把 colHeight 读回缩 zoom
 * 3. Overflow：内联卡片可 overflow-hidden 做圆角裁切；图区须先装下内容（缩 zoom），
 *    禁止靠外层裁切掩盖节点。fade 仅作极端兜底，非常态裁切路径。
 * 4. 活图在视口内常驻：layoutReady=false 时不卸载 ReactFlow（可叠 skeleton）。
 *    内联折叠或滚出视口后卸树（留状态条 + 占位高），滚回/展开再挂，布局走 ELK LRU。
 */
import { XyflowHost } from "@/components/xyflow/host";
import type { ReactNode } from "react";

/** Inline GraphView mounts only while the card is expanded and on-screen. */
export function shouldMountInlineGraphHost(opts: {
  expanded: boolean;
  inView: boolean;
}): boolean {
  return opts.expanded && opts.inView;
}

export type GraphFitMode = "width" | "view";

/** One ReactFlow store per collaboration graph. Safe to nest. */
export function GraphFlowHost({ children }: { children: ReactNode }) {
  return <XyflowHost>{children}</XyflowHost>;
}
