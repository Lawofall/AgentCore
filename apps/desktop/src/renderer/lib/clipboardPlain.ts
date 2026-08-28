/**
 * Clipboard hygiene for non-rich surfaces (composer + user bubbles).
 *
 * Composer is plain text + pills, not a rich editor. Native selection copy of a
 * styled bubble still writes text/html with background/radius; paste into
 * contenteditable keeps that wrapper because serialize matches and skip-rebuild.
 *
 * Copy: when the selection sits inside a `[data-copy-plain]` island, write
 * text/plain plus unstyled HTML (Word/Outlook prefer HTML and would paste empty
 * if we only set plain). Assistant bubbles stay unmarked — export is the
 * two-tier copy button.
 *
 * Paste: always insert text/plain (HTML-only clipboards stripped). File/screenshot
 * paste stays on the drop handler and preventDefault's first.
 */

export const COPY_PLAIN_ATTR = "data-copy-plain";

type ClipboardLike = {
  defaultPrevented: boolean;
  preventDefault: () => void;
  clipboardData: DataTransfer | null;
};

export type PasteRoom = {
  maxLength: number;
  currentLength: number;
  selectedLength: number;
};

export function normalizeClipboardPlain(text: string): string {
  return text.replace(/\r\n?/g, "\n").replaceAll("\uFFFC", "");
}

export function htmlToPlain(html: string): string {
  if (!html) return "";
  const doc = new DOMParser().parseFromString(html, "text/html");
  const raw = doc.body.innerText || doc.body.textContent || "";
  return normalizeClipboardPlain(raw.replace(/\u00a0/g, " "));
}

export function plainTextFromClipboard(
  data: DataTransfer | null | undefined,
): string {
  if (!data || typeof data.getData !== "function") return "";
  try {
    const plain = data.getData("text/plain");
    if (plain) return normalizeClipboardPlain(plain);
    const html = data.getData("text/html");
    if (html) return htmlToPlain(html);
  } catch {
    return "";
  }
  return "";
}

export function plainToUnstyledHtml(text: string): string {
  const escaped = text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
  return escaped.replace(/\n/g, "<br>");
}

export function clipPlainPaste(text: string, room?: PasteRoom): string {
  if (!room) return text;
  const cap = Math.max(
    0,
    room.maxLength - room.currentLength + room.selectedLength,
  );
  return text.slice(0, cap);
}

function nodeElement(node: Node | null): Element | null {
  if (!node) return null;
  return node instanceof Element ? node : node.parentElement;
}

/** Selection must sit entirely in one marked island (not spanning two messages). */
export function selectionPlainCopyIsland(): Element | null {
  const sel = window.getSelection();
  if (!sel || sel.rangeCount === 0 || sel.isCollapsed) return null;
  const islandA = nodeElement(sel.anchorNode)?.closest(`[${COPY_PLAIN_ATTR}]`);
  const islandF = nodeElement(sel.focusNode)?.closest(`[${COPY_PLAIN_ATTR}]`);
  if (!islandA || islandA !== islandF) return null;
  if (!islandA.contains(sel.anchorNode) || !islandA.contains(sel.focusNode)) {
    return null;
  }
  return islandA;
}

export function writePlainCopy(e: ClipboardLike, text: string): void {
  e.preventDefault();
  e.clipboardData?.setData("text/plain", text);
  e.clipboardData?.setData("text/html", plainToUnstyledHtml(text));
}

export function handlePlainCopy(e: ClipboardLike): boolean {
  if (!selectionPlainCopyIsland()) return false;
  const text = normalizeClipboardPlain(window.getSelection()?.toString() ?? "");
  if (!text) return false;
  writePlainCopy(e, text);
  return true;
}

function editableRoot(node: Node | null): HTMLElement | null {
  let n: Node | null = node;
  while (n) {
    if (n instanceof HTMLElement && n.isContentEditable) return n;
    n = n.parentNode;
  }
  return null;
}

/** Prefer insertText so contenteditable undo stays intact; Range is the fallback. */
export function insertPlainText(text: string): boolean {
  if (!text) return true;
  try {
    if (
      typeof document.execCommand === "function" &&
      document.execCommand("insertText", false, text)
    ) {
      return true;
    }
  } catch {
    /* Range fallback */
  }
  const sel = window.getSelection();
  if (!sel || sel.rangeCount === 0) return false;
  const range = sel.getRangeAt(0);
  range.deleteContents();
  const node = document.createTextNode(text);
  range.insertNode(node);
  range.setStartAfter(node);
  range.collapse(true);
  sel.removeAllRanges();
  sel.addRange(range);
  editableRoot(node)?.dispatchEvent(new Event("input", { bubbles: true }));
  return true;
}

export function handlePlainPaste(e: ClipboardLike, room?: PasteRoom): boolean {
  if (e.defaultPrevented) return false;
  e.preventDefault();
  const text = clipPlainPaste(plainTextFromClipboard(e.clipboardData), room);
  if (!text) return true;
  return insertPlainText(text);
}
