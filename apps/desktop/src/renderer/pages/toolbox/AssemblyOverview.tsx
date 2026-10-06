import { modelConfigApiErrorMessage } from "@/components/llm/ModelKeyForm";
import { Button, ConfirmDialog, IconButton } from "@/components/ui";
import { SimpleTooltip } from "@/components/ui/tooltip";
import { foldedIntoPreset } from "@/lib/assemblyFold";
import { conversationKeys, llmModelProfileKeys } from "@/lib/queryKeys";
import { notifySuccess } from "@/lib/toast";
import { cn } from "@/lib/utils";
import { ModelAssemblySection } from "@/pages/toolbox/ModelAssemblySection";
import { useEditingAssembly } from "@/pages/toolbox/useEditingAssembly";
import {
  type LlmModelProfileView,
  deleteLlmModelProfile,
} from "@/services/llmModelProfiles";
import { useQueryClient } from "@tanstack/react-query";
import { Plus, Star, Trash2 } from "lucide-react";
import { type ReactNode, useEffect, useRef, useState } from "react";

export function toolOffLine(off: number): string {
  return off === 0 ? "都开" : `关了 ${off} 把`;
}

export function envelopeOffLine(off: number): string {
  return off === 0 ? "都在" : `关了 ${off} 行`;
}

export function assemblyPromptSummary(
  roster: string,
  omitFactoryCatalog: boolean | null | undefined,
): string {
  if (roster === "加载中…" || roster === "没读到") return roster;
  return `${roster} · ${omitFactoryCatalog ? "出厂 关" : "出厂 开"}`;
}

export function promptRosterLine(counts: {
  always: number;
  onDemand: number;
  paths: number;
}): string {
  const base = `必带 ${counts.always} · 按需 ${counts.onDemand}`;
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
 * Every assembly on one row. Preset and owned names are the same chip.
 * Another name switches which copy is being edited. The current name,
 * clicked again, renames in place.
 * New and delete are icon buttons on that same row, to the right of the names.
 * Model knobs sit under the names and write as they change.
 * The adopt-as-default action stays with the model.
 * `names` and those two actions stay in the sticky bar; `model` scrolls away.
 */
export function AssemblyOverview({
  renderNames,
}: {
  /**
   * Pins the name row. `actions` is new / delete;
   * the shelf places that cluster to the left of search.
   */
  renderNames?: (names: ReactNode, actions?: ReactNode) => ReactNode;
}) {
  const queryClient = useQueryClient();
  const {
    profile,
    profiles,
    conversationId,
    pending,
    loading,
    select,
    star,
    createFromCurrent,
    rename,
  } = useEditingAssembly();
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
        <p className="text-sm text-muted-foreground">还没有装配。</p>
        <CopyIconButton
          label="新建一份"
          disabled={pending}
          onClick={() => void createFromCurrent()}
        >
          <Plus size={16} aria-hidden />
        </CopyIconButton>
      </div>
    );
    if (renderNames) return <div>{renderNames(names)}</div>;
    return names;
  }

  const presets = profiles.filter((row) => row.kind === "system");
  const mine = profiles.filter(
    (row) => row.kind === "user" && !foldedIntoPreset(row, presets),
  );
  const canAdopt = Boolean(conversationId) && !profile.is_default;

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
      <CopyIconButton
        label="新建一份"
        disabled={chipPending}
        onClick={() => void createFromCurrent()}
      >
        <Plus size={16} aria-hidden />
      </CopyIconButton>
      <CopyIconButton
        label="删除这份"
        disabled={deleting}
        onClick={() => setPendingDelete(profile)}
      >
        <Trash2 size={16} aria-hidden />
      </CopyIconButton>
    </div>
  );

  const names = (
    <div className="flex min-w-48 flex-1 flex-wrap items-center gap-2">
      {presets.map((row) => {
        const covers = profiles.filter(
          (item) => foldedIntoPreset(item, presets)?.id === row.id,
        );
        return (
          <AssemblyNameChip
            key={row.id}
            row={row}
            selected={
              row.id === profile.id ||
              covers.some((item) => item.id === profile.id)
            }
            starred={row.is_default || covers.some((item) => item.is_default)}
            pending={chipPending}
            face={chipIsFace(row, profile, presets)}
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
        );
      })}
      {mine.map((row) => (
        <AssemblyNameChip
          key={row.id}
          row={row}
          selected={row.id === profile.id}
          pending={chipPending}
          face={chipIsFace(row, profile, presets)}
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

  const model = (
    <>
      <div data-testid="assembly-model-block">
        <ModelAssemblySection />
        {canAdopt ? (
          <div className="mt-3 flex justify-end">
            <Button
              variant="neutral"
              size="md"
              disabled={chipPending}
              onClick={() => void star(profile.id)}
            >
              以后新建也用这份
            </Button>
          </div>
        ) : null}
      </div>
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
        description={
          pendingDelete
            ? assemblyDeleteDescription(pendingDelete, profiles)
            : ""
        }
        confirmLabel="删除"
        tone="danger"
        busy={deleting}
        onConfirm={() => void confirmDelete()}
      />
    </>
  );

  if (renderNames) {
    return (
      <div>
        {renderNames(names, actions)}
        <div id="model" className="scroll-mt-24">
          {model}
        </div>
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
      <div id="model" className="mt-3 scroll-mt-24">
        {model}
      </div>
    </div>
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

function assemblyDeleteDescription(
  row: LlmModelProfileView,
  profiles: readonly LlmModelProfileView[],
): string {
  const presets = profiles.filter((item) => item.kind === "system");
  const folded = Boolean(foldedIntoPreset(row, presets));
  if (row.kind === "system" && row.is_default) {
    return "从这排拿掉。星标改到剩下的另一份，还指着它的对话也改过去。";
  }
  if (row.kind === "system") {
    return "从这排拿掉。还指着它的对话会改用星标那份。";
  }
  if (folded && row.is_default) {
    return "预置还在这排。星标改到剩下的另一份，还指着它的对话也改过去。";
  }
  if (folded) {
    return "预置还在这排。还指着它的对话会改用星标那份。";
  }
  if (row.is_default) {
    return "星标改到剩下的另一份，还指着它的对话也改过去。";
  }
  return "还指着它的对话会改用星标那份。";
}

function chipIsFace(
  row: LlmModelProfileView,
  profile: LlmModelProfileView,
  presets: readonly LlmModelProfileView[],
): boolean {
  if (row.id === profile.id) return true;
  return foldedIntoPreset(profile, presets)?.id === row.id;
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
      aria-label="装配名称"
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
  starred,
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
  starred?: boolean;
}) {
  const marked = starred ?? Boolean(row.is_default);
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
        {marked ? (
          <Star size={14} className="shrink-0 fill-current" aria-hidden />
        ) : null}
      </span>
    );
  }
  return (
    <button
      type="button"
      aria-pressed={selected}
      aria-label={marked ? `${row.name}，新建对话用这份` : row.name}
      disabled={pending}
      onClick={() => {
        if (selected && face) onBeginRename();
        else onSelect();
      }}
      className={chipClass}
    >
      <span className="truncate">{row.name}</span>
      {marked ? (
        <Star size={14} className="shrink-0 fill-current" aria-hidden />
      ) : null}
    </button>
  );
}
