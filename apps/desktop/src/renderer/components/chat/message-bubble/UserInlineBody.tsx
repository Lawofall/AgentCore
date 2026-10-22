import { ImageThumbStrip } from "@/components/files/ImageThumbStrip";
import { IconButton } from "@/components/ui";
import { handlePlainPaste } from "@/lib/clipboardPlain";
import type { FileSource } from "@/lib/fileSource";
import {
  type InlineSpan,
  dropInlineIndex,
  parseInlineBody,
  serializeInlineBody,
} from "@/lib/inlineBody";
import { isVisualImageAttachment } from "@/services/messaging";
import type {
  AgentMentionMeta,
  MessageAttachmentMeta,
} from "@/stores/conversation";
import { X } from "lucide-react";
import {
  Fragment,
  type KeyboardEvent as ReactKeyboardEvent,
  forwardRef,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
} from "react";
import { AgentMentionChip } from "./AgentMentionChip";
import { AttachmentChip } from "./AttachmentChip";

function stripObject(text: string | null | undefined): string {
  return (text ?? "").replaceAll("\uFFFC", "");
}

function withEditGaps(spans: InlineSpan[]): InlineSpan[] {
  const out: InlineSpan[] = [];
  const pushGap = () => {
    if (out.at(-1)?.kind !== "text") out.push({ kind: "text", text: "" });
  };
  pushGap();
  for (const span of spans) {
    if (span.kind === "text") {
      const last = out.at(-1);
      if (last?.kind === "text") last.text += span.text;
      else out.push({ ...span });
    } else {
      pushGap();
      out.push(span);
    }
  }
  pushGap();
  return out;
}

export function UserChipTray({
  attachments,
  mentions,
}: {
  attachments: readonly MessageAttachmentMeta[];
  mentions: readonly AgentMentionMeta[];
}) {
  const files = attachments.filter((a) => !isVisualImageAttachment(a));
  if (files.length === 0 && mentions.length === 0) return null;
  return (
    <div
      data-testid="user-chip-tray"
      className="flex max-w-[80%] flex-wrap justify-end gap-1.5"
    >
      {mentions.map((a) => (
        <AgentMentionChip key={a.agentId} role={a.role} />
      ))}
      {files.map((a) => (
        <AttachmentChip key={a.id} att={a} interactive />
      ))}
    </div>
  );
}

export function UserImageStrip({
  attachments,
  source = null,
  sourcePending = false,
  onRemove,
}: {
  attachments: readonly MessageAttachmentMeta[];
  source?: FileSource | null;
  sourcePending?: boolean;
  onRemove?: (id: string) => void;
}) {
  return (
    <ImageThumbStrip
      attachments={attachments}
      source={source}
      sourcePending={sourcePending}
      onRemove={onRemove}
      align="end"
      className="max-w-[80%]"
    />
  );
}

/** Read-only history / interjection body: pills sit in the sentence, never as a second tray. */
export function UserInlineBody({
  content,
  attachments,
  mentions,
}: {
  content: string;
  attachments: readonly MessageAttachmentMeta[];
  mentions: readonly AgentMentionMeta[];
}) {
  return (
    <div
      data-testid="user-inline-body"
      className="whitespace-pre-wrap break-words"
    >
      {parseInlineBody(content).map((span, i) => {
        const key = `${span.kind}-${i}`;
        if (span.kind === "text") {
          return <Fragment key={key}>{stripObject(span.text)}</Fragment>;
        }
        if (span.kind === "attachment") {
          const att = attachments[span.index];
          if (!att || isVisualImageAttachment(att)) return null;
          return (
            <span key={key} className="mx-0.5 inline-flex align-middle">
              <AttachmentChip att={att} interactive />
            </span>
          );
        }
        const mention = mentions[span.index];
        if (!mention) return null;
        return (
          <span key={key} className="mx-0.5 inline-flex align-middle">
            <AgentMentionChip role={mention.role} />
          </span>
        );
      })}
    </div>
  );
}

export type UserInlineDraftFlush = {
  content: string;
  attachments: MessageAttachmentMeta[];
  mentions: AgentMentionMeta[];
};

export type UserInlineDraftHandle = {
  flush: () => UserInlineDraftFlush;
};

/**
 * Edit the same inline sequence. Dropping a pill rewrites draft tokens and the
 * local materials arrays; submit persists both.
 */
export const UserInlineDraft = forwardRef<
  UserInlineDraftHandle,
  {
    value: string;
    attachments: readonly MessageAttachmentMeta[];
    mentions: readonly AgentMentionMeta[];
    source?: FileSource | null;
    sourcePending?: boolean;
    onChange: (next: string) => void;
    onAttachmentsChange?: (next: readonly MessageAttachmentMeta[]) => void;
    onKeyDown: (e: ReactKeyboardEvent<HTMLDivElement>) => void;
  }
>(function UserInlineDraft(
  {
    value,
    attachments,
    mentions,
    source = null,
    sourcePending = false,
    onChange,
    onAttachmentsChange,
    onKeyDown,
  },
  ref,
) {
  const rootRef = useRef<HTMLDivElement>(null);
  const [draftAtts, setDraftAtts] = useState(attachments);
  const [draftMents, setDraftMents] = useState(mentions);
  const attsRef = useRef(draftAtts);
  const mentsRef = useRef(draftMents);
  attsRef.current = draftAtts;
  mentsRef.current = draftMents;
  const valueRef = useRef(value);
  valueRef.current = value;

  const flushFromDom = (): string => {
    const spans = withEditGaps(parseInlineBody(valueRef.current));
    const nodes =
      rootRef.current?.querySelectorAll<HTMLElement>("[data-text-span]") ?? [];
    for (const el of nodes) {
      const idx = Number(el.dataset.textSpan);
      const span = spans[idx];
      if (span?.kind !== "text") continue;
      span.text = stripObject(el.innerText ?? el.textContent);
    }
    return serializeInlineBody(spans);
  };

  useImperativeHandle(ref, () => ({
    flush: () => {
      const next = flushFromDom();
      valueRef.current = next;
      onChange(next);
      return {
        content: next,
        attachments: [...attsRef.current],
        mentions: [...mentsRef.current],
      };
    },
  }));

  useEffect(() => {
    onAttachmentsChange?.(draftAtts);
  }, [draftAtts, onAttachmentsChange]);

  useEffect(() => {
    const el = rootRef.current?.querySelector<HTMLElement>("[data-text-span]");
    if (!el) return;
    el.focus();
    const sel = window.getSelection();
    if (!sel) return;
    const range = document.createRange();
    range.selectNodeContents(el);
    range.collapse(false);
    sel.removeAllRanges();
    sel.addRange(range);
  }, []);

  const drop = (kind: "attachment" | "mention", index: number) => {
    const flushed = flushFromDom();
    const next = dropInlineIndex(flushed, kind, index);
    if (kind === "attachment") {
      setDraftAtts((prev) => prev.filter((_, i) => i !== index));
    } else {
      setDraftMents((prev) => prev.filter((_, i) => i !== index));
    }
    valueRef.current = next;
    onChange(next);
  };

  const spans = withEditGaps(parseInlineBody(value));

  return (
    <div className="flex flex-col items-end gap-2">
      <UserImageStrip
        attachments={draftAtts}
        source={source}
        sourcePending={sourcePending}
        onRemove={(id) => {
          const index = attsRef.current.findIndex((att) => att.id === id);
          if (index >= 0) drop("attachment", index);
        }}
      />
      <div
        ref={rootRef}
        data-testid="user-inline-draft"
        className="max-h-[240px] w-full overflow-y-auto whitespace-pre-wrap break-words bg-transparent px-2 py-1 text-sm text-foreground"
        onKeyDown={onKeyDown}
        onPaste={(e) => {
          handlePlainPaste(e);
        }}
      >
        {spans.map((span, i) => {
          const key = `${span.kind}-${i}`;
          if (span.kind === "text") {
            return (
              <span
                key={key}
                data-text-span={i}
                contentEditable
                suppressContentEditableWarning
                className="inline outline-none"
              >
                {stripObject(span.text)}
              </span>
            );
          }
          if (span.kind === "attachment") {
            const att = draftAtts[span.index];
            if (!att || isVisualImageAttachment(att)) return null;
            return (
              <span
                key={key}
                className="mx-0.5 inline-flex align-middle items-center"
                contentEditable={false}
              >
                <AttachmentChip att={att} />
                <IconButton
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={() => drop("attachment", span.index)}
                  aria-label="移除附件"
                  className="size-5 shrink-0"
                >
                  <X size={12} />
                </IconButton>
              </span>
            );
          }
          const mention = draftMents[span.index];
          if (!mention) return null;
          return (
            <span
              key={key}
              className="mx-0.5 inline-flex align-middle items-center"
              contentEditable={false}
            >
              <AgentMentionChip role={mention.role} />
              <IconButton
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => drop("mention", span.index)}
                aria-label="移除角色点名"
                className="size-5 shrink-0"
              >
                <X size={12} />
              </IconButton>
            </span>
          );
        })}
      </div>
    </div>
  );
});
