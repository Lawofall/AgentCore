import {
  SwitchboardBulkRow,
  SwitchboardSectionHeader,
} from "@/components/tools/SwitchboardBulkRow";
import { ASSEMBLY_CARD_GRID_CLASS, CatalogTile } from "@/components/ui";
import { Switch } from "@/components/ui/Switch";
import { SimpleTooltip } from "@/components/ui/tooltip";
import { patchConversationCache } from "@/hooks/useConversations";
import { notifyError, notifySuccess } from "@/lib/toast";
import { ASSEMBLY_TURN_HINT } from "@/pages/toolbox/assemblyTabs";
import { APP_PATHS } from "@/pages/toolbox/manual/paths";
import type { CapabilityTool } from "@/services/capabilities";
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
    return "还没有这场。改的是星标装配，之后新建的对话都用它。";
  }
  const who = title?.trim() ? `「${title.trim()}」` : "这场";
  return `改的是${who}这份装配。正在生成的这一轮不换；从下一次进入回合生效。`;
}

function switchMatchesQuery(
  row: { label: string; summary: string; tools?: readonly string[] },
  query: string,
  catalog: readonly CapabilityTool[] | undefined,
): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  const own = [row.label, row.summary, ...(row.tools ?? [])]
    .join("\n")
    .toLowerCase();
  if (own.includes(q)) return true;
  if (!catalog) return false;
  const names = new Set(row.tools ?? []);
  return catalog.some((tool) => {
    if (!names.has(tool.name)) return false;
    return [tool.name, tool.summary, tool.blurb, tool.description]
      .join("\n")
      .toLowerCase()
      .includes(q);
  });
}

/**
 * One switch per whole tool. The assembly page is one flat card per switch.
 * The composer menu stays one row per switch.
 * 「设为新会话默认」 is the only write to the account.
 */
export function ToolSwitchList({
  scope,
  conversationId,
  conversationTitle,
  readOnlyBoundary = false,
  showBinding = true,
  showSessionDefault = true,
  showTurnHint = false,
  showBulk = false,
  heading,
  layout = "rows",
  catalogTools,
  query = "",
  onMissChange,
}: {
  scope: "conversation" | "draft";
  conversationId?: string;
  conversationTitle?: string | null;
  /** 这场只看：改文件 / 跑命令即使开着也不会上台。 */
  readOnlyBoundary?: boolean;
  /** 身份说明在「总」。输入区菜单仍写整句。 */
  showBinding?: boolean;
  /** 工具箱用「总」的星标。输入区菜单仍保留这一行。 */
  showSessionDefault?: boolean;
  /** 有这场时，开关下写这一轮不换。 */
  showTurnHint?: boolean;
  /** 组装页与节标题同一行。输入区菜单不放。 */
  showBulk?: boolean;
  /** 组装页节标题，与全部打开 / 全部关闭同一行。 */
  heading?: string;
  /** 组装页一排矮卡，不按目录拆开。输入区菜单仍是行。 */
  layout?: "rows" | "shelf";
  /** Shelf only. Matches a card when the query hits a member manual. */
  catalogTools?: readonly CapabilityTool[];
  /** Shelf only. Filters the switch cards. */
  query?: string;
  /** Shelf only. True when a query is set and nothing in this section matches. */
  onMissChange?: (miss: boolean) => void;
}) {
  const navigate = useNavigate();
  const location = useLocation();
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

  const shelfRows =
    layout === "shelf" && displayed
      ? displayed.switches.filter((row) =>
          switchMatchesQuery(row, query, catalogTools),
        )
      : [];
  const shelfMiss =
    layout === "shelf" &&
    displayed != null &&
    Boolean(query.trim()) &&
    shelfRows.length === 0;

  useEffect(() => {
    if (layout !== "shelf" || !onMissChange) return;
    onMissChange(shelfMiss);
  }, [layout, onMissChange, shelfMiss]);

  const commit = async (disabled: string[], pendingKey: string) => {
    if (!displayed || pendingId) return;
    if (scope === "draft") {
      setPendingId(pendingKey);
      setBoard(overlay(displayed, disabled));
      try {
        const saved = await saveAccountToolSwitches(disabled);
        setBoard(saved);
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

  const setAsSessionDefault = async () => {
    if (!displayed || pendingId) return;
    setPendingId("default");
    try {
      await saveAccountToolSwitches(displayed.disabled);
      const n = displayed.disabled.length;
      notifySuccess(n === 0 ? "新会话将默认全开" : `新会话将默认关掉 ${n} 样`);
    } catch (err) {
      notifyError(err, "设置默认失败");
    } finally {
      setPendingId(null);
    }
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
        {showBulk ? (
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
          {shelfRows.map((row) => {
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
                    disabled={pendingId !== null}
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
                disabled={pendingId !== null}
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
      {showSessionDefault ? (
        <div className="mt-2 border-t border-border/60 px-1 pt-2">
          <SimpleTooltip label="写入账户默认；只影响之后新建的对话">
            <span className="block">
              <button
                type="button"
                disabled={pendingId !== null}
                onClick={() => void setAsSessionDefault()}
                className={`w-full rounded-lg px-2.5 py-1.5 text-left text-xs font-medium ${
                  pendingId !== null
                    ? "cursor-not-allowed text-muted-foreground/50"
                    : "text-foreground hover:bg-accent/50"
                }`}
              >
                设为新会话默认
              </button>
            </span>
          </SimpleTooltip>
        </div>
      ) : null}
    </div>
  );
}
