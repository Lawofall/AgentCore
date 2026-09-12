import { Button } from "@/components/ui/Button";
import { Page } from "@/components/ui/Page";
import { Compass } from "lucide-react";
import { useLocation, useNavigate } from "react-router-dom";

/**
 * Unmatched route. Previously these fell through to a blank content area with the
 * sidebar still rendered, which reads as a broken page rather than a wrong address —
 * a stale bookmark like `/analytics/spend` had no way to tell you it was stale.
 */
export function NotFoundPage() {
  const navigate = useNavigate();
  const location = useLocation();

  return (
    <Page>
      <div className="flex min-h-[60vh] flex-col items-center justify-center gap-3 text-center">
        <div className="flex size-10 items-center justify-center rounded-full bg-muted text-muted-foreground">
          <Compass size={18} />
        </div>
        <p className="text-sm font-medium text-foreground">页面不存在</p>
        <p className="max-w-md text-sm text-muted-foreground">
          没有匹配
          <code className="mx-1 rounded bg-muted px-1.5 py-0.5 font-mono text-xs">
            {location.pathname}
          </code>
          的页面，可能是链接过期或地址有误。
        </p>
        <div className="mt-2 flex items-center gap-2">
          <Button size="sm" onClick={() => navigate("/overview")}>
            回到总览
          </Button>
          <Button variant="outline" size="sm" onClick={() => navigate(-1)}>
            返回上一页
          </Button>
        </div>
      </div>
    </Page>
  );
}
