import { PageContainer } from "@/components/layout/PageContainer";
import { SectionTabs } from "@/components/ui";
import { cn } from "@/lib/utils";
import { APP_PATHS } from "@/pages/toolbox/manual/paths";
import { Store } from "lucide-react";
import { Link, Outlet, useLocation } from "react-router-dom";
import { TOOLBOX_KINDS, TOOLBOX_KIND_ICON, TOOLBOX_KIND_LABEL } from "./kinds";

function toolboxShellHeading(pathname: string): string {
  if (pathname === APP_PATHS.toolbox.market) return "市场";
  for (const kind of TOOLBOX_KINDS) {
    if (pathname === APP_PATHS.toolbox.mine[kind]) {
      return TOOLBOX_KIND_LABEL[kind];
    }
  }
  return "工具箱";
}

/**
 * 工具箱壳：种类 tab 即顶栏；右槽「市场」。手册入口在设置 · 关于。侧栏已点名，不重复可见 h1。
 * 市场是顶栏入口，不是与种类对等的模式开关。本机插头是工具图鉴上的卡。
 * 提示词工作台贴边分栏（`padding none`）；其它种类仍走页面留白。
 */
export function ToolboxShell() {
  const location = useLocation();
  const market = location.pathname === APP_PATHS.toolbox.market;
  const tools = location.pathname === APP_PATHS.toolbox.mine.tools;
  const skills = location.pathname === APP_PATHS.toolbox.mine.skills;
  const fill = skills || market || tools;
  /** 提示词是文档工作台：种类 tab 下贴边分栏，不套页面内边距卡片。 */
  const flushWorkbench = skills;

  return (
    <PageContainer
      width={skills ? "full" : "canvas"}
      fill={fill}
      padding={flushWorkbench ? "none" : "page"}
    >
      <div className={flushWorkbench ? "shrink-0 pt-6" : undefined}>
        <h1 className="sr-only">{toolboxShellHeading(location.pathname)}</h1>
        <SectionTabs
          aria-label="工具箱种类"
          className={cn("shrink-0", flushWorkbench && "px-6")}
          action={
            <Link
              to={APP_PATHS.toolbox.market}
              aria-current={market ? "page" : undefined}
              className={cn(
                "inline-flex shrink-0 items-center gap-1 text-sm transition-colors",
                market
                  ? "text-foreground"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              <Store size={14} />
              市场
            </Link>
          }
          items={TOOLBOX_KINDS.map((kind) => {
            const Icon = TOOLBOX_KIND_ICON[kind];
            return {
              to: APP_PATHS.toolbox.mine[kind],
              label: TOOLBOX_KIND_LABEL[kind],
              icon: <Icon size={14} />,
              end: true,
            };
          })}
        />
      </div>

      <div
        className={
          flushWorkbench
            ? "flex min-h-0 flex-1 flex-col"
            : fill
              ? "mt-6 flex min-h-0 flex-1 flex-col"
              : "mt-6"
        }
      >
        <Outlet />
      </div>
    </PageContainer>
  );
}
