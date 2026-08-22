#!/usr/bin/env node
/**
 * Regenerate all committed frontend contract artifacts from backend sources.
 *
 *   1. OpenAPI → packages/contract-rest-types/src/api.generated.ts
 *   2. OpenAPI paths → packages/contract-rest-types/src/paths.generated.ts
 *   3. EventType enum → packages/contract-types/src/eventTypes.generated.ts
 *   4. SSE payload models → packages/contract-types/src/events.generated.ts
 *   5. InteractionKind + wire table → packages/contract-types/src/interactionKinds.generated.ts
 *   6. ErrorCode → packages/contract-types/src/errorCodes.generated.ts
 *   7. validate SSE + REST path literal alignment
 *
 * CI runs this then `git diff --exit-code` to block silent drift.
 */
import { existsSync, unlinkSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { join, dirname } from "node:path";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const SERVER = join(ROOT, "apps", "server");

function run(cmd, args, opts = {}) {
  const r = spawnSync(cmd, args, { stdio: "inherit", cwd: opts.cwd ?? ROOT, shell: process.platform === "win32" });
  if (r.status !== 0) process.exit(r.status ?? 1);
}

console.log("gen:types — dump OpenAPI …");
run("uv", ["run", "python", "scripts/dump_openapi.py"], { cwd: SERVER });

console.log("gen:types — dump SSE event type union …");
run("uv", ["run", "python", "scripts/dump_sse_event_types.py"], { cwd: SERVER });

console.log("gen:types — dump SSE payload types …");
run("uv", ["run", "python", "scripts/dump_sse_payload_types.py"], { cwd: SERVER });

console.log("gen:types — dump InteractionKind + wire table …");
run("uv", ["run", "python", "scripts/dump_interaction_kinds.py"], { cwd: SERVER });

console.log("gen:types — dump ErrorCode catalog …");
run("uv", ["run", "python", "scripts/dump_error_codes.py"], { cwd: SERVER });

console.log("gen:types — dump REST path templates …");
run("uv", ["run", "python", "scripts/dump_rest_paths.py"], { cwd: SERVER });

console.log("gen:types — openapi-typescript …");
// win32: openapi-typescript writeFileSync on the existing ~900KB file often
// raises UNKNOWN/-4094 while the TS server has it mapped; unlink first.
const apiGenerated = join(
  ROOT,
  "packages",
  "contract-rest-types",
  "src",
  "api.generated.ts",
);
if (process.platform === "win32" && existsSync(apiGenerated)) {
  try {
    unlinkSync(apiGenerated);
  } catch (err) {
    console.warn(
      "gen:types — could not unlink api.generated.ts before rewrite:",
      err instanceof Error ? err.message : err,
    );
  }
}
run("pnpm", ["-C", join(ROOT, "packages", "contract-rest-types"), "gen"]);

console.log("gen:types — validate SSE contract alignment …");
run("uv", ["run", "python", "scripts/validate_sse_contract.py"], { cwd: SERVER });

console.log("gen:types — validate REST path literals vs OpenAPI …");
run("uv", ["run", "python", "scripts/validate_rest_paths.py"], { cwd: SERVER });

console.log("gen:types — done");
