import { expect, test } from "@playwright/test";
import {
  ensureAuthed,
  expectHashConversation,
  openWebapp,
  scriptPrompt,
  sendPrompt,
  waitTurnSettled,
} from "../helpers/app";

/**
 * Case 3 — 交互卡双提交面：
 * - 审批：POST interactions → 同流续段
 * - 计划复核：POST resume → 新流推进
 * 向量：`approval_resolved_continue`、`plan_review_resolved_continue`
 */
test.describe("交互卡闭环（双提交面）", () => {
  test("审批卡：POST interactions 后同流续段到完成", async ({ page }) => {
    await openWebapp(page);
    await ensureAuthed(page);

    const interactions = page.waitForRequest(
      (r) =>
        r.method() === "POST" &&
        /\/v1\/conversations\/[^/]+\/interactions\//.test(r.url()),
    );

    await sendPrompt(
      page,
      scriptPrompt("approval_resolved_continue", "需要跑一段代码"),
    );
    await expectHashConversation(page);

    await expect(page.getByText("Agent 请求执行")).toBeVisible({
      timeout: 30_000,
    });
    await page.getByRole("button", { name: "允许一次" }).click();

    const req = await interactions;
    expect(req.method()).toBe("POST");

    await waitTurnSettled(page);
    await expect(page.getByText("运行结果是 1。")).toBeVisible({
      timeout: 15_000,
    });
  });

  test("计划复核卡：POST resume 后新流推进到完成", async ({ page }) => {
    await openWebapp(page);
    await ensureAuthed(page);

    // Fresh draft so we don't collide with the previous conversation's hold.
    await page.getByRole("button", { name: "新对话" }).click();
    await expect(page.getByPlaceholder(/输入消息/)).toBeVisible();

    const resume = page.waitForRequest(
      (r) =>
        r.method() === "POST" &&
        /\/v1\/conversations\/[^/]+\/messages\/[^/]+\/resume$/.test(r.url()),
    );

    await sendPrompt(
      page,
      scriptPrompt("plan_review_resolved_continue", "请复核计划后放行"),
    );
    await expectHashConversation(page);

    await expect(page.getByText("计划复核")).toBeVisible({
      timeout: 30_000,
    });
    await page.getByRole("button", { name: "继续" }).click();

    const req = await resume;
    expect(req.method()).toBe("POST");

    await waitTurnSettled(page);
    await expect(page.getByText("计划复核")).toBeHidden({
      timeout: 20_000,
    });
  });
});
