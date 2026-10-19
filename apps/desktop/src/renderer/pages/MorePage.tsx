import { NarrowBackHeader } from "@/components/layout/NarrowBackHeader";
import { NarrowMenuButton } from "@/components/layout/NarrowMenuButton";
import { SectionLabel, SurfaceNavLink } from "@/components/ui";
import { useNarrowLayoutState } from "@/lib/narrowLayout";
import { NARROW_HIDDEN_SETTINGS_PATHS } from "@/lib/narrowProduct";
import { cn } from "@/lib/utils";
import { getLegalDoc } from "@/pages/legal/content";
import {
  Box,
  CreditCard,
  Gauge,
  GitBranch,
  Globe,
  Info,
  KeyRound,
  Keyboard,
  Layers,
  type LucideIcon,
  Shield,
  SlidersHorizontal,
  UserCog,
} from "lucide-react";
import { Outlet, useLocation, useNavigate } from "react-router-dom";

interface NavItem {
  icon: LucideIcon;
  label: string;
  path: string;
}

interface NavGroup {
  label: string;
  items: NavItem[];
}

// Settings are grouped by intent: 账户（含 Git 凭据 / 用量 / 订阅）、能力、模型（名单 / 服务商 / 联网搜索）、偏好（含关于）。
// 宽屏能力在工具箱，侧栏不放「能力」，模型组是「模型」「服务商」和「联网搜索」。窄屏没有工具箱，设置里保留「能力」。
// 旧 /more/feedback 书签见 router 重定向。
// 「外观」→「通用」（主题 / 字号 / 缩放 / 发送 / 系统通知 + 原本藏在关于页的诊断类开关）；旧路径见 router 重定向。
// Opening /more 宽屏落点见 MoreIndexRedirect；窄屏 /more 是设置列表，不重定向。
// 设定（画像 / 偏好 / 规则）在「文件」页，不设设置子页。
// 新会话边界：权限徽章里人选的那档（无设置子页；不标「默认」）。
// 产品公告 inbox 已迁 IM 官方号（消息页）；顶栏 Banner 仍走 notices/active。
const NAV_GROUPS: NavGroup[] = [
  {
    label: "账户",
    items: [
      { icon: UserCog, label: "账户设置", path: "/more/account" },
      { icon: GitBranch, label: "Git 凭据", path: "/more/git" },
      { icon: Gauge, label: "用量", path: "/more/usage" },
      { icon: CreditCard, label: "订阅", path: "/more/subscription" },
    ],
  },
  {
    label: "能力",
    items: [{ icon: Layers, label: "能力", path: "/more/model" }],
  },
  {
    label: "模型",
    items: [
      { icon: Box, label: "模型", path: "/more/models" },
      { icon: KeyRound, label: "服务商", path: "/more/providers" },
      { icon: Globe, label: "联网搜索", path: "/more/search" },
    ],
  },
  {
    label: "偏好",
    items: [
      { icon: SlidersHorizontal, label: "通用", path: "/more/general" },
      { icon: Shield, label: "消息隐私", path: "/more/messages" },
      { icon: Keyboard, label: "快捷键", path: "/more/shortcuts" },
      { icon: Info, label: "关于", path: "/more/about" },
    ],
  },
];

/** 窄屏不上：Git 本机凭据、快捷键。通用露出字号与发送方式。联网搜索单独一页，窄屏仍在。权威 → 前端技术 §五。 */

function visibleGroups(narrow: boolean): NavGroup[] {
  return NAV_GROUPS.map((group) => ({
    ...group,
    items: group.items.filter(
      (item) =>
        (narrow || item.path !== "/more/model") &&
        (!narrow || !NARROW_HIDDEN_SETTINGS_PATHS.has(item.path)),
    ),
  })).filter((group) => group.items.length > 0);
}

function MoreNav({ groups }: { groups: NavGroup[] }) {
  return (
    <nav className="flex h-full min-w-0 flex-1 flex-col overflow-y-auto bg-muted/30 py-4">
      <div className="space-y-4 px-2">
        {groups.map((group) => (
          <div key={group.label}>
            <SectionLabel className="px-3 pb-1">{group.label}</SectionLabel>
            <div className="space-y-0.5">
              {group.items.map((item) => (
                <NavRow key={item.path} item={item} />
              ))}
            </div>
          </div>
        ))}
      </div>
    </nav>
  );
}

function titleForPath(pathname: string, groups: NavGroup[]): string {
  const legalMatch = /^\/more\/legal\/([^/]+)/.exec(pathname);
  if (legalMatch) {
    const doc = getLegalDoc(legalMatch[1]);
    if (doc) return doc.title;
  }
  const items = groups.flatMap((g) => g.items);
  const exact = items.find((i) => i.path === pathname);
  if (exact) return exact.label;
  const prefix = items.find((i) => pathname.startsWith(`${i.path}/`));
  return prefix?.label ?? "设置";
}

export function MorePage() {
  const { isNarrow } = useNarrowLayoutState();
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const groups = visibleGroups(isNarrow);
  const isIndex = pathname === "/more";

  if (isNarrow) {
    if (isIndex) {
      return (
        <div className="flex h-full w-full flex-col">
          <header className="flex h-12 shrink-0 items-center gap-1 border-b border-border bg-card px-2 pt-[env(safe-area-inset-top)]">
            <NarrowMenuButton />
            <h1 className="min-w-0 flex-1 truncate text-center text-sm font-medium">
              设置
            </h1>
          </header>
          <MoreNav groups={groups} />
        </div>
      );
    }
    return (
      <div className="flex h-full w-full flex-col">
        <NarrowBackHeader
          title={titleForPath(pathname, NAV_GROUPS)}
          onBack={() => navigate("/more")}
        />
        <div className="min-h-0 flex-1 overflow-y-auto">
          <div className="w-full max-w-3xl px-4 py-6">
            <Outlet />
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-full w-full">
      {/* Secondary navigation */}
      <nav className="flex w-[220px] shrink-0 flex-col overflow-y-auto border-r border-border bg-muted/30 py-4">
        <div className="space-y-4 px-2">
          {groups.map((group) => (
            <div key={group.label}>
              <SectionLabel className="px-3 pb-1">{group.label}</SectionLabel>
              <div className="space-y-0.5">
                {group.items.map((item) => (
                  <NavRow key={item.path} item={item} />
                ))}
              </div>
            </div>
          ))}
        </div>
      </nav>

      {/* Content area — a left-anchored reading column (split layout, so it sets
          its own width rather than the centered content gradient). */}
      <div className="h-full w-full overflow-y-auto">
        <div
          className={cn(
            "w-full px-6 py-6",
            pathname === "/more/subscription" ? "max-w-4xl" : "max-w-3xl",
          )}
        >
          <Outlet />
        </div>
      </div>
    </div>
  );
}

/** One grouped nav row. */
function NavRow({ item }: { item: NavItem }) {
  const Icon = item.icon;
  return (
    <SurfaceNavLink to={item.path} className="relative">
      <Icon size={16} className="shrink-0" />
      <span className="min-w-0 flex-1 truncate">{item.label}</span>
    </SurfaceNavLink>
  );
}
