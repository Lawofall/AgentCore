/** Synthetic graph bookend id and ReactFlow node/edge type maps. */

import type { GraphLayout } from "@/stores/graph";
import { ListTree, MoveHorizontal } from "lucide-react";
import type { ReactNode } from "react";
import { ActSummaryNode } from "./ActSummaryNode";
import { AgentNode } from "./AgentNode";
import { EndpointNode } from "./EndpointNode";
import { StepEdge } from "./StepEdge";
import { SubTeamGroupNode } from "./SubTeamGroupNode";

export { INPUT_ID, isEndpointId } from "./ids";

export const nodeTypes = {
  agent: AgentNode,
  userInput: EndpointNode,
  captain: EndpointNode,
  subTeamGroup: SubTeamGroupNode,
  actSummary: ActSummaryNode,
};

export const edgeTypes = { step: StepEdge };

/** Stable identity — never pass `{ hideAttribution: true }` inline to ReactFlow. */
export const RF_PRO_OPTIONS = { hideAttribution: true } as const;

export const LAYOUT_OPTIONS: {
  kind: GraphLayout;
  label: string;
  icon: ReactNode;
}[] = [
  { kind: "leftright", label: "左右流", icon: <MoveHorizontal size={14} /> },
  { kind: "tree", label: "树形布局", icon: <ListTree size={14} /> },
];

export function prefersReducedMotion(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}
