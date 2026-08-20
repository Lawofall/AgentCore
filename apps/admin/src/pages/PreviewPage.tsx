import { ChatView } from "@/components/chat/ChatView";
import { Page, PageHeader } from "@/components/ui/Page";
import { Select } from "@/components/ui/Select";
import { PREVIEW_FIXTURES, type PreviewFixture } from "@/preview/fixtures";
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
  const requested = searchParams.get("s");
  const current =
    fixtures.find((f) => f.name === requested) ?? fixtures[0] ?? null;

  function selectScenario(name: string) {
    setSearchParams({ s: name }, { replace: true });
  }

  return (
    <Page>
      <PageHeader
        title="离线复盘预览"
        description="向量终态投影 · 与 protocol-conformance golden 同形。不回放中间帧。"
        note="隐藏路由，不进侧栏。现有会话复盘仍走 conversation-replay/。"
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
        <div className="flex min-w-0 flex-col gap-4">
          <div className="flex min-w-0 justify-end">
            <div className="min-w-0 max-w-[min(100%,36rem)] rounded-xl rounded-br-none border border-border/60 bg-muted/50 px-4 py-2.5 text-sm text-muted-foreground">
              （预览向量 · 用户消息占位）
            </div>
          </div>
          <ChatView
            content={current.projected.content}
            projected={current.projected}
          />
        </div>
      ) : (
        <p className="text-sm text-muted-foreground">没有可用向量。</p>
      )}
    </Page>
  );
}
