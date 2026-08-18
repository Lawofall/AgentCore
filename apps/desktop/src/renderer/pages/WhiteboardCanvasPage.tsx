import { Button, IconButton } from "@/components/ui";
import { notifyInfo } from "@/lib/toast";
import {
  type BoardApplyResult,
  registerBoardApplier,
} from "@/services/boardOps";
import {
  type BoardRasterResult,
  registerBoardReader,
} from "@/services/boardRead";
import {
  type BoardDetail,
  type BoardScene,
  getBoard,
  renameBoard,
  saveBoardScene,
} from "@/services/boards";
import type { BoardOp } from "@/types/events";
import {
  type SceneElement,
  type Viewport,
  type WhiteboardApi,
  WhiteboardCanvas,
  parseScene,
  serializeScene,
} from "@/whiteboard";
import { ArrowLeft, ArrowUp, Loader2, Sparkles, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

type SaveStatus = "idle" | "saving" | "saved" | "error";

const STATUS_TEXT: Record<SaveStatus, string> = {
  idle: "",
  saving: "保存中…",
  saved: "已保存",
  error: "保存失败",
};

/** One board's canvas (AI协作白板.md §六 自研引擎 / §十 M1). Loads the scene from the
 * backend into the self-built {@link WhiteboardCanvas}, autosaves it back (debounced) with
 * a CAS ``baseline`` so a stale tab/device never clobbers — on conflict autosave pauses and
 * offers a reload (§七 不覆盖). The 2026-06-27 engine reversal replaced Excalidraw here; the
 * backend board_ops protocol is unchanged (§五.4), the applier now drives `applyOps`.
 *
 * AI 入口（老板命令栏 / 选区 AI 动作）暂下线，底部保留「即将上线」骨架；手动画布与
 * board_ops / board_read 注册仍保留。 */
export function WhiteboardCanvasPage() {
  const { boardId = "" } = useParams();
  const navigate = useNavigate();

  const [board, setBoard] = useState<BoardDetail | null>(null);
  const [loadError, setLoadError] = useState(false);
  const [status, setStatus] = useState<SaveStatus>("idle");
  const [conflict, setConflict] = useState(false);
  const [title, setTitle] = useState("");

  /** Text artifact body expand (crystallized `artifactCard`); file workspace preview
   * needs a board conversation and is gated while AI entry is offline. */
  const [textExpand, setTextExpand] = useState<{
    title: string;
    body: string;
  } | null>(null);

  // Imperative engine handle — the AI applier reads the live scene + pushes ops through it.
  const apiRef = useRef<WhiteboardApi | null>(null);
  // CAS version of the last load/save; sent as the next write's baseline.
  const versionRef = useRef(0);
  // Latest scene snapshot from the engine (the debounced flush reads this).
  // Tagged with the board the change came from so a stale timer cannot flush A onto B.
  const latestRef = useRef<{
    boardId: string;
    elements: SceneElement[];
    viewport: Viewport;
  } | null>(null);
  // Serialized elements of the last persisted/loaded state — skip no-op saves (and ignore
  // pan/zoom, which never reach onChange) so merely opening a board doesn't bump the version.
  const savedSceneRef = useRef("");
  const conflictRef = useRef(false);
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Generation + source id: same-component route switch must not apply A onto B,
  // and persist must not write unless the route still matches the loaded board.
  const loadGenRef = useRef(0);
  const loadedSourceIdRef = useRef<string | null>(null);

  const fetchBoard = useCallback(() => {
    const requestedId = boardId;
    const gen = ++loadGenRef.current;
    loadedSourceIdRef.current = null;
    latestRef.current = null;
    versionRef.current = 0;
    savedSceneRef.current = "";
    setBoard(null);
    setTitle("");
    setLoadError(false);
    setStatus("idle");
    setConflict(false);
    conflictRef.current = false;
    getBoard(requestedId)
      .then((b) => {
        if (gen !== loadGenRef.current) return;
        versionRef.current = b.version;
        loadedSourceIdRef.current = b.id;
        setTitle(b.title);
        setBoard(b);
      })
      .catch(() => {
        if (gen !== loadGenRef.current) return;
        setLoadError(true);
      });
  }, [boardId]);

  useEffect(() => {
    fetchBoard();
  }, [fetchBoard]);

  // Drop in-flight load + pending autosave when the route id changes (or unmount).
  // biome-ignore lint/correctness/useExhaustiveDependencies: deps 故意含 boardId，切换时跑 cleanup bump gen
  useEffect(() => {
    return () => {
      loadGenRef.current += 1;
      loadedSourceIdRef.current = null;
      latestRef.current = null;
      if (saveTimer.current) {
        clearTimeout(saveTimer.current);
        saveTimer.current = null;
      }
    };
  }, [boardId]);

  const initialData = useMemo(() => {
    if (!board) return null;
    const parsed = parseScene(board.scene);
    savedSceneRef.current = JSON.stringify(parsed.elements);
    return parsed;
  }, [board]);

  // CAS-write the scene. Shared by the debounced autosave (user edits) and the AI applier
  // (which needs the resulting version for its 回执). Returns the new version, or null on
  // conflict/error. A no-op (elements unchanged) returns the current version.
  const persistScene = useCallback(
    async (
      elements: SceneElement[],
      viewport: Viewport,
    ): Promise<number | null> => {
      const sourceId = loadedSourceIdRef.current;
      if (!sourceId || sourceId !== boardId) return null;
      const key = JSON.stringify(elements);
      if (key === savedSceneRef.current) return versionRef.current;
      setStatus("saving");
      try {
        const scene = serializeScene(
          elements,
          viewport,
        ) as unknown as BoardScene;
        const res = await saveBoardScene(sourceId, scene, versionRef.current);
        if (loadedSourceIdRef.current !== sourceId) {
          return res.conflict ? null : res.version;
        }
        if (res.conflict) {
          conflictRef.current = true;
          setConflict(true);
          setStatus("idle");
          return null;
        }
        versionRef.current = res.version;
        savedSceneRef.current = key;
        setStatus("saved");
        return res.version;
      } catch {
        if (loadedSourceIdRef.current !== sourceId) return null;
        setStatus("error");
        return null;
      }
    },
    [boardId],
  );

  const flush = useCallback(async () => {
    const snap = latestRef.current;
    if (!snap || conflictRef.current) return;
    if (
      snap.boardId !== boardId ||
      snap.boardId !== loadedSourceIdRef.current
    ) {
      return;
    }
    await persistScene(snap.elements, snap.viewport);
  }, [persistScene, boardId]);

  const handleChange = useCallback(
    (elements: SceneElement[], viewport: Viewport) => {
      latestRef.current = { boardId, elements, viewport };
      if (conflictRef.current) return;
      if (saveTimer.current) clearTimeout(saveTimer.current);
      saveTimer.current = setTimeout(() => void flush(), 1500);
    },
    [flush, boardId],
  );

  // The AI's hands on this canvas (AI协作白板.md §六 M2): apply the op batch through the
  // engine, then CAS-save so the 回执 carries the real version. Registered (keyed by board
  // id) only while THIS canvas is open, so board_op_required for a board no one is viewing
  // fails cleanly (the handler reports「画布未打开」). Capability kept; UI entry is offline.
  const applyOps = useCallback(
    async (ops: BoardOp[]): Promise<BoardApplyResult> => {
      const api = apiRef.current;
      if (!api) throw new Error("画布尚未就绪");
      if (conflictRef.current) {
        throw new Error("白板存在版本冲突，已暂停修改，请先重新加载");
      }
      const { created } = api.applyOps(ops);
      const version = await persistScene(api.getScene(), api.getViewport());
      if (version === null) throw new Error("白板保存失败（可能版本冲突）");
      return { applied: ops.length, created, version };
    },
    [persistScene],
  );

  useEffect(() => {
    if (!boardId) return;
    return registerBoardApplier(boardId, applyOps);
  }, [boardId, applyOps]);

  // The AI's eyes on this canvas (AI协作白板.md §九 读图): rasterize a subset of elements to a
  // PNG for the vision reader. Read-only (no CAS save). Registered (keyed by board id) only
  // while THIS canvas is open, so board_read for a board no one is viewing fails cleanly.
  const rasterize = useCallback(
    async (ids: string[]): Promise<BoardRasterResult> => {
      const api = apiRef.current;
      if (!api) throw new Error("画布尚未就绪");
      return api.rasterizeElements(ids);
    },
    [],
  );

  useEffect(() => {
    if (!boardId) return;
    return registerBoardReader(boardId, rasterize);
  }, [boardId, rasterize]);

  const handleArtifactActivate = useCallback((el: SceneElement) => {
    if (el.type !== "artifactCard") return;
    if (el.artifactKind === "file" && el.ref) {
      notifyInfo("白板 AI 即将上线，暂无法预览工作区文件");
      return;
    }
    setTextExpand({
      title: el.title ?? "产物",
      body: el.text ?? "",
    });
  }, []);

  const commitTitle = useCallback(async () => {
    const next = title.trim();
    if (!board || !next || next === board.title) {
      setTitle(board?.title ?? "");
      return;
    }
    if (board.id !== boardId || loadedSourceIdRef.current !== boardId) {
      setTitle(board.title);
      return;
    }
    try {
      const updated = await renameBoard(boardId, next);
      if (loadedSourceIdRef.current !== boardId) return;
      setBoard((b) => (b ? { ...b, title: updated.title } : b));
    } catch {
      setTitle(board.title);
    }
  }, [title, board, boardId]);

  if (loadError) {
    return (
      <div className="absolute inset-0 flex flex-col items-center justify-center gap-3">
        <p className="text-sm text-muted-foreground">白板加载失败</p>
        <div className="flex gap-2">
          <Button variant="neutral" onClick={() => navigate("/whiteboard")}>
            返回列表
          </Button>
          <Button variant="primary" onClick={fetchBoard}>
            重试
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className="absolute inset-0 flex flex-col">
      <header className="flex h-11 shrink-0 items-center gap-2 border-b border-border bg-background px-3">
        <IconButton
          aria-label="返回白板列表"
          onClick={() => navigate("/whiteboard")}
        >
          <ArrowLeft size={16} />
        </IconButton>
        <input
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          onBlur={() => void commitTitle()}
          onKeyDown={(e) => {
            if (e.key === "Enter") e.currentTarget.blur();
          }}
          placeholder="未命名白板"
          aria-label="白板标题"
          className="min-w-0 max-w-xs flex-1 rounded-lg bg-transparent px-2 py-1 text-sm font-medium text-foreground outline-none hover:bg-accent focus:bg-accent"
        />
        <span className="ml-auto text-xs text-muted-foreground">
          {STATUS_TEXT[status]}
        </span>
      </header>

      {conflict ? (
        <div className="flex shrink-0 items-center gap-3 border-b border-primary/30 bg-primary/10 px-3 py-2">
          <span className="text-xs text-foreground">
            此白板已在别处更新，为避免覆盖已暂停自动保存。
          </span>
          <Button
            variant="primary"
            size="sm"
            className="ml-auto"
            onClick={fetchBoard}
          >
            重新加载
          </Button>
        </div>
      ) : null}

      <div className="relative flex-1">
        {board && initialData ? (
          <WhiteboardCanvas
            key={board.id}
            ref={apiRef}
            initialElements={initialData.elements}
            initialViewport={initialData.viewport}
            onChange={handleChange}
            onArtifactActivate={handleArtifactActivate}
          />
        ) : (
          <div className="flex h-full items-center justify-center">
            <Loader2 className="animate-spin text-muted-foreground" size={24} />
          </div>
        )}
      </div>

      {/* 老板命令栏 · AI 入口下线：保留视觉骨架，不可发送 */}
      <footer className="flex shrink-0 items-end gap-2 border-t border-border bg-card px-4 py-3">
        <output
          className="flex max-h-28 min-h-[2.5rem] flex-1 items-center gap-2 rounded-xl border border-border bg-background px-3 py-2 text-sm text-muted-foreground"
          aria-label="向 AI 下达白板指令（即将上线）"
        >
          <Sparkles size={15} className="shrink-0 opacity-50" />
          <span>即将上线</span>
          <span className="ml-auto text-xs opacity-70">
            手动画布可用 · AI 协作稍后开放
          </span>
        </output>
        <IconButton
          aria-label="下达指令（即将上线）"
          tone="primary"
          disabled
          className="size-10 rounded-xl"
        >
          <ArrowUp size={18} />
        </IconButton>
      </footer>

      {textExpand ? (
        <div className="absolute inset-0 z-40 flex items-center justify-center bg-background/80 p-6 backdrop-blur-sm">
          <div className="flex max-h-[min(80vh,640px)] w-full max-w-lg flex-col rounded-xl border border-border bg-card shadow-lg">
            <header className="flex shrink-0 items-center justify-between gap-2 border-b border-border px-4 py-3">
              <h2 className="truncate text-sm font-semibold text-foreground">
                {textExpand.title}
              </h2>
              <IconButton aria-label="关闭" onClick={() => setTextExpand(null)}>
                <X size={16} />
              </IconButton>
            </header>
            <pre className="min-h-0 flex-1 overflow-auto whitespace-pre-wrap break-words p-4 text-sm text-foreground">
              {textExpand.body}
            </pre>
          </div>
        </div>
      ) : null}
    </div>
  );
}
