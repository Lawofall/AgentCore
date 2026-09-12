import type React from "react";
import { useEffect, useRef, useState } from "react";
import { FileTypeIcon } from "./FileTypeIcon";

/**
 * 内联编辑行：与树行单列对齐。
 * - 文件：icon = 类型图标（占 chevron/类型 那一列）
 * - 目录：icon = null → 13px 空占位（对齐 chevron，无第二 Folder 图标）
 */
export function InlineRow({
  indent,
  icon,
  children,
}: {
  indent: number;
  /** 文件传类型图标；目录传 null（仅 chevron 列占位）。 */
  icon: React.ReactNode | null;
  children: React.ReactNode;
}) {
  return (
    <div
      className="flex items-center gap-1.5 rounded-lg pr-1 text-xs"
      style={{ paddingLeft: indent }}
    >
      {icon != null ? (
        <span className="shrink-0">{icon}</span>
      ) : (
        <span className="w-[13px] shrink-0" aria-hidden="true" />
      )}
      {children}
    </div>
  );
}

export function InlineCreateRow({
  kind,
  depth,
  indentBase = 0,
  onSubmit,
  onCancel,
}: {
  kind: "file" | "dir";
  depth: number;
  indentBase?: number;
  onSubmit: (name: string) => void;
  onCancel: () => void;
}) {
  return (
    <li>
      <InlineRow
        indent={depth * 14 + 8 + indentBase}
        icon={kind === "dir" ? null : <FileTypeIcon size={13} />}
      >
        <InlineInput initial="" onSubmit={onSubmit} onCancel={onCancel} />
      </InlineRow>
    </li>
  );
}

export function InlineInput({
  initial,
  onSubmit,
  onCancel,
  ariaLabel,
}: {
  initial: string;
  onSubmit: (value: string) => void;
  onCancel: () => void;
  ariaLabel?: string;
}) {
  const [value, setValue] = useState(initial);
  const ref = useRef<HTMLInputElement>(null);

  useEffect(() => {
    ref.current?.focus();
    ref.current?.select();
  }, []);

  return (
    <input
      ref={ref}
      value={value}
      aria-label={ariaLabel}
      onChange={(e) => setValue(e.target.value)}
      onClick={(e) => e.stopPropagation()}
      onKeyDown={(e) => {
        if (e.key === "Enter") onSubmit(value);
        else if (e.key === "Escape") onCancel();
      }}
      onBlur={onCancel}
      className="my-0.5 h-5 min-w-0 flex-1 rounded border border-primary/50 bg-background px-1 text-xs outline-none"
    />
  );
}
