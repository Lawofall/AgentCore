import type { ToolApproval, ToolFace } from "@/services/capabilities";
import {
  FolderOpen,
  Folders,
  Globe,
  type LucideIcon,
  Monitor,
  Network,
  Presentation,
  Search,
  Terminal,
} from "lucide-react";

/** Per-face display label + icon for the tool catalog. */
export const FACE_META: Record<ToolFace, { label: string; icon: LucideIcon }> =
  {
    file: { label: "文件", icon: FolderOpen },
    folder: { label: "文件夹", icon: Folders },
    search: { label: "检索", icon: Search },
    web: { label: "网络", icon: Globe },
    execution: { label: "执行", icon: Terminal },
    host_browser: { label: "本机 · 浏览器", icon: Monitor },
    board: { label: "白板", icon: Presentation },
    orchestration: { label: "编排", icon: Network },
  };

/** Reading order for the grouped tool list (matches backend `TOOL_FACE_ORDER`). */
export const FACE_ORDER: ToolFace[] = [
  "file",
  "folder",
  "search",
  "web",
  "execution",
  "host_browser",
  "board",
  "orchestration",
];

export const APPROVAL_LABEL: Record<ToolApproval, string> = {
  never: "自动执行",
  grantable: "需审批",
};

export const RESIDENT_LABEL = {
  resident: "开场即用",
  deferred: "查阅后启用",
} as const;

/** Which side of the team holds a tool — the CEO coordinator, the 队员 (workers),
 * or both. Neutral styling: this is metadata, not a status. */
export function availabilityLabel(availableTo: string[]): string {
  const ceo = availableTo.includes("ceo");
  const worker = availableTo.includes("worker");
  if (ceo && worker) return "全员";
  if (ceo) return "CEO";
  return "队员";
}
