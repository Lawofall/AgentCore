/**
 * In-place text overlay — a transparent textarea sitting on the element's screen box.
 * Follows pan/zoom/rotation via rAF (the same trick as the selection chrome). The engine
 * owns the session + scene; this component only holds the draft until flush.
 */

import {
  type InputEvent,
  type KeyboardEvent,
  type RefObject,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import type { WhiteboardEngine } from "./engine";
import {
  type EditOverlayLayout,
  editOverlayLayout,
  sameOverlayLayout,
} from "./textEditor";
import type { TextEditSession } from "./types";

export function TextEditOverlay({
  engine,
  session,
  textareaRef,
}: {
  engine: WhiteboardEngine;
  session: TextEditSession;
  textareaRef: RefObject<HTMLTextAreaElement | null>;
}) {
  const [layout, setLayout] = useState<EditOverlayLayout>(() =>
    readLayout(engine, session),
  );
  const growH = useRef<number | null>(null);

  useLayoutEffect(() => {
    setLayout(readLayout(engine, session));
    growH.current = null;
  }, [engine, session]);

  useEffect(() => {
    let raf = 0;
    const tick = () => {
      const next = readLayout(engine, session);
      setLayout((prev) => (sameOverlayLayout(prev, next) ? prev : next));
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [engine, session]);

  const el = session.id
    ? (engine.getScene().find((e) => e.id === session.id) ?? null)
    : null;
  const initial = el?.text ?? "";

  const onInput = (e: InputEvent<HTMLTextAreaElement>) => {
    if (!layout.grow) return;
    const ta = e.currentTarget;
    ta.style.height = "auto";
    ta.style.height = `${ta.scrollHeight}px`;
    growH.current = ta.scrollHeight;
  };

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    e.stopPropagation();
    if (e.key === "Escape") {
      e.preventDefault();
      engine.commitEditing();
    }
  };

  // biome-ignore lint/correctness/useExhaustiveDependencies: session is an intentional re-run key for focus + grow
  useLayoutEffect(() => {
    const node = textareaRef.current;
    if (!node) return;
    node.focus();
    const len = node.value.length;
    node.setSelectionRange(len, len);
    if (layout.grow) {
      node.style.height = "auto";
      node.style.height = `${node.scrollHeight}px`;
      growH.current = node.scrollHeight;
    }
  }, [session, textareaRef, layout.grow]);

  return (
    <textarea
      key={session.id ?? `n:${session.world[0]},${session.world[1]}`}
      ref={textareaRef}
      data-testid="wb-text-edit"
      defaultValue={initial}
      spellCheck={false}
      rows={1}
      aria-label="编辑文字"
      onInput={onInput}
      onKeyDown={onKeyDown}
      onBlur={(e) => {
        // Remounting the textarea (session A→B) blurs the old node after startEdit
        // already opened B — ignore that stale blur so we don't flush B with A's value.
        if (e.currentTarget !== textareaRef.current) return;
        engine.commitEditing();
      }}
      style={{
        position: "absolute",
        left: layout.left,
        top: layout.top,
        width: layout.grow ? undefined : layout.width,
        minWidth: layout.grow ? layout.width : undefined,
        height: layout.grow ? (growH.current ?? layout.height) : layout.height,
        minHeight: layout.grow ? layout.height : undefined,
        transform: layout.rotate ? `rotate(${layout.rotate}rad)` : undefined,
        transformOrigin: "center center",
        fontSize: layout.fontSize,
        lineHeight: 1.3,
        textAlign: layout.align,
        color: layout.color,
        padding: layout.pad,
        boxSizing: "border-box",
        margin: 0,
        border: "none",
        outline: "1.5px solid var(--primary)",
        outlineOffset: 2,
        resize: "none",
        overflow: "hidden",
        background: "transparent",
        fontFamily: "ui-sans-serif, system-ui, sans-serif",
        zIndex: 5,
        caretColor: "var(--primary)",
        whiteSpace: "pre-wrap",
        wordBreak: "break-word",
      }}
    />
  );
}

function readLayout(
  engine: WhiteboardEngine,
  session: TextEditSession,
): EditOverlayLayout {
  const el = session.id
    ? (engine.getScene().find((e) => e.id === session.id) ?? null)
    : null;
  return editOverlayLayout(session, el, engine.getViewport());
}
