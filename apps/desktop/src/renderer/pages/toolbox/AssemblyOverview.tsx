import { modelConfigApiErrorMessage } from "@/components/llm/ModelKeyForm";
import { ConfirmDialog, IconButton } from "@/components/ui";
import { SimpleTooltip } from "@/components/ui/tooltip";
import { isToolboxCapsule, officialHint } from "@/lib/assemblyLock";
import { PROMPT_ALWAYS_LABEL } from "@/lib/promptShelfTile";
import { conversationKeys, llmModelProfileKeys } from "@/lib/queryKeys";
import { notifySuccess } from "@/lib/toast";
import { cn } from "@/lib/utils";
import { useEditingAssembly } from "@/pages/toolbox/useEditingAssembly";
import {
  type LlmModelProfileView,
  deleteLlmModelProfile,
} from "@/services/llmModelProfiles";
import { useQueryClient } from "@tanstack/react-query";
import { Plus, Trash2 } from "lucide-react";
import { type ReactNode, useEffect, useRef, useState } from "react";

export function toolOffLine(off: number): string {
  return off === 0 ? "都开" : `关了 ${off} 把`;
}

export function envelopeOffLine(off: number): string {
  return off === 0 ? "都在" : `关了 ${off} 行`;
}

export function factoryOffLine(off: number, total: number): string {
  if (total <= 0 || off <= 0) return "出厂 开";
  if (off >= total) return "出厂 关";
  return `出厂 关了 ${off} 本`;
}

export function assemblyPromptSummary(
  roster: string,
  omittedCount: number,
  total = omittedCount,
): string {
  if (roster === "加载中…" || roster === "没读到") return roster;
  return `${roster} · ${factoryOffLine(omittedCount, total)}`;
}

export function promptRosterLine(counts: {
  always: number;
  onDemand: number;
  paths: number;
}): string {
  const base = `${PROMPT_ALWAYS_LABEL} ${counts.always} · 按需 ${counts.onDemand}`;
  return counts.paths > 0 ? `${base} · 碰到文件 ${counts.paths}` : base;
}

export function plugEnableLine(count: number): string {
  return `启用 ${count}`;
}

export function countPromptModes(documents: readonly { applyMode: string }[]): {
  always: number;
  onDemand: number;
  paths: number;
} {
  let always = 0;
  let onDemand = 0;
  let paths = 0;
  for (const doc of documents) {
    if (doc.applyMode === "always") always += 1;
    else if (doc.applyMode === "paths") paths += 1;
    else onDemand += 1;
  }
  return { always, onDemand, paths };
}

/**
 * 标准, 完整, and owned names on one row. 极简 is not a capsule here; pick
 * it in the composer. Another name switches which assembly is in use.
 * Official chips stay recipes: click pins them, click again does nothing.
 * An owned chip, clicked again, renames in place. New copies 标准. Delete
 * is an icon on that same row. Picking a name switches this conversation,
 * or the next new chat when none is open. Model lives in the composer.
 */
export function AssemblyOverview({
  renderNames,
}: {
  /**
   * Pins the name row. `actions` is new / delete;
   * the shelf places that cluster to the left of 市场.
   */
  renderNames?: (names: ReactNode, actions?: ReactNode) => ReactNode;
}) {
  const queryClient = useQueryClient();
  const { profile, profiles, pending, loading, select, create, rename } =
    useEditingAssembly();
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [pendingDelete, setPendingDelete] =
    useState<LlmModelProfileView | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [renameNonce, setRenameNonce] = useState(0);

  // biome-ignore lint/correctness/useExhaustiveDependencies: profile id is the reset key when the open assembly changes
  useEffect(() => {
    setRenaming(false);
  }, [profile?.id]);

  if (loading && !profile) {
    const names = <p className="text-sm text-muted-foreground">加载中…</p>;
    if (renderNames) return <div>{renderNames(names)}</div>;
    return names;
  }
  if (!profile) {
    const names = (
      <div className="flex min-w-0 flex-1 flex-wrap items-center gap-2">
        <p className="text-sm text-muted-foreground">还没有能力。</p>
        <AssemblyCreateButton
          disabled={pending}
          onClick={() => void create()}
        />
      </div>
    );
    if (renderNames) return <div>{renderNames(names)}</div>;
    return names;
  }

  const presets = profiles.filter(
    (row) => row.kind === "system" && isToolboxCapsule(row),
  );
  const mine = profiles.filter((row) => row.kind === "user");
  const confirmDelete = async () => {
    if (!pendingDelete) return;
    const target = pendingDelete;
    setDeleting(true);
    try {
      await deleteLlmModelProfile(target.id);
      notifySuccess(`已删除「${target.name}」`);
      setDeleteError(null);
      setPendingDelete(null);
      void queryClient.invalidateQueries({
        queryKey: llmModelProfileKeys.list,
      });
      void queryClient.invalidateQueries({
        queryKey: conversationKeys.grouped,
      });
    } catch (err) {
      setDeleteError(modelConfigApiErrorMessage(err, "删除失败，请重试"));
    } finally {
      setDeleting(false);
    }
  };

  const chipPending = pending || deleting;
  const commitRename = async (name: string) => {
    const ok = await rename(name);
    if (ok) setRenaming(false);
    else setRenameNonce((n) => n + 1);
  };

  const actions = (
    <div
      className="flex items-center gap-0.5"
      data-testid="assembly-copy-actions"
    >
      <AssemblyCreateButton
        disabled={chipPending}
        onClick={() => void create()}
      />
      {profile.kind !== "system" ? (
        <CopyIconButton
          label="删除这份"
          disabled={deleting}
          onClick={() => setPendingDelete(profile)}
        >
          <Trash2 size={16} aria-hidden />
        </CopyIconButton>
      ) : null}
    </div>
  );

  const names = (
    <div className="flex min-w-48 flex-1 flex-wrap items-center gap-2">
      {presets.map((row) => (
        <AssemblyNameChip
          key={row.id}
          row={row}
          selected={row.id === profile.id}
          pending={chipPending}
          face={row.id === profile.id}
          renaming={renaming}
          renameNonce={renameNonce}
          onBeginRename={() => setRenaming(true)}
          onCommitRename={(name) => void commitRename(name)}
          onCancelRename={() => setRenaming(false)}
          onSelect={() => {
            setRenaming(false);
            void select(row.id);
          }}
        />
      ))}
      {mine.map((row) => (
        <AssemblyNameChip
          key={row.id}
          row={row}
          selected={row.id === profile.id}
          pending={chipPending}
          face={row.id === profile.id}
          renaming={renaming}
          renameNonce={renameNonce}
          onBeginRename={() => setRenaming(true)}
          onCommitRename={(name) => void commitRename(name)}
          onCancelRename={() => setRenaming(false)}
          onSelect={() => {
            setRenaming(false);
            void select(row.id);
          }}
        />
      ))}
    </div>
  );

  const deleteChrome = (
    <>
      {deleteError ? (
        <p className="mt-3 text-xs text-muted-foreground" role="alert">
          {deleteError}
        </p>
      ) : null}
      <ConfirmDialog
        open={pendingDelete !== null}
        onOpenChange={(open) => {
          if (!open) setPendingDelete(null);
        }}
        title={`删除「${pendingDelete?.name ?? ""}」？`}
        description={pendingDelete ? assemblyDeleteDescription() : ""}
        confirmLabel="删除"
        tone="danger"
        busy={deleting}
        onConfirm={() => void confirmDelete()}
      />
    </>
  );

  const hint = officialHint(profile);
  const hintLine = hint ? (
    <p className="text-xs text-muted-foreground">{hint}</p>
  ) : null;

  if (renderNames) {
    return (
      <div>
        {renderNames(names, actions)}
        {hintLine ? <div className="pb-1">{hintLine}</div> : null}
        {deleteChrome}
      </div>
    );
  }

  return (
    <div className="w-full" data-testid="assembly-overview">
      <div
        className="flex flex-wrap items-start gap-x-3 gap-y-2"
        data-testid="assembly-name-row"
      >
        {names}
        <div className="ml-auto">{actions}</div>
      </div>
      {hintLine}
      {deleteChrome}
    </div>
  );
}

function AssemblyCreateButton({
  disabled,
  onClick,
}: {
  disabled?: boolean;
  onClick: () => void;
}) {
  return (
    <SimpleTooltip label="新建一份">
      <IconButton
        size="md"
        aria-label="新建一份"
        disabled={disabled}
        onClick={onClick}
      >
        <Plus size={16} aria-hidden />
      </IconButton>
    </SimpleTooltip>
  );
}

function CopyIconButton({
  label,
  disabled,
  onClick,
  children,
}: {
  label: string;
  disabled?: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <SimpleTooltip label={label}>
      <IconButton
        size="md"
        aria-label={label}
        disabled={disabled}
        onClick={onClick}
      >
        {children}
      </IconButton>
    </SimpleTooltip>
  );
}

function assemblyDeleteDescription(): string {
  return "还指着它的对话会改用剩下的另一份。";
}

function AssemblyNameField({
  initial,
  onCommit,
  onCancel,
}: {
  initial: string;
  onCommit: (value: string) => void;
  onCancel: () => void;
}) {
  const [value, setValue] = useState(initial);
  const ref = useRef<HTMLInputElement>(null);
  const done = useRef(false);

  useEffect(() => {
    ref.current?.focus();
    ref.current?.select();
  }, []);

  const finish = (fn: () => void) => {
    if (done.current) return;
    done.current = true;
    fn();
  };

  const commit = () => {
    const next = value.trim();
    if (!next || next === initial) onCancel();
    else onCommit(next);
  };

  return (
    <input
      ref={ref}
      value={value}
      aria-label="能力名称"
      onChange={(event) => setValue(event.target.value)}
      onKeyDown={(event) => {
        if (event.key === "Enter") {
          event.preventDefault();
          finish(commit);
        } else if (event.key === "Escape") {
          event.preventDefault();
          finish(onCancel);
        }
      }}
      onBlur={() => finish(commit)}
      style={{ width: `${Math.max(Array.from(value).length, 2)}em` }}
      className="h-8 max-w-48 min-w-8 bg-transparent text-sm text-inherit outline-none"
    />
  );
}

function AssemblyNameChip({
  row,
  selected,
  pending,
  face,
  renaming,
  renameNonce,
  onBeginRename,
  onCommitRename,
  onCancelRename,
  onSelect,
}: {
  row: LlmModelProfileView;
  selected: boolean;
  pending: boolean;
  face: boolean;
  renaming: boolean;
  renameNonce: number;
  onBeginRename: () => void;
  onCommitRename: (name: string) => void;
  onCancelRename: () => void;
  onSelect: () => void;
}) {
  const chipClass = cn(
    "inline-flex h-8 max-w-full items-center gap-1 rounded-full px-3 text-sm",
    selected
      ? "bg-accent text-accent-foreground"
      : "border border-border bg-card text-foreground hover:bg-muted",
  );
  if (renaming && face) {
    return (
      <span className={chipClass}>
        <AssemblyNameField
          key={renameNonce}
          initial={row.name}
          onCommit={onCommitRename}
          onCancel={onCancelRename}
        />
      </span>
    );
  }
  return (
    <button
      type="button"
      aria-pressed={selected}
      aria-label={row.name}
      disabled={pending}
      onClick={() => {
        if (row.kind === "system") {
          if (!selected) onSelect();
          return;
        }
        if (selected && face) onBeginRename();
        else onSelect();
      }}
      className={chipClass}
    >
      <span className="truncate">{row.name}</span>
    </button>
  );
}
