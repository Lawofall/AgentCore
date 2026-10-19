import { AssemblySectionRedirect } from "@/pages/toolbox/assemblyPages";
import {
  assemblyShelfPath,
  toolboxTabFromPath,
} from "@/pages/toolbox/assemblyTabs";
// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";

afterEach(cleanup);

function LocationProbe() {
  const location = useLocation();
  return (
    <div>
      {location.pathname}
      {location.hash}
    </div>
  );
}

function renderRedirect(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/toolbox" element={<LocationProbe />} />
        <Route path="/more/model" element={<LocationProbe />} />
        <Route
          path="/toolbox/tools"
          element={<AssemblySectionRedirect tab="tools" />}
        />
        <Route
          path="/toolbox/mcp"
          element={<AssemblySectionRedirect tab="plugs" />}
        />
        <Route
          path="/toolbox/model"
          element={<AssemblySectionRedirect tab="overview" />}
        />
        <Route
          path="/more/model/prompts"
          element={<AssemblySectionRedirect tab="prompts" />}
        />
      </Routes>
    </MemoryRouter>,
  );
}

describe("工具箱落点", () => {
  it("旧节地址回到这一页的锚点", () => {
    expect(assemblyShelfPath("overview", "toolbox")).toBe("/toolbox");
    expect(assemblyShelfPath("tools", "toolbox")).toBe("/toolbox#tools");
    expect(assemblyShelfPath("prompts", "settings")).toBe(
      "/more/model#prompts",
    );
    expect(assemblyShelfPath(null, "toolbox")).toBe("/toolbox");
    expect(assemblyShelfPath("overview", "toolbox")).not.toContain("#model");
  });

  it("旧装配模型节落到货架首页", () => {
    expect(toolboxTabFromPath("/toolbox/model")).toBe("overview");
    expect(toolboxTabFromPath("/more/model/model")).toBe("overview");
    renderRedirect("/toolbox/model");
    expect(screen.getByText("/toolbox")).toBeTruthy();
    expect(screen.queryByText("/toolbox#model")).toBeNull();
  });

  it("工具节落到工具锚点", () => {
    renderRedirect("/toolbox/tools");
    expect(screen.getByText("/toolbox#tools")).toBeTruthy();
  });

  it("插头节落到插头锚点", () => {
    renderRedirect("/toolbox/mcp");
    expect(screen.getByText("/toolbox#plugs")).toBeTruthy();
  });

  it("窄屏交代节落到同一页", () => {
    renderRedirect("/more/model/prompts");
    expect(screen.getByText("/more/model#prompts")).toBeTruthy();
  });
});
