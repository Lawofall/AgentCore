import { PageContainer } from "@/components/layout/PageContainer";
import { Outlet } from "react-router-dom";

/**
 * 工具箱壳：目录画布。装配是一页货架，不画可见标题。
 * 市场是深页。出厂说明书从组装页的工具矮卡打开。手册入口在设置 · 关于。
 */
export function ToolboxShell() {
  return (
    <PageContainer width="canvas" padding="page">
      <Outlet />
    </PageContainer>
  );
}
