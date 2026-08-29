import { Button, Textarea } from "@/components/ui";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@/components/ui/dialog";
import { notifySuccess } from "@/lib/toast";
import { ApiError } from "@/services/api";
import { type FolderMeta, listFolders } from "@/services/folders";
import type {
  WorkflowDefinition,
  WorkflowSlot,
} from "@/services/workflowDefinition";
import type { WorkflowSource } from "@/services/workflowSource";
import { type UserWorkflow, runWorkflow } from "@/services/workflows";
import { Loader2, Play, RotateCcw } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useSuggestedSlots } from "./useSuggestedSlots";

const SELECT_CLASS =
  "h-8 w-full rounded-lg border border-border bg-background px-2.5 text-sm text-foreground focus:border-primary focus:outline-none focus:ring-1 focus:ring-ring";

function errMsg(e: unknown, fallback: string): string {
  return e instanceof ApiError ? (e.serverMessage ?? fallback) : fallback;
}

/**
 * 工作区下拉的三态。「请求挂了」与「一个工作区都没建」结论完全不同，
 * 合并成一个空列表会让用户以为自己没建过工作区。
 */
type FoldersState =
  | { status: "loading" }
  | { status: "ready"; items: FolderMeta[] }
  | { status: "error"; message: string };

/**
 * 一个可换参数。输入框预填 `default`（= 固化那轮的原值），清空后 placeholder 仍显示
 * 默认值，提示「留空 = 用它」——这不是要用户从零填的必填表单。
 */
function SlotOverrideField({
  slot,
  value,
  onChange,
}: {
  slot: WorkflowSlot;
  value: string;
  onChange: (next: string) => void;
}) {
  const id = `wf-run-slot-${slot.key}`;
  return (
    <label className="block" htmlFor={id}>
      <span className="mb-1 block text-xs text-muted-foreground">
        {slot.label}
      </span>
      <Textarea
        id={id}
        className="w-full text-sm"
        rows={2}
        value={value}
        maxLength={4000}
        placeholder={slot.default || "留空则用默认值"}
        onChange={(e) => onChange(e.target.value)}
      />
    </label>
  );
}

/**
 * 「跑一次」：选工作区 +（可换参数）+ 本轮补充 → 直起。
 *
 * 槽位态只存**改过的那些**（`overrides`）：没改的 key 压根不进请求，服务端回落到
 * definition 里的 `default`，于是「打开就点开跑」与没有槽位时逐字同一个请求。
 * 还没有槽位的对话固化工作流，打开时按需抽一次（见 {@link useSuggestedSlots}）：
 * 抽到就是同一套槽位交互，抽不到 / 抽挂了就是无参数形态，两种都不挡开跑。
 *
 * 报错口径：对话框自带 inline 错误位，加载与提交失败都只走 inline，不再另弹 toast；
 * toast 只留给开跑成功（此时对话框已关闭，inline 位没了）。按需抽槽不进错误位——
 * 它是锦上添花，失败了照常按上一轮原样跑。
 */
export function RunWorkflowDialog({
  open,
  workflowId,
  workflowName,
  definition,
  source,
  onSlotsSuggested,
  onClose,
}: {
  open: boolean;
  workflowId: string;
  workflowName: string;
  /** 已存 definition（跑的是服务端那份，别传画布里未保存的改动）。 */
  definition?: WorkflowDefinition;
  /** 工作流顶层的出处（服务端权威字段）：按需抽槽只认对话固化来的。 */
  source?: WorkflowSource | null;
  /** 按需抽出槽位后的最新工作流：父层据此更新自己那份，别再抽第二遍。 */
  onSlotsSuggested?: (workflow: UserWorkflow) => void;
  onClose: () => void;
}) {
  const navigate = useNavigate();
  const [foldersState, setFoldersState] = useState<FoldersState>({
    status: "loading",
  });
  const [folderId, setFolderId] = useState("");
  const [note, setNote] = useState("");
  // Map 而非对象字面量：槽位 key 由后端给，`toString` 这种会撞上原型链。
  const [overrides, setOverrides] = useState<Map<string, string>>(new Map());
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // 重开 / 连点重试会有多个请求在飞，只认最后一个的结果。
  const loadSeq = useRef(0);

  const loadFolders = useCallback(async () => {
    const seq = ++loadSeq.current;
    setFoldersState({ status: "loading" });
    try {
      const all = await listFolders();
      if (seq !== loadSeq.current) return;
      const cloud = all.filter((f) => f.mode === "cloud");
      const items = cloud.length > 0 ? cloud : all;
      setFoldersState({ status: "ready", items });
      setFolderId(items[0]?.id ?? "");
    } catch (e) {
      if (seq !== loadSeq.current) return;
      setFoldersState({
        status: "error",
        message: errMsg(e, "文件夹列表加载失败"),
      });
      setFolderId("");
    }
  }, []);

  useEffect(() => {
    if (!open) return;
    setNote("");
    setOverrides(new Map());
    setError(null);
    void loadFolders();
  }, [open, loadFolders]);

  const ready = foldersState.status === "ready" ? foldersState : null;
  const noFolders = ready !== null && ready.items.length === 0;
  const {
    slots: slotList,
    pending: suggesting,
    fresh: justSuggested,
  } = useSuggestedSlots({
    open,
    workflowId,
    definition,
    source,
    onSuggested: onSlotsSuggested,
  });

  const submit = async () => {
    if (!folderId || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      const result = await runWorkflow(workflowId, {
        folderId,
        note: note.trim() || null,
        slots: Object.fromEntries(overrides),
      });
      notifySuccess("已按工作流开跑");
      onClose();
      if (result.conversationId) {
        navigate(`/conversations/${result.conversationId}`);
      }
    } catch (e) {
      setError(errMsg(e, "跑一次失败"));
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
      <DialogContent className="max-w-md">
        <DialogTitle>跑一次 · {workflowName}</DialogTitle>
        <DialogDescription>
          {slotList.length > 0
            ? "参数已按上次的值预填：直接开跑就是原样重跑，改了哪个就是换那个再跑。"
            : "选择文件夹后按保存的图直起；可选填本轮补充说明（不改图）。"}
        </DialogDescription>

        <div className="mt-4 space-y-3">
          <div>
            <label className="block">
              <span className="mb-1 block text-xs text-muted-foreground">
                文件夹
              </span>
              <select
                className={SELECT_CLASS}
                value={folderId}
                disabled={ready === null || noFolders}
                onChange={(e) => setFolderId(e.target.value)}
              >
                {ready === null ? (
                  <option value="">
                    {foldersState.status === "loading"
                      ? "加载中…"
                      : "文件夹列表加载失败"}
                  </option>
                ) : noFolders ? (
                  <option value="">还没有文件夹</option>
                ) : (
                  ready.items.map((f) => (
                    <option key={f.id} value={f.id}>
                      {f.name}
                      {f.mode === "cloud" ? "" : "（本地）"}
                    </option>
                  ))
                )}
              </select>
            </label>

            {foldersState.status === "error" && (
              <div className="mt-1.5 flex flex-wrap items-center gap-2">
                <p className="min-w-0 flex-1 text-xs text-muted-foreground">
                  {foldersState.message}
                </p>
                <Button
                  variant="neutral"
                  size="sm"
                  onClick={() => void loadFolders()}
                >
                  重试
                </Button>
              </div>
            )}
            {noFolders && (
              <p className="mt-1.5 text-xs text-muted-foreground">
                还没有可用的文件夹：先到「文件」里新建一个再回来跑。
              </p>
            )}
          </div>

          {suggesting && (
            <div className="flex items-start gap-2 rounded-xl border border-dashed border-border p-3">
              <Loader2
                size={12}
                className="mt-0.5 shrink-0 animate-spin text-muted-foreground"
              />
              <p className="text-xs text-muted-foreground">
                正在看看这个工作流里有哪些值可以换（要读一遍任务，最长约 20
                秒）。不想等就直接开跑，按上一轮原样跑。
              </p>
            </div>
          )}

          {slotList.length > 0 && (
            <section className="space-y-2 rounded-xl border border-border p-3">
              <div className="flex items-center justify-between gap-2">
                <p className="text-xs font-medium text-foreground">可换参数</p>
                {overrides.size > 0 && (
                  <Button
                    variant="ghost"
                    size="sm"
                    icon={<RotateCcw size={12} />}
                    onClick={() => setOverrides(new Map())}
                  >
                    还原默认
                  </Button>
                )}
              </div>
              {justSuggested && (
                <p className="text-xs text-muted-foreground">
                  刚从任务里认出这几个值，已按上一轮预填：抽得不对就直接改，不用管也行——不动就是原样重跑。
                </p>
              )}
              {slotList.map((slot) => (
                <SlotOverrideField
                  key={slot.key}
                  slot={slot}
                  value={overrides.get(slot.key) ?? slot.default}
                  onChange={(next) =>
                    setOverrides((prev) => new Map(prev).set(slot.key, next))
                  }
                />
              ))}
            </section>
          )}

          <label className="block" htmlFor="wf-run-note">
            <span className="mb-1 block text-xs text-muted-foreground">
              本轮补充（可选）
            </span>
            <Textarea
              id="wf-run-note"
              className="w-full text-sm"
              rows={3}
              value={note}
              maxLength={4000}
              placeholder="空则只按图跑；有则作为本轮附加上下文"
              onChange={(e) => setNote(e.target.value)}
            />
          </label>

          {error && <p className="text-xs text-muted-foreground">{error}</p>}
        </div>

        <div className="mt-5 flex justify-end gap-2">
          <Button variant="neutral" size="md" onClick={onClose}>
            取消
          </Button>
          <Button
            size="md"
            disabled={!folderId || submitting}
            icon={
              submitting ? (
                <Loader2 size={14} className="animate-spin" />
              ) : (
                <Play size={14} />
              )
            }
            onClick={() => void submit()}
          >
            开跑
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
