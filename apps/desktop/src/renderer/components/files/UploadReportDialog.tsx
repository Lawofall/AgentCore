import { Button } from "@/components/ui";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { UPLOAD_MAX_FILES, type UploadReport } from "@/lib/folderUpload";

/**
 * 一次上传里没成的每一项——**逐条列全**，不折叠成一句「上传失败」。
 *
 * 整夹上传动辄上百个文件，只报个数字等于让用户自己去比对哪些没到。超限、逐项失败、
 * 按忽略规则跳过的，各自成组列出原始相对路径。
 */
export function UploadReportDialog({
  report,
  onClose,
}: {
  report: UploadReport | null;
  onClose: () => void;
}) {
  if (!report) return null;
  const { uploaded, failures, ignored, truncated } = report;
  return (
    <Dialog open onOpenChange={(next) => !next && onClose()}>
      <DialogContent size="md">
        <DialogHeader>
          <DialogTitle>上传结果</DialogTitle>
          <DialogDescription asChild>
            <div className="space-y-1 text-sm text-muted-foreground">
              <p>已上传 {uploaded} 个文件。</p>
              {truncated && (
                <p>
                  这次选择超过 {UPLOAD_MAX_FILES}{" "}
                  个文件，只取了前一批；剩下的请分批再传。
                </p>
              )}
            </div>
          </DialogDescription>
        </DialogHeader>

        <DialogBody className="max-h-[50vh] space-y-4">
          {failures.length > 0 && (
            <section className="space-y-1">
              <h3 className="text-sm font-medium">
                未上传（{failures.length}）
              </h3>
              <ul className="space-y-1">
                {failures.map((f) => (
                  <li
                    key={f.path}
                    className="flex items-baseline gap-2 text-xs"
                  >
                    <span className="min-w-0 flex-1 break-all">{f.path}</span>
                    <span className="shrink-0 text-muted-foreground">
                      {f.reason}
                    </span>
                  </li>
                ))}
              </ul>
            </section>
          )}

          {ignored.length > 0 && (
            <section className="space-y-1">
              <h3 className="text-sm font-medium">
                按忽略规则跳过（{ignored.length}）
              </h3>
              <p className="text-xs text-muted-foreground">
                这些在工作区里本就不会显示（依赖目录 / 构建产物 / 索引文件），
                传上去也看不到，所以没传。
              </p>
              <ul className="space-y-1">
                {ignored.map((path) => (
                  <li
                    key={path}
                    className="break-all text-xs text-muted-foreground"
                  >
                    {path}
                  </li>
                ))}
              </ul>
            </section>
          )}
        </DialogBody>

        <DialogFooter>
          <Button variant="outline" size="md" onClick={onClose}>
            知道了
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
