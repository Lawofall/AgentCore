import { NarrowBackHeader } from "@/components/layout/NarrowBackHeader";
import { SectionLabel, SurfaceNavLink } from "@/components/ui";
import { useNarrowLayoutState } from "@/lib/narrowLayout";
import { getLegalDoc } from "@/pages/legal/content";
import {
  Cpu,
  Gauge,
  GitBranch,
  Info,
  KeyRound,
  Keyboard,
  type LucideIcon,
  MessageSquarePlus,
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

// Settings are grouped by intent, four groups over ten items: 账户（含 Git 凭据 /
// 用量）、模型（组合 + Key 相邻）、偏好、关于（含反馈）。之前是六组，其中三组只有
// 一项——组标题比内容还多，扫读全是分隔线。合并时只动分组，路径不变。
// 「外观」→「通用」（多收了原本藏在关于页的诊断类开关）；旧路径见 router 重定向。
// Opening /more 宽屏落点见 MoreIndexRedirect；窄屏 /more 是设置列表，不重定向。
// 「自动化」已迁至工具箱 #/toolbox/automations。
// 设定（画像 / 偏好 / 规则）在「文件」页，不设设置子页。
// 新会话默认权限配方：对话内权限徽章「设为新会话默认」（无设置子页）。
// 产品公告 inbox 已迁 IM 官方号（消息页）；顶栏 Banner 仍走 notices/active。
const NAV_GROUPS: NavGroup[] = [
  {
    label: "账户",
    items: [
      { icon: UserCog, label: "账户设置", path: "/more/account" },
      { icon: GitBranch, label: "Git 凭据", path: "/more/git" },
      { icon: Gauge, label: "用量", path: "/more/usage" },
    ],
  },
  {
    label: "模型",
    items: [
      { icon: Cpu, label: "模型", path: "/more/model" },
      { icon: KeyRound, label: "服务商", path: "/more/providers" },
    ],
  },
  {
    label: "偏好",
    items: [
      { icon: SlidersHorizontal, label: "通用", path: "/more/general" },
      { icon: Shield, label: "消息隐私", path: "/more/messages" },
      { icon: Keyboard, label: "快捷键", path: "/more/shortcuts" },
    ],
  },
  {
    label: "关于",
    items: [
      { icon: Info, label: "关于", path: "/more/about" },
      { icon: MessageSquarePlus, label: "反馈", path: "/more/feedback" },
    ],
  },
];

/** 窄屏不上：Git 本机凭据、暗色/诊断、快捷键、反馈。权威 → 前端技术 §五。 */
const NARROW_HIDE_PATHS = new Set([
  "/more/git",
  "/more/general",
  "/more/shortcuts",
  "/more/feedback",
]);

function visibleGroups(narrow: boolean): NavGroup[] {
  if (!narrow) return NAV_GROUPS;
  return NAV_GROUPS.map((group) => ({
    ...group,
    items: group.items.filter((item) => !NARROW_HIDE_PATHS.has(item.path)),
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
        <div className="w-full max-w-3xl px-6 py-6">
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
