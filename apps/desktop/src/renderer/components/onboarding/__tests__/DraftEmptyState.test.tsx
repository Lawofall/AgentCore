// @vitest-environment jsdom
/**
 * 草稿空态：问候一句，不放任务 chips、不讲怎么用。
 */

import { DraftEmptyState } from "@/components/onboarding/DraftEmptyState";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

afterEach(cleanup);

describe("DraftEmptyState", () => {
  it("shows a greeting without starter tasks or a how-to lecture", () => {
    render(<DraftEmptyState />);
    expect(screen.getByText(/今天想解决什么问题/)).toBeTruthy();
    expect(screen.queryAllByRole("button")).toHaveLength(0);
    expect(screen.queryByRole("button", { name: /怎么用/ })).toBeNull();
    expect(screen.queryByRole("link", { name: /产品手册/ })).toBeNull();
    expect(screen.queryByText(/多 Agent/)).toBeNull();
    expect(screen.queryByText(/拉起/)).toBeNull();
  });
});
