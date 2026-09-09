/** Consult-style identifiers are not storefront titles. */
const IDENT_NAME = /^[a-z][a-z0-9_]*$/;

export const PLATFORM_AUTHOR = "官方";

export function isOfficialAuthor(author: string): boolean {
  return author === PLATFORM_AUTHOR;
}

export function listingCopy(row: { name: string; description: string }): {
  title: string;
  subtitle: string;
  ident: string | null;
} {
  const description = row.description.trim();
  if (IDENT_NAME.test(row.name) && description) {
    return { title: description, subtitle: "", ident: row.name };
  }
  return { title: row.name, subtitle: description, ident: null };
}
