/** Unified-diff row chrome. Classification (layer C), not status. */

export type DiffChromeKind = "add" | "del" | "context";

export function diffSign(type: DiffChromeKind): string {
  if (type === "add") return "+";
  if (type === "del") return "-";
  return " ";
}

export function diffRowClass(type: DiffChromeKind): string {
  if (type === "add") return "bg-diff-add text-foreground";
  if (type === "del") return "bg-diff-del text-foreground";
  return "text-muted-foreground";
}

export function diffSignClass(type: DiffChromeKind): string {
  if (type === "add") return "bg-diff-add-gutter text-diff-add-fg";
  if (type === "del") return "bg-diff-del-gutter text-diff-del-fg";
  return "text-muted-foreground/50";
}
