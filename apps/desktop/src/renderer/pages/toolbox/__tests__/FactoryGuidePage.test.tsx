import { FactoryGuidePage } from "@/pages/toolbox/FactoryGuidePage";
import { OfficialShelfRedirect } from "@/pages/toolbox/assemblyPages";
import { APP_PATHS } from "@/pages/toolbox/manual/paths";
// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import {
  MemoryRouter,
  Route,
  Routes,
  useLocation,
  useSearchParams,
} from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";

afterEach(cleanup);

function CatalogProbe() {
  const [params] = useSearchParams();
  const loc = useLocation();
  return (
    <div
      data-testid="catalog"
      data-path={loc.pathname}
      data-hash={loc.hash}
      data-tool={params.get("tool") ?? ""}
      data-skill={params.get("skill") ?? ""}
    />
  );
}

function renderGuides(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/toolbox/guides" element={<FactoryGuidePage />} />
        <Route path="/toolbox" element={<CatalogProbe />} />
        <Route path="/toolbox/mine/skills" element={<CatalogProbe />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("FactoryGuidePage", () => {
  it("旧说明书书签收向我的", () => {
    renderGuides(APP_PATHS.toolbox.guides);
    expect(screen.getByTestId("catalog").getAttribute("data-path")).toBe(
      APP_PATHS.toolbox.mine.skills,
    );
  });

  it("保留 ?tool= 落到组装页", () => {
    renderGuides(`${APP_PATHS.toolbox.guides}?tool=web_search`);
    expect(screen.getByTestId("catalog").getAttribute("data-path")).toBe(
      APP_PATHS.toolbox.root,
    );
    expect(screen.getByTestId("catalog").getAttribute("data-tool")).toBe(
      "web_search",
    );
    expect(screen.getByTestId("catalog").getAttribute("data-hash")).toBe(
      "#tools",
    );
  });

  it("保留 ?skill= 落到组装页", () => {
    renderGuides(`${APP_PATHS.toolbox.guides}?skill=staffing`);
    expect(screen.getByTestId("catalog").getAttribute("data-path")).toBe(
      APP_PATHS.toolbox.root,
    );
    expect(screen.getByTestId("catalog").getAttribute("data-skill")).toBe(
      "staffing",
    );
    expect(screen.getByTestId("catalog").getAttribute("data-hash")).toBe(
      "#prompts",
    );
  });
});

describe("旧官方深页", () => {
  function renderOfficial(path: string) {
    return render(
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/toolbox/official" element={<OfficialShelfRedirect />} />
          <Route path="/toolbox" element={<CatalogProbe />} />
        </Routes>
      </MemoryRouter>,
    );
  }

  it("落到组装页的工具节", () => {
    renderOfficial(APP_PATHS.toolbox.official);
    const catalog = screen.getByTestId("catalog");
    expect(catalog.getAttribute("data-path")).toBe(APP_PATHS.toolbox.root);
    expect(catalog.getAttribute("data-hash")).toBe("#tools");
  });

  it("?tool= 留在工具节", () => {
    renderOfficial(`${APP_PATHS.toolbox.official}?tool=write`);
    const catalog = screen.getByTestId("catalog");
    expect(catalog.getAttribute("data-tool")).toBe("write");
    expect(catalog.getAttribute("data-hash")).toBe("#tools");
  });

  it("只有 ?skill= 时落到交代", () => {
    renderOfficial(`${APP_PATHS.toolbox.official}?skill=staffing`);
    const catalog = screen.getByTestId("catalog");
    expect(catalog.getAttribute("data-skill")).toBe("staffing");
    expect(catalog.getAttribute("data-hash")).toBe("#prompts");
  });
});
