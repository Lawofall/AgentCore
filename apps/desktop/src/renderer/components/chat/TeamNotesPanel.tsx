import { agentColorVar, agentGlyph } from "@/lib/agentIdentity";
import { useStreamAwareDisclosure } from "@/stores/disclosure";
import type { TeamNote } from "@/stores/execution";
import { ChevronDown, ChevronRight, StickyNote } from "lucide-react";
import { useState } from "react";

/** 团队便签墙 (§2.2 通) note kind → 中文 label + tone. `decision` (我定了) is a choice others must
 * depend on (an interface / field name / format / naming); `heads_up` (提个醒) is a pitfall /
 * discovery worth flagging; `claim` (我领了) is a piece of work / file this worker is taking, so a
 * sibling doesn't duplicate it (success tone reads as「已认领」, distinct from decision's brand blue
 * and heads_up's caution amber). Mirrors the backend NoteWall labels (runtime/runs/notewall.py);
 * an unknown kind falls back to 提个醒 (the lower-commitment one, matching the backend coercion). */
const NOTE_BADGE_GRAY = "bg-muted text-muted-foreground";

const NOTE_KIND_META: Record<string, { label: string; className: string }> = {
  decision: { label: "约定", className: NOTE_BADGE_GRAY },
  heads_up: { label: "提醒", className: NOTE_BADGE_GRAY },
  claim: { label: "认领", className: NOTE_BADGE_GRAY },
};

/** 便签会过期 → supersession (§2.2): a note marked `superseded` (改写) / `voided` (作废) is shown
 * struck-through + dimmed with this badge, so a reader never mistakes a stale decision for current
 * truth. `active` notes carry no status badge. */
const NOTE_STATUS_META: Record<string, { label: string; className: string }> = {
  superseded: { label: "已更新", className: NOTE_BADGE_GRAY },
  voided: { label: "已作废", className: NOTE_BADGE_GRAY },
};

export interface TeamNotesPanelProps {
  notes: TeamNote[];
  /**
   * 墙已升。空数组时仅在此项为真才画空态；缺省/无墙仍不渲染。
   */
  noteWall?: boolean;
  /**
   * Tighter padding. Collapsible header when controlled or when
   * `defaultExpanded` / `disclosureKey` is set.
   */
  compact?: boolean;
  /**
   * Uncontrolled collapse seed. Prefer `disclosureKey` + `live` for
   * stream-aware persistence.
   */
  defaultExpanded?: boolean;
  /**
   * Persistent key for uncontrolled stream-aware open state
   * (`${messageId}:team-notes`). Omit in controlled mode.
   */
  disclosureKey?: string | null;
  /**
   * Live signal for stream-aware uncontrolled mode: running + active notes →
   * expand by default; settled → collapse default (user override persists).
   */
  live?: boolean;
  /**
   * Controlled open state. When set, the wall is collapsible and ignores internal
   * state — pair with `onExpandedChange` (chat `InlineTeamGraph`).
   */
  expanded?: boolean;
  /** Controlled toggle; called with the next open state. */
  onExpandedChange?: (expanded: boolean) => void;
}

/**
 * 团队便签墙 (§2.2 通) — the in-chat「团队便签」panel: the one-line decisions / heads-ups workers
 * broadcast to their CONCURRENT siblings via `post_note` WHILE they worked (`team_note_posted` →
 * {@link Execution.teamNotes}). This is the visible, glass-box face of the note wall — and what
 * makes it worth more than direct chat: every broadcast is a recorded, attributed, kind-tagged
 * fact shown in ONE place, not a conversation. Each note is fire-and-forget (贴事实·不要求回应),
 * shown with its author (谁贴的) and kind (我定了 / 提个醒), in post order. Renders nothing when
 * the wall was never raised (缺省无墙). A raised empty wall shows an honest empty state.
 *
 * Chat lifts open state via `expanded` / `onExpandedChange`. The compact /
 * stream-aware `disclosureKey` path remains for uncontrolled hosts.
 */
export function TeamNotesPanel({
  notes,
  noteWall = false,
  compact = false,
  defaultExpanded,
  disclosureKey,
  live = false,
  expanded: expandedProp,
  onExpandedChange,
}: TeamNotesPanelProps) {
  const controlled = expandedProp !== undefined;
  const collapsible =
    controlled || defaultExpanded !== undefined || disclosureKey != null;

  // Uncontrolled stream-aware path. Hooks must stay unconditional.
  const [streamExpanded, toggleStream] = useStreamAwareDisclosure(
    controlled ? null : (disclosureKey ?? null),
    live,
  );
  const [legacyExpanded, setLegacyExpanded] = useState(defaultExpanded ?? true);

  const expanded = controlled
    ? expandedProp
    : disclosureKey != null
      ? streamExpanded
      : legacyExpanded;

  if (notes.length === 0 && !noteWall) return null;

  const empty = notes.length === 0;

  if (!collapsible) {
    return (
      <section
        className={
          compact
            ? "border-t border-border px-2.5 py-2"
            : "border-t border-border px-3 py-2.5"
        }
      >
        <NotesHeader count={notes.length} compact={compact} />
        {empty ? (
          <EmptyHint compact={compact} />
        ) : (
          <NotesList notes={notes} compact={compact} />
        )}
      </section>
    );
  }

  const toggle = () => {
    if (controlled) {
      onExpandedChange?.(!expanded);
    } else if (disclosureKey != null) {
      toggleStream();
    } else {
      setLegacyExpanded((v) => !v);
    }
  };

  return (
    <section
      className={
        compact
          ? "border-t border-border px-2.5 py-1.5"
          : "border-t border-border px-3 py-2"
      }
    >
      <button
        type="button"
        className="flex w-full items-center gap-1.5 text-xs font-medium text-muted-foreground hover:text-foreground"
        onClick={(e) => {
          e.stopPropagation();
          toggle();
        }}
        aria-expanded={expanded}
      >
        {expanded ? (
          <ChevronDown size={14} className="shrink-0" />
        ) : (
          <ChevronRight size={14} className="shrink-0" />
        )}
        <StickyNote size={14} className="shrink-0" />
        <span className="flex-1 text-left">团队便签</span>
        <span className="tabular-nums">{notes.length}</span>
      </button>
      {expanded &&
        (empty ? (
          <EmptyHint compact={compact} className="mt-1.5" />
        ) : (
          <NotesList notes={notes} compact={compact} className="mt-1.5" />
        ))}
    </section>
  );
}

function NotesHeader({
  count,
  compact,
}: {
  count: number;
  compact: boolean;
}) {
  return (
    <div
      className={`mb-2 flex items-center gap-1.5 text-xs font-medium text-muted-foreground ${
        compact ? "mb-1.5" : ""
      }`}
    >
      <StickyNote size={14} className="shrink-0" />
      <span className="flex-1">团队便签</span>
      <span className="tabular-nums">{count}</span>
    </div>
  );
}

function EmptyHint({
  compact,
  className = "",
}: {
  compact: boolean;
  className?: string;
}) {
  return (
    <p
      className={`text-muted-foreground ${compact ? "text-xs" : "text-sm"} ${className}`}
    >
      对齐点会贴在这里
    </p>
  );
}

function NotesList({
  notes,
  compact,
  className = "",
}: {
  notes: TeamNote[];
  compact: boolean;
  className?: string;
}) {
  return (
    <ul
      className={`space-y-1.5 ${compact ? "max-h-36 space-y-1 overflow-y-auto" : ""} ${className}`}
    >
      {notes.map((note) => (
        <NoteRow key={note.noteId} note={note} compact={compact} />
      ))}
    </ul>
  );
}

/** One sticky note: the author's identity disc + role, a kind badge (我定了 / 提个醒), and the
 *  one-line broadcast text. Identity color is derived from the role (角色身份, agentIdentity) so a
 *  note reads as「同一拨人」with its graph node. */
function NoteRow({
  note,
  compact,
}: {
  note: TeamNote;
  compact: boolean;
}) {
  const kind = NOTE_KIND_META[note.kind] ?? NOTE_KIND_META.heads_up;
  const author = note.role || note.agentId;
  const status = NOTE_STATUS_META[note.status];
  const stale = status != null;
  return (
    <li
      className={`flex items-start gap-2 rounded-lg bg-muted ${
        compact ? "px-2 py-1.5" : "px-2.5 py-2"
      } ${stale ? "opacity-60" : ""}`}
    >
      <span
        className="mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full text-xs font-semibold"
        style={{
          backgroundColor: `color-mix(in oklab, ${agentColorVar(author)} 18%, transparent)`,
          color: agentColorVar(author),
        }}
        aria-hidden
      >
        {agentGlyph(author)}
      </span>
      <div className="min-w-0 flex-1">
        <div className="mb-0.5 flex items-center gap-1.5">
          <span className="min-w-0 truncate text-xs font-medium text-foreground">
            {author}
          </span>
          <span
            className={`shrink-0 rounded px-1.5 py-0.5 text-xs font-medium ${kind.className}`}
          >
            {kind.label}
          </span>
          {status && (
            <span
              className={`shrink-0 rounded px-1.5 py-0.5 text-xs font-medium ${status.className}`}
            >
              {status.label}
            </span>
          )}
          {note.source === "ceo" && (
            <span
              className={`shrink-0 rounded px-1.5 py-0.5 text-xs font-medium ${NOTE_BADGE_GRAY}`}
            >
              团队共识
            </span>
          )}
          {note.source === "inherited" && (
            <span
              className={`shrink-0 rounded px-1.5 py-0.5 text-xs font-medium ${NOTE_BADGE_GRAY}`}
            >
              上轮遗留
            </span>
          )}
        </div>
        <p
          className={`whitespace-pre-wrap break-words leading-snug text-foreground ${
            compact ? "text-xs" : "text-sm"
          } ${stale ? "line-through" : ""}`}
        >
          {note.text}
        </p>
      </div>
    </li>
  );
}
