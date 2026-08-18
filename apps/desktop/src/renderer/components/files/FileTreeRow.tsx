import { Button, SurfaceRow, surfaceRowIndent } from "@/components/ui";
import { ContextMenu, ContextMenuTrigger } from "@/components/ui/context-menu";
import { statusPillSoft } from "@/components/ui/tone-presets";
import { SimpleTooltip } from "@/components/ui/tooltip";
import type { FileNode, FileSource } from "@/lib/fileSource";
import { type DropUploadCapture, captureDropUpload } from "@/lib/folderUpload";
import {
  AGENTCORE_ROOT_LABEL,
  AGENTCORE_ROOT_TOOLTIP,
  countDescendantFiles,
  isAgentCoreRootDir,
  stageDirCaption,
  stageDirMeta,
} from "@/lib/stageDirs";
import { cn } from "@/lib/utils";
import { ChevronDown, ChevronRight, Loader2 } from "lucide-react";
import type React from "react";
import { InlineCreateRow, InlineInput, InlineRow } from "./FileTreeInline";
import { FileTreeRowMenu } from "./FileTreeRowMenu";
import { FileTypeIcon } from "./FileTypeIcon";
import { DRAG_MIME, type DragPayload, parseDragPayload } from "./fileTreeDrag";
import {
  type RowClickIntent,
  clickIntent,
  isSelectionOnlyClick,
} from "./fileTreeSelection";
import type { BatchMenuActions } from "./fileTreeTypes";
import { FileRowMeta, TruncatedNotice } from "./parts";
import type { useFileTreeData } from "./useFileTreeData";

export interface FileTreeRowProps {
  node: FileNode;
  depth: number;
  /** 整棵树的统一额外左内边距（嵌套在工作区根之下时 > 0）。 */
  indentBase: number;
  source: FileSource;
  data: ReturnType<typeof useFileTreeData>;
  expanded: Set<string>;
  /** When non-null, only render children whose path is in the set. */
  filterVisible?: Set<string> | null;
  activePath: string | null;
  creating: { dir: string; kind: "file" | "dir" } | null;
  renaming: string | null;
  dropTarget: string | null;
  /** 选中的行（高亮）；单选即一项。 */
  selectedPaths: ReadonlySet<string>;
  /**
   * 拖走整个选区时的清单（已剔掉「祖先也在选区里」的后代）。只有被拖的行本身在选区内才
   * 用它——拖选区外的行搬的是那一行。
   */
  dragPaths: readonly string[];
  /** 已剪切待移动的行（半透明示意）。 */
  cutPaths: ReadonlySet<string>;
  /** 剪贴板非空（文件夹行据此显示「粘贴」）。 */
  hasClipboard: boolean;
  /**
   * 多选态下的批量动作（≥2 项时非空）；本行在选区内才换成批量菜单，选区外的行由
   * {@link FileTreeRowProps.onContextSelect} 先把选区收敛成单选。
   */
  batchMenu: BatchMenuActions | null;
  onToggle: (dir: string) => void;
  onOpenFile: (path: string, name: string) => void;
  /** 行点击：带 Ctrl/Cmd·Shift 意图更新选区（普通点击才继续打开 / 展开）。 */
  onSelect: (node: FileNode, intent: RowClickIntent) => void;
  /** 右键按下时先定选区（点在选区内保持整批，点在选区外收敛成这一行）。 */
  onContextSelect: (node: FileNode) => void;
  onContextCreate: (dir: string, kind: "file" | "dir") => void;
  onStartRename: (path: string) => void;
  onSubmitRename: (path: string, name: string) => void;
  onCancelRename: () => void;
  onSubmitCreate: (name: string) => void;
  onCancelCreate: () => void;
  onDelete: (node: FileNode) => void;
  onCopy: (paths: string[]) => void;
  onCut: (paths: string[]) => void;
  onPaste: (destDir: string) => void;
  /**
   * 落一个被拖来的节点到 `destDir`。收整个载荷（而非裸路径）是因为它可能来自**另一棵**
   * 树——父子文件夹在文件中枢里就是两个源，同源/异源由接收方分派。
   */
  onMoveInto: (payload: DragPayload, destDir: string) => void;
  /** 落一次外部拖入（可能是整个文件夹，故收 drop 事件里同步捕获的 entry 而非 FileList）。 */
  onUpload: (capture: DropUploadCapture, destDir: string) => void;
  onDropTarget: (path: string | null) => void;
  /** Reload a directory after a mutation that adds siblings (e.g. export docx). */
  onReloadDir: (dir: string) => void;
}

export function FileTreeRow(props: FileTreeRowProps) {
  const { node, depth, source, data, expanded, dropTarget, indentBase } = props;
  const indent = depth * 14 + 8 + indentBase;
  const rowStyle = surfaceRowIndent(depth, indentBase);

  const startDrag = (e: React.DragEvent) => {
    // 拖的是选区里的行 → 整批一起搬（文件管理器通行做法）；拖选区外的行 → 只搬这一行，
    // 且不动选区——用户按住的是它，不是那一批。
    const inSelection =
      props.selectedPaths.has(node.path) && props.dragPaths.length > 0;
    const payload: DragPayload = {
      sourceId: source.id,
      paths: inSelection ? [...props.dragPaths] : [node.path],
    };
    e.dataTransfer.setData(DRAG_MIME, JSON.stringify(payload));
    e.dataTransfer.effectAllowed = "move";
  };

  // 本行在多选选区内时右键菜单对整批生效；否则（含单选）走单项菜单。
  const rowBatch =
    props.batchMenu && props.selectedPaths.has(node.path)
      ? props.batchMenu
      : null;
  const onContextMenu = () => props.onContextSelect(node);

  if (!node.isDir) {
    const isActive = props.activePath === node.path;
    const isSelected = props.selectedPaths.has(node.path);
    const isCut = props.cutPaths.has(node.path);
    return (
      <li>
        {props.renaming === node.path ? (
          <InlineRow
            indent={indent}
            icon={<FileTypeIcon name={node.name} size={13} />}
          >
            <InlineInput
              initial={node.name}
              onSubmit={(v) => props.onSubmitRename(node.path, v)}
              onCancel={props.onCancelRename}
            />
          </InlineRow>
        ) : (
          <ContextMenu>
            <ContextMenuTrigger asChild>
              <SurfaceRow
                variant="file"
                active={isActive}
                selected={isSelected}
                cut={isCut}
                draggable
                onDragStart={startDrag}
                onContextMenu={onContextMenu}
                style={rowStyle}
              >
                <SimpleTooltip label={`预览 ${node.path}`}>
                  <Button
                    variant="ghost"
                    onClick={(e) => {
                      const intent = clickIntent(e);
                      props.onSelect(node, intent);
                      // 加减选 / 连选只动选区：否则每加选一个文件都会顺手把预览换掉。
                      if (isSelectionOnlyClick(intent)) return;
                      props.onOpenFile(node.path, node.name);
                    }}
                    className="h-auto min-w-0 flex-1 justify-start gap-1.5 overflow-hidden rounded-none px-0 py-1.5 text-left text-xs font-normal"
                  >
                    <FileTypeIcon name={node.name} size={13} />
                    <span className="min-w-0 flex-1 truncate">{node.name}</span>
                    <FileRowMeta node={node} />
                  </Button>
                </SimpleTooltip>
              </SurfaceRow>
            </ContextMenuTrigger>
            <FileTreeRowMenu {...props} batch={rowBatch} />
          </ContextMenu>
        )}
      </li>
    );
  }

  // Directory row.
  const open = expanded.has(node.path);
  const isTarget = dropTarget === node.path;
  const isSelected = props.selectedPaths.has(node.path);
  const isCut = props.cutPaths.has(node.path);
  const status = data.statusOf(node.path);
  const children = data.childrenOf(node.path);
  const stage = stageDirMeta(node.path);
  const stageCaption = stage
    ? stageDirCaption(stage, countDescendantFiles(node.path, data.childrenOf))
    : null;
  // 约定根改叫「AI 工作间」并退成次要行（钉顶、不跟用户文件抢视觉权重）：与条目区消歧，且它是过程材料。
  const isWorkroom = isAgentCoreRootDir(node.path);

  return (
    <li>
      {props.renaming === node.path ? (
        <InlineRow indent={indent} icon={null}>
          <InlineInput
            initial={node.name}
            onSubmit={(v) => props.onSubmitRename(node.path, v)}
            onCancel={props.onCancelRename}
          />
        </InlineRow>
      ) : (
        <ContextMenu>
          <ContextMenuTrigger asChild>
            <SurfaceRow
              variant="file"
              selected={isSelected}
              dropTarget={isTarget}
              cut={isCut}
              draggable
              onDragStart={startDrag}
              onContextMenu={onContextMenu}
              onDragOver={(e) => {
                // 只读源不高亮：亮了却什么都不会发生，比不亮更难懂。
                if (!source.caps.edit) return;
                const internal = e.dataTransfer.types.includes(DRAG_MIME);
                if (!internal && !source.caps.transfer) return;
                e.preventDefault();
                e.stopPropagation();
                props.onDropTarget(node.path);
              }}
              onDrop={(e) => {
                e.preventDefault();
                e.stopPropagation();
                props.onDropTarget(null);
                const raw = e.dataTransfer.getData(DRAG_MIME);
                if (raw) {
                  const p = parseDragPayload(raw);
                  if (p) props.onMoveInto(p, node.path);
                  return;
                }
                if (!source.caps.transfer) return;
                // entry 必须在事件里同步取走，之后 `dataTransfer.items` 就空了。
                const capture = captureDropUpload(e.dataTransfer);
                if (capture.entries.length > 0 || capture.looseFiles.length > 0)
                  props.onUpload(capture, node.path);
              }}
              style={rowStyle}
            >
              <SimpleTooltip
                label={
                  stage?.tooltip ??
                  (isWorkroom ? AGENTCORE_ROOT_TOOLTIP : node.path)
                }
              >
                <Button
                  variant="ghost"
                  onClick={(e) => {
                    const intent = clickIntent(e);
                    props.onSelect(node, intent);
                    // 加减选 / 连选只动选区：连选跨过折叠的目录时不该把它们一个个展开。
                    if (isSelectionOnlyClick(intent)) return;
                    props.onToggle(node.path);
                  }}
                  className={cn(
                    "h-auto min-w-0 flex-1 justify-start gap-1.5 overflow-hidden rounded-none px-0 py-1.5 text-left text-xs font-normal",
                    isWorkroom && "text-muted-foreground",
                  )}
                >
                  {open ? (
                    <ChevronDown
                      size={13}
                      className="shrink-0 text-muted-foreground"
                    />
                  ) : (
                    <ChevronRight
                      size={13}
                      className="shrink-0 text-muted-foreground"
                    />
                  )}
                  <span className="min-w-0 flex-1 truncate">
                    {isWorkroom ? AGENTCORE_ROOT_LABEL : node.name}
                  </span>
                  {stageCaption && (
                    <span
                      className={`shrink-0 rounded-full px-1.5 py-0.5 text-xs leading-none ${statusPillSoft.muted}`}
                    >
                      {stageCaption}
                    </span>
                  )}
                  {/* 工作间行是刻意压低的次要行，不给它挂元信息。 */}
                  {!isWorkroom && <FileRowMeta node={node} />}
                </Button>
              </SimpleTooltip>
            </SurfaceRow>
          </ContextMenuTrigger>
          <FileTreeRowMenu {...props} batch={rowBatch} />
        </ContextMenu>
      )}

      {open && (
        <ul>
          {props.creating?.dir === node.path && (
            <InlineCreateRow
              kind={props.creating.kind}
              depth={depth + 1}
              indentBase={indentBase}
              onSubmit={props.onSubmitCreate}
              onCancel={props.onCancelCreate}
            />
          )}
          {status === "loading" && children === undefined && (
            <li
              className="flex items-center gap-1.5 py-1 text-xs text-muted-foreground"
              style={{ paddingLeft: (depth + 1) * 14 + 8 + indentBase }}
            >
              <Loader2 size={12} className="animate-spin" />
              加载中…
            </li>
          )}
          {status === "error" && (
            <li
              className="py-1 text-xs text-muted-foreground"
              style={{ paddingLeft: (depth + 1) * 14 + 8 + indentBase }}
            >
              加载失败
            </li>
          )}
          {children?.length === 0 && !props.creating && (
            <li
              className="py-1 text-xs text-muted-foreground/60"
              style={{ paddingLeft: (depth + 1) * 14 + 8 + indentBase }}
            >
              空文件夹
            </li>
          )}
          {children
            ?.filter(
              (child) =>
                !props.filterVisible || props.filterVisible.has(child.path),
            )
            .map((child) => (
              <FileTreeRow
                key={child.path}
                {...props}
                node={child}
                depth={depth + 1}
              />
            ))}
          {data.truncatedOf(node.path) && (
            <TruncatedNotice
              indent={(depth + 1) * 14 + 8 + indentBase}
              shown={children?.length ?? 0}
            />
          )}
        </ul>
      )}
    </li>
  );
}
