/**
 * Definition-state workflow canvas (队员步骤 / 等人关卡 + 连线).
 * Uses @xyflow/react already in the desktop app — does NOT touch projectExecution.
 */

import { Button } from "@/components/ui";
import { cn } from "@/lib/utils";
import {
  type WorkflowDefNode,
  type WorkflowDefinition,
  createAgentStepNode,
  createHumanGateNode,
  isWorkflowConnectionAllowed,
} from "@/services/workflowDefinition";
import {
  Background,
  type Connection,
  type Edge,
  MarkerType,
  type Node,
  type OnSelectionChangeParams,
  ReactFlow,
  ReactFlowProvider,
  addEdge,
  useEdgesState,
  useNodesState,
} from "@xyflow/react";
import { Hand, UserRound } from "lucide-react";
import { useCallback, useEffect, useMemo } from "react";
import {
  defToFlow,
  flowToDef,
  nodeSubtitle,
  nodeTitle,
} from "./workflowCanvasModel";
import {
  type WorkflowCanvasNodeData,
  workflowNodeTypes,
} from "./workflowNodes";

const FIT_VIEW_OPTIONS = { padding: 0.2 } as const;
const RF_PRO_OPTIONS = { hideAttribution: true } as const;

function WorkflowCanvasInner({
  definition,
  selectedId,
  onChange,
  onSelect,
  className,
}: {
  definition: WorkflowDefinition;
  selectedId: string | null;
  onChange: (next: WorkflowDefinition) => void;
  onSelect: (id: string | null) => void;
  className?: string;
}) {
  const defMap = useMemo(() => {
    const m = new Map<string, WorkflowDefNode>();
    for (const n of definition.nodes) m.set(n.id, n);
    return m;
  }, [definition.nodes]);

  const initial = useMemo(() => defToFlow(definition), [definition]);
  const [nodes, setNodes, onNodesChange] = useNodesState(initial.nodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(initial.edges);

  // Re-hydrate when parent definition identity changes (load / reset / inspector),
  // preserving drag positions for nodes that still exist.
  useEffect(() => {
    const next = defToFlow(definition);
    setNodes((prev) => {
      const pos = new Map(prev.map((n) => [n.id, n.position]));
      return next.nodes.map((n) => ({
        ...n,
        position: pos.get(n.id) ?? n.position,
      }));
    });
    setEdges(next.edges);
  }, [definition, setEdges, setNodes]);

  useEffect(() => {
    setNodes((prev) =>
      prev.map((n) => ({ ...n, selected: n.id === selectedId })),
    );
  }, [selectedId, setNodes]);

  const emit = useCallback(
    (
      nextNodes: Node<WorkflowCanvasNodeData>[],
      nextEdges: Edge[],
      map: Map<string, WorkflowDefNode>,
    ) => {
      onChange(flowToDef(definition, nextNodes, nextEdges, map));
    },
    [definition, onChange],
  );

  const isValidConnection = useCallback(
    (connection: Connection | Edge) => {
      const from = connection.source;
      const to = connection.target;
      if (!from || !to) return false;
      return isWorkflowConnectionAllowed(definition, from, to);
    },
    [definition],
  );

  const onConnect = useCallback(
    (connection: Connection) => {
      if (
        !connection.source ||
        !connection.target ||
        !isWorkflowConnectionAllowed(
          definition,
          connection.source,
          connection.target,
        )
      ) {
        return;
      }
      setEdges((eds) => {
        const next = addEdge(
          {
            ...connection,
            markerEnd: {
              type: MarkerType.ArrowClosed,
              width: 16,
              height: 16,
            },
          },
          eds,
        );
        setNodes((nds) => {
          emit(nds, next, defMap);
          return nds;
        });
        return next;
      });
    },
    [defMap, definition, emit, setEdges, setNodes],
  );

  const addNode = (kind: "agent_step" | "human_gate") => {
    const defNode =
      kind === "human_gate" ? createHumanGateNode() : createAgentStepNode();
    const pos = {
      x: 40 + nodes.length * 24,
      y: 40 + nodes.length * 24,
    };
    const flowNode: Node<WorkflowCanvasNodeData> = {
      id: defNode.id,
      type: "workflowNode",
      position: pos,
      data: {
        kind,
        title: nodeTitle(defNode),
        subtitle: nodeSubtitle(defNode),
      },
    };
    const nextDefs = new Map(defMap);
    nextDefs.set(defNode.id, defNode);
    const nextNodes = [...nodes, flowNode];
    setNodes(nextNodes);
    emit(nextNodes, edges, nextDefs);
    onSelect(defNode.id);
  };

  const onNodesDelete = useCallback(
    (deleted: Node[]) => {
      const ids = new Set(deleted.map((n) => n.id));
      const nextNodes = nodes.filter((n) => !ids.has(n.id));
      const nextEdges = edges.filter(
        (e) => !ids.has(e.source) && !ids.has(e.target),
      );
      setNodes(nextNodes);
      setEdges(nextEdges);
      const nextDefs = new Map(defMap);
      for (const id of ids) nextDefs.delete(id);
      emit(nextNodes, nextEdges, nextDefs);
      if (selectedId && ids.has(selectedId)) onSelect(null);
    },
    [defMap, edges, emit, nodes, onSelect, selectedId, setEdges, setNodes],
  );

  const onEdgesDelete = useCallback(
    (deleted: Edge[]) => {
      const ids = new Set(deleted.map((e) => e.id));
      const nextEdges = edges.filter((e) => !ids.has(e.id));
      setEdges(nextEdges);
      emit(nodes, nextEdges, defMap);
    },
    [defMap, edges, emit, nodes, setEdges],
  );

  const onSelectionChange = useCallback(
    ({ nodes: sel }: OnSelectionChangeParams) => {
      const id = sel[0]?.id ?? null;
      if (id !== selectedId) onSelect(id);
    },
    [onSelect, selectedId],
  );

  return (
    <div className={cn("flex h-full min-h-[420px] flex-col", className)}>
      <div className="flex flex-wrap items-center gap-2 border-b border-border px-3 py-2">
        <Button
          variant="neutral"
          size="sm"
          icon={<UserRound size={14} />}
          onClick={() => addNode("agent_step")}
        >
          队员步骤
        </Button>
        <Button
          variant="neutral"
          size="sm"
          icon={<Hand size={14} />}
          onClick={() => addNode("human_gate")}
        >
          等人关卡
        </Button>
        <p className="ml-auto text-xs text-muted-foreground">
          拖拽连线建立依赖；选中后右侧可编辑
        </p>
      </div>
      <div className="min-h-0 flex-1">
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={workflowNodeTypes}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onConnect={onConnect}
          isValidConnection={isValidConnection}
          onNodesDelete={onNodesDelete}
          onEdgesDelete={onEdgesDelete}
          onSelectionChange={onSelectionChange}
          fitView
          fitViewOptions={FIT_VIEW_OPTIONS}
          deleteKeyCode={["Backspace", "Delete"]}
          proOptions={RF_PRO_OPTIONS}
        >
          <Background gap={16} size={1} />
        </ReactFlow>
      </div>
    </div>
  );
}

export function WorkflowCanvas(props: {
  definition: WorkflowDefinition;
  selectedId: string | null;
  onChange: (next: WorkflowDefinition) => void;
  onSelect: (id: string | null) => void;
  className?: string;
}) {
  return (
    <ReactFlowProvider>
      <WorkflowCanvasInner {...props} />
    </ReactFlowProvider>
  );
}
