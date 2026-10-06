import { EnvelopeSwitchList } from "@/components/tools/EnvelopeSwitchList";
import { ToolSwitchList } from "@/components/tools/ToolSwitchList";
import { useCapabilities } from "@/components/tools/useCapabilities";
import { SearchField } from "@/components/ui";
import { cn } from "@/lib/utils";
import { AssemblyOverview } from "@/pages/toolbox/AssemblyOverview";
import { GuidelinesPage } from "@/pages/toolbox/GuidelinesPage";
import { hasLocalMcp } from "@/pages/toolbox/assemblyTabs";
import { APP_PATHS } from "@/pages/toolbox/manual/paths";
import { McpRoute } from "@/pages/toolbox/mcp/McpPage";
import { useEditingAssembly } from "@/pages/toolbox/useEditingAssembly";
import { useEffect, useState } from "react";
import { Link, useLocation } from "react-router-dom";

/**
 * One scroll for the assembly editor. Name chips and copy actions stick.
 * Model knobs scroll away; tools, envelopes, prompts, and plugs follow.
 */
export function AssemblyShelfPage() {
  const { hash } = useLocation();
  const { profile, conversationId, conversation } = useEditingAssembly();
  const [query, setQuery] = useState("");
  const [toolMiss, setToolMiss] = useState(false);
  const [promptMiss, setPromptMiss] = useState(false);
  const { data: capabilities, status: capabilityStatus } = useCapabilities();
  const catalogTools =
    capabilityStatus === "loading" ? undefined : (capabilities?.tools ?? []);
  const showPlugs = hasLocalMcp();
  const searching = query.trim() !== "";
  const hideTools = searching && toolMiss;
  const hidePrompts = searching && promptMiss;

  useEffect(() => {
    const id = hash.replace(/^#/, "");
    if (!id) return;
    document.getElementById(id)?.scrollIntoView({ block: "start" });
  }, [hash]);

  const boardKey = `${profile?.id ?? ""}:${conversationId ?? ""}`;

  return (
    <div data-testid="assembly-shelf" className="flex w-full flex-col gap-6">
      <h1 className="sr-only">工具箱</h1>
      <AssemblyOverview
        renderNames={(names, actions) => (
          <div
            className="sticky top-0 z-10 bg-background pt-2"
            data-testid="assembly-name-row"
          >
            <div className="flex flex-wrap items-start gap-x-3 gap-y-2 pb-3">
              {names}
              <div className="ml-auto flex max-w-full flex-wrap items-center justify-end gap-x-3 gap-y-2">
                {actions}
                <SearchField
                  aria-label="搜提示词、工具"
                  placeholder="搜提示词、工具"
                  value={query}
                  onValueChange={setQuery}
                  className="w-52"
                />
                <Link
                  to={APP_PATHS.toolbox.market}
                  className="inline-flex h-8 shrink-0 items-center text-sm text-muted-foreground transition-colors hover:text-foreground"
                >
                  市场
                </Link>
              </div>
            </div>
          </div>
        )}
      />
      {searching && toolMiss && promptMiss ? (
        <p className="text-sm text-muted-foreground">
          没有匹配「{query.trim()}」的条目。
        </p>
      ) : null}
      <section
        id="tools"
        className={cn("scroll-mt-24", hideTools && "hidden")}
        hidden={hideTools}
      >
        <ToolSwitchList
          key={boardKey}
          heading="工具"
          layout="shelf"
          scope={conversationId ? "conversation" : "draft"}
          conversationId={conversationId ?? undefined}
          conversationTitle={conversation?.title}
          readOnlyBoundary={conversation?.permissionAxes?.boundary === "read"}
          showBinding={false}
          showSessionDefault={false}
          showTurnHint={false}
          showBulk={!searching}
          catalogTools={catalogTools}
          query={query}
          onMissChange={setToolMiss}
        />
      </section>
      {searching ? null : (
        <section id="envelope" className="scroll-mt-24">
          <EnvelopeSwitchList
            key={boardKey}
            heading="信封"
            layout="cards"
            scope={conversationId ? "conversation" : "draft"}
            conversationId={conversationId ?? undefined}
            conversationTitle={conversation?.title}
            showBinding={false}
            showTurnHint={false}
            showBulk
          />
        </section>
      )}
      <section
        id="prompts"
        className={cn("scroll-mt-24", hidePrompts && "hidden")}
        hidden={hidePrompts}
      >
        <GuidelinesPage
          query={query}
          suppressMiss
          onMissChange={setPromptMiss}
        />
      </section>
      {showPlugs && !searching ? (
        <section id="plugs" className="scroll-mt-24">
          <McpRoute />
        </section>
      ) : null}
    </div>
  );
}
