/**
 * Derive beta pack / runtime icons from stable sources (「测」角标).
 * The ligature is near-neutral, so a hue shift would not mark the channel.
 * Badge sits in the empty top-right. The mark leans 6°, so the C's top
 * terminal sits further right: r is 10% of the side and the margin is
 * 3.125%, which clears that stroke and stays inside the squircle.
 *
 *   node scripts/generate-beta-icons.mjs
 */
import { mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import sharp from "sharp";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");

/** @param {string} src @param {string} dest */
async function makeBetaIcon(src, dest) {
  const base = sharp(src).ensureAlpha();
  const meta = await base.metadata();
  const w = meta.width ?? 512;
  const h = meta.height ?? 512;
  const size = Math.min(w, h);
  const badgeR = Math.round(size * 0.1);
  const margin = Math.round(size * 0.03125);
  const cx = w - badgeR - margin;
  const cy = badgeR + margin;
  const fontSize = Math.round(badgeR * 1.15);
  const svg = Buffer.from(
    `<svg width="${w}" height="${h}" xmlns="http://www.w3.org/2000/svg">
  <circle cx="${cx}" cy="${cy}" r="${badgeR}" fill="#c45c26"/>
  <text x="${cx}" y="${cy + 1}" font-size="${fontSize}"
    font-family="Segoe UI, 'PingFang SC', 'Microsoft YaHei', sans-serif"
    font-weight="700" fill="#ffffff" text-anchor="middle"
    dominant-baseline="central">测</text>
</svg>`,
  );

  mkdirSync(dirname(dest), { recursive: true });
  await sharp(src)
    .ensureAlpha()
    .composite([{ input: svg, blend: "over" }])
    .png()
    .toFile(dest);
  console.log(`→ ${dest}`);
}

async function main() {
  await makeBetaIcon(
    join(root, "build/icon-win.png"),
    join(root, "resources/channel-icons/icon-win-beta.png"),
  );
  await makeBetaIcon(
    join(root, "build/icon-mac.png"),
    join(root, "resources/channel-icons/icon-mac-beta.png"),
  );
  await makeBetaIcon(
    join(root, "resources/icon.png"),
    join(root, "resources/icon-beta.png"),
  );
  console.log("✓ beta icons generated");
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
