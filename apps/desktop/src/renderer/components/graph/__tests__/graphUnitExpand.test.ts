import { describe, expect, it } from "vitest";
import {
  GRAPH_UNIT_EXPAND_TOUCHED,
  resolveGraphExpandedUnits,
} from "../graphUnitExpand";

describe("resolveGraphExpandedUnits", () => {
  const defaults = new Set(["lead", "mpm"]);

  it("defaults to expand all foldable units before any user toggle", () => {
    expect([
      ...resolveGraphExpandedUnits({
        defaults,
        touched: false,
        collapsedFingerprint: "mpm",
        sessionCollapsed: null,
        persist: true,
      }),
    ]).toEqual(["lead", "mpm"]);
  });

  it("after touch, empty collapsed list keeps every current leader open", () => {
    expect([
      ...resolveGraphExpandedUnits({
        defaults,
        touched: true,
        collapsedFingerprint: "",
        sessionCollapsed: null,
        persist: true,
      }),
    ]).toEqual(["lead", "mpm"]);
  });

  it("after touch, only listed leaders stay collapsed", () => {
    expect([
      ...resolveGraphExpandedUnits({
        defaults,
        touched: true,
        collapsedFingerprint: "lead",
        sessionCollapsed: null,
        persist: true,
      }),
    ]).toEqual(["mpm"]);
  });

  it("a leader that is not in the collapsed list stays open", () => {
    expect([
      ...resolveGraphExpandedUnits({
        defaults: new Set(["lead", "mpm", "eng"]),
        touched: true,
        collapsedFingerprint: "mpm",
        sessionCollapsed: null,
        persist: true,
      }),
    ]).toEqual(["lead", "eng"]);
  });

  it("session collapses apply when not persisting; null collapses nothing", () => {
    expect([
      ...resolveGraphExpandedUnits({
        defaults,
        touched: false,
        collapsedFingerprint: "",
        sessionCollapsed: new Set(["lead"]),
        persist: false,
      }),
    ]).toEqual(["mpm"]);
    expect([
      ...resolveGraphExpandedUnits({
        defaults,
        touched: false,
        collapsedFingerprint: "mpm",
        sessionCollapsed: null,
        persist: false,
      }),
    ]).toEqual(["lead", "mpm"]);
  });

  it("ignores the touched sentinel if it leaks into the collapsed list", () => {
    expect([
      ...resolveGraphExpandedUnits({
        defaults,
        touched: true,
        collapsedFingerprint: `lead,${GRAPH_UNIT_EXPAND_TOUCHED}`,
        sessionCollapsed: null,
        persist: true,
      }),
    ]).toEqual(["mpm"]);
  });
});
