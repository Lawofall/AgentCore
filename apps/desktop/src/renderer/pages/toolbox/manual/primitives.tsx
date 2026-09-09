import { Button, SurfaceRowButton } from "@/components/ui";
import {
  AlertTriangle,
  Check,
  ChevronRight,
  Info,
  Lightbulb,
  type LucideIcon,
} from "lucide-react";
import type { ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { APP_PATHS } from "./paths";
import { resolveCanonicalSectionId, resolveSectionHref } from "./sectionIds";

export function GoLink({ to, children }: { to: string; children: ReactNode }) {
  const navigate = useNavigate();
  return (
    <Button
      variant="ghost"
      onClick={() => navigate(to)}
      className="h-auto px-0 py-0 font-medium text-primary underline-offset-2 hover:underline"
    >
      {children}
    </Button>
  );
}

/**
 * 手册内节间深链：`to` 为节 ID（见 sectionIds.ts）。
 * 同页优先滚动；DOM 未挂载时按注册表解析章 path 再 navigate。
 */
export function JumpLink({
  to,
  children,
}: { to: string; children: ReactNode }) {
  const navigate = useNavigate();
  return (
    <Button
      variant="ghost"
      onClick={() => {
        const id = resolveCanonicalSectionId(to);
        const el = document.getElementById(id);
        if (el) {
          el.scrollIntoView({ behavior: "smooth", block: "start" });
          return;
        }
        const href = resolveSectionHref(id);
        if (href) navigate(href);
      }}
      className="h-auto px-0 py-0 font-medium text-primary underline-offset-2 hover:underline"
    >
      {children}
    </Button>
  );
}

export function Lead({ children }: { children: ReactNode }) {
  return (
    <p className="text-sm leading-relaxed text-muted-foreground">{children}</p>
  );
}

const CALLOUT = {
  tip: {
    Icon: Lightbulb,
    box: "border-primary/30 bg-primary/5",
    icon: "text-primary",
  },
  info: {
    Icon: Info,
    box: "border-border bg-muted/30",
    icon: "text-muted-foreground",
  },
  warning: {
    Icon: AlertTriangle,
    box: "border-border bg-muted/40",
    icon: "text-muted-foreground",
  },
} as const;

export function Callout({
  variant = "tip",
  children,
}: {
  variant?: keyof typeof CALLOUT;
  children: ReactNode;
}) {
  const c = CALLOUT[variant];
  const Icon = c.Icon;
  return (
    <div className={`flex gap-3 rounded-xl border p-4 ${c.box}`}>
      <Icon size={16} className={`mt-0.5 shrink-0 ${c.icon}`} />
      <div className="text-sm leading-relaxed text-foreground">{children}</div>
    </div>
  );
}

export function CardGrid({
  cols = 2,
  children,
}: {
  cols?: 2 | 3;
  children: ReactNode;
}) {
  return (
    <div
      className={`grid gap-3 ${cols === 3 ? "sm:grid-cols-3" : "sm:grid-cols-2"}`}
    >
      {children}
    </div>
  );
}

export function InfoCard({
  icon,
  title,
  desc,
  highlight,
}: {
  icon?: ReactNode;
  title: string;
  desc: string;
  highlight?: boolean;
}) {
  return (
    <div
      className={`rounded-xl border p-4 ${highlight ? "border-primary/40 bg-primary/5" : "border-border bg-card"}`}
    >
      {icon && (
        <div className="mb-2 flex size-8 items-center justify-center rounded-lg bg-primary/10 text-primary">
          {icon}
        </div>
      )}
      <p className="text-sm font-medium text-foreground">{title}</p>
      <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
        {desc}
      </p>
    </div>
  );
}

export function Steps({
  items,
}: { items: { title: string; desc: ReactNode }[] }) {
  return (
    <ol className="space-y-0">
      {items.map((s, i) => (
        <li key={s.title} className="flex gap-3">
          <div className="flex flex-col items-center">
            <span className="z-10 flex size-6 shrink-0 items-center justify-center rounded-full bg-primary/10 text-xs font-medium text-primary">
              {i + 1}
            </span>
            {i < items.length - 1 && (
              <span className="my-1 w-px flex-1 bg-border" />
            )}
          </div>
          <div className="min-w-0 flex-1 pb-5">
            <p className="text-sm font-medium text-foreground">{s.title}</p>
            <p className="mt-0.5 text-xs leading-relaxed text-muted-foreground">
              {s.desc}
            </p>
          </div>
        </li>
      ))}
    </ol>
  );
}

export function Bullets({
  items,
}: { items: { title: string; desc: ReactNode }[] }) {
  return (
    <ul className="space-y-2.5">
      {items.map((b) => (
        <li key={b.title} className="flex gap-2.5">
          <span className="mt-[7px] size-1.5 shrink-0 rounded-full bg-primary/60" />
          <p className="text-sm leading-relaxed text-foreground">
            <span className="font-medium">{b.title}</span>
            <span className="text-muted-foreground"> — {b.desc}</span>
          </p>
        </li>
      ))}
    </ul>
  );
}

/** 好 / 差说法对照——用于「怎么下任务」这类「该怎么说」的示范。 */
export function DoDont({
  good,
  bad,
}: {
  good: { label?: string; items: ReactNode[] };
  bad: { label?: string; items: ReactNode[] };
}) {
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      <div className="rounded-xl border border-success/30 bg-success/5 p-4">
        <p className="mb-2 flex items-center gap-1.5 text-sm font-medium text-success">
          <Check size={14} className="shrink-0" />
          {good.label ?? "这样说"}
        </p>
        <ul className="space-y-1.5">
          {good.items.map((t, i) => (
            <li
              // biome-ignore lint/suspicious/noArrayIndexKey: 静态示例文案，无重排
              key={i}
              className="text-xs leading-relaxed text-foreground"
            >
              {t}
            </li>
          ))}
        </ul>
      </div>
      <div className="rounded-xl border border-destructive/30 bg-destructive/5 p-4">
        <p className="mb-2 flex items-center gap-1.5 text-sm font-medium text-destructive">
          <AlertTriangle size={14} className="shrink-0" />
          {bad.label ?? "别这样"}
        </p>
        <ul className="space-y-1.5">
          {bad.items.map((t, i) => (
            <li
              // biome-ignore lint/suspicious/noArrayIndexKey: 静态示例文案，无重排
              key={i}
              className="text-xs leading-relaxed text-muted-foreground"
            >
              {t}
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

export function Faq({ items }: { items: { q: string; a: ReactNode }[] }) {
  return (
    <div className="divide-y divide-border overflow-hidden rounded-xl border border-border bg-card">
      {items.map((f) => (
        <div key={f.q} className="p-4">
          <p className="text-sm font-medium text-foreground">{f.q}</p>
          <div className="mt-1 text-xs leading-relaxed text-muted-foreground">
            {f.a}
          </div>
        </div>
      ))}
    </div>
  );
}

/** 三列边界表——用于 Git / 代码能力等「会做 / 需你放行 / 不会做」说明。 */
export function BoundaryTable({
  rows,
}: {
  rows: { can: string; approve: string; wont: string }[];
}) {
  return (
    <div className="mt-2 overflow-hidden rounded-lg border border-border text-xs">
      <div className="grid grid-cols-3 border-b border-border bg-muted/40 font-medium text-foreground">
        <span className="px-3 py-2">会做</span>
        <span className="border-l border-border px-3 py-2">需你放行</span>
        <span className="border-l border-border px-3 py-2">不会做</span>
      </div>
      {rows.map((r) => (
        <div
          key={r.can}
          className="grid grid-cols-3 border-b border-border last:border-b-0"
        >
          <span className="px-3 py-2 leading-relaxed">{r.can}</span>
          <span className="border-l border-border px-3 py-2 leading-relaxed">
            {r.approve}
          </span>
          <span className="border-l border-border px-3 py-2 leading-relaxed">
            {r.wont}
          </span>
        </div>
      ))}
    </div>
  );
}

const DEFAULT_SETTINGS_ROWS: { label: string; desc: string; to: string }[] = [
  {
    label: "模型组合",
    desc: "账号默认组合与组合管理",
    to: APP_PATHS.more.model,
  },
  {
    label: "服务商",
    desc: "接入额度或自带 Key（BYOK）",
    to: APP_PATHS.more.providers,
  },
  {
    label: "提示词",
    desc: "所有对话共用的提示词，在工具箱查看与调整",
    to: APP_PATHS.toolbox.guidelines,
  },
  { label: "用量", desc: "查看花费与额度", to: APP_PATHS.more.usage },
  {
    label: "通用",
    desc: "界面主题与进阶开关",
    to: APP_PATHS.more.general,
  },
  {
    label: "快捷键",
    desc: "常用操作的键盘快捷键",
    to: APP_PATHS.more.shortcuts,
  },
  { label: "关于", desc: "版本、产品手册与法律信息", to: APP_PATHS.more.about },
];

/** 设置速查行——内容源可传入 rows；旧章 SettingsTable 仍用默认列表。 */
export function SettingsRows({
  rows,
}: {
  rows: { label: string; desc: string; to: string }[];
}) {
  const navigate = useNavigate();
  return (
    <div className="divide-y divide-border overflow-hidden rounded-xl border border-border bg-card">
      {rows.map((r) => (
        <SurfaceRowButton
          key={r.to}
          variant="settings"
          onClick={() => navigate(r.to)}
          className="h-auto w-full justify-start gap-3 rounded-none p-3 hover:bg-accent/50"
        >
          <div className="min-w-0 flex-1">
            <p className="text-sm font-medium text-foreground">{r.label}</p>
            <p className="text-xs text-muted-foreground">{r.desc}</p>
          </div>
          <ChevronRight size={16} className="shrink-0 text-muted-foreground" />
        </SurfaceRowButton>
      ))}
    </div>
  );
}

export function SettingsTable() {
  return <SettingsRows rows={DEFAULT_SETTINGS_ROWS} />;
}

export function SectionHeading({
  icon: Icon,
  index,
  title,
  id,
}: {
  icon: LucideIcon;
  index: number;
  title: string;
  id: string;
}) {
  return (
    <div id={id} className="flex items-center gap-3 scroll-mt-6">
      <span className="flex size-9 items-center justify-center rounded-xl bg-primary/10 text-primary">
        <Icon size={18} />
      </span>
      <div>
        <p className="text-xs font-medium text-muted-foreground">
          {String(index).padStart(2, "0")}
        </p>
        <h2 className="text-base font-medium text-foreground">{title}</h2>
      </div>
    </div>
  );
}
