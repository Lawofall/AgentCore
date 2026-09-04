import { TabChip } from "@/components/ui";
import {
  ContextMenu,
  ContextMenuContent,
  ContextMenuItem,
  ContextMenuSeparator,
  ContextMenuTrigger,
} from "@/components/ui/context-menu";
import { FileText, X } from "lucide-react";
import { type Tab, tabKey } from "./storage";

/**
 * Horizontal tab strip for the detail pane. Pointer-down (not click) activates so
 * a tab switches even when the close button steals the click; middle-click closes
 * (browser-tab convention). Right-click opens 关闭 / 关闭其他 / 关闭全部. Overflows
 * scroll horizontally rather than wrapping.
 */
export function DetailTabs({
  tabs,
  activeKey,
  onActivate,
  onClose,
  onCloseOthers,
  onCloseAll,
}: {
  tabs: Tab[];
  activeKey: string | null;
  onActivate: (key: string) => void;
  onClose: (key: string) => void;
  onCloseOthers: (key: string) => void;
  onCloseAll: () => void;
}) {
  return (
    <div className="scrollbar-hidden flex shrink-0 items-stretch overflow-x-auto border-b">
      {tabs.map((t) => {
        const key = tabKey(t.wsId, t.path);
        const active = key === activeKey;
        return (
          <ContextMenu key={key}>
            <ContextMenuTrigger asChild>
              <TabChip
                variant="strip"
                active={active}
                icon={<FileText size={13} className="shrink-0 opacity-60" />}
                label={t.name}
                title={t.path}
                onSelect={() => onActivate(key)}
                onClose={() => onClose(key)}
                className="max-w-[180px]"
              />
            </ContextMenuTrigger>
            <ContextMenuContent className="min-w-40">
              <ContextMenuItem onSelect={() => onClose(key)}>
                <X size={14} className="shrink-0" />
                <span className="flex-1 truncate">关闭</span>
              </ContextMenuItem>
              <ContextMenuItem
                disabled={tabs.length <= 1}
                onSelect={() => onCloseOthers(key)}
              >
                <span className="flex-1 truncate">关闭其他</span>
              </ContextMenuItem>
              <ContextMenuSeparator />
              <ContextMenuItem onSelect={onCloseAll}>
                <span className="flex-1 truncate">关闭全部</span>
              </ContextMenuItem>
            </ContextMenuContent>
          </ContextMenu>
        );
      })}
    </div>
  );
}
