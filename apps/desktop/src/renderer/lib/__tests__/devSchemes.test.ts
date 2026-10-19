// @vitest-environment jsdom
import { act, renderHook } from "@testing-library/react";
import type { ComponentType } from "react";
import { afterEach, describe, expect, it } from "vitest";
import {
  CURRENT_SCHEME_ID,
  cycleActiveSchemeGroup,
  getSchemeId,
  isSchemePanelOpen,
  registerScheme,
  resetSchemesForTests,
  setSchemeId,
  useSchemeId,
  useSchemeSlot,
} from "../devSchemes";

const Current: ComponentType<{ label: string }> = () => null;
const Row: ComponentType<{ label: string }> = () => null;
const Split: ComponentType<{ label: string }> = () => null;

afterEach(() => {
  resetSchemesForTests();
});

describe("devSchemes", () => {
  it("defaults to current and remembers a pick in sessionStorage", () => {
    registerScheme({
      id: "picker-top",
      label: "贴目录顶",
      group: "model-picker",
      groupLabel: "模型弹层",
      slots: { "composer-model-shelf": Row },
    });
    expect(getSchemeId("model-picker")).toBe(CURRENT_SCHEME_ID);
    setSchemeId("model-picker", "picker-top");
    expect(getSchemeId("model-picker")).toBe("picker-top");
    expect(sessionStorage.getItem("agentcore:dev-scheme")).toContain(
      "picker-top",
    );
  });

  it("cycles 现行 → 贴目录顶 → 合一双栏 → 现行", () => {
    registerScheme({
      id: "picker-top",
      label: "贴目录顶",
      group: "model-picker",
      groupLabel: "模型弹层",
      slots: { "composer-model-shelf": Row },
    });
    registerScheme({
      id: "picker-split",
      label: "合一双栏",
      group: "model-picker",
      groupLabel: "模型弹层",
      slots: { "composer-model-shelf": Split },
    });
    expect(isSchemePanelOpen()).toBe(false);
    cycleActiveSchemeGroup();
    expect(getSchemeId("model-picker")).toBe("picker-top");
    expect(isSchemePanelOpen()).toBe(true);
    cycleActiveSchemeGroup();
    expect(getSchemeId("model-picker")).toBe("picker-split");
    cycleActiveSchemeGroup();
    expect(getSchemeId("model-picker")).toBe(CURRENT_SCHEME_ID);
  });

  it("swaps the slot component when the scheme changes", () => {
    const { result } = renderHook(() => {
      const id = useSchemeId("model-picker");
      const View = useSchemeSlot("composer-model-shelf", Current);
      return { id, View };
    });
    act(() => {
      registerScheme({
        id: "picker-top",
        label: "贴目录顶",
        group: "model-picker",
        groupLabel: "模型弹层",
        slots: { "composer-model-shelf": Row },
      });
      setSchemeId("model-picker", "picker-top");
    });
    expect(result.current.id).toBe("picker-top");
    expect(result.current.View).toBe(Row);
  });
});
