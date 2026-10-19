import { describe, expect, it } from "vitest";
import {
  channelRedirectFace,
  resolveToolEndStatus,
  resolveToolWireStatus,
} from "../channelRedirect";

describe("resolveToolWireStatus", () => {
  it("keeps live redirect", () => {
    expect(
      resolveToolWireStatus("redirect", { code: "source_grep_redirect" }),
    ).toBe("redirect");
  });

  it("does not remap error + redirect code", () => {
    expect(
      resolveToolWireStatus("error", { code: "source_grep_redirect" }),
    ).toBe("error");
  });

  it("leaves real faults as error", () => {
    expect(resolveToolWireStatus("error", { code: "TOOL_ERROR" })).toBe(
      "error",
    );
  });
});

describe("resolveToolEndStatus", () => {
  it("never returns running", () => {
    expect(resolveToolEndStatus("running")).toBe("success");
  });
});

describe("channelRedirectFace", () => {
  it("titles a grep steer as 改用搜索", () => {
    expect(channelRedirectFace("source_grep_redirect")?.label).toBe("改用搜索");
  });

  it("steers dump / non-url reads to the read pen", () => {
    expect(channelRedirectFace("source_dump_redirect")?.toolName).toBe("read");
    expect(channelRedirectFace("not_a_web_url")?.toolName).toBe("read");
  });
});
