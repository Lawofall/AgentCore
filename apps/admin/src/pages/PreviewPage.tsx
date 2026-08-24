import { ChatView } from "@/components/chat/ChatView";
import { ProcessLane } from "@/components/chat/ProcessLane";
import { resolveChatTurn } from "@/components/chat/chatTurn";
import { CollapsibleBody } from "@/components/conversation-replay/shared";
import { Page, PageHeader } from "@/components/ui/Page";
import { Select } from "@/components/ui/Select";
import { PREVIEW_FIXTURES, type PreviewFixture } from "@/preview/fixtures";
import { X } from "lucide-react";
import { useState } from "react";
import { useSearchParams } from "react-router-dom";

/**
 * Hidden offline preview (`/preview`) — pick a conformance vector and render
 * its golden `projected` through admin ChatView. No client fold, no frames.
 * Reach by URL; not in the sidebar.
 */
export function PreviewPage({
  fixtures = PREVIEW_FIXTURES,
}: {
  fixtures?: PreviewFixture[];
}) {
  const [searchParams, setSearchParams] = useSearchParams();
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const requested = searchParams.get("s");
  const current =
    fixtures.find((f) => f.name === requested) ?? fixtures[0] ?? null;
  const previewTurn = current
    ? resolveChatTurn({
        content: current.projected.content,
        projected: current.projected,
      })
    : null;
  const selectedRun =
    previewTurn?.runs.find((r) => r.id === selectedRunId) ?? null;

  function selectScenario(name: string) {
    setSelectedRunId(null);
    setSearchParams({ s: name }, { replace: true });
  }

  return (
    <Page>
      <PageHeader
        title="离线复盘预览"
        description="向量终态投影 · 与 protocol-conformance golden 同形。不回放中间帧。"
        note="隐藏路由，不进侧栏。现有会话复盘仍走 conversation-replay/。点协作图节点可打开队员过程坞。"
        filters={
          <Select
            aria-label="场景"
            className="min-w-[16rem]"
            value={current?.name ?? ""}
            options={fixtures.map((f) => ({
              value: f.name,
              label: f.name,
            }))}
            onChange={(e) => selectScenario(e.target.value)}
          />
        }
      />
      {current && (
        <p className="mb-4 text-sm text-muted-foreground">
          {current.description}
        </p>
      )}
      {current ? (
        <div className="flex min-w-0 items-start gap-0">
          <div className="mx-auto flex min-w-0 w-full max-w-3xl flex-1 flex-col gap-4">
            <div className="flex min-w-0 justify-end">
              <div className="min-w-0 max-w-[80%] rounded-xl rounded-br-none bg-muted px-4 py-3 text-muted-foreground text-sm">
                （预览向量 · 用户消息占位）
              </div>
            </div>
            <ChatView
              content={current.projected.content}
              projected={current.projected}
              selectedRunId={selectedRunId}
              onSelectRun={setSelectedRunId}
            />
          </div>
          {selectedRun && (
            <aside
              aria-label="队员过程"
              className="sticky top-4 ml-4 flex w-full max-w-[400px] shrink-0 flex-col border-border border-l bg-background pl-4 lg:w-[400px]"
            >
              <div className="mb-2 flex items-center justify-between gap-2">
                <span className="text-xs font-medium text-foreground">
                  {selectedRun.role || selectedRun.agentId || "队员"}
                </span>
                <button
                  type="button"
                  aria-label="关闭"
                  onClick={() => setSelectedRunId(null)}
                  className="rounded-md p-1 text-muted-foreground outline-none hover:bg-muted hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
                >
                  <X size={14} />
                </button>
              </div>
              {selectedRun.task && (
                <p className="mb-3 text-sm text-muted-foreground">
                  {selectedRun.task}
                </p>
              )}
              {(() => {
                const processHasContent = selectedRun.process.some(
                  (s) => s.kind === "content",
                );
                const bodyBlock = selectedRun.outputSummary ? (
                  <CollapsibleBody content={selectedRun.outputSummary} />
                ) : null;
                const showLane =
                  selectedRun.process.length > 0 ||
                  (!processHasContent && bodyBlock);
                if (!showLane) {
                  if (!selectedRun.task) {
                    return (
                      <p className="text-muted-foreground text-xs italic">
                        该向量未带队员过程
                      </p>
                    );
                  }
                  return null;
                }
                return (
                  <ProcessLane
                    steps={selectedRun.process}
                    collapse={false}
                    hideContentSteps={false}
                    fallbackContent={!processHasContent ? bodyBlock : null}
                  />
                );
              })()}
            </aside>
          )}
        </div>
      ) : (
        <p className="text-sm text-muted-foreground">没有可用向量。</p>
      )}
    </Page>
  );
}
