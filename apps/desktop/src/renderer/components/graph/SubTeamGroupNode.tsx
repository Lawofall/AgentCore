import { Handle, type NodeProps, Position } from "@xyflow/react";
import { graphNodeDimClass, useGraphNodeDimmed } from "./graphHover";

interface SubTeamGroupData {
  parentRole: string;
  memberCount: number;
  handleDirection: "horizontal" | "vertical";
  variant?: "debate";
  [key: string]: unknown;
}

export function SubTeamGroupNode({ data }: NodeProps) {
  const d = data as SubTeamGroupData;
  const horizontal = d.handleDirection === "horizontal";
  const dimmed = useGraphNodeDimmed();
  const label =
    d.variant === "debate"
      ? `辩论 · ${d.memberCount} 辩手 run`
      : `${d.parentRole} 子队 · ${d.memberCount} 人`;

  // 辩论整场外框不画（阶段只挂 DebateStageBands 轮次/结辩标签，无阶段填充）；仅保留布局与 Handle。
  const isDebate = d.variant === "debate";
  // 浅色画布上 --muted 已接近白，bg-muted/20 看不见；深 token 低透明。
  const boxClass = isDebate ? "" : "rounded-xl bg-muted-foreground/3";

  return (
    <div className={`h-full w-full ${boxClass} ${graphNodeDimClass(dimmed)}`}>
      {!isDebate && (
        <div className="px-2.5 py-1 text-xs font-medium text-muted-foreground">
          {label}
        </div>
      )}
      <Handle
        type="target"
        position={horizontal ? Position.Left : Position.Top}
        className="!bg-transparent"
      />
      <Handle
        type="source"
        position={horizontal ? Position.Right : Position.Bottom}
        className="!bg-transparent"
      />
    </div>
  );
}
