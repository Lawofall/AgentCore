import {
  SwitchboardBulkRow,
  SwitchboardSectionHeader,
} from "@/components/tools/SwitchboardBulkRow";
import { ASSEMBLY_CARD_GRID_CLASS, CatalogTile } from "@/components/ui";
import { Switch } from "@/components/ui/Switch";
import { notifyError } from "@/lib/toast";
import { ASSEMBLY_TURN_HINT } from "@/pages/toolbox/assemblyTabs";
import {
  type EnvelopeSwitchboard,
  draftEnvelopeBoard,
  getComposerDraftOmittedProjections,
  loadAccountEnvelopeSwitches,
  loadConversationEnvelopeSwitches,
  omittedAfterToggle,
  saveAccountEnvelopeSwitches,
  saveConversationEnvelopeSwitches,
  subscribeComposerDraftOmittedProjections,
} from "@/services/envelopeSwitches";
import { useEffect, useState, useSyncExternalStore } from "react";

function overlay(
  board: EnvelopeSwitchboard,
  omitted: readonly string[],
): EnvelopeSwitchboard {
  const off = new Set(omitted);
  return {
    omitted: [...omitted],
    switches: board.switches.map((row) => ({ ...row, off: off.has(row.id) })),
  };
}

function bindingCaption(
  scope: "conversation" | "draft",
  title?: string | null,
): string {
  const scopeLine =
    scope === "draft"
      ? "还没有这场。改的是星标装配，之后新建的对话都用它。"
      : `改的是${title?.trim() ? `「${title.trim()}」` : "这场"}这份装配。正在生成的这一轮不换；从下一次进入回合生效。`;
  return `${scopeLine}关掉只是不写这行，不改执行。不能改字。`;
}

/**
 * One row per envelope projection. On means the envelope still carries that
 * line. Off drops the line; execution, the boundary, and approvals stay.
 */
export function EnvelopeSwitchList({
  scope,
  conversationId,
  conversationTitle,
  showBinding = true,
  showTurnHint = false,
  showBulk = false,
  heading,
  layout = "rows",
}: {
  scope: "conversation" | "draft";
  conversationId?: string;
  conversationTitle?: string | null;
  /** 身份说明在「总」。 */
  showBinding?: boolean;
  /** 有这场时，开关下写这一轮不换。 */
  showTurnHint?: boolean;
  /** 组装页与节标题同一行。 */
  showBulk?: boolean;
  /** 组装页节标题，与全部打开 / 全部关闭同一行。 */
  heading?: string;
  /** 组装页是矮卡。输入区仍是行。 */
  layout?: "rows" | "cards";
}) {
  const draft = useSyncExternalStore(
    subscribeComposerDraftOmittedProjections,
    getComposerDraftOmittedProjections,
    () => null,
  );
  const [board, setBoard] = useState<EnvelopeSwitchboard | null>(
    scope === "draft" ? draftEnvelopeBoard() : null,
  );
  const [error, setError] = useState<string | null>(null);
  const [pendingId, setPendingId] = useState<string | null>(null);

  useEffect(() => {
    if (scope === "draft") {
      let alive = true;
      setBoard(draftEnvelopeBoard());
      setError(null);
      void loadAccountEnvelopeSwitches()
        .then((next) => {
          if (alive) setBoard(next);
        })
        .catch(() => {
          if (alive) setBoard(draftEnvelopeBoard());
        });
      return () => {
        alive = false;
      };
    }
    if (!conversationId) return;
    let alive = true;
    setError(null);
    setBoard(null);
    void loadConversationEnvelopeSwitches(conversationId)
      .then((next) => {
        if (alive) setBoard(next);
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
        ? overlay(board, draft ?? board.omitted)
        : board;

  const commit = async (omitted: string[], pendingKey: string) => {
    if (!displayed || pendingId) return;
    if (scope === "draft") {
      setPendingId(pendingKey);
      setBoard(overlay(displayed, omitted));
      try {
        const saved = await saveAccountEnvelopeSwitches(omitted);
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
    setBoard(overlay(displayed, omitted));
    try {
      const saved = await saveConversationEnvelopeSwitches(
        conversationId ?? "",
        omitted,
      );
      setBoard(saved);
    } catch (err) {
      setBoard(previous);
      notifyError(err, "开关没保存上");
    } finally {
      setPendingId(null);
    }
  };

  const toggle = (id: string, on: boolean) => {
    if (!displayed) return;
    void commit(omittedAfterToggle(displayed.omitted, id, on), id);
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
    <div data-testid="envelope-switch-list">
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
      {layout === "cards" ? (
        <div className={ASSEMBLY_CARD_GRID_CLASS}>
          {displayed.switches.map((row) => (
            <CatalogTile
              key={row.id}
              density="compact"
              title={row.label}
              description={row.summary}
              dim={row.off}
              accessory={
                <Switch
                  checked={!row.off}
                  disabled={pendingId !== null}
                  label={row.label}
                  onCheckedChange={(on) => void toggle(row.id, on)}
                />
              }
            />
          ))}
        </div>
      ) : (
        displayed.switches.map((row) => (
          <div
            key={row.id}
            className="flex items-center gap-3 border-b border-border/60 py-2"
          >
            <div className="min-w-0 flex-1">
              <span className="block text-sm text-foreground">{row.label}</span>
              <span className="block text-xs text-muted-foreground">
                {row.summary}
              </span>
            </div>
            <Switch
              checked={!row.off}
              disabled={pendingId !== null}
              label={row.label}
              onCheckedChange={(on) => void toggle(row.id, on)}
            />
          </div>
        ))
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
