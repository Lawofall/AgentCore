import { PageContainer } from "@/components/layout/PageContainer";
import { PageHeader, SectionTabs } from "@/components/ui";
import { APP_PATHS, TOOLBOX_PAGE_BACK } from "@/pages/toolbox/manual/paths";
import { useStandingInboxBadge } from "@/stores/standingInbox";
import { Outlet } from "react-router-dom";

/** Counts above this render as `99+` so a tab can't stretch. */
const MAX_BADGE = 99;

/**
 * 工具箱 · 自动化专页壳：任务 | 收件箱（同一对象的下划线 tab，不是能力导航）。
 */
export function AutomationsPage() {
  const inboxBadge = useStandingInboxBadge();

  return (
    <PageContainer width="canvas">
      <PageHeader title="自动化" back={TOOLBOX_PAGE_BACK} bordered={false} />

      <SectionTabs
        aria-label="自动化分区"
        items={[
          {
            to: APP_PATHS.toolbox.automations.root,
            label: "任务",
            end: true,
          },
          {
            to: APP_PATHS.toolbox.automations.inbox,
            label: "收件箱",
            badge:
              inboxBadge > 0 ? (
                <span
                  aria-label={`${inboxBadge} 条待处理`}
                  className="flex h-5 min-w-5 items-center justify-center rounded-full bg-primary/10 px-1 text-xs font-medium text-primary"
                >
                  {inboxBadge > MAX_BADGE ? `${MAX_BADGE}+` : inboxBadge}
                </span>
              ) : undefined,
          },
        ]}
      />

      <div className="mt-6">
        <Outlet />
      </div>
    </PageContainer>
  );
}
