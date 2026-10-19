import {
  SwitchboardBulkRow,
  SwitchboardSectionHeader,
} from "@/components/tools/SwitchboardBulkRow";
import { ASSEMBLY_CARD_GRID_CLASS, CatalogTile } from "@/components/ui";
import { Switch } from "@/components/ui/Switch";
import { patchConversationCache } from "@/hooks/useConversations";
import { refreshAssembliesAfterWrite } from "@/lib/assemblyWrite";
import { notifyError } from "@/lib/toast";
import { ASSEMBLY_TURN_HINT } from "@/pages/toolbox/assemblyTabs";
import { APP_PATHS } from "@/pages/toolbox/manual/paths";
import {
  type ToolSwitchboard,
  disabledAfterToggle,
  getComposerDraftDisabledTools,
  loadAccountToolSwitches,
  loadConversationToolSwitches,
  saveAccountToolSwitches,
  saveConversationToolSwitches,
  subscribeComposerDraftDisabledTools,
} from "@/services/toolSwitches";
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState, useSyncExternalStore } from "react";
import { useLocation, useNavigate } from "react-router-dom";

function overlay(
  board: ToolSwitchboard,
  disabled: readonly string[],
): ToolSwitchboard {
  const off = new Set(disabled);
  return {
    disabled: [...disabled],
    switches: board.switches.map((row) => ({ ...row, off: off.has(row.id) })),
  };
}

function bindingCaption(
  scope: "conversation" | "draft",
  title?: string | null,
): string {
  if (scope === "draft") {
    return "还没有这场。改的是下次新建会用的这份能力。";
  }
  const who = title?.trim() ? `「${title.trim()}」` : "这场";
  return `改的是${who}这份能力。正在生成的这一轮不换；从下一次进入回合生效。`;
}

/**
 * One switch per whole tool. The assembly page is one flat card per switch.
 * The composer menu stays one row per switch.
 */
export function ToolSwitchList({
  scope,
  conversationId,
  conversationTitle,
  readOnlyBoundary = false,
  showBinding = true,
  showTurnHint = false,
  showBulk = false,
  heading,
  layout = "rows",
  locked = false,
}: {
  scope: "conversation" | "draft";
  conversationId?: string;
  conversationTitle?: string | null;
  /** 这场只看：改文件 / 跑命令即使开着也不会上台。 */
  readOnlyBoundary?: boolean;
  /** 身份说明在「总」。输入区菜单仍写整句。 */
  showBinding?: boolean;
  /** 有这场时，开关下写这一轮不换。 */
  showTurnHint?: boolean;
  /** 组装页与节标题同一行。输入区菜单不放。 */
  showBulk?: boolean;
  /** 组装页节标题，与全部打开 / 全部关闭同一行。 */
  heading?: string;
  /** 组装页一排矮卡，不按目录拆开。输入区菜单仍是行。 */
  layout?: "rows" | "shelf";
  /** Official 完整: switches are the catalog, not an editor. */
  locked?: boolean;
}) {
  const navigate = useNavigate();
  const location = useLocation();
  const queryClient = useQueryClient();
  const draft = useSyncExternalStore(
    subscribeComposerDraftDisabledTools,
    getComposerDraftDisabledTools,
    () => null,
  );
  const [board, setBoard] = useState<ToolSwitchboard | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pendingId, setPendingId] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setError(null);
    setBoard(null);
    const load =
      scope === "draft"
        ? loadAccountToolSwitches()
        : conversationId
          ? loadConversationToolSwitches(conversationId)
          : Promise.reject(new Error("missing conversation"));
    void load
      .then((next) => {
        if (!alive) return;
        setBoard(next);
        if (scope === "conversation" && conversationId) {
          patchConversationCache(conversationId, {
            disabledTools: next.disabled,
          });
        }
      })
      .catch(() => {
        if (alive) setError("开关没加载上");
      });
    return () => {
      alive = false;
    };
  }, [scope, conversationId]);

  const displayed =
    board == null
      ? null
      : scope === "draft"
        ? overlay(board, draft ?? board.disabled)
        : board;

  const commit = async (disabled: string[], pendingKey: string) => {
    if (!displayed || pendingId || locked) return;
    if (scope === "draft") {
      setPendingId(pendingKey);
      setBoard(overlay(displayed, disabled));
      try {
        const saved = await saveAccountToolSwitches(disabled);
        setBoard(saved);
        await refreshAssembliesAfterWrite(queryClient);
      } catch (err) {
        setBoard(displayed);
        notifyError(err, "开关没保存上");
      } finally {
        setPendingId(null);
      }
      return;
    }
    const previous = displayed;
    setPendingId(pendingKey);
    setBoard(overlay(displayed, disabled));
    try {
      const saved = await saveConversationToolSwitches(
        conversationId ?? "",
        disabled,
      );
      setBoard(saved);
      if (conversationId) {
        patchConversationCache(conversationId, {
          disabledTools: saved.disabled,
        });
      }
      await refreshAssembliesAfterWrite(queryClient, conversationId);
    } catch (err) {
      setBoard(previous);
      notifyError(err, "开关没保存上");
    } finally {
      setPendingId(null);
    }
  };

  const toggle = (id: string, on: boolean) => {
    if (!displayed) return;
    void commit(disabledAfterToggle(displayed.disabled, id, on), id);
  };

  if (error) {
    return (
      <>
        <SwitchboardSectionHeader heading={heading} />
        <p className="text-xs text-muted-foreground" role="alert">
          {error}
        </p>
      </>
    );
  }
  if (!displayed) {
    return (
      <>
        <SwitchboardSectionHeader heading={heading} />
        <p className="text-sm text-muted-foreground">加载中…</p>
      </>
    );
  }

  const offCount = displayed.switches.filter((row) => row.off).length;

  return (
    <div data-testid="tool-switch-list">
      <SwitchboardSectionHeader heading={heading}>
        {showBulk && !locked ? (
          <SwitchboardBulkRow
            offCount={offCount}
            total={displayed.switches.length}
            disabled={pendingId !== null}
            onOpen={() => void commit([], "bulk")}
            onClose={() =>
              void commit(
                displayed.switches.map((row) => row.id),
                "bulk",
              )
            }
          />
        ) : null}
      </SwitchboardSectionHeader>
      {layout === "shelf" ? (
        <div className={ASSEMBLY_CARD_GRID_CLASS}>
          {displayed.switches.map((row) => {
            const withheld =
              readOnlyBoundary && (row.id === "files" || row.id === "run");
            return (
              <CatalogTile
                key={row.id}
                density="compact"
                title={row.label}
                description={row.summary}
                dim={row.off}
                onClick={() =>
                  navigate({
                    pathname: location.pathname,
                    search: `?tool=${encodeURIComponent(row.doc_tool)}`,
                    hash: "#tools",
                  })
                }
                accessory={
                  <Switch
                    checked={!row.off}
                    disabled={pendingId !== null || locked}
                    label={row.label}
                    onCheckedChange={(on) => void toggle(row.id, on)}
                  />
                }
              >
                {withheld ? (
                  <span className="text-xs text-muted-foreground">
                    这场只看，不会上台
                  </span>
                ) : null}
              </CatalogTile>
            );
          })}
        </div>
      ) : (
        displayed.switches.map((row) => {
          const withheld =
            readOnlyBoundary && (row.id === "files" || row.id === "run");
          return (
            <div
              key={row.id}
              className="flex items-center gap-3 border-b border-border/60 py-2"
            >
              <button
                type="button"
                className="min-w-0 flex-1 text-left"
                onClick={() =>
                  navigate({
                    pathname: APP_PATHS.toolbox.root,
                    search: `?tool=${encodeURIComponent(row.doc_tool)}`,
                    hash: "#tools",
                  })
                }
              >
                <span className="block text-sm text-foreground">
                  {row.label}
                </span>
                <span className="block text-xs text-muted-foreground">
                  {row.summary}
                </span>
                {withheld ? (
                  <span className="block text-xs text-muted-foreground">
                    这场只看，不会上台
                  </span>
                ) : null}
              </button>
              <Switch
                checked={!row.off}
                disabled={pendingId !== null || locked}
                label={row.label}
                onCheckedChange={(on) => void toggle(row.id, on)}
              />
            </div>
          );
        })
      )}
      {showBinding ? (
        <p className="mt-2 text-xs text-muted-foreground">
          {bindingCaption(scope, conversationTitle)}
        </p>
      ) : null}
      {showTurnHint && scope === "conversation" ? (
        <p className="mt-2 text-xs text-muted-foreground">
          {ASSEMBLY_TURN_HINT}
        </p>
      ) : null}
    </div>
  );
}
