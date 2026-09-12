import { Button, Input, Textarea } from "@/components/ui";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { APP_PATHS } from "@/pages/toolbox/manual/paths";
import { ApiError } from "@/services/api";
import {
  type WorkflowTemplate,
  type WorkflowTemplateSlot,
  createWorkflowFromPlaybook,
} from "@/services/workflows";
import { Copy, Loader2 } from "lucide-react";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

const SELECT_CLASS =
  "h-8 w-full rounded-lg border border-border bg-background px-2.5 text-sm text-foreground focus:border-primary focus:outline-none focus:ring-1 focus:ring-ring";

function errMsg(e: unknown, fallback: string): string {
  return e instanceof ApiError ? (e.serverMessage ?? fallback) : fallback;
}

/**
 * 一个主槽。带 `choices` 的槽只接受枚举值，渲染成选择器——填自由文本会被后端拒。
 */
function SlotField({
  slot,
  value,
  onChange,
}: {
  slot: WorkflowTemplateSlot;
  value: string;
  onChange: (next: string) => void;
}) {
  const id = `wf-tpl-slot-${slot.key}`;
  const title = (
    <span className="mb-1 block text-xs text-muted-foreground">
      {slot.label}
      {slot.required ? "" : "（可选）"}
    </span>
  );

  if (slot.choices.length > 0) {
    return (
      <div>
        <label className="block" htmlFor={id}>
          {title}
          <select
            id={id}
            className={SELECT_CLASS}
            value={value}
            onChange={(e) => onChange(e.target.value)}
          >
            <option value="" disabled={slot.required}>
              {slot.required ? "请选择" : "不指定（用模板默认）"}
            </option>
            {slot.choices.map((c) => (
              <option key={c.value} value={c.value}>
                {c.label}
              </option>
            ))}
          </select>
        </label>
        {slot.hint && (
          <p className="mt-1 text-xs text-muted-foreground">{slot.hint}</p>
        )}
      </div>
    );
  }

  return (
    <label className="block" htmlFor={id}>
      {title}
      <Textarea
        id={id}
        className="w-full text-sm"
        rows={3}
        value={value}
        maxLength={4000}
        placeholder={slot.hint ?? undefined}
        onChange={(e) => onChange(e.target.value)}
      />
    </label>
  );
}

/**
 * 「使用」官方模板 → 收主槽 → from-playbook → 跳转新工作流画布。
 *
 * 槽位定义（必填与否、可选值）全部来自接口目录，本地不猜。
 * 报错口径：对话框自带 inline 错误位，失败只走 inline，不再另弹 toast。
 */
export function UseTemplateDialog({
  open,
  template,
  onClose,
}: {
  open: boolean;
  template: WorkflowTemplate | null;
  onClose: () => void;
}) {
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [slotValues, setSlotValues] = useState<Record<string, string>>({});
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open || !template) return;
    setName(template.title);
    const next: Record<string, string> = {};
    for (const s of template.slots) next[s.key] = "";
    setSlotValues(next);
    setError(null);
  }, [open, template]);

  if (!template) return null;

  const missing = template.slots.filter(
    (s) => s.required && !(slotValues[s.key] ?? "").trim(),
  );

  const submit = async () => {
    if (missing.length > 0 || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      const created = await createWorkflowFromPlaybook({
        playbook: template.id,
        name: name.trim() || template.title,
        slots: slotValues,
      });
      onClose();
      navigate(APP_PATHS.toolbox.workflows.edit(created.id));
    } catch (e) {
      setError(errMsg(e, "复制失败"));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
    >
      <DialogContent size="md">
        <DialogHeader>
          <DialogTitle>使用 · {template.title}</DialogTitle>
          <DialogDescription>
            填写主参数后复制为我的工作流，可再在画布里改。
          </DialogDescription>
        </DialogHeader>

        <DialogBody className="space-y-3">
          <label className="block" htmlFor="wf-tpl-name">
            <span className="mb-1 block text-xs text-muted-foreground">
              工作流名称
            </span>
            <Input
              id="wf-tpl-name"
              className="w-full"
              value={name}
              maxLength={200}
              onChange={(e) => setName(e.target.value)}
            />
          </label>

          {template.slots.map((slot) => (
            <SlotField
              key={slot.key}
              slot={slot}
              value={slotValues[slot.key] ?? ""}
              onChange={(next) =>
                setSlotValues((prev) => ({ ...prev, [slot.key]: next }))
              }
            />
          ))}

          {missing.length > 0 && (
            <p className="text-xs text-muted-foreground">
              还需填写：{missing.map((s) => s.label).join("、")}
            </p>
          )}
          {error && <p className="text-xs text-muted-foreground">{error}</p>}
        </DialogBody>

        <DialogFooter>
          <Button variant="outline" size="md" onClick={onClose}>
            取消
          </Button>
          <Button
            size="md"
            disabled={missing.length > 0 || submitting}
            icon={
              submitting ? (
                <Loader2 size={14} className="animate-spin" />
              ) : (
                <Copy size={14} />
              )
            }
            onClick={() => void submit()}
          >
            复制为我的
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
