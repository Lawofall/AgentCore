// @vitest-environment jsdom
import {
  FONT_SIZE_SCALE,
  ZOOM_DEFAULT,
  ZOOM_MAX,
  ZOOM_MIN,
  applyFontSize,
  applyZoom,
  clampZoom,
  formatZoomPercent,
  isFontSize,
  zoomStep,
} from "@/lib/appearance";
import { uiGet, uiRemove } from "@/lib/uiStorage";
import { useUIStore } from "@/stores/ui";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

afterEach(() => {
  document.documentElement.style.removeProperty("--font-scale");
  document.documentElement.style.removeProperty("zoom");
});

describe("appearance math", () => {
  it("accepts the four named sizes", () => {
    expect(isFontSize("sm")).toBe(true);
    expect(isFontSize("md")).toBe(true);
    expect(isFontSize("lg")).toBe(true);
    expect(isFontSize("xl")).toBe(true);
    expect(isFontSize("default")).toBe(false);
  });

  it("clamps and steps zoom in 10% ticks", () => {
    expect(clampZoom(Number.NaN)).toBe(ZOOM_DEFAULT);
    expect(clampZoom(0.2)).toBe(ZOOM_MIN);
    expect(clampZoom(3)).toBe(ZOOM_MAX);
    expect(zoomStep(1, 1)).toBe(1.1);
    expect(zoomStep(ZOOM_MAX, 1)).toBe(ZOOM_MAX);
    expect(zoomStep(ZOOM_MIN, -1)).toBe(ZOOM_MIN);
    expect(formatZoomPercent(1.1)).toBe("110%");
  });
});

describe("applyFontSize / applyZoom", () => {
  it("writes --font-scale from the named size", () => {
    applyFontSize("lg");
    expect(
      document.documentElement.style.getPropertyValue("--font-scale"),
    ).toBe(String(FONT_SIZE_SCALE.lg));
  });

  it("sets html zoom and clears it at 100%", () => {
    applyZoom(1.2);
    expect(document.documentElement.style.getPropertyValue("zoom")).toBe("1.2");
    applyZoom(1);
    expect(document.documentElement.style.getPropertyValue("zoom")).toBe("");
  });
});

describe("useUIStore appearance persist", () => {
  beforeEach(() => {
    uiRemove("font-size");
    uiRemove("zoom");
    useUIStore.setState({ fontSize: "md", zoom: 1 });
  });

  afterEach(() => {
    useUIStore.setState({ fontSize: "md", zoom: 1 });
    uiRemove("font-size");
    uiRemove("zoom");
  });

  it("writes named size and zoom ticks to uiStorage", () => {
    useUIStore.getState().setFontSize("xl");
    expect(uiGet<string>("font-size")).toBe("xl");
    useUIStore.getState().nudgeZoom(1);
    expect(uiGet<number>("zoom")).toBe(1.1);
    useUIStore.getState().resetZoom();
    expect(uiGet<number>("zoom")).toBe(1);
  });
});

describe("useUIStore send / notifications persist", () => {
  beforeEach(() => {
    uiRemove("send-on-enter");
    uiRemove("os-notifications");
    useUIStore.setState({ sendOnEnter: true, osNotifications: true });
  });

  afterEach(() => {
    useUIStore.setState({ sendOnEnter: true, osNotifications: true });
    uiRemove("send-on-enter");
    uiRemove("os-notifications");
  });

  it("writes both flags to uiStorage", () => {
    useUIStore.getState().setSendOnEnter(false);
    expect(uiGet<boolean>("send-on-enter")).toBe(false);
    useUIStore.getState().setOsNotifications(false);
    expect(uiGet<boolean>("os-notifications")).toBe(false);
  });
});
