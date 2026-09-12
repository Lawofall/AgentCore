import { Button, ConfirmDialog } from "@/components/ui";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { baseName, parentDir } from "@/lib/fileSource";
import type { BatchFailure } from "./fileTreeBatch";
import type { SelectedItem } from "./fileTreeSelection";

/** 批量删除的确认态（`null` = 没在确认）。 */
export interface BatchConfirmState {
  items: readonly SelectedItem[];
  /** 删到哪去 / 还能不能捞回来（按源能力成文，与单项删除同一句）。 */
  restoreHint: string;
  busy: boolean;
}

/** 批量结果里失败的那部分（`null` = 没有失败，成功已由 toast 报过）。 */
export interface BatchFailureState {
  title: string;
  failures: readonly BatchFailure[];
}

function locationLabel(path: string): string {
  return parentDir(path) || "根目录";
}

/**
 * 确认框里的待删清单。
 *
 * 逐项列名字 + 所在目录：批量删可能包含被折叠、被筛掉的行，光报个数字等于让用户闭着眼确认；
 * 同名文件也只有带上目录才分得清删的是哪一个。
 */
function ItemList({ items }: { items: readonly SelectedItem[] }) {
  return (
    <ul className="max-h-56 overflow-y-auto rounded-lg bg-muted/40 px-3 py-2">
      {items.map((item) => (
        <li
          key={item.path}
          className="flex items-baseline gap-2 py-0.5 text-xs"
        >
          <span className="min-w-0 flex-1 truncate">{baseName(item.path)}</span>
          <span className="shrink-0 truncate text-xs text-muted-foreground/70">
            {item.isDir ? "文件夹 · " : ""}
            {locationLabel(item.path)}
          </span>
        </li>
      ))}
    </ul>
  );
}

/**
 * 失败清单。
 *
 * 批量是逐项调单项端点，部分成功是常态；这里把**每一项**的名字与原因摊开，不折叠、不只给
 * 条数——「有几项失败了但不知道是哪几项」等于没报。
 */
function FailureList({ failures }: { failures: readonly BatchFailure[] }) {
  return (
    <ul className="max-h-64 space-y-1.5 overflow-y-auto rounded-lg bg-muted/40 px-3 py-2">
      {failures.map((f) => (
        <li key={f.path} className="text-xs">
          <div className="flex items-baseline gap-2">
            <span className="min-w-0 flex-1 truncate font-medium">
              {f.name}
            </span>
            <span className="shrink-0 truncate text-xs text-muted-foreground/70">
              {locationLabel(f.path)}
            </span>
          </div>
          <p className="text-xs text-muted-foreground">{f.reason}</p>
        </li>
      ))}
    </ul>
  );
}

/**
 * 批量动作的两个模态：删除前确认（列出要删什么）与结束后的失败清单（列出哪几项、为什么）。
 * 全成功不弹任何框——那条只值一个 toast。
 */
export function FileTreeBatchDialogs({
  confirm,
  onConfirmDelete,
  onCancelDelete,
  failure,
  onCloseFailure,
}: {
  confirm: BatchConfirmState | null;
  onConfirmDelete: () => void;
  onCancelDelete: () => void;
  failure: BatchFailureState | null;
  onCloseFailure: () => void;
}) {
  const dirCount = confirm?.items.filter((i) => i.isDir).length ?? 0;
  return (
    <>
      <ConfirmDialog
        open={confirm !== null}
        onOpenChange={(open) => {
          if (!open) onCancelDelete();
        }}
        title={`删除选中的 ${confirm?.items.length ?? 0} 项？`}
        description={
          dirCount > 0
            ? `其中 ${dirCount} 个文件夹将连同内容一起删除。${confirm?.restoreHint ?? ""}`
            : (confirm?.restoreHint ?? "")
        }
        confirmLabel="删除"
        tone="danger"
        busy={confirm?.busy ?? false}
        onConfirm={onConfirmDelete}
      >
        {confirm && <ItemList items={confirm.items} />}
      </ConfirmDialog>

      <Dialog
        open={failure !== null}
        onOpenChange={(open) => {
          if (!open) onCloseFailure();
        }}
      >
        <DialogContent size="md">
          <DialogHeader>
            <DialogTitle>{failure?.title}</DialogTitle>
            <DialogDescription>
              以下项目没有完成，其余项已生效。
            </DialogDescription>
          </DialogHeader>
          <DialogBody>
            {failure && <FailureList failures={failure.failures} />}
          </DialogBody>
          <DialogFooter>
            <Button variant="outline" size="md" onClick={onCloseFailure}>
              知道了
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
