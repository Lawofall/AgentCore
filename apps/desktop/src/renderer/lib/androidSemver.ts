function semverParts(version: string): [number, number, number] {
  const core = (version.split("-")[0] ?? version).trim();
  const bits = core.split(".").map((x) => {
    const n = Number.parseInt(x, 10);
    return Number.isFinite(n) ? n : 0;
  });
  return [bits[0] ?? 0, bits[1] ?? 0, bits[2] ?? 0];
}

export function compareSemver(a: string, b: string): number {
  const pa = semverParts(a);
  const pb = semverParts(b);
  for (let i = 0; i < 3; i++) {
    const av = pa[i] ?? 0;
    const bv = pb[i] ?? 0;
    if (av !== bv) return av < bv ? -1 : 1;
  }
  return 0;
}

export function isAndroidVersionOutdated(
  localVersion: string,
  remoteVersion: string | null | undefined,
): boolean {
  const remote = remoteVersion?.trim();
  if (!remote) return false;
  if (localVersion === "dev") return false;
  return compareSemver(localVersion, remote) < 0;
}
