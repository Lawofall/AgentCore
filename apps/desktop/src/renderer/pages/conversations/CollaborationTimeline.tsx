import { Card, SectionLabel } from "@/components/ui";
import { conversationKeys } from "@/lib/queryKeys";
import { DEBATE_DIR, RESEARCH_DIR } from "@/lib/stageDirs";
import { filesFocusState } from "@/pages/conversations/constants";
import {
  type CollaborationTimelineItem,
  dossierSourceLabel,
  fetchCollaborationTimeline,
  formatActChain,
} from "@/services/collaborationTimeline";
import { wsListFiles } from "@/services/workspaces";
import { turnDetailPath } from "@/stores/ui";
import { useQuery } from "@tanstack/react-query";
import { FileText, GitBranch, Network } from "lucide-react";
import { useNavigate } from "react-router-dom";

function stageFiles(paths: string[], prefix: string): string[] {
  return paths
    .filter((p) => p.replace(/\\/g, "/").startsWith(prefix) && !p.endsWith("/"))
    .map((p) => p.replace(/\\/g, "/"))
    .sort();
}

/**
 * 文件夹筛选态顶部：协作时间线（会话 + 幕摘要）+ 阶段产物并列。
 * 约定文档引用条为路径级消费事实，非跨会话过程边。
 */
export function CollaborationTimelinePanel({
  folderId,
}: {
  folderId: string;
}) {
  const navigate = useNavigate();
  const timelineQ = useQuery({
    queryKey: conversationKeys.collaborationTimeline(folderId),
    queryFn: () => fetchCollaborationTimeline(folderId, { limit: 20 }),
    staleTime: 30_000,
  });
  const dossierQ = useQuery({
    queryKey: ["folder-dossier-snapshot", folderId],
    queryFn: async () => {
      const { files } = await wsListFiles(`folder:${folderId}`, {
        recursive: true,
      });
      const paths = files.filter((f) => !f.isDir).map((f) => f.path);
      return {
        research: stageFiles(paths, `${RESEARCH_DIR}/`),
        debate: stageFiles(paths, `${DEBATE_DIR}/`),
      };
    },
    // Local / preview may 409 or miss auth — keep last good data (incl. preview seed).
    retry: false,
    staleTime: 30_000,
  });

  const items = timelineQ.data?.items ?? [];
  const note =
    timelineQ.data?.dossier_refs_note ??
    "路径级约定文档消费事实（本场辩论开赛注入或会话内 file_read），非跨会话过程边";
  const research = dossierQ.data?.research ?? [];
  const debate = dossierQ.data?.debate ?? [];
  const loading = timelineQ.isLoading;

  return (
    <Card
      variant="muted"
      className="mb-3 shrink-0 bg-card/40 p-3"
      data-testid="collaboration-timeline"
    >
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <div className="mb-2 flex items-center gap-1.5">
            <Network size={14} className="shrink-0 text-muted-foreground" />
            <SectionLabel>文件夹协作时间线</SectionLabel>
            {timelineQ.data != null && (
              <span className="text-xs text-muted-foreground/60 tabular-nums">
                {timelineQ.data.total}
              </span>
            )}
          </div>
          {loading && (
            <p className="text-xs text-muted-foreground">加载协作摘要…</p>
          )}
          {!loading && items.length === 0 && (
            <p className="text-xs text-muted-foreground">
              此文件夹尚无带协作图的会话
            </p>
          )}
          <ul className="space-y-1.5">
            {items.map((it) => (
              <TimelineRow
                key={it.conversation_id}
                item={it}
                onOpenGraph={() =>
                  navigate(
                    turnDetailPath(
                      it.conversation_id,
                      it.host_turn_id,
                      "graph",
                    ),
                  )
                }
                onOpenFile={() => navigate("/files", filesFocusState(folderId))}
              />
            ))}
          </ul>
          {items.some((i) => (i.dossier_refs?.length ?? 0) > 0) && (
            <p className="mt-2 text-xs text-muted-foreground/70">{note}</p>
          )}
        </div>

        <aside className="w-44 shrink-0 border-l border-border/60 pl-3">
          <div className="mb-1.5 flex items-center gap-1">
            <FileText size={12} className="text-muted-foreground" />
            <SectionLabel>阶段产物</SectionLabel>
          </div>
          <DossierGroup
            label={`${RESEARCH_DIR}/`}
            paths={research}
            onOpen={() => navigate("/files", filesFocusState(folderId))}
          />
          <DossierGroup
            label={`${DEBATE_DIR}/`}
            paths={debate}
            onOpen={() => navigate("/files", filesFocusState(folderId))}
          />
          {research.length === 0 &&
            debate.length === 0 &&
            !dossierQ.isLoading && (
              <p className="text-xs text-muted-foreground/70">暂无阶段产物</p>
            )}
        </aside>
      </div>
    </Card>
  );
}

function TimelineRow({
  item,
  onOpenGraph,
  onOpenFile,
}: {
  item: CollaborationTimelineItem;
  onOpenGraph: () => void;
  onOpenFile: () => void;
}) {
  const chain = formatActChain(item.acts);
  const updated = item.updated_at
    ? new Date(item.updated_at).toLocaleString("zh-CN", {
        month: "numeric",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      })
    : "";

  return (
    <li>
      <button
        type="button"
        onClick={onOpenGraph}
        className="flex w-full items-start gap-2 rounded-lg px-2 py-1.5 text-left transition-colors hover:bg-accent"
        data-testid="collaboration-timeline-row"
      >
        <GitBranch
          size={14}
          className="mt-0.5 shrink-0 text-muted-foreground"
        />
        <span className="min-w-0 flex-1">
          <span className="flex items-baseline gap-2">
            <span className="truncate text-sm font-medium text-foreground">
              {item.title?.trim() || "未命名对话"}
            </span>
            {updated && (
              <span className="shrink-0 text-xs text-muted-foreground tabular-nums">
                {updated}
              </span>
            )}
          </span>
          {chain && (
            <span className="mt-0.5 block truncate text-xs text-muted-foreground">
              {chain}
            </span>
          )}
          {(item.dossier_refs?.length ?? 0) > 0 && (
            <span className="mt-1 flex flex-wrap gap-1">
              {item.dossier_refs?.map((ref) => (
                // biome-ignore lint/a11y/useSemanticElements: nested in outer row button
                <span
                  role="link"
                  key={ref.path}
                  tabIndex={0}
                  onClick={(e) => {
                    e.stopPropagation();
                    onOpenFile();
                  }}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.stopPropagation();
                      onOpenFile();
                    }
                  }}
                  className="inline-flex max-w-full items-center gap-1 truncate rounded border border-border bg-muted/40 px-1.5 py-0.5 text-xs text-muted-foreground hover:border-foreground/30 hover:text-foreground"
                  title={`${ref.path} · ${dossierSourceLabel(ref.sources)}`}
                >
                  <span className="truncate">
                    {ref.path.startsWith(`${RESEARCH_DIR}/`)
                      ? ref.path.slice(RESEARCH_DIR.length + 1)
                      : ref.path}
                  </span>
                  <span className="shrink-0 opacity-70">
                    {dossierSourceLabel(ref.sources)}
                  </span>
                </span>
              ))}
            </span>
          )}
        </span>
      </button>
    </li>
  );
}

function DossierGroup({
  label,
  paths,
  onOpen,
}: {
  label: string;
  paths: string[];
  onOpen: () => void;
}) {
  if (paths.length === 0) return null;
  return (
    <div className="mb-2">
      <button
        type="button"
        onClick={onOpen}
        className="text-xs font-medium text-muted-foreground hover:text-foreground"
      >
        {label}
        <span className="ml-1 tabular-nums opacity-60">{paths.length}</span>
      </button>
      <ul className="mt-0.5 space-y-0.5">
        {paths.slice(0, 6).map((p) => (
          <li key={p}>
            <button
              type="button"
              onClick={onOpen}
              className="block w-full truncate text-left text-xs text-muted-foreground hover:text-foreground"
              title={p}
            >
              {p.split("/").pop()}
            </button>
          </li>
        ))}
        {paths.length > 6 && (
          <li className="text-xs text-muted-foreground/60">
            +{paths.length - 6}
          </li>
        )}
      </ul>
    </div>
  );
}
