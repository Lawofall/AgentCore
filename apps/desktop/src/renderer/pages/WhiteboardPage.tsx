import { PageContainer } from "@/components/layout/PageContainer";
import { Button, Card, EmptyHint, PageHeader } from "@/components/ui";
import {
  type BoardSummary,
  createBoard,
  deleteBoard,
  listBoards,
} from "@/services/boards";
import { Loader2, Plus, Presentation, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

// 顶部「白板」入口 = 账号级板列表：列出本人全部白板、建板、开板、删板。
// 白板不挂文件夹（AI协作白板.md 关键决策 · 否决 board ∈ folder）。
function formatUpdated(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleString("zh-CN", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function WhiteboardPage() {
  const navigate = useNavigate();
  const [boards, setBoards] = useState<BoardSummary[] | null>(null);
  const [error, setError] = useState(false);
  const [creating, setCreating] = useState(false);
  // 二次确认删除：首点亮「确认删除」、再点才删，避免误删（不用原生 confirm）。
  const [confirmingId, setConfirmingId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(false);
    try {
      setBoards(await listBoards());
    } catch {
      setError(true);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const handleCreate = useCallback(async () => {
    setCreating(true);
    try {
      const board = await createBoard();
      navigate(`/whiteboard/${board.id}`);
    } catch {
      setError(true);
      setCreating(false);
    }
  }, [navigate]);

  const handleDelete = useCallback(async (id: string) => {
    try {
      await deleteBoard(id);
      setBoards((prev) => prev?.filter((b) => b.id !== id) ?? null);
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
      disabled={creating}
      onClick={() => void handleCreate()}
    >
      {creating ? "创建中…" : "新建白板"}
    </Button>
  );

  return (
    <PageContainer width="canvas">
      <PageHeader title="白板" action={createButton()} />

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
      ) : boards === null ? (
        <div className="mt-16 flex justify-center">
          <Loader2 className="animate-spin text-muted-foreground" size={24} />
        </div>
      ) : boards.length === 0 ? (
        <EmptyHint
          className="mt-16"
          icon={<Presentation className="text-muted-foreground/60" size={40} />}
          title="还没有白板"
          action={createButton()}
        />
      ) : (
        <div className="mt-8 grid grid-cols-2 gap-4 sm:grid-cols-3">
          {boards.map((board) => (
            <Card
              key={board.id}
              variant="interactive"
              className="group relative flex cursor-pointer flex-col gap-3 p-4 shadow-sm transition-shadow hover:shadow-md"
              onClick={() => navigate(`/whiteboard/${board.id}`)}
            >
              <div className="flex h-16 items-center justify-center rounded-lg bg-muted">
                <Presentation className="text-muted-foreground/70" size={24} />
              </div>
              <div className="min-w-0">
                <h3 className="truncate text-sm font-medium text-foreground">
                  {board.title}
                </h3>
                <p className="mt-1 text-xs text-muted-foreground">
                  更新于 {formatUpdated(board.updated_at)}
                </p>
              </div>
              {confirmingId === board.id ? (
                <button
                  type="button"
                  className="absolute right-2 top-2 rounded-lg bg-destructive px-2 py-1 text-xs font-medium text-destructive-foreground hover:bg-destructive/90"
                  onClick={(e) => {
                    e.stopPropagation();
                    void handleDelete(board.id);
                  }}
                >
                  确认删除
                </button>
              ) : (
                <button
                  type="button"
                  aria-label="删除白板"
                  className="absolute right-2 top-2 hidden rounded-lg p-1.5 text-muted-foreground hover:bg-destructive/10 hover:text-destructive group-hover:block"
                  onClick={(e) => {
                    e.stopPropagation();
                    setConfirmingId(board.id);
                  }}
                >
                  <Trash2 size={14} />
                </button>
              )}
            </Card>
          ))}
        </div>
      )}
    </PageContainer>
  );
}
