import { Badge, Button } from "@/components/ui";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  SKILL_STORE_GROUPS,
  type SkillStoreGroup,
} from "@/pages/toolbox/market/skillStoreGroups";
import { isSkillStoreGroup } from "@/services/skillStore";
import { useEffect, useState } from "react";

export function PublishSkillDialog({
  open,
  busy,
  initialGroup,
  onOpenChange,
  onConfirm,
}: {
  open: boolean;
  busy: boolean;
  initialGroup: string | null;
  onOpenChange: (open: boolean) => void;
  onConfirm: (group: SkillStoreGroup) => void;
}) {
  const preset = isSkillStoreGroup(initialGroup) ? initialGroup : null;
  const [group, setGroup] = useState<SkillStoreGroup | null>(preset);

  useEffect(() => {
    if (open) setGroup(preset);
  }, [open, preset]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent size="md">
        <DialogHeader>
          <DialogTitle>上架到市场</DialogTitle>
          <DialogDescription>
            选一个分组。装进去的人按这个逛货架。
          </DialogDescription>
        </DialogHeader>
        <DialogBody>
          <fieldset className="m-0 flex flex-wrap gap-1.5 border-0 p-0">
            <legend className="sr-only">提示词分组</legend>
            {SKILL_STORE_GROUPS.map((row) => (
              <Badge
                key={row.id}
                as="button"
                type="button"
                pill
                tone={group === row.id ? "primary" : "muted"}
                aria-pressed={group === row.id}
                onClick={() => setGroup(row.id)}
              >
                {row.label}
              </Badge>
            ))}
          </fieldset>
        </DialogBody>
        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            disabled={busy}
            onClick={() => onOpenChange(false)}
          >
            取消
          </Button>
          <Button
            type="button"
            disabled={busy || group == null}
            onClick={() => {
              if (group) onConfirm(group);
            }}
          >
            上架
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
