import {
  type ToolboxTab,
  assemblyHomeFromPath,
  assemblyShelfPath,
} from "@/pages/toolbox/assemblyTabs";
import { APP_PATHS } from "@/pages/toolbox/manual/paths";
import { Navigate, useLocation } from "react-router-dom";

/** Old official shelf. `?tool=` / `?skill=` open the read dialog on this page. */
export function OfficialShelfRedirect() {
  const { search } = useLocation();
  const params = new URLSearchParams(
    search.startsWith("?") ? search.slice(1) : search,
  );
  const hash =
    params.get("skill") && !params.get("tool") ? "#prompts" : "#tools";
  return <Navigate to={`${APP_PATHS.toolbox.root}${search}${hash}`} replace />;
}

/** Old section URLs land on the one shelf, at that section. */
export function AssemblySectionRedirect({ tab }: { tab: ToolboxTab }) {
  const { pathname } = useLocation();
  return (
    <Navigate
      to={assemblyShelfPath(tab, assemblyHomeFromPath(pathname))}
      replace
    />
  );
}
