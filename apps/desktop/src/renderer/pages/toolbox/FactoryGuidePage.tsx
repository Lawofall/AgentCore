import { APP_PATHS } from "@/pages/toolbox/manual/paths";
import { Navigate, useSearchParams } from "react-router-dom";

/** 旧 /toolbox/guides：?tool= / ?skill= 落到组装页并打开读卡，其余收到交代。 */
export function FactoryGuidePage() {
  const [params] = useSearchParams();
  const next = new URLSearchParams();
  const tool = params.get("tool");
  const skill = params.get("skill");
  if (tool) next.set("tool", tool);
  if (skill) next.set("skill", skill);
  const q = next.toString();
  if (!tool && !skill) {
    return <Navigate to={APP_PATHS.toolbox.mine.skills} replace />;
  }
  const hash = tool ? "#tools" : "#prompts";
  return (
    <Navigate
      to={`${APP_PATHS.toolbox.root}${q ? `?${q}` : ""}${hash}`}
      replace
    />
  );
}
