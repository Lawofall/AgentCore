import {
  SettingRow,
  SettingsSection,
  SettingsStack,
} from "@/components/settings";
import { PageHeader } from "@/components/ui";
import { Switch } from "@/components/ui/Switch";
import { type Theme, resolveDark } from "@/lib/theme";
import { cn } from "@/lib/utils";
import { useUIStore } from "@/stores/ui";
import { type LucideIcon, Monitor, Moon, Sun } from "lucide-react";
import { SearchConnectionSection } from "./SearchConnectionSettings";

interface ThemeOption {
  value: Theme;
  label: string;
  description: string;
  icon: LucideIcon;
}

const THEME_OPTIONS: ThemeOption[] = [
  {
    value: "light",
    label: "浅色",
    description: "始终使用浅色界面。",
    icon: Sun,
  },
  {
    value: "dark",
    label: "深色",
    description: "始终使用深色界面。",
    icon: Moon,
  },
  {
    value: "system",
    label: "跟随系统",
    description: "随操作系统的外观自动切换。",
    icon: Monitor,
  },
];

/**
 * 通用设置（/more/general）— 主题与联网搜索。
 *
 * 主题写入共享的 `useUIStore.theme`（持久化到 localStorage），应用由 `lib/theme.ts`
 * 统一收口（AppShell 的 `useApplyTheme` 切 root `.dark` 类、`系统`档随 OS 跟随），
 * 与命令面板的「切换主题」命令同一条链路——这里只是它的可视化入口。
 *
 * 旧路径 `/more/appearance` 在 router 里重定向到这里。
 */
export function GeneralSettings() {
  const theme = useUIStore((s) => s.theme);
  const setTheme = useUIStore((s) => s.setTheme);
  const developerMode = useUIStore((s) => s.developerMode);
  const setDeveloperMode = useUIStore((s) => s.setDeveloperMode);

  return (
    <div>
      <PageHeader title="通用" />

      <SettingsStack>
        <SettingsSection title="开发者模式">
          <SettingRow
            label="查看这一回合"
            description="打开后，回复底栏可以打开这一回合的记录。"
            control={
              <Switch
                checked={developerMode}
                label="开发者模式"
                onCheckedChange={setDeveloperMode}
              />
            }
          />
        </SettingsSection>

        <SettingsSection title="主题" contentClassName="space-y-2">
          {THEME_OPTIONS.map((option) => (
            <ThemeRow
              key={option.value}
              option={option}
              selected={theme === option.value}
              onSelect={() => setTheme(option.value)}
            />
          ))}
        </SettingsSection>

        <SearchConnectionSection />
      </SettingsStack>
    </div>
  );
}

/** One selectable theme row: icon badge + label/description. The 跟随系统 row
 * also shows what it currently resolves to. */
function ThemeRow({
  option,
  selected,
  onSelect,
}: {
  option: ThemeOption;
  selected: boolean;
  onSelect: () => void;
}) {
  const Icon = option.icon;
  const resolvedHint =
    option.value === "system"
      ? ` · 当前解析为「${resolveDark("system") ? "深色" : "浅色"}」`
      : "";

  return (
    <SettingRow
      variant="select"
      selected={selected}
      onClick={onSelect}
      label={option.label}
      description={`${option.description}${resolvedHint}`}
      leading={
        <span
          className={cn(
            "flex size-8 shrink-0 items-center justify-center rounded-lg",
            selected
              ? "bg-primary/10 text-primary"
              : "bg-muted text-muted-foreground",
          )}
        >
          <Icon size={16} />
        </span>
      }
    />
  );
}
