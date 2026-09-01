import {
  ContextMenuContent,
  ContextMenuItem,
  ContextMenuSeparator,
} from "@/components/ui/context-menu";
import { Crosshair, Maximize2, ScanSearch } from "lucide-react";
import { INPUT_ID, isEndpointId } from "./constants";

export function GraphContextMenu({
  menuNodeId,
  captainRunId,
  taskMessageId,
  finalAnswerId,
  onNodeSelect,
  showRunDetailHere,
  onClose,
  activateNode,
  centerNode,
  fitView,
}: {
  menuNodeId: string | null;
  captainRunId: string | undefined;
  taskMessageId: string | null;
  finalAnswerId: string | null;
  onNodeSelect?: (runId: string) => void;
  showRunDetailHere: (runId: string) => void;
  onClose?: () => void;
  activateNode: (id: string) => void;
  centerNode: (id: string) => void;
  fitView: () => void;
}) {
  return (
    <ContextMenuContent>
      {menuNodeId !== null && (
        <>
          {!isEndpointId(menuNodeId) && menuNodeId !== captainRunId && (
            <ContextMenuItem
              onSelect={() => {
                if (onNodeSelect) onNodeSelect(menuNodeId);
                else {
                  showRunDetailHere(menuNodeId);
                  onClose?.();
                }
              }}
            >
              <ScanSearch size={14} className="shrink-0" />
              <span className="flex-1 truncate">查看详情</span>
            </ContextMenuItem>
          )}
          {menuNodeId === INPUT_ID && taskMessageId && (
            <ContextMenuItem onSelect={() => activateNode(INPUT_ID)}>
              <ScanSearch size={14} className="shrink-0" />
              <span className="flex-1 truncate">查看完整提问</span>
            </ContextMenuItem>
          )}
          {menuNodeId === captainRunId && finalAnswerId && (
            <ContextMenuItem
              onSelect={() => captainRunId && activateNode(captainRunId)}
            >
              <ScanSearch size={14} className="shrink-0" />
              <span className="flex-1 truncate">查看最终回答</span>
            </ContextMenuItem>
          )}
          <ContextMenuItem onSelect={() => centerNode(menuNodeId)}>
            <Crosshair size={14} className="shrink-0" />
            <span className="flex-1 truncate">居中此节点</span>
          </ContextMenuItem>
          <ContextMenuSeparator />
        </>
      )}
      <ContextMenuItem onSelect={() => fitView()}>
        <Maximize2 size={14} className="shrink-0" />
        <span className="flex-1 truncate">适应画布</span>
      </ContextMenuItem>
    </ContextMenuContent>
  );
}
