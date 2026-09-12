// @vitest-environment jsdom
/**
 * Render tests for the admin deploy snapshot (formerly the 系统 page).
 *
 * 这一块曾把三件「信息未知」当成告警常年亮红黄：未注入构建 SHA 判成异轨、只有管理员进得来
 * 却提示「去创建首个管理员」、品牌发布通道探针失败用 destructive 展示。这些用例钉住修好的
 * 口径：没有构建 SHA = 中性「未知」，真 SHA 不同 = 警告「异轨」，探针未配置时整块不出现。
 * The leading block comment keeps the @vitest-environment directive file-leading.
 */

import { DeploySnapshot } from "@/components/DeploySnapshot";
import {
  type AdminSystemStatus,
  fetchSystemStatus,
} from "@/services/adminSystem";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const build = vi.hoisted(() => ({ sha: "unknown" }));

vi.mock("@/services/adminSystem", () => ({ fetchSystemStatus: vi.fn() }));
vi.mock("@/services/releaseDrift", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/services/releaseDrift")>()),
  fetchReleaseDrift: vi.fn(() => Promise.resolve(null)),
}));
vi.mock("@/lib/clientBuildInfo", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/clientBuildInfo")>()),
  clientGitSha: () => build.sha,
}));

const fetchSystemStatusMock = vi.mocked(fetchSystemStatus);

function status(overrides: Partial<AdminSystemStatus> = {}): AdminSystemStatus {
  return {
    billing_mode: "byok",
    quota: {
      daily_tokens: 0,
      daily_requests: 0,
      daily_cost_nano: 0,
      monthly_cost_nano: 0,
    },
    database_ok: true,
    version: "0.3.1",
    git_sha: "unknown",
    built_at: "unknown",
    users_total: 1,
    users_active: 1,
    admins: 1,
    ...overrides,
  };
}

async function renderSnapshot(overrides: Partial<AdminSystemStatus> = {}) {
  fetchSystemStatusMock.mockResolvedValue(status(overrides));
  const view = render(<DeploySnapshot />);
  await waitFor(() => expect(screen.getByText("版本")).toBeTruthy());
  return view;
}

/** 「控制台 ↔ API」这一行的徽章（同行里只有它带文案）。 */
function driftBadge(label: string): HTMLElement {
  const row = screen.getByText("控制台 ↔ API").parentElement;
  if (!row) throw new Error("控制台 ↔ API row not found");
  return within(row).getByText(label);
}

beforeEach(() => {
  build.sha = "unknown";
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("DeploySnapshot 构建漂移", () => {
  it("未注入构建 SHA 时是中性「未知」，不是异轨", async () => {
    const { container } = await renderSnapshot({ git_sha: "unknown" });

    const badge = driftBadge("未知");
    expect(badge.className).toContain("text-muted-foreground");
    expect(badge.className).not.toContain("warning");
    expect(screen.queryByText("异轨")).toBeNull();
    expect(container.querySelector('[class*="warning"]')).toBeNull();
    expect(container.querySelector(".text-destructive")).toBeNull();
  });

  it("两侧都有 SHA 且不同时才亮警告色的「异轨」", async () => {
    build.sha = "9f2c1ab";

    await renderSnapshot({ git_sha: "3d81f0442c6f1b0d9e8a77c5b3e21f4a6d0c9b8e" });

    const badge = driftBadge("异轨");
    expect(badge.className).toContain("text-warning");
  });

  it("短 SHA 与完整 SHA 同源时判为同部署", async () => {
    build.sha = "9f2c1ab";

    await renderSnapshot({ git_sha: "9f2c1ab42c6f1b0d9e8a77c5b3e21f4a6d0c9b8e" });

    expect(driftBadge("同部署").className).toContain("text-success");
  });
});

describe("DeploySnapshot 构建时间", () => {
  it("按全站 MM-DD HH:mm 口径显示，不再吐裸 ISO 串", async () => {
    const iso = "2026-08-12T22:41:07Z";
    await renderSnapshot({ built_at: iso });

    expect(screen.queryByText(iso)).toBeNull();
    const d = new Date(iso);
    const p = (n: number) => String(n).padStart(2, "0");
    const expected = `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
    expect(screen.getByText(expected)).toBeTruthy();
  });

  it("后端没注入构建时间时显示「未知」", async () => {
    await renderSnapshot({ built_at: "unknown" });

    const row = screen.getByText("API 构建时间").parentElement;
    if (!row) throw new Error("API 构建时间 row not found");
    expect(within(row).getByText("未知")).toBeTruthy();
  });
});

describe("DeploySnapshot 假告警", () => {
  it("小部署不再挂「首次部署引导」——进得来总览就说明管理员已存在", async () => {
    await renderSnapshot({ admins: 1, users_total: 1, users_active: 1 });

    expect(screen.queryByText("首次部署引导")).toBeNull();
  });

  it("未配置发布通道探针时不显示「发布漂移」卡", async () => {
    await renderSnapshot();

    expect(screen.queryByText("发布漂移")).toBeNull();
  });

  it("不挂号池卡或全局额度明细——那些在供给·额度", async () => {
    await renderSnapshot();

    expect(screen.queryByText("平台额度账号")).toBeNull();
    expect(screen.queryByText("全局配额默认值")).toBeNull();
    expect(screen.queryByText("计费模式")).toBeNull();
  });
});
