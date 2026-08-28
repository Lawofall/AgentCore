import { handlePlainCopy } from "@/lib/clipboardPlain";
import { useEffect } from "react";

/**
 * Document-level copy: selection often lives in a user bubble while focus stays
 * on the composer, so a bubble `onCopy` never fires.
 */
export function usePlainCopyListener(): void {
  useEffect(() => {
    const onCopy = (e: ClipboardEvent) => {
      handlePlainCopy(e);
    };
    document.addEventListener("copy", onCopy);
    return () => document.removeEventListener("copy", onCopy);
  }, []);
}
