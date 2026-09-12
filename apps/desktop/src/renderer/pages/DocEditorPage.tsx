import { ShareDocDialog } from "@/components/docs/ShareDocDialog";
import { CanvasShell } from "@/components/layout/CanvasShell";
import {
  Button,
  IconButton,
  SegmentedControl,
  Textarea,
} from "@/components/ui";
import {
  type DocBlock,
  type DocBody,
  type DocChartBlock,
  type DocTableBlock,
  MAX_CHART_ITEMS,
  MAX_TABLE_COLUMNS,
  MAX_TABLE_ROWS,
  chartWithAddedItem,
  chartWithItemLabel,
  chartWithItemValue,
  chartWithRemovedItem,
  chartWithTitle,
  createChartBlock,
  createHeadingBlock,
  createListBlock,
  createParagraphBlock,
  createTableBlock,
  parseDocBody,
  tableWithAddedColumn,
  tableWithAddedRow,
  tableWithCell,
  tableWithHeaderCell,
  tableWithRemovedColumn,
  tableWithRemovedRow,
} from "@/lib/docBody";
import {
  type DocDetail,
  getDoc,
  renameDoc,
  saveDocBody,
} from "@/services/docs";
import {
  BarChart3,
  Heading,
  Link2,
  Loader2,
  Plus,
  Table,
  Trash2,
  Type,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

type SaveStatus = "idle" | "saving" | "saved" | "error";

const STATUS_TEXT: Record<SaveStatus, string> = {
  idle: "",
  saving: "保存中…",
  saved: "已保存",
  error: "保存失败",
};

function serializeBody(body: DocBody): string {
  return JSON.stringify(body);
}

export function DocEditorPage() {
  const { docId = "" } = useParams();
  const navigate = useNavigate();

  const [doc, setDoc] = useState<DocDetail | null>(null);
  const [blocks, setBlocks] = useState<DocBlock[]>([]);
  const [loadError, setLoadError] = useState(false);
  const [status, setStatus] = useState<SaveStatus>("idle");
  const [conflict, setConflict] = useState(false);
  const [title, setTitle] = useState("");

  const [shareOpen, setShareOpen] = useState(false);

  const versionRef = useRef(0);
  const savedRef = useRef("");
  const latestRef = useRef<DocBlock[] | null>(null);
  const conflictRef = useRef(false);
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const persistInflight = useRef<Promise<boolean> | null>(null);
  const loadGenRef = useRef(0);
  const loadedSourceIdRef = useRef<string | null>(null);

  const fetchDoc = useCallback(() => {
    const requestedId = docId;
    const gen = ++loadGenRef.current;
    loadedSourceIdRef.current = null;
    latestRef.current = null;
    versionRef.current = 0;
    savedRef.current = "";
    setDoc(null);
    setBlocks([]);
    setTitle("");
    setLoadError(false);
    setStatus("idle");
    setConflict(false);
    setShareOpen(false);
    conflictRef.current = false;
    getDoc(requestedId)
      .then((d) => {
        if (gen !== loadGenRef.current) return;
        const parsed = parseDocBody(d.body);
        versionRef.current = d.version;
        loadedSourceIdRef.current = d.id;
        savedRef.current = serializeBody(parsed);
        latestRef.current = parsed.blocks;
        setTitle(d.title);
        setBlocks(parsed.blocks);
        setDoc(d);
      })
      .catch(() => {
        if (gen !== loadGenRef.current) return;
        setLoadError(true);
      });
  }, [docId]);

  useEffect(() => {
    fetchDoc();
    return () => {
      loadGenRef.current += 1;
      loadedSourceIdRef.current = null;
      latestRef.current = null;
      if (saveTimer.current) {
        clearTimeout(saveTimer.current);
        saveTimer.current = null;
      }
    };
  }, [fetchDoc]);

  const persist = useCallback(
    async (next: DocBlock[]): Promise<boolean> => {
      const sourceId = loadedSourceIdRef.current;
      if (!sourceId || sourceId !== docId) return false;

      const run = async (): Promise<boolean> => {
        const body: DocBody = { schemaVersion: 1, blocks: next };
        const key = serializeBody(body);
        if (key === savedRef.current) return true;
        setStatus("saving");
        try {
          const res = await saveDocBody(sourceId, body, versionRef.current);
          if (loadedSourceIdRef.current !== sourceId) return false;
          if (res.conflict) {
            conflictRef.current = true;
            setConflict(true);
            setStatus("idle");
            return false;
          }
          versionRef.current = res.version;
          savedRef.current = key;
          setStatus("saved");
          return true;
        } catch {
          if (loadedSourceIdRef.current !== sourceId) return false;
          setStatus("error");
          return false;
        }
      };

      const queued = persistInflight.current
        ? persistInflight.current.then(run, run)
        : run();
      persistInflight.current = queued;
      void queued.finally(() => {
        if (persistInflight.current === queued) persistInflight.current = null;
      });
      return queued;
    },
    [docId],
  );

  const scheduleSave = useCallback(
    (next: DocBlock[]) => {
      latestRef.current = next;
      setBlocks(next);
      if (conflictRef.current) return;
      if (doc && !doc.can_write) return;
      if (saveTimer.current) clearTimeout(saveTimer.current);
      saveTimer.current = setTimeout(() => {
        const snap = latestRef.current;
        if (!snap) return;
        void persist(snap);
      }, 800);
    },
    [persist, doc],
  );

  const commitTitle = useCallback(async () => {
    const next = title.trim();
    if (!doc || !next || next === doc.title) {
      setTitle(doc?.title ?? "");
      return;
    }
    if (doc.id !== docId || loadedSourceIdRef.current !== docId) {
      setTitle(doc.title);
      return;
    }
    if (!doc.can_write) {
      setTitle(doc.title);
      return;
    }
    try {
      const updated = await renameDoc(docId, next);
      if (loadedSourceIdRef.current !== docId) return;
      setDoc((d) => (d ? { ...d, title: updated.title } : d));
    } catch {
      setTitle(doc.title);
    }
  }, [title, doc, docId]);

  const flushPending = useCallback(async (): Promise<boolean> => {
    if (saveTimer.current) {
      clearTimeout(saveTimer.current);
      saveTimer.current = null;
    }
    await commitTitle();
    const snap = latestRef.current;
    if (!snap) return true;
    return persist(snap);
  }, [commitTitle, persist]);

  const updateBlock = (id: string, next: DocBlock) => {
    scheduleSave(blocks.map((b) => (b.id === id ? next : b)));
  };

  const removeBlock = (id: string) => {
    scheduleSave(blocks.filter((b) => b.id !== id));
  };

  const addBlock = (block: DocBlock) => {
    scheduleSave([...blocks, block]);
  };

  if (loadError) {
    return (
      <div className="absolute inset-0 flex flex-col items-center justify-center gap-3">
        <p className="text-sm text-muted-foreground">文档加载失败</p>
        <div className="flex gap-2">
          <Button variant="neutral" onClick={() => navigate("/docs")}>
            返回列表
          </Button>
          <Button variant="primary" onClick={fetchDoc}>
            重试
          </Button>
        </div>
      </div>
    );
  }

  return (
    <CanvasShell
      backAriaLabel="返回文档列表"
      onBack={() => navigate("/docs")}
      title={
        <input
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          onBlur={() => void commitTitle()}
          onKeyDown={(e) => {
            if (e.key === "Enter") e.currentTarget.blur();
          }}
          placeholder="未命名文档"
          aria-label="文档标题"
          readOnly={!doc?.can_write}
          className="min-w-0 max-w-xs flex-1 rounded-lg bg-transparent px-2 py-1 text-sm font-medium text-foreground outline-none hover:bg-accent focus:bg-accent read-only:hover:bg-transparent read-only:focus:bg-transparent"
        />
      }
      status={STATUS_TEXT[status]}
      actions={
        doc?.can_write ? (
          <Button
            variant="neutral"
            icon={<Link2 size={14} />}
            onClick={() => setShareOpen(true)}
          >
            分享
          </Button>
        ) : null
      }
      banner={
        conflict ? (
          <div className="flex shrink-0 items-center gap-3 border-b border-primary/30 bg-primary/10 px-3 py-2">
            <span className="text-xs text-foreground">
              此文档已在别处更新，为避免覆盖已暂停自动保存。
            </span>
            <Button
              variant="primary"
              size="sm"
              className="ml-auto"
              onClick={fetchDoc}
            >
              重新加载
            </Button>
          </div>
        ) : null
      }
    >
      {doc ? (
        <div className="mx-auto flex h-full max-w-3xl flex-col overflow-y-auto px-6 py-6">
          {doc.can_write ? null : (
            <p className="mb-4 text-sm text-muted-foreground">
              只读成员不能改这份文档。
            </p>
          )}
          <div className="flex flex-col gap-4">
            {blocks.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                还没有内容。
                {doc.can_write
                  ? "用下方按钮加上标题、段落、列表、表或图。"
                  : ""}
              </p>
            ) : null}
            {blocks.map((block) => (
              <BlockEditor
                key={block.id}
                block={block}
                readOnly={!doc.can_write}
                onChange={(next) => updateBlock(block.id, next)}
                onRemove={() => removeBlock(block.id)}
              />
            ))}
          </div>
          {doc.can_write ? (
            <div className="mt-6 flex flex-wrap gap-2 border-t border-border pt-4">
              <Button
                variant="neutral"
                size="md"
                icon={<Heading size={14} />}
                onClick={() => addBlock(createHeadingBlock())}
              >
                标题
              </Button>
              <Button
                variant="neutral"
                size="md"
                icon={<Type size={14} />}
                onClick={() => addBlock(createParagraphBlock())}
              >
                段落
              </Button>
              <Button
                variant="neutral"
                size="md"
                icon={<Plus size={14} />}
                onClick={() => addBlock(createListBlock())}
              >
                列表
              </Button>
              <Button
                variant="neutral"
                size="md"
                icon={<Table size={14} />}
                onClick={() => addBlock(createTableBlock())}
              >
                表
              </Button>
              <Button
                variant="neutral"
                size="md"
                icon={<BarChart3 size={14} />}
                onClick={() => addBlock(createChartBlock())}
              >
                图
              </Button>
            </div>
          ) : null}
        </div>
      ) : (
        <div className="flex h-full items-center justify-center">
          <Loader2 className="animate-spin text-muted-foreground" size={24} />
        </div>
      )}
      {doc?.can_write ? (
        <ShareDocDialog
          docId={doc.id}
          title={title || doc.title}
          open={shareOpen}
          onOpenChange={setShareOpen}
          onFlush={flushPending}
        />
      ) : null}
    </CanvasShell>
  );
}

function BlockEditor({
  block,
  readOnly,
  onChange,
  onRemove,
}: {
  block: DocBlock;
  readOnly: boolean;
  onChange: (next: DocBlock) => void;
  onRemove: () => void;
}) {
  return (
    <div className="group relative rounded-xl border border-border bg-card p-3">
      {readOnly ? null : (
        <IconButton
          aria-label="删除这块"
          className="absolute right-2 top-2 hidden group-hover:flex"
          onClick={onRemove}
        >
          <Trash2 size={14} />
        </IconButton>
      )}
      {block.type === "heading" ? (
        <div className="flex flex-col gap-2 pr-8">
          {readOnly ? null : (
            <SegmentedControl
              aria-label="标题级别"
              value={String(block.level)}
              onChange={(v) =>
                onChange({ ...block, level: Number(v) as 1 | 2 | 3 })
              }
              items={[
                { value: "1", label: "一" },
                { value: "2", label: "二" },
                { value: "3", label: "三" },
              ]}
            />
          )}
          <input
            value={block.text}
            readOnly={readOnly}
            onChange={(e) => onChange({ ...block, text: e.target.value })}
            placeholder="标题"
            className="w-full bg-transparent text-xl font-medium text-foreground outline-none placeholder:text-muted-foreground"
          />
        </div>
      ) : null}
      {block.type === "paragraph" ? (
        <Textarea
          value={block.text}
          readOnly={readOnly}
          onChange={(e) => onChange({ ...block, text: e.target.value })}
          placeholder="写一段…"
          rows={4}
          className="w-full border-0 bg-transparent p-0 text-sm shadow-none focus-visible:ring-0"
        />
      ) : null}
      {block.type === "list" ? (
        <div className="flex flex-col gap-2 pr-8">
          {readOnly ? null : (
            <SegmentedControl
              aria-label="列表类型"
              value={block.ordered ? "ol" : "ul"}
              onChange={(v) => onChange({ ...block, ordered: v === "ol" })}
              items={[
                { value: "ul", label: "无序" },
                { value: "ol", label: "有序" },
              ]}
            />
          )}
          <Textarea
            value={block.items.join("\n")}
            readOnly={readOnly}
            onChange={(e) =>
              onChange({ ...block, items: e.target.value.split("\n") })
            }
            placeholder={"一行一项"}
            rows={4}
            className="w-full border-0 bg-transparent p-0 text-sm shadow-none focus-visible:ring-0"
          />
        </div>
      ) : null}
      {block.type === "table" ? (
        <TableBlockEditor
          block={block}
          readOnly={readOnly}
          onChange={onChange}
        />
      ) : null}
      {block.type === "chart" ? (
        <ChartBlockEditor
          block={block}
          readOnly={readOnly}
          onChange={onChange}
        />
      ) : null}
    </div>
  );
}

function TableBlockEditor({
  block,
  readOnly,
  onChange,
}: {
  block: DocTableBlock;
  readOnly: boolean;
  onChange: (next: DocTableBlock) => void;
}) {
  const canAddCol = block.columns.length < MAX_TABLE_COLUMNS;
  const canRemoveCol = block.columns.length > 1;
  const canAddRow = block.rows.length < MAX_TABLE_ROWS;
  const canRemoveRow = block.rows.length > 1;
  const thClass = "border border-border bg-muted/40 p-1 font-medium";

  return (
    <div className="flex flex-col gap-3 pr-8">
      <div className="overflow-x-auto">
        <table className="w-full min-w-[12rem] border-collapse text-sm">
          <thead>
            <tr>
              {block.columns.map((col, ci) => (
                // biome-ignore lint/suspicious/noArrayIndexKey: 网格按位置编辑，索引即稳定身份。
                <th key={ci} className={thClass}>
                  <input
                    value={col}
                    readOnly={readOnly}
                    onChange={(e) =>
                      onChange(tableWithHeaderCell(block, ci, e.target.value))
                    }
                    placeholder={`列 ${ci + 1}`}
                    aria-label={`表头 ${ci + 1}`}
                    className="w-full min-w-[4rem] bg-transparent px-1 py-0.5 text-sm font-medium text-foreground outline-none placeholder:text-muted-foreground"
                  />
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {block.rows.map((row, ri) => (
              // biome-ignore lint/suspicious/noArrayIndexKey: 网格按位置编辑，索引即稳定身份。
              <tr key={ri}>
                {row.map((cell, ci) => (
                  // biome-ignore lint/suspicious/noArrayIndexKey: 网格按位置编辑，索引即稳定身份。
                  <td key={ci} className="border border-border p-1">
                    <input
                      value={cell}
                      readOnly={readOnly}
                      onChange={(e) =>
                        onChange(tableWithCell(block, ri, ci, e.target.value))
                      }
                      placeholder="…"
                      aria-label={`第 ${ri + 1} 行第 ${ci + 1} 列`}
                      className="w-full min-w-[4rem] bg-transparent px-1 py-0.5 text-sm text-foreground outline-none placeholder:text-muted-foreground"
                    />
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {readOnly ? null : (
        <div className="flex flex-wrap gap-2">
          <Button
            variant="neutral"
            size="sm"
            disabled={!canAddCol}
            onClick={() => {
              const next = tableWithAddedColumn(block);
              if (next) onChange(next);
            }}
          >
            加列
          </Button>
          <Button
            variant="neutral"
            size="sm"
            disabled={!canRemoveCol}
            onClick={() => {
              const next = tableWithRemovedColumn(block);
              if (next) onChange(next);
            }}
          >
            删列
          </Button>
          <Button
            variant="neutral"
            size="sm"
            disabled={!canAddRow}
            onClick={() => {
              const next = tableWithAddedRow(block);
              if (next) onChange(next);
            }}
          >
            加行
          </Button>
          <Button
            variant="neutral"
            size="sm"
            disabled={!canRemoveRow}
            onClick={() => {
              const next = tableWithRemovedRow(block);
              if (next) onChange(next);
            }}
          >
            删行
          </Button>
        </div>
      )}
    </div>
  );
}

function ChartBarPreview({ items }: { items: DocChartBlock["items"] }) {
  const max = Math.max(...items.map((i) => i.value), 1);
  const barClass = "flex min-w-0 flex-1 flex-col items-center gap-1";
  return (
    <div
      className="flex h-28 items-end gap-2 border-b border-border pb-1 pt-2"
      aria-label="柱形预览"
      role="img"
    >
      {items.map((item, i) => (
        // biome-ignore lint/suspicious/noArrayIndexKey: 柱形按位置对应数据行，索引即稳定身份。
        <div key={i} className={barClass}>
          <div
            className="w-full max-w-12 rounded-t bg-primary/70 transition-[height]"
            style={{
              height: `${Math.round((item.value / max) * 100)}%`,
              minHeight: item.value > 0 ? "4px" : "0",
            }}
          />
          <span className="w-full truncate text-center text-xs text-muted-foreground">
            {item.label || "…"}
          </span>
        </div>
      ))}
    </div>
  );
}

function ChartBlockEditor({
  block,
  readOnly,
  onChange,
}: {
  block: DocChartBlock;
  readOnly: boolean;
  onChange: (next: DocChartBlock) => void;
}) {
  const canAdd = block.items.length < MAX_CHART_ITEMS;
  const canRemove = block.items.length > 1;

  return (
    <div className="flex flex-col gap-3 pr-8">
      <input
        value={block.title}
        readOnly={readOnly}
        onChange={(e) => onChange(chartWithTitle(block, e.target.value))}
        placeholder="图标题（可选）"
        aria-label="图标题"
        className="w-full bg-transparent text-sm font-medium text-foreground outline-none placeholder:text-muted-foreground"
      />
      <ChartBarPreview items={block.items} />
      <ul className="flex flex-col gap-2">
        {block.items.map((item, index) => (
          // biome-ignore lint/suspicious/noArrayIndexKey: 图例项按位置编辑，索引即稳定身份。
          <li key={index} className="flex flex-wrap items-center gap-2">
            <input
              value={item.label}
              readOnly={readOnly}
              onChange={(e) =>
                onChange(chartWithItemLabel(block, index, e.target.value))
              }
              placeholder={`项 ${index + 1}`}
              aria-label={`第 ${index + 1} 项名称`}
              className="min-w-[6rem] flex-1 bg-transparent px-1 py-0.5 text-sm text-foreground outline-none placeholder:text-muted-foreground"
            />
            <input
              type="number"
              min={0}
              step="any"
              value={item.value}
              readOnly={readOnly}
              onChange={(e) =>
                onChange(chartWithItemValue(block, index, e.target.value))
              }
              aria-label={`第 ${index + 1} 项数值`}
              className="w-24 bg-transparent px-1 py-0.5 text-sm text-foreground outline-none"
            />
          </li>
        ))}
      </ul>
      {readOnly ? null : (
        <div className="flex flex-wrap gap-2">
          <Button
            variant="neutral"
            size="sm"
            disabled={!canAdd}
            onClick={() => {
              const next = chartWithAddedItem(block);
              if (next) onChange(next);
            }}
          >
            加点
          </Button>
          <Button
            variant="neutral"
            size="sm"
            disabled={!canRemove}
            onClick={() => {
              const next = chartWithRemovedItem(block);
              if (next) onChange(next);
            }}
          >
            删点
          </Button>
        </div>
      )}
    </div>
  );
}
