import { Button } from "@/components/ui";
import { AlertTriangle, Home, RotateCw } from "lucide-react";
import { useEffect } from "react";
import {
  isRouteErrorResponse,
  useNavigate,
  useRouteError,
} from "react-router-dom";

/** Flatten whatever React Router caught into a readable message + stack for the
 * dev surface / console. A render error is a real `Error` (name/message/stack); a
 * thrown Response folds to its status; anything else is best-effort stringified. */
function describeRouteError(error: unknown): string {
  if (error instanceof Error)
    return `${error.name}: ${error.message}\n\n${error.stack ?? "(no stack)"}`;
  if (isRouteErrorResponse(error)) {
    const data =
      typeof error.data === "string"
        ? error.data
        : JSON.stringify(error.data, null, 2);
    return `${error.status} ${error.statusText}\n${data}`;
  }
  try {
    return JSON.stringify(error, null, 2);
  } catch {
    return String(error);
  }
}

/**
 * App-styled fallback for the root route's `errorElement`. React Router renders
 * this for an unmatched path (404) or any error thrown while rendering/loading a
 * route, replacing its bare-bones default ("Unexpected Application Error · Hey
 * developer 👋"). Keeps the user on a themed surface with a clear way back rather
 * than a dead end. A render error is recovered by navigating home (remounts the
 * tree); the reload button is the hard fallback when state is wedged.
 *
 * The boundary used to swallow the error entirely; it now logs it (so it lands in
 * DevTools / the dev terminal forwarder) and, in dev, shows the message + stack
 * inline so an intermittent render crash is diagnosable without opening DevTools.
 */
export function RouteError() {
  const error = useRouteError();
  const navigate = useNavigate();
  const is404 = isRouteErrorResponse(error) && error.status === 404;

  // Surface the real cause: a 404 is expected (bad hash), but any other error is
  // an actual render/loader crash worth logging with its stack every time.
  useEffect(() => {
    if (!is404)
      console.error("[RouteError] 路由渲染/加载抛出未捕获错误:", error);
  }, [error, is404]);

  const title = is404 ? "页面不存在" : "出了点问题";
  const detail = is404 ? "这个地址没有对应的页面。" : null;
  const devDetail =
    import.meta.env.DEV && !is404 ? describeRouteError(error) : null;

  return (
    <div className="flex h-screen w-screen flex-col items-center justify-center gap-4 bg-background px-6 text-center">
      <div className="flex size-12 items-center justify-center rounded-full bg-muted/40 text-muted-foreground">
        <AlertTriangle size={24} />
      </div>
      <div className="space-y-1">
        <h1 className="text-xl font-semibold text-foreground">{title}</h1>
        {detail && (
          <p className="max-w-sm text-sm text-muted-foreground">{detail}</p>
        )}
      </div>
      <div className="flex items-center gap-2">
        <Button
          size="md"
          icon={<Home size={15} />}
          onClick={() => navigate("/")}
        >
          回到对话
        </Button>
        <Button
          variant="neutral"
          size="md"
          icon={<RotateCw size={15} />}
          onClick={() => window.location.reload()}
        >
          重新加载
        </Button>
      </div>
      {/* Dev-only crash detail: the message + JS stack of whatever threw, so an
          intermittent render error is diagnosable straight from the screen. */}
      {devDetail && (
        <pre className="mt-2 max-h-72 w-full max-w-2xl overflow-auto whitespace-pre-wrap rounded-lg border border-border bg-muted/40 p-3 text-left text-xs text-muted-foreground">
          {devDetail}
        </pre>
      )}
    </div>
  );
}
