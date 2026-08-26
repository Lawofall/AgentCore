import { describe, expect, it } from "vitest";
import {
  LLM_EMPTY_RESPONSE_MESSAGE,
  LLM_ERROR_MESSAGE,
  LLM_UNPRODUCTIVE_MESSAGE,
  TURN_INTERRUPTED_EMPTY_MESSAGE,
} from "./errorCopy";

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
