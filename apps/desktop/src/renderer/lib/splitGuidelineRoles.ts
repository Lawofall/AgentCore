/** Split the capability-catalog prompt templates into the pieces the 工具箱
 * 「AI 提示词」page actually shows: one `<身份>` per role, one shared worker
 * contract, and never the CEO `<按需目录>` (that catalog is the thin-skill cards).
 *
 * Live turns still assemble the full strings; this is display-only. */

const IDENTITY_BLOCK = /<身份>[\s\S]*?<\/身份>/;
const ON_DEMAND_BLOCK = /<按需目录>[\s\S]*?<\/按需目录>/g;

export function splitWorkerGuideline(text: string): {
  identity: string;
  contract: string;
} {
  const source = text.trim();
  if (!source) return { identity: "", contract: "" };
  const match = source.match(IDENTITY_BLOCK);
  if (!match) {
    return { identity: source, contract: "" };
  }
  return {
    identity: match[0].trim(),
    contract: source.replace(match[0], "").trim(),
  };
}

/** CEO catalog delta minus the on-demand directory. Tagged `<身份>` wins. */
export function extractCeoIdentity(addon: string): string {
  const source = addon.trim();
  if (!source) return "";
  const match = source.match(IDENTITY_BLOCK);
  if (match) return match[0].trim();
  return source.replace(ON_DEMAND_BLOCK, "").trim();
}

/** Leaf and nested workers share the deliverable contract in the catalog. */
export function workerContractFromGuidelines(
  leaf: string,
  nested: string,
): string {
  const fromLeaf = splitWorkerGuideline(leaf).contract;
  if (fromLeaf) return fromLeaf;
  return splitWorkerGuideline(nested).contract;
}
