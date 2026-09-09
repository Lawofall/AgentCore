/**
 * Definition-state workflow canvas (队员步骤 / 等人关卡 + 连线).
 * Uses @xyflow/react already in the desktop app — does NOT touch projectExecution.
 */

import { Button } from "@/components/ui";
import {
  XYFLOW_FIT_PADDING,
  XYFLOW_PRO_OPTIONS,
  XyflowHost,
} from "@/components/xyflow/host";
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
  type OnInit,
  type OnSelectionChangeParams,
  ReactFlow,
  addEdge,
  useEdgesState,
  useNodesState,
} from "@xyflow/react";
import { Hand, UserRound } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef } from "react";
import {
  defToFlow,
  flowToDef,
  mergeFlowEdges,
  mergeFlowNodes,
  nodeSubtitle,
  nodeTitle,
} from "./workflowCanvasModel";
import {
  type WorkflowCanvasNodeData,
  workflowNodeTypes,
} from "./workflowNodes";

const WORKFLOW_DELETE_KEYS = ["Backspace", "Delete"];

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
  const definitionRef = useRef(definition);
  definitionRef.current = definition;
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;
  const selectedIdRef = useRef(selectedId);
  selectedIdRef.current = selectedId;
  const onSelectRef = useRef(onSelect);
  onSelectRef.current = onSelect;

  const initial = useMemo(() => defToFlow(definition), [definition]);
  const [nodes, setNodes, onNodesChange] = useNodesState(initial.nodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(initial.edges);
  const nodesRef = useRef(nodes);
  nodesRef.current = nodes;
  const edgesRef = useRef(edges);
  edgesRef.current = edges;

  // Re-hydrate when parent definition identity changes (load / reset / inspector),
  // preserving drag positions and RF measured size so StoreUpdater can skip.
  useEffect(() => {
    const next = defToFlow(definition);
    setNodes((prev) => mergeFlowNodes(prev, next.nodes));
    setEdges((prev) => mergeFlowEdges(prev, next.edges));
  }, [definition, setEdges, setNodes]);

  useEffect(() => {
    setNodes((prev) => {
      let changed = false;
      const out = prev.map((n) => {
        const selected = n.id === selectedId;
        if (Boolean(n.selected) === selected) return n;
        changed = true;
        return { ...n, selected };
      });
      return changed ? out : prev;
    });
  }, [selectedId, setNodes]);

  const emit = useCallback(
    (
      nextNodes: Node<WorkflowCanvasNodeData>[],
      nextEdges: Edge[],
      map: Map<string, WorkflowDefNode>,
    ) => {
      onChangeRef.current(
        flowToDef(definitionRef.current, nextNodes, nextEdges, map),
      );
    },
    [],
  );

  const isValidConnection = useCallback((connection: Connection | Edge) => {
    const from = connection.source;
    const to = connection.target;
    if (!from || !to) return false;
    return isWorkflowConnectionAllowed(definitionRef.current, from, to);
  }, []);

  const onConnect = useCallback(
    (connection: Connection) => {
      const def = definitionRef.current;
      if (
        !connection.source ||
        !connection.target ||
        !isWorkflowConnectionAllowed(def, connection.source, connection.target)
      ) {
        return;
      }
      const defMap = new Map(def.nodes.map((n) => [n.id, n]));
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
    [emit, setEdges, setNodes],
  );

  const addNode = (kind: "agent_step" | "human_gate") => {
    const def = definitionRef.current;
    const defMap = new Map(def.nodes.map((n) => [n.id, n]));
    const defNode =
      kind === "human_gate" ? createHumanGateNode() : createAgentStepNode();
    const current = nodesRef.current;
    const pos = {
      x: 40 + current.length * 24,
      y: 40 + current.length * 24,
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
    const nextNodes = [...current, flowNode];
    setNodes(nextNodes);
    emit(nextNodes, edgesRef.current, nextDefs);
    onSelect(defNode.id);
  };

  const onNodesDelete = useCallback(
    (deleted: Node[]) => {
      const ids = new Set(deleted.map((n) => n.id));
      const nextNodes = nodesRef.current.filter((n) => !ids.has(n.id));
      const nextEdges = edgesRef.current.filter(
        (e) => !ids.has(e.source) && !ids.has(e.target),
      );
      setNodes(nextNodes);
      setEdges(nextEdges);
      const def = definitionRef.current;
      const nextDefs = new Map(def.nodes.map((n) => [n.id, n]));
      for (const id of ids) nextDefs.delete(id);
      emit(nextNodes, nextEdges, nextDefs);
      if (selectedIdRef.current && ids.has(selectedIdRef.current)) {
        onSelectRef.current(null);
      }
    },
    [emit, setEdges, setNodes],
  );

  const onEdgesDelete = useCallback(
    (deleted: Edge[]) => {
      const ids = new Set(deleted.map((e) => e.id));
      const nextEdges = edgesRef.current.filter((e) => !ids.has(e.id));
      setEdges(nextEdges);
      const def = definitionRef.current;
      emit(
        nodesRef.current,
        nextEdges,
        new Map(def.nodes.map((n) => [n.id, n])),
      );
    },
    [emit, setEdges],
  );

  const onSelectionChange = useCallback(
    ({ nodes: sel }: OnSelectionChangeParams) => {
      const id = sel[0]?.id ?? null;
      if (id !== selectedIdRef.current) onSelectRef.current(id);
    },
    [],
  );

  const onInit = useCallback<OnInit<Node<WorkflowCanvasNodeData>>>((inst) => {
    inst.fitView({ padding: XYFLOW_FIT_PADDING, duration: 0 });
  }, []);

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
          onInit={onInit}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onConnect={onConnect}
          isValidConnection={isValidConnection}
          onNodesDelete={onNodesDelete}
          onEdgesDelete={onEdgesDelete}
          onSelectionChange={onSelectionChange}
          deleteKeyCode={WORKFLOW_DELETE_KEYS}
          proOptions={XYFLOW_PRO_OPTIONS}
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
    <XyflowHost>
      <WorkflowCanvasInner {...props} />
    </XyflowHost>
  );
}
