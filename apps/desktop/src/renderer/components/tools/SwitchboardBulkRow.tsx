import {
  type SwitchboardBulkAction,
  switchboardBulkActions,
} from "@/components/tools/switchboardBulk";
import { Button } from "@/components/ui";
import type { ReactNode } from "react";

const LABEL: Record<SwitchboardBulkAction, string> = {
  open: "全部打开",
  close: "全部关闭",
};

/** Section title and bulk actions share one row. Bulk sits on the right. */
export function SwitchboardSectionHeader({
  heading,
  children,
}: {
  heading?: string;
  children?: ReactNode;
}) {
  if (!heading && !children) return null;
  return (
    <div className="mb-3 flex min-h-7 items-center justify-between gap-3">
      {heading ? (
        <h2 className="min-w-0 text-sm font-medium text-foreground">
          {heading}
        </h2>
      ) : (
        <span />
      )}
      {children}
    </div>
  );
}

/** Open-all / close-all for a tool or envelope list. Not shown in the composer menu. */
export function SwitchboardBulkRow({
  offCount,
  total,
  disabled,
  onOpen,
  onClose,
}: {
  offCount: number;
  total: number;
  disabled: boolean;
  onOpen: () => void;
  onClose: () => void;
}) {
  const actions = switchboardBulkActions(offCount, total);
  if (actions.length === 0) return null;
  return (
    <div className="flex shrink-0 items-center gap-2">
      {actions.map((action) => (
        <Button
          key={action}
          variant="ghost"
          size="sm"
          disabled={disabled}
          onClick={action === "open" ? onOpen : onClose}
        >
          {LABEL[action]}
        </Button>
      ))}
    </div>
  );
}
