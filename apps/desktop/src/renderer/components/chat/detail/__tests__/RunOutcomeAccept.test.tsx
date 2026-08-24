// @vitest-environment jsdom
/**
 * Run detail · 跑一半改方向 Step 4: surfaces redirect_ignored and records an
 * explicit accept via accept-outcome. Deterministic failure is audit-only (no card).
 */

import { resetTurnAuditCacheForTests } from "@/hooks/useTurnAudit";
import type { AgentAuditEvent } from "@agentcore/contract-rest-types/audit";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { RunOutcomeAcceptSection } from "../sections/RunOutcomeAccept";

vi.mock("@/lib/preview", () => ({ isWebPreview: vi.fn(() => false) }));
vi.mock("@/services/audit", () => ({ fetchTurnAudit: vi.fn() }));
vi.mock("@/services/runRedirect", () => ({ acceptRunOutcome: vi.fn() }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const { fetchTurnAudit } = await import("@/services/audit");
const { acceptRunOutcome } = await import("@/services/runRedirect");

afterEach(cleanup);

function audit(
  partial: Partial<AgentAuditEvent> &
    Pick<AgentAuditEvent, "action" | "run_id">,
): AgentAuditEvent {
  return {
    id: partial.id ?? "ev-1",
    turn_id: "msg-1",
    trace_id: null,
    execution_id: null,
    parent_run_id: null,
    seq: 0,
    category: "state",
    actor_kind: "system",
    target_type: null,
    target_ref: null,
    outcome: "ok",
    detail: {},
    created_at: "2026-07-06T00:00:00Z",
    ...partial,
  };
}

const props = {
  conversationId: "conv-1",
  messageId: "msg-1",
  runId: "r1",
};

beforeEach(() => {
  resetTurnAuditCacheForTests();
  vi.mocked(fetchTurnAudit).mockReset();
  vi.mocked(acceptRunOutcome).mockReset();
});

describe("RunOutcomeAcceptSection", () => {
  it("renders nothing when no trigger audit rows exist for the run", async () => {
    vi.mocked(fetchTurnAudit).mockResolvedValue({ data: [], total: 0 });
    const { container } = render(<RunOutcomeAcceptSection {...props} />);
    await waitFor(() => expect(fetchTurnAudit).toHaveBeenCalled());
    await vi.mocked(fetchTurnAudit).mock.results[0]?.value;
    expect(container.firstChild).toBeNull();
  });

  it("offers accept when run.redirect_ignored is present", async () => {
    vi.mocked(fetchTurnAudit).mockResolvedValue({
      data: [audit({ action: "run.redirect_ignored", run_id: "r1" })],
      total: 1,
    });
    render(<RunOutcomeAcceptSection {...props} />);
    expect(await screen.findByText("改方向未生效")).toBeTruthy();
    expect(screen.getByRole("button", { name: "接受此结果" })).toBeTruthy();
  });

  it("hides the card when only deterministic_failure is present", async () => {
    vi.mocked(fetchTurnAudit).mockResolvedValue({
      data: [audit({ action: "run.deterministic_failure", run_id: "r1" })],
      total: 1,
    });
    const { container } = render(<RunOutcomeAcceptSection {...props} />);
    await waitFor(() => expect(fetchTurnAudit).toHaveBeenCalled());
    await vi.mocked(fetchTurnAudit).mock.results[0]?.value;
    expect(container.firstChild).toBeNull();
    expect(screen.queryByText("重试大概率仍会失败")).toBeNull();
    expect(screen.queryByRole("button", { name: "接受此结果" })).toBeNull();
  });

  it("still offers redirect_ignored when deterministic_failure is also present", async () => {
    vi.mocked(fetchTurnAudit).mockResolvedValue({
      data: [
        audit({ id: "a", action: "run.deterministic_failure", run_id: "r1" }),
        audit({ id: "b", action: "run.redirect_ignored", run_id: "r1" }),
      ],
      total: 2,
    });
    render(<RunOutcomeAcceptSection {...props} />);
    expect(await screen.findByText("改方向未生效")).toBeTruthy();
    expect(screen.queryByText("重试大概率仍会失败")).toBeNull();
  });

  it("hides leftover outcome_accepted when redirect_ignored is absent", async () => {
    vi.mocked(fetchTurnAudit).mockResolvedValue({
      data: [
        audit({ action: "run.deterministic_failure", run_id: "r1" }),
        audit({ id: "acc", action: "run.outcome_accepted", run_id: "r1" }),
      ],
      total: 2,
    });
    const { container } = render(<RunOutcomeAcceptSection {...props} />);
    await waitFor(() => expect(fetchTurnAudit).toHaveBeenCalled());
    await vi.mocked(fetchTurnAudit).mock.results[0]?.value;
    expect(container.firstChild).toBeNull();
    expect(screen.queryByText("已接受此结果")).toBeNull();
  });

  it("shows accepted state when run.outcome_accepted already exists", async () => {
    vi.mocked(fetchTurnAudit).mockResolvedValue({
      data: [
        audit({ action: "run.redirect_ignored", run_id: "r1" }),
        audit({ id: "acc", action: "run.outcome_accepted", run_id: "r1" }),
      ],
      total: 2,
    });
    render(<RunOutcomeAcceptSection {...props} />);
    expect(await screen.findByText("已接受此结果")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "接受此结果" })).toBeNull();
  });

  it("records accept on button click and refetches audit", async () => {
    vi.mocked(fetchTurnAudit).mockResolvedValue({
      data: [audit({ action: "run.redirect_ignored", run_id: "r1" })],
      total: 1,
    });
    vi.mocked(acceptRunOutcome).mockResolvedValue({ recorded: true });
    render(<RunOutcomeAcceptSection {...props} />);
    fireEvent.click(await screen.findByRole("button", { name: "接受此结果" }));
    await waitFor(() => {
      expect(acceptRunOutcome).toHaveBeenCalledWith("conv-1", {
        messageId: "msg-1",
        runId: "r1",
        reason: "redirect_ignored",
      });
    });
    await waitFor(() => {
      expect(fetchTurnAudit).toHaveBeenCalledTimes(2);
    });
    expect(await screen.findByText("已接受此结果")).toBeTruthy();
  });
});
