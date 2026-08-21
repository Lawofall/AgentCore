import { FileTree } from "@/components/files/FileTree";
import { AgentCoreSection } from "@/components/files/fileWorkbench/AgentCoreSection";
import { EntriesSection } from "@/components/files/fileWorkbench/EntriesSection";
import { queryClient } from "@/lib/queryClient";
import { AGENTCORE_ROOT } from "@/lib/stageDirs";
import {
  FILES_PREVIEW_PROJECT_FOLDER_ID,
  FILES_PREVIEW_SCENES,
  type FilesPreviewSceneId,
  alwaysQuotaForScene,
  entriesForScene,
  filesPreviewSource,
} from "@/preview/filesScenes";
import { FlaskConical } from "lucide-react";
import { useSearchParams } from "react-router-dom";

const PROJECT_FOLDER_ID = FILES_PREVIEW_PROJECT_FOLDER_ID;

function seedFilesPreviewCaches(sceneId: FilesPreviewSceneId) {
  const quotas = alwaysQuotaForScene(sceneId);
  const entries = entriesForScene(sceneId);
  queryClient.setQueryData(["scope-entries", "global"], entries.global);
  queryClient.setQueryData(
    ["scope-entries", PROJECT_FOLDER_ID],
    entries.project,
  );
  queryClient.setQueryData(["always-quota", "global"], quotas.global);
  queryClient.setQueryData(["always-quota", PROJECT_FOLDER_ID], quotas.project);
}

/**
 * Offline UI preview for the AgentCore flat entries rail (`#/preview/files`).
 * Seeds React Query caches — no backend. Deep-link: `#/preview/files?s=files-quota-normal`.
 */
export function FilesPreviewPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const scenes = FILES_PREVIEW_SCENES;
  const requested = searchParams.get("s");
  const current = scenes.find((s) => s.id === requested) ?? scenes[0] ?? null;
  const selected = current?.id ?? null;
  if (selected) seedFilesPreviewCaches(selected);
  const select = (id: string) => setSearchParams({ s: id }, { replace: true });

  return (
    <div
      className="flex h-full min-h-0 w-full"
      data-preview-files={selected ?? ""}
    >
      <aside className="flex w-64 shrink-0 flex-col border-r border-border">
        <div className="flex items-center gap-2 border-b border-border px-4 py-3">
          <FlaskConical size={18} className="shrink-0 text-primary" />
          <div className="min-w-0 flex-1">
            <h1 className="truncate text-base font-semibold text-foreground">
              文件 · 条目轨预览
            </h1>
            <p className="text-xs text-muted-foreground">
              {scenes.length} 个场景 · 离线自检
            </p>
          </div>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto p-2">
          <ul className="space-y-0.5">
            {scenes.map((s) => (
              <li key={s.id}>
                <button
                  type="button"
                  onClick={() => select(s.id)}
                  className={`w-full rounded-lg px-3 py-2.5 text-left ${
                    selected === s.id
                      ? "bg-accent text-foreground"
                      : "text-muted-foreground hover:bg-accent hover:text-foreground"
                  }`}
                >
                  <span className="block truncate text-sm font-medium">
                    {s.title}
                  </span>
                  <span className="mt-0.5 block truncate text-xs opacity-70">
                    {s.description}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      </aside>
      <div className="min-h-0 min-w-0 flex-1 bg-background p-4">
        {/* w-72 = FileWorkbench 左栏默认宽度，好让截图能看出窄栏截断。 */}
        <div
          className="mx-auto h-full w-72 overflow-y-auto rounded-xl border border-border bg-card p-2 shadow-sm"
          data-files-rail
        >
          <AgentCoreSection
            scope={{ kind: "global" }}
            memoryActivePath={null}
            documentActivePath={
              selected === "files-quota-empty" ? null : "g-rule"
            }
            onOpenEntry={() => undefined}
            onEntryDeleted={() => undefined}
            onEntryRenamed={() => undefined}
            onOpenUpdates={() => undefined}
            forceOpen
          />
          <div className="my-3 border-t border-border" />
          <div className="px-2 pb-1 text-xs font-medium text-muted-foreground">
            示例文件夹
          </div>
          {/* 与生产同一挂法：全局设定钉顶；文件夹条目进树的 ``.agentcore`` 抽屉。 */}
          <FileTree
            source={filesPreviewSource}
            chrome={false}
            indent={14}
            activePath={null}
            onOpenFile={() => undefined}
            forceExpandPaths={[AGENTCORE_ROOT]}
            renderWorkroomLead={(indent) => (
              <EntriesSection
                scope={{ kind: "folder", folderId: PROJECT_FOLDER_ID }}
                memoryActivePath={null}
                documentActivePath={null}
                onOpen={() => undefined}
                onDeleted={() => undefined}
                onRenamed={() => undefined}
                indent={indent}
              />
            )}
          />
        </div>
      </div>
    </div>
  );
}
