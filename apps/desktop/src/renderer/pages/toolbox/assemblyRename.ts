/**
 * Rename the assembly the shelf is editing.
 * A system preset cannot be patched, so it is materialized first and the
 * owned id is what receives the name.
 */
export async function renameAssemblyRecord(input: {
  profileId: string;
  kind: string;
  currentName: string;
  nextName: string;
  materialize: (id: string) => Promise<string>;
  updateName: (id: string, name: string) => Promise<void>;
}): Promise<{ id: string; changed: boolean }> {
  const trimmed = input.nextName.trim();
  if (!trimmed || trimmed === input.currentName) {
    return { id: input.profileId, changed: false };
  }
  let id = input.profileId;
  if (input.kind === "system") {
    id = await input.materialize(input.profileId);
  }
  await input.updateName(id, trimmed);
  return { id, changed: true };
}
