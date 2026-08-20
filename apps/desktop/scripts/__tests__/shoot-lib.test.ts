import { describe, expect, it } from "vitest";
import {
  buildShots,
  evenCuts,
  resolveWorkerCount,
  shardScenarios,
} from "../shoot-lib.mjs";

describe("evenCuts", () => {
  it("spaces three cuts inside (0, total)", () => {
    expect(evenCuts(10, 3)).toEqual([3, 5, 8]);
  });

  it("drops cuts that would land on 0 or total", () => {
    expect(evenCuts(1, 3)).toEqual([]);
    expect(evenCuts(2, 3)).toEqual([1]);
  });

  it("is empty when frames is 0", () => {
    expect(evenCuts(10, 0)).toEqual([]);
  });
});

describe("buildShots", () => {
  it("emits one terminal shot plus frame files without dropping scenarios", () => {
    const shots = buildShots(
      [
        { name: "alpha", events: 10 },
        { name: "beta", events: 1 },
      ],
      3,
    );
    expect(shots.map((s) => s.file)).toEqual([
      "alpha.png",
      "alpha.f3.png",
      "alpha.f5.png",
      "alpha.f8.png",
      "beta.png",
    ]);
  });
});

describe("shardScenarios", () => {
  it("keeps every scenario exactly once", () => {
    const scenarios = ["a", "b", "c", "d", "e"].map((name) => ({ name }));
    const shards = shardScenarios(scenarios, 2);
    expect(shards).toHaveLength(2);
    expect(shards.flat().map((s) => s.name).sort()).toEqual([
      "a",
      "b",
      "c",
      "d",
      "e",
    ]);
    const names = new Set(shards.flat().map((s) => s.name));
    expect(names.size).toBe(5);
  });

  it("drops empty shards when workers exceed scenarios", () => {
    expect(shardScenarios([{ name: "only" }], 4)).toEqual([[{ name: "only" }]]);
  });
});

describe("resolveWorkerCount", () => {
  it("caps at 4 by default even on wide machines", () => {
    expect(resolveWorkerCount({ cpus: 16, env: undefined })).toBe(4);
  });

  it("honors SHOOT_WORKERS and never exceeds scenario count", () => {
    expect(resolveWorkerCount({ env: "8", scenarioCount: 3 })).toBe(3);
    expect(resolveWorkerCount({ env: "1", cpus: 16 })).toBe(1);
  });

  it("treats SHOOT_WORKERS=0 as unset", () => {
    expect(resolveWorkerCount({ cpus: 2, env: "0" })).toBe(2);
  });
});
