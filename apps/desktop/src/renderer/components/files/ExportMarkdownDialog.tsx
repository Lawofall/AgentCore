import { Button, IconButton } from "@/components/ui";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { SimpleTooltip } from "@/components/ui/tooltip";
import type { DocLayout, FileSource, MarkdownExportFormat } from "@/lib/fileSource";
import {
  canExportMarkdown,
  exportWorkspaceMarkdown,
} from "@/lib/markdownExport";
import { FileType } from "lucide-react";
import { useEffect, useState } from "react";

const LAYOUTS: { id: DocLayout; title: string; hint: string }[] = [
  { id: "standard", title: "技术报告", hint: "说明、方案；无首行缩进" },
  { id: "official", title: "正式文书", hint: "首行缩进、公文页边距" },
];

const FORMATS: { id: MarkdownExportFormat; title: string }[] = [
  { id: "docx", title: "Word" },
  { id: "pdf", title: "PDF" },
];

/**
 * 文件树「导出」与文件 tab 动作条共用：先选 Word/PDF，再点档位即导出。
 * 动词已在入口说完，档位不再二次确认。取消 / Esc / 点遮罩只关窗。
 */
export function ExportMarkdownDialog({
  open,
  fileName,
  onOpenChange,
  onPick,
}: {
  open: boolean;
  fileName: string;
  onOpenChange: (open: boolean) => void;
  onPick: (format: MarkdownExportFormat, layout: DocLayout) => void;
}) {
  const [format, setFormat] = useState<MarkdownExportFormat>("docx");
  useEffect(() => {
    if (open) setFormat("docx");
  }, [open]);
  const stem = fileName.replace(/\.md$/i, "");
  const ext = format === "pdf" ? "pdf" : "docx";
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent size="md">
        <DialogHeader>
          <DialogTitle>导出</DialogTitle>
          <DialogDescription>
            同目录生成 {stem}.{ext}
          </DialogDescription>
        </DialogHeader>
        <DialogBody>
          <div className="mb-3 flex gap-2">
            {FORMATS.map((item) => (
              <Button
                key={item.id}
                type="button"
                variant={format === item.id ? "primary" : "outline"}
                size="md"
                className="flex-1"
                onClick={() => setFormat(item.id)}
              >
                {item.title}
              </Button>
            ))}
          </div>
          <div className="flex flex-col gap-2">
            {LAYOUTS.map((item) => (
              <Button
                key={item.id}
                type="button"
                variant="outline"
                size="md"
                className="h-auto w-full flex-col items-start justify-start gap-0.5 py-2 text-left"
                onClick={() => onPick(format, item.id)}
              >
                <span className="text-sm font-medium text-foreground">
                  {item.title}
                </span>
                <span className="text-xs font-normal text-muted-foreground">
                  {item.hint}
                </span>
              </Button>
            ))}
          </div>
        </DialogBody>
        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            size="md"
            onClick={() => onOpenChange(false)}
          >
            取消
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** 文件 tab 动作条：打开同一套导出对话框。 */
export function MarkdownExportControl({
  source,
  path,
  name,
}: {
  source: FileSource;
  path: string;
  name: string;
}) {
  const [open, setOpen] = useState(false);
  if (!canExportMarkdown(source, path)) return null;
  return (
    <>
      <SimpleTooltip label="导出 Word / PDF">
        <IconButton
          onClick={() => setOpen(true)}
          aria-label="导出 Word / PDF"
        >
          <FileType size={14} />
        </IconButton>
      </SimpleTooltip>
      <ExportMarkdownDialog
        open={open}
        fileName={name}
        onOpenChange={setOpen}
        onPick={(format, layout) => {
          setOpen(false);
          void exportWorkspaceMarkdown(source, path, format, layout);
        }}
      />
    </>
  );
}
