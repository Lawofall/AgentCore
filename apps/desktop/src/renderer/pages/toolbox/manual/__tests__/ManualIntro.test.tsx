// @vitest-environment jsdom
import { ManualIntro } from "@/pages/toolbox/manual/ManualIntro";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";

afterEach(cleanup);

describe("ManualIntro", () => {
  it("renders compressed intro: what + mindset + 5 分钟上手", () => {
    render(
      <MemoryRouter initialEntries={["/toolbox/manual/intro"]}>
        <ManualIntro />
      </MemoryRouter>,
    );
    expect(screen.getByText("这是什么")).toBeTruthy();
    expect(screen.getByText("你怎么用")).toBeTruthy();
    expect(screen.getByText("5 分钟上手")).toBeTruthy();
    expect(screen.queryByText("快速上手")).toBeNull();
    expect(screen.getByText("协作，是更高级的智能")).toBeTruthy();
    expect(screen.getByText(/fashitianxia\.xyz\/download/)).toBeTruthy();
    expect(screen.getByText(/app\.fashitianxia\.xyz/)).toBeTruthy();
    // 开箱即用：第一步就是说目标，不再拿「先去接额度」当门槛。
    expect(screen.getByText("说目标")).toBeTruthy();
    expect(screen.queryByText("接入额度后开聊")).toBeNull();
    expect(screen.getByText(/平台代付，打开就能聊/)).toBeTruthy();
    expect(screen.queryByText(/建议任务/)).toBeNull();
    // BYOK 仍在，但降级为可选升级。
    expect(screen.getByText(/可选升级/)).toBeTruthy();
  });
});
