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
 * 市场是顶栏入口，不是与种类对等的模式开关。出厂工具与本机插头在提示词目录里。
 * 提示词与创作 / 工作流同一套画布留白；市场货架自己填满剩余高度。
 */
export function ToolboxShell() {
  const location = useLocation();
  const market = location.pathname === APP_PATHS.toolbox.market;

  return (
    <PageContainer width="canvas" fill={market} padding="page">
      <h1 className="sr-only">{toolboxShellHeading(location.pathname)}</h1>
      <SectionTabs
        aria-label="工具箱种类"
        className="shrink-0"
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

      <div className={market ? "mt-6 flex min-h-0 flex-1 flex-col" : "mt-6"}>
        <Outlet />
      </div>
    </PageContainer>
  );
}
