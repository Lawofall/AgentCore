import { describe, expect, it } from "vitest";
import { switchboardBulkActions } from "../switchboardBulk";

describe("switchboardBulkActions", () => {
  it("都开只出关闭", () => {
    expect(switchboardBulkActions(0, 7)).toEqual(["close"]);
  });

  it("都关只出打开", () => {
    expect(switchboardBulkActions(7, 7)).toEqual(["open"]);
  });

  it("有开有关两颗都在，打开在前", () => {
    expect(switchboardBulkActions(2, 7)).toEqual(["open", "close"]);
  });

  it("空名单不画", () => {
    expect(switchboardBulkActions(0, 0)).toEqual([]);
  });
});
