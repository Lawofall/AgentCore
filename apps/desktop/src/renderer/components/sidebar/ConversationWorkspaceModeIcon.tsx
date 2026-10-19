import { SimpleTooltip } from "@/components/ui/tooltip";
import { LOCAL_TRADITIONAL_LABEL } from "@/lib/conversationWorkspaceMode";
import { Cloud, HardDrive } from "lucide-react";

/** Sidebar conversation row — cloud-only marker (desktop noise reduction). */
export function ConversationCloudIcon() {
  return (
    <SimpleTooltip label="云端">
      <Cloud
        size={14}
        aria-label="云端"
        className="shrink-0 text-sidebar-muted"
      />
    </SimpleTooltip>
  );
}

/** Workspace group header — shows cloud or local as the group anchor. */
export function GroupWorkspaceModeIcon({ isLocal }: { isLocal: boolean }) {
  const label = isLocal ? LOCAL_TRADITIONAL_LABEL : "云端";
  const Icon = isLocal ? HardDrive : Cloud;
  return (
    <SimpleTooltip label={label}>
      <Icon
        size={14}
        aria-label={label}
        className="shrink-0 text-sidebar-muted"
      />
    </SimpleTooltip>
  );
}
