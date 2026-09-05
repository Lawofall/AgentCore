import {
  SettingRow,
  SettingsSection,
  SettingsStack,
} from "@/components/settings";
import { PageHeader } from "@/components/ui";
import { Switch } from "@/components/ui/Switch";
import { hasLocalEngine } from "@/lib/capabilities";
import { type Theme, resolveDark } from "@/lib/theme";
import { cn } from "@/lib/utils";
import { clearSidecarHealth } from "@/services/sidecarHealth";
import { useUIStore } from "@/stores/ui";
import { type LucideIcon, Monitor, Moon, Sun } from "lucide-react";

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
 * 通用设置（/more/general）— 主题 + 进阶开关。
 *
 * 主题写入共享的 `useUIStore.theme`（持久化到 localStorage），应用由 `lib/theme.ts`
 * 统一收口（AppShell 的 `useApplyTheme` 切 root `.dark` 类、`系统`档随 OS 跟随），
 * 与命令面板的「切换主题」命令同一条链路——这里只是它的可视化入口。
 *
 * 旧「外观」页只有主题一块、下半屏全空；「允许本机执行」曾藏在「关于」里没人找得到，
 * 故两者合页。旧路径 `/more/appearance` 在 router 里重定向到这里。
 */
export function GeneralSettings() {
  const theme = useUIStore((s) => s.theme);
  const setTheme = useUIStore((s) => s.setTheme);
  const showAdvanced = hasLocalEngine();

  return (
    <div>
      <PageHeader title="通用" />

      <SettingsStack>
        <SettingsSection
          title="主题"
          description="也可在命令面板（Ctrl/Cmd+K）中快速切换。"
          contentClassName="space-y-2"
        >
          {THEME_OPTIONS.map((option) => (
            <ThemeRow
              key={option.value}
              option={option}
              selected={theme === option.value}
              onSelect={() => setTheme(option.value)}
            />
          ))}
        </SettingsSection>

        {showAdvanced ? <AdvancedSection /> : null}
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

/**
 * 进阶开关——仅有本机引擎时由调用方挂载整段。
 *
 * 允许本机执行：强制关走云。展示用 `preference !== "off"`（unset 与 on 都算
 * 允许），勿绑 `sidecarEnabled`（unset→默认 false，会显示关却仍默认同侧）。
 */
function AdvancedSection() {
  const sidecarPreference = useUIStore((s) => s.sidecarPreference);
  const setSidecarEnabled = useUIStore((s) => s.setSidecarEnabled);

  const localEngineAllowed = sidecarPreference !== "off";
  const onToggleLocalEngine = (next: boolean): void => {
    setSidecarEnabled(next);
    if (next) clearSidecarHealth();
  };

  return (
    <SettingsSection
      title="进阶"
      description="本机文件夹是否走同侧引擎（不是离线）。"
      divider
      contentClassName="space-y-2"
    >
      <SettingRow
        align="start"
        label="允许本机执行"
        description="关闭后全部走云端过桥；开启则本机文件夹默认同侧引擎（与盘同侧）。启动失败会自动改走云。「我的文件」始终走云。这不是离线模式：AI 推理仍在云端，断网时只能浏览缓存与本机文件（只读），不能发送。"
        control={
          <Switch
            checked={localEngineAllowed}
            onCheckedChange={onToggleLocalEngine}
            label="允许本机执行"
          />
        }
      />
    </SettingsSection>
  );
}
