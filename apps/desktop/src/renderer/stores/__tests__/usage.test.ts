import { useUsageStore } from "@/stores/usage";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const summaryBody = {
  today: {
    usage: { input: 0, output: 0, reasoning: 0, cache_hit: 0, cache_miss: 0 },
    cost: {
      input: 0,
      cached: 0,
      output: 0,
      total: 0,
      currency: "CNY",
      cny_total: 0,
    },
    requests: 0,
  },
  month: {
    usage: { input: 0, output: 0, reasoning: 0, cache_hit: 0, cache_miss: 0 },
    cost: {
      input: 0,
      cached: 0,
      output: 0,
      total: 0,
      currency: "CNY",
      cny_total: 0,
    },
    requests: 0,
  },
  quota: {
    daily_tokens: 2_000_000,
    monthly_cost_nano: 5_000_000_000,
    daily_cost_nano: 0,
  },
  billing_mode: "platform",
  recent_daily_cost: [],
};

const turnBody = {
  message_id: "m1",
  usage: {
    input: 100,
    output: 50,
    reasoning: 0,
    cache_hit: 0,
    cache_miss: 100,
  },
  cost: {
    input: 14,
    cached: 0,
    output: 14,
    total: 28,
    currency: "CNY",
    cny_total: 0,
  },
  rounds: 1,
  agents: [],
};

beforeEach(() => {
  useUsageStore.setState({
    summary: null,
    loading: false,
    error: null,
    messageCosts: {},
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("fetchSummary", () => {
  it("stores the summary on success", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(json(summaryBody))),
    );

    await useUsageStore.getState().fetchSummary();

    const s = useUsageStore.getState();
    expect(s.summary?.quota.daily_tokens).toBe(2_000_000);
    expect(s.error).toBeNull();
  });

  it("sets a soft error on failure", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.reject(new TypeError("offline"))),
    );

    await useUsageStore.getState().fetchSummary();

    const s = useUsageStore.getState();
    expect(s.summary).toBeNull();
    expect(s.error).not.toBeNull();
  });
});

describe("loadMessageCost", () => {
  it("fetches and caches a turn's payroll by message id", async () => {
    const fetchMock = vi.fn(() => Promise.resolve(json(turnBody)));
    vi.stubGlobal("fetch", fetchMock);

    await useUsageStore.getState().loadMessageCost("m1");

    expect(useUsageStore.getState().messageCosts.m1?.cost.total).toBe(28);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("dedupes: a cached message is not re-fetched", async () => {
    const fetchMock = vi.fn(() => Promise.resolve(json(turnBody)));
    vi.stubGlobal("fetch", fetchMock);

    await useUsageStore.getState().loadMessageCost("m1");
    await useUsageStore.getState().loadMessageCost("m1");

    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("swallows a failed fetch (cost is supplementary, never throws)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.reject(new TypeError("offline"))),
    );

    await expect(
      useUsageStore.getState().loadMessageCost("m1"),
    ).resolves.toBeUndefined();
    expect(useUsageStore.getState().messageCosts.m1).toBeUndefined();
  });
});
