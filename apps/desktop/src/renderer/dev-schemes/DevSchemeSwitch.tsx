import { IconButton, SegmentedControl } from "@/components/ui";
import {
  CURRENT_SCHEME_ID,
  focusSchemeGroup,
  listSchemeGroups,
  listSchemes,
  setSchemeId,
  setSchemePanelOpen,
  useSchemeId,
  useSchemePanelOpen,
} from "@/lib/devSchemes";
import { chord } from "@/lib/shortcuts";
import { X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import "./register";

/**
 * DEV 界面方案面板。默认不画；命令面板「开发」或 Ctrl/Cmd+. 打开。
 * × / Esc / 点外面关掉，不留角落按钮。生产构建不挂。
 */
export function DevSchemeSwitch() {
  const groups = listSchemeGroups();
  const [group, setGroup] = useState(groups[0]?.id ?? "");
  const open = useSchemePanelOpen();
  const rootRef = useRef<HTMLDivElement>(null);
  const active = useSchemeId(group);

  useEffect(() => {
    if (!group && groups[0]) setGroup(groups[0].id);
  }, [group, groups]);

  useEffect(() => {
    if (group) focusSchemeGroup(group);
  }, [group]);

  useEffect(() => {
    if (!open) return;
    const dismiss = () => setSchemePanelOpen(false);
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      e.preventDefault();
      dismiss();
    };
    const onPtr = (e: PointerEvent) => {
      const node = rootRef.current;
      if (!node) return;
      if (e.target instanceof Node && node.contains(e.target)) return;
      dismiss();
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("pointerdown", onPtr);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("pointerdown", onPtr);
    };
  }, [open]);

  if (!open || groups.length === 0) return null;

  const currentGroup = groups.find((row) => row.id === group) ?? groups[0];
  const schemes = listSchemes(currentGroup.id);
  const items = [
    { value: CURRENT_SCHEME_ID, label: "现行" },
    ...schemes.map((scheme) => ({ value: scheme.id, label: scheme.label })),
  ];

  return (
    <div
      ref={rootRef}
      data-testid="dev-scheme-switch"
      className="pointer-events-auto fixed top-14 right-4 z-[60] w-80 rounded-xl border border-border bg-popover p-2 shadow-overlay [-webkit-app-region:no-drag]"
    >
      <div className="mb-2 flex items-center gap-2">
        <p className="min-w-0 flex-1 truncate text-xs text-muted-foreground">
          {currentGroup.label}
          <span className="ml-1 text-muted-foreground/80">{chord(".")}</span>
        </p>
        {groups.length > 1 ? (
          <select
            aria-label="方案组"
            className="max-w-28 truncate rounded-lg border border-border bg-background px-1.5 py-0.5 text-xs"
            value={currentGroup.id}
            onChange={(e) => setGroup(e.target.value)}
          >
            {groups.map((row) => (
              <option key={row.id} value={row.id}>
                {row.label}
              </option>
            ))}
          </select>
        ) : null}
        <IconButton
          type="button"
          size="sm"
          aria-label="关闭界面方案"
          title="关闭"
          onClick={() => setSchemePanelOpen(false)}
        >
          <X size={14} />
        </IconButton>
      </div>
      <SegmentedControl
        aria-label="界面方案"
        value={active}
        onChange={(id) => setSchemeId(currentGroup.id, id)}
        items={items}
        className="flex-wrap"
      />
    </div>
  );
}
