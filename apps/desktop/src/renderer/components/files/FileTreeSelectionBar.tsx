import { Button } from "@/components/ui";
import { SimpleTooltip } from "@/components/ui/tooltip";
import { Download, Scissors, Trash2, X } from "lucide-react";

/**
 * 多选时的操作条：说清「选了几项」，并把批量动作放在手边。
 *
 * 只在选中 ≥2 项时出现——单选沿用行高亮 + 右键菜单，没必要为一项挂条。移动**不**给独立按钮：
 * 目标目录靠既有的「剪切 → 右键目标文件夹 · 粘贴到此文件夹」表达，不为批量另造一套目标选择面。
 */
export function FileTreeSelectionBar({
  count,
  downloadableCount,
  canDownload,
  canMutate,
  busy,
  indent = 0,
  onDownload,
  onCut,
  onDelete,
  onClear,
}: {
  count: number;
  /** 选区里能下载的项数（文件另存；文件夹整夹 zip）。 */
  downloadableCount: number;
  canDownload: boolean;
  canMutate: boolean;
  busy: boolean;
  indent?: number;
  onDownload: () => void;
  onCut: () => void;
  onDelete: () => void;
  onClear: () => void;
}) {
  return (
    <div
      className="flex items-center gap-1 rounded-lg bg-accent/60 py-1 pr-1 text-xs"
      style={{ paddingLeft: indent + 8 }}
    >
      <span className="text-muted-foreground" aria-live="polite">
        已选择 {count} 项
      </span>
      <div className="flex-1" />
      {canDownload && downloadableCount > 0 && (
        <SimpleTooltip label="文件另存；文件夹下载为 zip">
          <Button
            variant="ghost"
            disabled={busy}
            onClick={onDownload}
            icon={<Download size={13} />}
          >
            下载
          </Button>
        </SimpleTooltip>
      )}
      {canMutate && (
        <>
          <SimpleTooltip label="剪切后右键目标文件夹「粘贴到此文件夹」完成移动">
            <Button
              variant="ghost"
              disabled={busy}
              onClick={onCut}
              icon={<Scissors size={13} />}
            >
              剪切
            </Button>
          </SimpleTooltip>
          <Button
            variant="danger"
            disabled={busy}
            onClick={onDelete}
            icon={<Trash2 size={13} />}
          >
            删除
          </Button>
        </>
      )}
      <SimpleTooltip label="清空选择（Esc）">
        <Button
          variant="ghost"
          aria-label="清空选择"
          onClick={onClear}
          icon={<X size={13} />}
          className="px-1.5"
        />
      </SimpleTooltip>
    </div>
  );
}
