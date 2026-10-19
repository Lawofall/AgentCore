/**
 * Rename the assembly the shelf is editing.
 * Official recipes keep their names; only an owned row can be renamed.
 */
export async function renameAssemblyRecord(input: {
  profileId: string;
  kind: string;
  currentName: string;
  nextName: string;
  updateName: (id: string, name: string) => Promise<void>;
}): Promise<{ id: string; changed: boolean }> {
  const trimmed = input.nextName.trim();
  if (!trimmed || trimmed === input.currentName) {
    return { id: input.profileId, changed: false };
  }
  if (input.kind === "system") {
    return { id: input.profileId, changed: false };
  }
  await input.updateName(input.profileId, trimmed);
  return { id: input.profileId, changed: true };
}
