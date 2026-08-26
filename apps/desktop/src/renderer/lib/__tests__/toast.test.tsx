// @vitest-environment jsdom
/**
 * notifyError 的两条出口：后端错误交由 describeError 说出服务端文案与补救动作，
 * 纯客户端字符串原样显示——两者都必须留住调用方给的标题（`context`）。
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

const toastFn = vi.fn();
const toastError = vi.fn();

vi.mock("sonner", () => ({
  toast: Object.assign((...args: unknown[]) => toastFn(...args), {
    error: (...args: unknown[]) => toastError(...args),
    success: vi.fn(),
    warning: vi.fn(),
  }),
}));

import { notifyError } from "@/lib/toast";
import { ApiError } from "@/services/api";

/** 后端 `{error:{code,message}}` 契约的原样回包。 */
function apiError(status: number, code: string, message: string): ApiError {
  return new ApiError(status, JSON.stringify({ error: { code, message } }));
}

beforeEach(() => {
  toastFn.mockReset();
  toastError.mockReset();
});

describe("notifyError", () => {
  it("后端错误：显示服务端文案，不是通用兜底", () => {
    notifyError(
      apiError(422, "VALIDATION_ERROR", "文件超出 52428800 字节的上传上限"),
      "附件驻留失败",
    );

    expect(toastError).toHaveBeenCalledWith(
      "附件驻留失败",
      expect.objectContaining({
        description: "文件超出 52428800 字节的上传上限",
      }),
    );
  });

  it("后端错误带补救动作：一键去配置的按钮跟着出", () => {
    notifyError(
      apiError(402, "LLM_KEY_REQUIRED", "请先接入自己的 API Key"),
      "附件驻留失败",
    );

    expect(toastFn).toHaveBeenCalledWith(
      "附件驻留失败",
      expect.objectContaining({
        description: "请先接入自己的 API Key",
        action: expect.objectContaining({ label: "去服务商" }),
      }),
    );
  });

  it("纯客户端字符串：原样显示，标题不丢", () => {
    notifyError("附件暂存已失效，请重新附加", "附件驻留失败");

    expect(toastError).toHaveBeenCalledWith(
      "附件驻留失败",
      expect.objectContaining({ description: "附件暂存已失效，请重新附加" }),
    );
  });

  it("字符串且没给标题：原文当标题，不凭空造描述", () => {
    notifyError("离线时无法发送，请恢复连接后再试");

    expect(toastError).toHaveBeenCalledWith(
      "离线时无法发送，请恢复连接后再试",
      expect.objectContaining({ description: undefined }),
    );
  });
});
