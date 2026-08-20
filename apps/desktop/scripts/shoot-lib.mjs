import os from "node:os";

/** Up to `frames` evenly-spaced event counts in (0, total) for mid-stream frames. */
export function evenCuts(total, frames) {
  const cuts = new Set();
  for (let i = 1; i <= frames; i++) {
    const k = Math.round((total * i) / (frames + 1));
    if (k > 0 && k < total) cuts.add(k);
  }
  return [...cuts].sort((a, b) => a - b);
}

/** Terminal shot + optional mid-stream frames. Frames of one scenario stay adjacent. */
export function buildShots(scenarios, frames) {
  const shots = [];
  for (const s of scenarios) {
    shots.push({ name: s.name, k: null, file: `${s.name}.png` });
    if (frames > 0) {
      for (const k of evenCuts(s.events, frames)) {
        shots.push({ name: s.name, k, file: `${s.name}.f${k}.png` });
      }
    }
  }
  return shots;
}

/**
 * Round-robin scenarios into `workers` buckets so every mid-stream frame of a
 * scenario stays on the same worker (hash-scrub reuse). Empty shards dropped.
 */
export function shardScenarios(scenarios, workers) {
  const n = Math.max(1, workers | 0);
  const shards = Array.from({ length: n }, () => []);
  scenarios.forEach((s, i) => {
    shards[i % n].push(s);
  });
  return shards.filter((shard) => shard.length > 0);
}

/**
 * Parallel Chromium pages. `SHOOT_WORKERS` overrides; otherwise min(4, CPUs),
 * and never more workers than scenarios.
 */
export function resolveWorkerCount({
  cpus,
  env = process.env.SHOOT_WORKERS,
  scenarioCount = Number.POSITIVE_INFINITY,
} = {}) {
  const cpuN =
    cpus ??
    (typeof os.availableParallelism === "function"
      ? os.availableParallelism()
      : os.cpus().length);
  const parsed = Number(env);
  const requested =
    Number.isFinite(parsed) && parsed > 0
      ? parsed | 0
      : Math.min(4, Math.max(1, cpuN | 0));
  const cap = Number.isFinite(scenarioCount)
    ? Math.max(1, scenarioCount | 0)
    : requested;
  return Math.max(1, Math.min(requested, cap));
}
