import { DirTypeIcon, FileTypeIcon } from "@/components/files/FileTypeIcon";
import { IconButton } from "@/components/ui";
import { handlePlainPaste } from "@/lib/clipboardPlain";
import {
  INLINE_OBJECT,
  hasInlineMarkers,
  parseInlineBody,
  plainText,
  reconcileInlineBody,
  serializeInlineBody,
} from "@/lib/inlineBody";
import { cn } from "@/lib/utils";
import { AlertCircle, Loader2, MessageSquare, Users, X } from "lucide-react";
import {
  type ClipboardEvent,
  type KeyboardEvent as ReactKeyboardEvent,
  type Ref,
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useLayoutEffect,
  useRef,
} from "react";
import { type Root, createRoot } from "react-dom/client";
import type {
  PendingAgentMention,
  PendingAttachment,
} from "./composerAttachments";

export type ComposerBodyHandle = {
  focus: () => void;
  getCaret: () => number;
  setCaret: (offset: number) => void;
};

export const ComposerBodyEditor = forwardRef(function ComposerBodyEditor(
  {
    value,
    attachments,
    agentMentions,
    placeholder,
    className,
    maxLength,
    onChange,
    onReconcile,
    onRemoveAttachment,
    onRemoveAgent,
    onCaret,
    onKeyDown,
    onPaste,
  }: {
    value: string;
    attachments: PendingAttachment[];
    agentMentions: PendingAgentMention[];
    placeholder?: string;
    className?: string;
    maxLength: number;
    onChange: (next: string) => void;
    onReconcile: (
      attachments: PendingAttachment[],
      mentions: PendingAgentMention[],
    ) => void;
    onRemoveAttachment: (id: string) => void;
    onRemoveAgent: (id: string) => void;
    onCaret: (caret: number) => void;
    onKeyDown: (e: ReactKeyboardEvent) => void;
    onPaste: (e: ClipboardEvent) => void;
  },
  ref: Ref<ComposerBodyHandle>,
) {
  const rootRef = useRef<HTMLDivElement>(null);
  const lastEmittedRef = useRef<string | null>(null);
  const pillRoots = useRef(new Map<string, Root>());
  const composingRef = useRef(false);

  const unmountPills = useCallback(() => {
    const roots = [...pillRoots.current.values()];
    pillRoots.current.clear();
    if (roots.length === 0) return;
    // Nested createRoot.unmount() during the parent commit/input path races
    // React's render ("synchronously unmount a root while React was already rendering").
    queueMicrotask(() => {
      for (const r of roots) r.unmount();
    });
  }, []);

  const mountPill = useCallback(
    (host: HTMLElement) => {
      const key = host.dataset.pillKey;
      if (!key) return;
      let root = pillRoots.current.get(key);
      if (!root) {
        root = createRoot(host);
        pillRoots.current.set(key, root);
      }
      const kind = host.dataset.inline;
      const index = Number(host.dataset.index);
      if (kind === "attachment") {
        const att = attachments[index];
        if (!att) return;
        root.render(
          <EditorAttachmentPill
            att={att}
            onRemove={() => onRemoveAttachment(att.id)}
          />,
        );
      } else if (kind === "mention") {
        const mention = agentMentions[index];
        if (!mention) return;
        root.render(
          <EditorMentionPill
            mention={mention}
            onRemove={() => onRemoveAgent(mention.id)}
          />,
        );
      }
    },
    [agentMentions, attachments, onRemoveAgent, onRemoveAttachment],
  );

  const rebuild = useCallback(() => {
    const el = rootRef.current;
    if (!el) return;
    unmountPills();
    el.replaceChildren();
    const spans = parseInlineBody(value);
    for (const [i, span] of spans.entries()) {
      if (span.kind === "text") {
        if (span.text) el.appendChild(document.createTextNode(span.text));
        continue;
      }
      const host = document.createElement("span");
      host.contentEditable = "false";
      host.dataset.inline = span.kind;
      host.dataset.index = String(span.index);
      host.dataset.pillKey = `${span.kind}:${span.index}:${i}`;
      host.className = "mx-0.5 inline-flex align-middle";
      el.appendChild(host);
      mountPill(host);
    }
  }, [mountPill, unmountPills, value]);

  useLayoutEffect(() => {
    if (value === lastEmittedRef.current) {
      for (const host of rootRef.current?.querySelectorAll<HTMLElement>(
        "[data-pill-key]",
      ) ?? []) {
        mountPill(host);
      }
      return;
    }
    rebuild();
    lastEmittedRef.current = value;
  }, [value, rebuild, mountPill]);

  useEffect(() => () => unmountPills(), [unmountPills]);

  const serialize = useCallback((): string => {
    const el = rootRef.current;
    if (!el) return "";
    const spans: ReturnType<typeof parseInlineBody> = [];
    const walk = (node: Node) => {
      if (node.nodeType === Node.TEXT_NODE) {
        const text = (node.textContent ?? "").replaceAll(INLINE_OBJECT, "");
        if (text) spans.push({ kind: "text", text });
        return;
      }
      if (node.nodeType !== Node.ELEMENT_NODE) return;
      const html = node as HTMLElement;
      if (html.dataset.inline === "attachment") {
        spans.push({ kind: "attachment", index: Number(html.dataset.index) });
        return;
      }
      if (html.dataset.inline === "mention") {
        spans.push({ kind: "mention", index: Number(html.dataset.index) });
        return;
      }
      if (html.tagName === "BR") {
        spans.push({ kind: "text", text: "\n" });
        return;
      }
      if (html.tagName === "DIV" && html !== el) {
        if (spans.length > 0) {
          const last = spans[spans.length - 1];
          if (last?.kind === "text" && !last.text.endsWith("\n")) {
            last.text += "\n";
          } else if (last?.kind !== "text") {
            spans.push({ kind: "text", text: "\n" });
          }
        }
      }
      for (const child of Array.from(node.childNodes)) walk(child);
    };
    walk(el);
    return serializeInlineBody(spans);
  }, []);

  const getCaretFromDom = useCallback((): number => {
    const el = rootRef.current;
    const sel = window.getSelection();
    if (!el || !sel || sel.rangeCount === 0) return serialize().length;
    const range = sel.getRangeAt(0);
    const pre = document.createRange();
    pre.selectNodeContents(el);
    pre.setEnd(range.endContainer, range.endOffset);
    const probe = document.createElement("div");
    probe.appendChild(pre.cloneContents());
    return serializeFromClone(probe);
  }, [serialize]);

  useImperativeHandle(
    ref,
    () => ({
      focus: () => rootRef.current?.focus(),
      getCaret: getCaretFromDom,
      setCaret: (offset: number) => placeCaret(rootRef.current, offset),
    }),
    [getCaretFromDom],
  );

  const emitFromDom = useCallback(() => {
    const el = rootRef.current;
    if (!el) return;
    const raw = serialize();
    if (raw.length > maxLength) return;
    const rec = reconcileInlineBody(raw, attachments, agentMentions);
    lastEmittedRef.current = rec.value;
    onChange(rec.value);
    const attChanged =
      rec.attachments.length !== attachments.length ||
      rec.attachments.some((a, i) => a.id !== attachments[i]?.id);
    const mentChanged =
      rec.mentions.length !== agentMentions.length ||
      rec.mentions.some((a, i) => a.id !== agentMentions[i]?.id);
    if (attChanged || mentChanged) onReconcile(rec.attachments, rec.mentions);
    const caret = getCaretFromDom();
    onCaret(caret);
    if (rec.value !== raw) {
      rebuild();
      placeCaret(el, Math.min(caret, rec.value.length));
    }
  }, [
    agentMentions,
    attachments,
    getCaretFromDom,
    maxLength,
    onCaret,
    onChange,
    onReconcile,
    rebuild,
    serialize,
  ]);

  const showPlaceholder = !plainText(value).trim() && !hasInlineMarkers(value);

  return (
    <div className="relative min-w-0 flex-1">
      {showPlaceholder && placeholder ? (
        <div
          aria-hidden
          className={cn(
            "pointer-events-none absolute inset-0 text-sm text-muted-foreground",
            className,
          )}
        >
          {placeholder}
        </div>
      ) : null}
      <div
        ref={rootRef}
        role="textbox"
        aria-multiline="true"
        aria-label={placeholder}
        contentEditable
        tabIndex={0}
        suppressContentEditableWarning
        data-testid="composer-body"
        data-copy-plain=""
        className={cn(
          "block w-full resize-none overflow-y-auto bg-transparent text-sm text-foreground focus:outline-none whitespace-pre-wrap break-words",
          className,
        )}
        onInput={() => {
          if (!composingRef.current) emitFromDom();
        }}
        onCompositionStart={() => {
          composingRef.current = true;
        }}
        onCompositionEnd={() => {
          composingRef.current = false;
          emitFromDom();
        }}
        onKeyDown={onKeyDown}
        onPaste={(e) => {
          onPaste(e);
          if (e.defaultPrevented) return;
          const selectedLength = selectedSerializedLength(rootRef.current);
          handlePlainPaste(e, {
            maxLength,
            currentLength: value.length,
            selectedLength,
          });
        }}
        onMouseUp={() => onCaret(getCaretFromDom())}
        onKeyUp={() => onCaret(getCaretFromDom())}
      />
    </div>
  );
});

function selectedSerializedLength(root: HTMLElement | null): number {
  if (!root) return 0;
  const sel = window.getSelection();
  if (!sel || sel.rangeCount === 0 || sel.isCollapsed) return 0;
  if (!root.contains(sel.anchorNode) || !root.contains(sel.focusNode)) return 0;
  const probe = document.createElement("div");
  probe.appendChild(sel.getRangeAt(0).cloneContents());
  return serializeFromClone(probe);
}

function serializeFromClone(probe: HTMLElement): number {
  let n = 0;
  const walk = (node: Node) => {
    if (node.nodeType === Node.TEXT_NODE) {
      n += (node.textContent ?? "").replaceAll(INLINE_OBJECT, "").length;
      return;
    }
    if (node.nodeType !== Node.ELEMENT_NODE) return;
    const html = node as HTMLElement;
    if (html.dataset.inline === "attachment") {
      n += `\uFFFCA${html.dataset.index}\uFFFC`.length;
      return;
    }
    if (html.dataset.inline === "mention") {
      n += `\uFFFCM${html.dataset.index}\uFFFC`.length;
      return;
    }
    if (html.tagName === "BR") {
      n += 1;
      return;
    }
    for (const child of Array.from(node.childNodes)) walk(child);
  };
  walk(probe);
  return n;
}

function placeCaret(el: HTMLElement | null, offset: number): void {
  if (!el) return;
  el.focus();
  const sel = window.getSelection();
  if (!sel) return;
  let remaining = Math.max(0, offset);
  const range = document.createRange();
  const visit = (node: Node): boolean => {
    if (node.nodeType === Node.TEXT_NODE) {
      const text = (node.textContent ?? "").replaceAll(INLINE_OBJECT, "");
      if (remaining <= text.length) {
        range.setStart(node, remaining);
        range.collapse(true);
        return true;
      }
      remaining -= text.length;
      return false;
    }
    if (node.nodeType !== Node.ELEMENT_NODE) return false;
    const html = node as HTMLElement;
    if (html.dataset.inline) {
      const tokenLen =
        html.dataset.inline === "attachment"
          ? `\uFFFCA${html.dataset.index}\uFFFC`.length
          : `\uFFFCM${html.dataset.index}\uFFFC`.length;
      if (remaining <= tokenLen) {
        range.setStartAfter(html);
        range.collapse(true);
        return true;
      }
      remaining -= tokenLen;
      return false;
    }
    if (html.tagName === "BR") {
      if (remaining <= 1) {
        range.setStartAfter(html);
        range.collapse(true);
        return true;
      }
      remaining -= 1;
      return false;
    }
    for (const child of Array.from(node.childNodes)) {
      if (visit(child)) return true;
    }
    return false;
  };
  if (!visit(el)) {
    range.selectNodeContents(el);
    range.collapse(false);
  }
  sel.removeAllRanges();
  sel.addRange(range);
}

function EditorAttachmentPill({
  att,
  onRemove,
}: {
  att: PendingAttachment;
  onRemove: () => void;
}) {
  const uploading = att.uploadState === "uploading";
  const failed = att.uploadState === "error";
  return (
    <span
      data-upload-state={att.uploadState}
      className={cn(
        "inline-flex max-w-[220px] items-center gap-1.5 rounded-lg px-2 py-1 text-xs",
        failed
          ? "bg-muted/40 text-muted-foreground"
          : "bg-accent text-accent-foreground",
      )}
    >
      {uploading ? (
        <Loader2
          size={12}
          className="shrink-0 animate-spin text-muted-foreground"
          aria-hidden
        />
      ) : failed ? (
        <AlertCircle size={12} className="shrink-0" aria-hidden />
      ) : att.kind === "dir" ? (
        <DirTypeIcon name={att.name} path={att.path} size={12} />
      ) : att.kind === "conversation" ? (
        <MessageSquare size={12} className="shrink-0" />
      ) : (
        <FileTypeIcon name={att.name} path={att.path} size={12} />
      )}
      {(uploading || failed) && (
        <span className="shrink-0 text-muted-foreground">
          {uploading ? "上传中" : "上传失败"}
        </span>
      )}
      <span className="truncate">
        {att.name}
        {att.kind === "dir" ? "/" : ""}
      </span>
      {att.truncated && !uploading && !failed && (
        <span className="shrink-0 text-muted-foreground">
          {att.kind === "dir"
            ? "部分"
            : att.kind === "conversation"
              ? "近期"
              : "已截断"}
        </span>
      )}
      <IconButton
        onMouseDown={(e) => e.preventDefault()}
        onClick={onRemove}
        aria-label="移除附件"
        className="size-5 shrink-0"
      >
        <X size={12} />
      </IconButton>
    </span>
  );
}

function EditorMentionPill({
  mention,
  onRemove,
}: {
  mention: PendingAgentMention;
  onRemove: () => void;
}) {
  return (
    <span className="inline-flex max-w-[220px] items-center gap-1.5 rounded-lg bg-accent px-2 py-1 text-xs text-accent-foreground">
      <Users size={12} className="shrink-0" />
      <span className="shrink-0 text-muted-foreground">点名</span>
      <span className="truncate">{mention.role}</span>
      <IconButton
        onMouseDown={(e) => e.preventDefault()}
        onClick={onRemove}
        aria-label="移除角色点名"
        className="size-5 shrink-0"
      >
        <X size={12} />
      </IconButton>
    </span>
  );
}
