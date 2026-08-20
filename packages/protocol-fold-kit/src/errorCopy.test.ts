import { describe, expect, it } from "vitest";
import {
  EMPTY_RESPONSE_CHIP_LABELS,
  LLM_EMPTY_RESPONSE_MESSAGE,
  LLM_ERROR_MESSAGE,
  LLM_UNPRODUCTIVE_MESSAGE,
  TURN_INTERRUPTED_EMPTY_MESSAGE,
  degradedFinishChipLabel,
  isEmptyResponseUserSurface,
} from "./errorCopy";

describe("EMPTY_RESPONSE_CHIP_LABELS", () => {
  it("pins diagnosis → chip copy", () => {
    const expected = "上游返回了网页或登录页，请检查服务商地址与鉴权";
    expect(EMPTY_RESPONSE_CHIP_LABELS.upstream_non_api).toBe(expected);
    expect(EMPTY_RESPONSE_CHIP_LABELS.oauth_expired).toBe(expected);
    expect(EMPTY_RESPONSE_CHIP_LABELS.silent_empty).toBe("模型返回空内容");
    expect(EMPTY_RESPONSE_CHIP_LABELS.length_empty).toContain("截断");
  });
});

describe("degradedFinishChipLabel", () => {
  it("prefers the diagnosis map, else the · suffix", () => {
    expect(degradedFinishChipLabel("upstream_non_api", undefined)).toBe(
      EMPTY_RESPONSE_CHIP_LABELS.upstream_non_api,
    );
    expect(degradedFinishChipLabel(undefined, "主句 · 后缀说明")).toBe(
      "后缀说明",
    );
    expect(degradedFinishChipLabel(undefined, "没有分隔")).toBeUndefined();
  });
});

describe("isEmptyResponseUserSurface", () => {
  it("treats code / diagnosis / 空响应句子 as the red-card surface", () => {
    expect(isEmptyResponseUserSurface({ code: "LLM_EMPTY_RESPONSE" })).toBe(
      true,
    );
    expect(
      isEmptyResponseUserSurface({ emptyDiagnosis: "silent_empty" }),
    ).toBe(true);
    expect(
      isEmptyResponseUserSurface({ message: "模型多次空响应 · 其它" }),
    ).toBe(true);
    expect(isEmptyResponseUserSurface({ message: "模型空响应" })).toBe(true);
    expect(isEmptyResponseUserSurface({ message: "模型调用失败，请重试。" })).toBe(
      false,
    );
  });
});

describe("empty-failure sentences", () => {
  it("pins the four overlapping finish faces", () => {
    expect(LLM_ERROR_MESSAGE).toBe("模型调用失败，请重试。");
    expect(LLM_UNPRODUCTIVE_MESSAGE).toBe(
      "工具连续无有效进展或参数无效，请重试。",
    );
    expect(LLM_EMPTY_RESPONSE_MESSAGE).toBe("模型返回空内容，请重试。");
    expect(TURN_INTERRUPTED_EMPTY_MESSAGE).toBe(
      "已中断。直接发送下一条即可重试。",
    );
  });
});
