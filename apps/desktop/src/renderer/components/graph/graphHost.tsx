/**
 * 协作图嵌入宿主契约——聊天内嵌 / 全屏放大 两入口共用。
 *
 * 单一约定（实现落在 GraphView + useGraphViewport + computeLayout）：
 * 1. Provider：每个 ReactFlow 实例由 GraphView 自带 ReactFlowProvider（内联不再缺宿主）。
 * 2. 尺寸：容器 ResizeObserver 测宽（width）或由外层定高；bbox 来自
 *    computeLayout 的结构包围盒（固定 NODE_* footprint；原点钉在 padding）。
 * 3. Fit：必须按结构 bbox，不得因测高 / soft-center / originY / 宿主回读高度伺服相机。
 *    - view：ReactFlow fitView（全屏基准，勿回归）
 *    - width：fit-to-width；zoom/目标高只由结构 bbox × 列宽（fitWidthBox）决定，
 *      onMeasure 写出宿主高度，禁止再把 colHeight 读回缩 zoom
 * 4. Overflow：内联卡片可 overflow-hidden 做圆角裁切；图区须先装下内容（缩 zoom），
 *    禁止靠外层裁切掩盖节点。fade 仅作极端兜底，非常态裁切路径。
 * 5. 宿主常驻：layoutReady=false 时不卸载 ReactFlow（可叠 skeleton）；内联折叠优先隐藏。
 */
import { ReactFlowProvider } from "@xyflow/react";
import type { ReactNode } from "react";

export type GraphFitMode = "width" | "view";

/** Wraps one ReactFlow instance. Safe to nest when an ancestor also provides. */
export function GraphFlowHost({ children }: { children: ReactNode }) {
  return <ReactFlowProvider>{children}</ReactFlowProvider>;
}
