import { formatCompact } from "@/lib/format";
import { NODE_HEIGHT, NODE_WIDTH } from "@agentcore/graph-layout";
import {
  BaseEdge,
  type Edge,
  EdgeLabelRenderer,
  type EdgeProps,
  getBezierPath,
  getSmoothStepPath,
} from "@xyflow/react";
import { useContext } from "react";
import { GraphHoverContext } from "./graphHover";
import {
  GraphCaptainRunIdContext,
  useGraphDocumentMode,
  useGraphInjectPaint,
  useStepEdgeAnimated,
} from "./graphLive";

/**
 * Real information handoff carried on a dependency edge (前端UX设计.md §五 信息流边):
 * HOW the upstream teammate's product actually reached this run, read from the target
 * run's `receivedContext` dependency block (resolved by `source_run_id` in GraphView).
 * Turns a bare scheduling arrow into "X 把它的产物（全文/摘要/指针）交给了 Y" — and flags
 * when that product was `truncated` on the way. Null on bookend / delegate / revision
 * edges (no teammate-to-teammate dependency block).
 */
export interface EdgeHandoff {
  fidelity: "" | "pointer" | "summarize" | "pass_through";
  truncated: boolean;
  sourceRole: string;
  chars: number;
}

type StepEdgeData = Edge<{
  animated?: boolean;
  kind?: "dep" | "delegate" | "continuation" | "inject" | "handoff";
  handoff?: EdgeHandoff | null;
  injectHighlight?: boolean;
  injectDimmed?: boolean;
  handleDirection?: "horizontal" | "vertical";
  pathType?: "smoothstep" | "bezier";
  sourcePortIndex?: number;
  sourcePortTotal?: number;
  targetPortIndex?: number;
  targetPortTotal?: number;
}>;

/** Fidelity → short edge-label text (only the lossy ones get a label; 全文 stays clean). */
const FIDELITY_SHORT: Record<string, string> = {
  pointer: "指针",
  summarize: "摘要",
};

/** Fidelity → full hover description (native title). */
const FIDELITY_FULL: Record<string, string> = {
  pointer: "递指针（仅给了引用 / 产物清单）",
  summarize: "摘要（压缩后的版本）",
  pass_through: "全文（完整产物）",
};

function handoffTitle(h: EdgeHandoff): string {
  const parts: string[] = [];
  if (h.sourceRole) parts.push(`来自 ${h.sourceRole}`);
  const full = h.fidelity ? FIDELITY_FULL[h.fidelity] : null;
  if (full) parts.push(full);
  if (h.chars > 0) parts.push(`${formatCompact(h.chars)} 字`);
  if (h.truncated) parts.push("已截断");
  return parts.join(" · ");
}

// Three particles, evenly phased, ride the edge toward a running node to convey
// "data flowing downstream" (replaces the old dashed stroke, whose `dash`
// keyframe was never defined). Pure SVG `animateMotion` — no extra dependency.
const PARTICLE_BEGINS = ["0s", "0.5s", "1s"];
const PARTICLE_DUR = "1.5s";

function portOffset(index: number, total: number, crossSize: number): number {
  if (total <= 1) return 0;
  const portRange = crossSize * 0.6;
  return portRange * (index / (total - 1)) - portRange / 2;
}

export function StepEdge(props: EdgeProps<StepEdgeData>) {
  const {
    sourceX,
    sourceY,
    targetX,
    targetY,
    sourcePosition,
    targetPosition,
    style,
    data,
  } = props;

  const horizontal = data?.handleDirection === "horizontal";
  const crossSize = horizontal ? NODE_HEIGHT : NODE_WIDTH;
  const srcOffset = portOffset(
    data?.sourcePortIndex ?? 0,
    data?.sourcePortTotal ?? 1,
    crossSize,
  );
  const tgtOffset = portOffset(
    data?.targetPortIndex ?? 0,
    data?.targetPortTotal ?? 1,
    crossSize,
  );
  const adjSourceX = horizontal ? sourceX : sourceX + srcOffset;
  const adjSourceY = horizontal ? sourceY + srcOffset : sourceY;
  const adjTargetX = horizontal ? targetX : targetX + tgtOffset;
  const adjTargetY = horizontal ? targetY + tgtOffset : targetY;

  // Tree layout uses organic bezier; leftright keeps orthogonal smoothstep.
  const useBezier = data?.pathType === "bezier";
  const [edgePath, labelX, labelY] = useBezier
    ? getBezierPath({
        sourceX: adjSourceX,
        sourceY: adjSourceY,
        targetX: adjTargetX,
        targetY: adjTargetY,
        sourcePosition,
        targetPosition,
      })
    : getSmoothStepPath({
        sourceX: adjSourceX,
        sourceY: adjSourceY,
        targetX: adjTargetX,
        targetY: adjTargetY,
        sourcePosition,
        targetPosition,
        borderRadius: 10,
      });

  const documentMode = useGraphDocumentMode();
  const captainRunId = useContext(GraphCaptainRunIdContext);
  const liveAnimated = useStepEdgeAnimated(props.target, captainRunId);
  const injectPaint = useGraphInjectPaint();
  const isAnimated = documentMode ? liveAnimated : (data?.animated ?? false);
  // 信息流边 (1A): surface a small label only on a LOSSY handoff — a summarized /
  // pointer-only product, or one truncated on the way. A 全文 (pass_through) handoff
  // stays a clean line (the common, ideal case), so labels mark exactly where a
  // teammate worked from less than the full product.
  const handoff = data?.handoff ?? null;
  const fidelityShort = handoff?.fidelity
    ? FIDELITY_SHORT[handoff.fidelity]
    : null;
  const showLabel = !!handoff && (!!fidelityShort || handoff.truncated);
  // A delegation edge (captain → nested sub-worker, 阶段2 父子分组) is dashed so
  // a sub-team reads as grouped under its parent, distinct from the solid DAG
  // dependency / bookend flow.
  const isDelegate = data?.kind === "delegate";
  const isContinuation = data?.kind === "continuation";
  const isHandoff = data?.kind === "handoff";
  const isInject = data?.kind === "inject";
  const injectHighlight = documentMode
    ? isInject || (injectPaint?.highlightEdgeIds.has(props.id) ?? false)
    : data?.injectHighlight === true;
  const injectDimmed = documentMode
    ? !!injectPaint?.dimUnrelatedEdges &&
      !(injectPaint?.focusedEdgeIds.has(props.id) ?? false)
    : data?.injectDimmed === true;

  const { hoveredNodeId, keepBrightIds } = useContext(GraphHoverContext);
  // Bright when both ends sit on the hover path (full upstream/downstream set).
  const isHoverRelated =
    keepBrightIds?.has(props.source) && keepBrightIds.has(props.target);
  const hoverActive = hoveredNodeId != null;

  let strokeOpacity: number;
  let strokeWidth: number;
  let strokeColor: string;
  if (injectDimmed && !isHoverRelated) {
    strokeOpacity = 0.08;
    strokeWidth = 1.5;
    strokeColor = "var(--muted-foreground)";
  } else if (isAnimated) {
    strokeOpacity = 1;
    strokeWidth = 2;
    strokeColor = "var(--primary)";
  } else if (injectHighlight) {
    strokeOpacity = 1;
    strokeWidth = 2.5;
    strokeColor = "var(--primary)";
  } else if (!hoverActive) {
    strokeOpacity =
      isDelegate || isContinuation || isHandoff || isInject ? 0.35 : 0.4;
    strokeWidth = 1.5;
    strokeColor = "var(--muted-foreground)";
  } else if (isHoverRelated) {
    strokeOpacity = 1;
    strokeWidth = 2;
    strokeColor = "var(--primary)";
  } else {
    strokeOpacity = 0.1;
    strokeWidth = 1.5;
    strokeColor = "var(--muted-foreground)";
  }

  let strokeDasharray: string | undefined;
  if (isContinuation) strokeDasharray = "2 4";
  else if (isHandoff) strokeDasharray = "3 3";
  else if (isDelegate) strokeDasharray = "5 4";
  else if (isInject) strokeDasharray = "6 4";

  return (
    <>
      <BaseEdge
        path={edgePath}
        style={{
          ...style,
          stroke: strokeColor,
          strokeWidth,
          opacity: strokeOpacity,
          strokeDasharray,
        }}
      />
      {isAnimated &&
        PARTICLE_BEGINS.map((begin) => (
          <circle key={begin} r={3} fill="var(--primary)">
            <animateMotion
              dur={PARTICLE_DUR}
              begin={begin}
              repeatCount="indefinite"
              path={edgePath}
            />
          </circle>
        ))}
      {isHandoff && (
        <EdgeLabelRenderer>
          <div
            className="nodrag nopan pointer-events-none absolute flex items-center gap-1 rounded-full border border-border bg-card px-1.5 py-0.5 text-xs text-muted-foreground shadow-sm"
            style={{
              transform: `translate(-50%, -50%) translate(${labelX}px,${labelY}px)`,
            }}
            title="已由新队员接手"
          >
            接替
          </div>
        </EdgeLabelRenderer>
      )}
      {showLabel && handoff && (
        <EdgeLabelRenderer>
          <div
            className="nodrag nopan pointer-events-none absolute flex items-center gap-1 rounded-full border border-border bg-card px-1.5 py-0.5 text-xs shadow-sm"
            style={{
              transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`,
            }}
            title={handoffTitle(handoff)}
          >
            {fidelityShort && (
              <span className="text-muted-foreground">{fidelityShort}</span>
            )}
            {handoff.truncated && (
              <span className="font-medium text-muted-foreground">截断</span>
            )}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  );
}
