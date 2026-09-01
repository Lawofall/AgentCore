import {
  SettingRow,
  SettingsSection,
  SettingsStack,
} from "@/components/settings";
import { Card } from "@/components/ui";
import {
  COMMAND_CATEGORY_ORDER,
  type PaletteCommand,
  buildPaletteCommands,
} from "@/lib/paletteCommands";
import { GLOBAL_SHORTCUTS, shortcutChords } from "@/lib/shortcuts";
import { useSidebarStore } from "@/stores/sidebar";
import { useUIStore } from "@/stores/ui";
import { useMemo } from "react";
import { useNavigate } from "react-router-dom";
import { SettingsHeader } from "./SettingsHeader";

/**
 * 快捷键设置（/more/shortcuts）— 快捷键与命令参考。
 *
 * Both sections render from the same sources the rest of the app uses, so this
 * page can never drift: 全局快捷键 from `lib/shortcuts.GLOBAL_SHORTCUTS` (the very
 * table the AppShell handler dispatches off), and 命令面板命令 from
 * `buildPaletteCommands` (the registry the palette renders). It is a read-only
 * reference — running happens via the chords or the palette itself.
 */
export function ShortcutsSettings() {
  const navigate = useNavigate();
  const theme = useUIStore((s) => s.theme);
  const sidebarCollapsed = useSidebarStore((s) => s.collapsed);

  // Built only to read each command's title / icon / shortcut for display; the
  // `run` closures are never invoked here.
  const commands = useMemo(
    () =>
      buildPaletteCommands({
        navigate,
        theme,
        sidebarCollapsed,
        openBookmarksInPalette: () => {},
      }),
    [navigate, theme, sidebarCollapsed],
  );

  return (
    <div>
      <SettingsHeader
        title="快捷键"
        description={
          <>
            全局快捷键随处可用；命令面板（
            {shortcutChords(GLOBAL_SHORTCUTS[0])[0]}
            ）里可搜索并运行下列所有命令。
          </>
        }
      />

      <SettingsStack>
        <SettingsSection title="全局快捷键" titleSize="base">
          <Card className="overflow-hidden">
            {GLOBAL_SHORTCUTS.map((s, i) => (
              <ShortcutRow
                key={s.id}
                divider={i > 0}
                label={s.label}
                chords={shortcutChords(s)}
              />
            ))}
            {/* Esc is owned by the dialog (Radix), not the global handler — listed
                here for completeness so the reference is whole. */}
            <ShortcutRow divider label="关闭命令面板" chords={["Esc"]} />
          </Card>
        </SettingsSection>

        <SettingsSection
          title="命令面板命令"
          titleSize="base"
          description={
            <>
              按 {shortcutChords(GLOBAL_SHORTCUTS[0])[0]}{" "}
              打开命令面板后输入即可运行。
            </>
          }
          contentClassName="space-y-4"
        >
          {COMMAND_CATEGORY_ORDER.map((category) => {
            const items = commands.filter((c) => c.category === category);
            if (items.length === 0) return null;
            return (
              <div key={category}>
                <p className="px-1 pb-1 text-xs font-medium text-muted-foreground">
                  {category}
                </p>
                <Card className="overflow-hidden">
                  {items.map((c, i) => (
                    <CommandRow key={c.id} divider={i > 0} cmd={c} />
                  ))}
                </Card>
              </div>
            );
          })}
        </SettingsSection>
      </SettingsStack>
    </div>
  );
}

/** A small key-cap chip. */
function Kbd({ children }: { children: React.ReactNode }) {
  return (
    <kbd className="rounded-lg border border-border bg-muted px-1.5 py-0.5 text-xs text-muted-foreground">
      {children}
    </kbd>
  );
}

/** One global-shortcut row: action label + its chord(s) (alternates joined by 或). */
function ShortcutRow({
  divider,
  label,
  chords,
}: {
  divider: boolean;
  label: string;
  chords: string[];
}) {
  return (
    <SettingRow
      surface="list"
      divider={divider}
      label={label}
      control={
        <span className="flex shrink-0 items-center gap-1">
          {chords.map((c, i) => (
            <span key={c} className="flex items-center gap-1">
              {i > 0 && (
                <span className="text-xs text-muted-foreground">或</span>
              )}
              <Kbd>{c}</Kbd>
            </span>
          ))}
        </span>
      }
    />
  );
}

/** One palette-command row: icon + title + its shortcut (or state hint). */
function CommandRow({
  divider,
  cmd,
}: { divider: boolean; cmd: PaletteCommand }) {
  const Icon = cmd.icon;
  return (
    <SettingRow
      surface="list"
      divider={divider}
      leading={<Icon size={16} className="shrink-0 text-muted-foreground" />}
      label={cmd.title}
      control={
        cmd.shortcut ? (
          <Kbd>{cmd.shortcut}</Kbd>
        ) : cmd.hint ? (
          <span className="shrink-0 text-xs text-muted-foreground">
            {cmd.hint}
          </span>
        ) : undefined
      }
    />
  );
}
