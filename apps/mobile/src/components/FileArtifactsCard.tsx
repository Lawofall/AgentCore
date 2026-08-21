// 「本回合产出文件」卡（前端UX设计.md §九「回合内文件呈现」，手机端全新实现，对标桌面
// components/chat/FileArtifactsCard.tsx 语义）。主清单只认路径验收态（delivery_status.artifacts）；
// 历史缺 delivery 时由 ChatPage 旁路 process/events。挂在答复正文下方；点任一可预览行 →
// 跳到该对话的文件页并直接打开预览。「查看改动」在卡内展开（无右坞）。
// 行尾修改时间：按路径向已有工作区 list 取 mtime_ms（对不上或缺字段就空着；sizeBytes 不画在行上）。
import { formatFileMtime } from "@/components/FileBrowser";
import { TurnFileChangesReview } from "@/components/TurnFileChangesReview";
import {
  artifactListingLookupKey,
  listingDesksFor,
  loadFileListingMeta,
} from "@/lib/artifactListingMeta";
import {
  type FileArtifact,
  type FileOp,
  hasChangePreviews,
} from "@/lib/fileArtifacts";
import { stageFileLabel } from "@/lib/stageDirs";
import {
  ArrowRight,
  ChevronDown,
  ChevronRight,
  ChevronUp,
  Diff,
  FilePlus,
  FolderOpen,
  type LucideIcon,
  Trash2,
  X,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

const OP_META: Record<
  FileOp,
  { label: string; Icon: LucideIcon; cls: string; preview: boolean }
> = {
  write: { label: "写入", Icon: FilePlus, cls: "art-write", preview: true },
  edit: { label: "编辑", Icon: FilePlus, cls: "art-edit", preview: true },
  delete: { label: "删除", Icon: Trash2, cls: "art-delete", preview: false },
  move: { label: "移动", Icon: ArrowRight, cls: "art-move", preview: true },
};

function artifactMetaLabel(
  meta: { sizeBytes?: number; mtimeMs?: number } | undefined,
): string | null {
  if (typeof meta?.mtimeMs === "number") return formatFileMtime(meta.mtimeMs);
  return null;
}

function rowVisual(artifact: FileArtifact): {
  Icon: LucideIcon;
  cls: string;
  badge: string | null;
  preview: boolean;
  badgeTitle?: string;
} {
  if (artifact.acceptance === "accepted") {
    return {
      Icon: FilePlus,
      cls: "art-neutral",
      badge: null,
      preview: true,
    };
  }
  if (artifact.acceptance === "rejected") {
    const detail =
      artifact.acceptanceDetail || artifact.acceptanceReason || undefined;
    return {
      Icon: X,
      cls: "art-rejected",
      badge: "未通过",
      preview: true,
      badgeTitle: detail,
    };
  }
  // 无验收态时：删除/移动仍标操作；写入/编辑不显示（勿用工具名冒充交付成功）。
  if (artifact.op === "delete" || artifact.op === "move") {
    const meta = OP_META[artifact.op];
    return {
      Icon: meta.Icon,
      cls: meta.cls,
      badge: meta.label,
      preview: meta.preview,
    };
  }
  return {
    Icon: FilePlus,
    cls: "art-neutral",
    badge: null,
    preview: true,
  };
}

function ArtifactBody({
  artifact,
  visual,
  metaLabel,
}: {
  artifact: FileArtifact;
  visual: ReturnType<typeof rowVisual>;
  metaLabel?: string | null;
}) {
  const dir = artifact.path.slice(
    0,
    artifact.path.length - artifact.name.length,
  );
  const stageLabel = stageFileLabel(artifact.path);
  return (
    <>
      <visual.Icon size={14} className={`artifact-icon ${visual.cls}`} />
      <span className="artifact-path">
        {artifact.op === "move" && artifact.fromPath ? (
          <span className="artifact-dir">{artifact.fromPath} → </span>
        ) : dir ? (
          <span className="artifact-dir">{dir}</span>
        ) : null}
        <span className="artifact-name">{artifact.name}</span>
      </span>
      {stageLabel && <span className="artifact-stage">{stageLabel}</span>}
      {visual.badge && (
        <span title={visual.badgeTitle} className={`artifact-op ${visual.cls}`}>
          {visual.badge}
        </span>
      )}
      {metaLabel ? <span className="artifact-meta">{metaLabel}</span> : null}
    </>
  );
}

function useArtifactListingMeta(
  artifacts: FileArtifact[],
  conversationId: string | null,
): ReadonlyMap<string, { sizeBytes?: number; mtimeMs?: number }> {
  const [byKey, setByKey] = useState<
    Map<string, { sizeBytes?: number; mtimeMs?: number }>
  >(() => new Map());
  const artifactsRef = useRef(artifacts);
  artifactsRef.current = artifacts;
  const deskSig = listingDesksFor(artifacts, conversationId)
    .map((d) => `${d.kind}:${d.id}`)
    .join("|");
  const pathSig = artifacts
    .map((a) => `${a.workspaceId ?? ""}:${a.path}`)
    .join("|");

  // deskSig/pathSig are the real inputs; array identity from ChatPage is unstable.
  // biome-ignore lint/correctness/useExhaustiveDependencies: signature deps
  useEffect(() => {
    const current = artifactsRef.current;
    const desks = listingDesksFor(current, conversationId);
    if (desks.length === 0) {
      setByKey((prev) => (prev.size === 0 ? prev : new Map()));
      return;
    }
    let cancelled = false;
    void Promise.all(
      desks.map(async (desk) => ({
        desk,
        map: await loadFileListingMeta(desk),
      })),
    ).then((loaded) => {
      if (cancelled) return;
      const next = new Map<string, { sizeBytes?: number; mtimeMs?: number }>();
      for (const a of current) {
        const lookup = artifactListingLookupKey(a, conversationId);
        if (!lookup) continue;
        const desk = a.workspaceId
          ? loaded.find(
              (x) => x.desk.kind === "ws" && x.desk.id === a.workspaceId,
            )
          : loaded.find(
              (x) => x.desk.kind === "conv" && x.desk.id === conversationId,
            );
        const meta = desk?.map.get(a.path);
        if (meta) next.set(lookup, meta);
      }
      setByKey((prev) => {
        if (next.size === 0 && prev.size === 0) return prev;
        return next;
      });
    });
    return () => {
      cancelled = true;
    };
  }, [conversationId, deskSig, pathSig]);

  return byKey;
}

export function FileArtifactsCard({
  artifacts,
  conversationId,
  messageId = null,
  reviewArtifacts,
}: {
  artifacts: FileArtifact[];
  conversationId: string | null;
  /** Assistant message id；有则「查看改动」可拉 A1+ 真 diff。 */
  messageId?: string | null;
  /** A1 工具参数预览源（process/events）；缺省回落 artifacts。 */
  reviewArtifacts?: FileArtifact[];
}) {
  const navigate = useNavigate();
  const listingMeta = useArtifactListingMeta(artifacts, conversationId);
  // 文件不多（≤4）默认展开一目了然；多了先收起，避免长清单淹没答复。
  const [expanded, setExpanded] = useState(artifacts.length <= 4);
  const [reviewOpen, setReviewOpen] = useState(false);

  if (artifacts.length === 0) return null;

  const reviewSource = reviewArtifacts ?? artifacts;
  const canReview =
    hasChangePreviews(reviewSource) || (!!conversationId && !!messageId);

  const open = (a: FileArtifact) => {
    if (!conversationId) return;
    // 有落地 desk → 跟桌面一样按 workspace 取字节（跨 desk 产物）；否则会话出生桌。
    if (a.workspaceId) {
      navigate(`/files/${encodeURIComponent(a.workspaceId)}`, {
        state: {
          openPath: a.path,
          fromConversationId: conversationId,
        },
      });
      return;
    }
    navigate(`/c/${conversationId}/files`, { state: { openPath: a.path } });
  };

  return (
    <div className="artifacts">
      <div className="artifacts-head-row">
        <button
          type="button"
          className="artifacts-head"
          onClick={() => setExpanded((v) => !v)}
        >
          <FolderOpen size={15} className="artifacts-folder" aria-hidden />
          <span className="artifacts-title">本回合产出文件</span>
          <span className="artifacts-count">{artifacts.length}</span>
          {expanded ? (
            <ChevronUp size={15} className="artifact-go" aria-hidden />
          ) : (
            <ChevronDown size={15} className="artifact-go" aria-hidden />
          )}
        </button>
        {canReview && (
          <button
            type="button"
            className="artifacts-review-btn"
            aria-label="查看改动"
            aria-expanded={reviewOpen}
            onClick={() => setReviewOpen((v) => !v)}
          >
            <Diff size={14} aria-hidden />
            查看改动
          </button>
        )}
      </div>
      {expanded && (
        <ul className="artifacts-list">
          {artifacts.map((a) => {
            const visual = rowVisual(a);
            const isDelete = a.op === "delete";
            const canOpen = visual.preview && !isDelete && !!conversationId;
            const lookup = artifactListingLookupKey(a, conversationId);
            const metaLabel = lookup
              ? artifactMetaLabel(listingMeta.get(lookup))
              : null;
            if (!canOpen) {
              return (
                <li
                  key={`${a.acceptance ?? a.op ?? "file"}:${a.path}`}
                  className="artifact-row artifact-static"
                >
                  <ArtifactBody
                    artifact={a}
                    visual={visual}
                    metaLabel={metaLabel}
                  />
                </li>
              );
            }
            return (
              <li key={`${a.acceptance ?? a.op ?? "file"}:${a.path}`}>
                <button
                  type="button"
                  className="artifact-row"
                  onClick={() => open(a)}
                  title={
                    stageFileLabel(a.path)
                      ? `在文件页查看约定文档 ${a.path}`
                      : `在工作区查看 ${a.path}`
                  }
                >
                  <ArtifactBody
                    artifact={a}
                    visual={visual}
                    metaLabel={metaLabel}
                  />
                  <ChevronRight size={14} className="artifact-go" aria-hidden />
                </button>
              </li>
            );
          })}
        </ul>
      )}
      {reviewOpen && (
        <TurnFileChangesReview
          artifacts={reviewSource}
          conversationId={conversationId}
          messageId={messageId}
        />
      )}
    </div>
  );
}
