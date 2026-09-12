import { PageContainer } from "@/components/layout/PageContainer";
import { Button, Card, EmptyHint, PageHeader } from "@/components/ui";
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
  type DocSummary,
  createDoc,
  deleteDoc,
  listDocs,
} from "@/services/docs";
import {
  type FolderMeta,
  listFolders,
  listFoldersSharedWithMe,
} from "@/services/folders";
import { FileText, Loader2, Plus, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

function formatUpdated(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleString("zh-CN", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function writableCloudFolders(
  owned: FolderMeta[],
  shared: FolderMeta[],
): FolderMeta[] {
  const rows = [...owned, ...shared];
  return rows.filter((f) => f.mode === "cloud" && f.myRole !== "viewer");
}

export function DocsPage() {
  const navigate = useNavigate();
  const [docs, setDocs] = useState<DocSummary[] | null>(null);
  const [error, setError] = useState(false);
  const [creating, setCreating] = useState(false);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [folders, setFolders] = useState<FolderMeta[] | null>(null);
  const [folderId, setFolderId] = useState("");
  const [confirmingId, setConfirmingId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(false);
    try {
      setDocs(await listDocs());
    } catch {
      setError(true);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const openPicker = useCallback(async () => {
    setPickerOpen(true);
    setFolders(null);
    try {
      const [owned, shared] = await Promise.all([
        listFolders(),
        listFoldersSharedWithMe(),
      ]);
      const items = writableCloudFolders(owned, shared);
      setFolders(items);
      setFolderId(items[0]?.id ?? "");
    } catch {
      setFolders([]);
    }
  }, []);

  const handleCreate = useCallback(async () => {
    if (!folderId) return;
    setCreating(true);
    try {
      const doc = await createDoc({ folder_id: folderId });
      navigate(`/docs/${doc.id}`);
    } catch {
      setError(true);
      setCreating(false);
    }
  }, [folderId, navigate]);

  const handleDelete = useCallback(async (id: string) => {
    try {
      await deleteDoc(id);
      setDocs((prev) => prev?.filter((d) => d.id !== id) ?? null);
    } catch {
      setError(true);
    } finally {
      setConfirmingId(null);
    }
  }, []);

  const createButton = () => (
    <Button
      variant="primary"
      size="md"
      icon={<Plus size={16} />}
      onClick={() => void openPicker()}
    >
      新建文档
    </Button>
  );

  const noFolders = folders !== null && folders.length === 0;

  return (
    <PageContainer width="canvas">
      <PageHeader title="文档" action={createButton()} />

      {error ? (
        <div className="mt-8 rounded-xl border border-border bg-card p-6 text-center">
          <p className="text-sm text-muted-foreground">加载失败</p>
          <Button
            variant="neutral"
            className="mt-3"
            onClick={() => void load()}
          >
            重试
          </Button>
        </div>
      ) : docs === null ? (
        <div className="mt-16 flex justify-center">
          <Loader2 className="animate-spin text-muted-foreground" size={24} />
        </div>
      ) : docs.length === 0 ? (
        <EmptyHint
          className="mt-16"
          icon={<FileText className="text-muted-foreground/60" size={40} />}
          title="还没有文档"
          action={createButton()}
        />
      ) : (
        <div className="mt-8 grid grid-cols-2 gap-4 sm:grid-cols-3">
          {docs.map((doc) => (
            <Card
              key={doc.id}
              variant="interactive"
              className="group relative flex cursor-pointer flex-col gap-3 p-4 shadow-sm transition-shadow hover:shadow-md"
              onClick={() => navigate(`/docs/${doc.id}`)}
            >
              <div className="flex h-16 items-center justify-center rounded-lg bg-muted">
                <FileText className="text-muted-foreground/70" size={24} />
              </div>
              <div className="min-w-0">
                <h3 className="truncate text-sm font-medium text-foreground">
                  {doc.title}
                </h3>
                <p className="mt-1 text-xs text-muted-foreground">
                  {doc.folder_name} · 更新于 {formatUpdated(doc.updated_at)}
                </p>
              </div>
              {doc.can_write ? (
                confirmingId === doc.id ? (
                  <button
                    type="button"
                    className="absolute right-2 top-2 rounded-lg bg-destructive px-2 py-1 text-xs font-medium text-destructive-foreground hover:bg-destructive/90"
                    onClick={(e) => {
                      e.stopPropagation();
                      void handleDelete(doc.id);
                    }}
                  >
                    确认删除
                  </button>
                ) : (
                  <button
                    type="button"
                    aria-label="删除文档"
                    className="absolute right-2 top-2 hidden rounded-lg p-1.5 text-muted-foreground hover:bg-destructive/10 hover:text-destructive group-hover:block"
                    onClick={(e) => {
                      e.stopPropagation();
                      setConfirmingId(doc.id);
                    }}
                  >
                    <Trash2 size={14} />
                  </button>
                )
              ) : null}
            </Card>
          ))}
        </div>
      )}

      <Dialog open={pickerOpen} onOpenChange={setPickerOpen}>
        <DialogContent size="md">
          <DialogHeader>
            <DialogTitle>新建文档</DialogTitle>
            <DialogDescription>
              文档挂在云文件夹上，协作桌成员看见同一份。
            </DialogDescription>
          </DialogHeader>
          <DialogBody>
            {folders === null ? (
              <div className="flex justify-center py-6">
                <Loader2
                  className="animate-spin text-muted-foreground"
                  size={20}
                />
              </div>
            ) : noFolders ? (
              <p className="text-sm text-muted-foreground">
                还没有可写入的云文件夹。先到文件页新建一个云文件夹。
              </p>
            ) : (
              <label className="block text-sm">
                <span className="mb-1.5 block text-muted-foreground">
                  云文件夹
                </span>
                <select
                  className="h-8 w-full rounded-lg border border-border bg-background px-2.5 text-sm text-foreground focus:border-primary focus:outline-none focus:ring-1 focus:ring-ring"
                  value={folderId}
                  onChange={(e) => setFolderId(e.target.value)}
                >
                  {folders.map((f) => (
                    <option key={f.id} value={f.id}>
                      {f.name}
                    </option>
                  ))}
                </select>
              </label>
            )}
          </DialogBody>
          <DialogFooter>
            <Button variant="neutral" onClick={() => setPickerOpen(false)}>
              取消
            </Button>
            <Button
              variant="primary"
              disabled={creating || !folderId || noFolders}
              onClick={() => void handleCreate()}
            >
              {creating ? "创建中…" : "创建"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </PageContainer>
  );
}
