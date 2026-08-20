// Self-service account management for the mobile client (设置·账户设置).
//
// Profile / password / avatar / 注销 over the same endpoints the desktop uses.
// REST DTOs track OpenAPI via @agentcore/contract-rest-types.
import type { User } from "@/api/auth";
import { apiFetch } from "@/api/client";
import type { components } from "@/types/api.generated";
import { restPath } from "@agentcore/contract-rest-types/paths";

type Schemas = components["schemas"];

/** Mirror of the server's avatar_upload_max_bytes so an oversized pick fails fast. */
export const AVATAR_MAX_BYTES = 5 * 1024 * 1024;

async function readUser(res: Response, fallback: string): Promise<User> {
  if (!res.ok) throw new Error(await errorMessage(res, fallback));
  return (await res.json()) as User;
}

/** Logged-in catch-up: send a verification code to `email`. */
export async function sendEmailCode(email: string): Promise<void> {
  const res = await apiFetch(restPath("/v1/auth/email/send-code"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email } satisfies Schemas["EmailSendCodeRequest"]),
  });
  if (!res.ok) throw new Error(await errorMessage(res, "发送验证码失败"));
}

/** Logged-in catch-up: confirm the 6-digit code and refresh the user. */
export async function verifyEmail(email: string, code: string): Promise<User> {
  const res = await apiFetch(restPath("/v1/auth/email/verify"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, code } satisfies Schemas["EmailCodeRequest"]),
  });
  return readUser(res, "验证失败");
}

/** Patch profile fields. OpenAPI types may lag username on `UpdateProfileRequest`. */
export interface UpdateProfileInput {
  display_name?: string;
  email?: string | null;
  username?: string;
}

/** Edit nickname, username, and/or email (PATCH semantics). Returns the refreshed user. */
export async function updateProfile(update: UpdateProfileInput): Promise<User> {
  const res = await apiFetch("/v1/auth/me", {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(update),
  });
  return readUser(res, "保存失败");
}

/** Change password; the backend keeps THIS device signed in and revokes the others. */
export async function changePassword(
  currentPassword: string,
  newPassword: string,
): Promise<void> {
  const body = {
    current_password: currentPassword,
    new_password: newPassword,
  } satisfies Schemas["ChangePasswordRequest"];
  const res = await apiFetch("/v1/auth/change-password", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(await errorMessage(res, "修改失败"));
}

/** Upload a new avatar (raw image bytes; server re-encodes to WebP). */
export async function uploadAvatar(file: File): Promise<User> {
  const res = await apiFetch("/v1/users/me/avatar", {
    method: "POST",
    headers: { "Content-Type": file.type || "application/octet-stream" },
    body: file,
  });
  return readUser(res, "上传失败");
}

/** Remove the avatar and fall back to the initial. */
export async function deleteAvatar(): Promise<User> {
  return readUser(
    await apiFetch("/v1/users/me/avatar", { method: "DELETE" }),
    "操作失败",
  );
}

/** Self-service 注销: soft-delete behind a password re-confirm. */
export async function deleteAccount(password: string): Promise<void> {
  const res = await apiFetch("/v1/auth/me", {
    method: "DELETE",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      password,
    } satisfies Schemas["DeleteAccountRequest"]),
  });
  if (!res.ok) throw new Error(await errorMessage(res, "注销失败"));
}

/** Fetch an avatar as an object URL under bearer auth. */
export async function fetchAvatarObjectUrl(
  avatarUrl: string,
): Promise<string | null> {
  try {
    const res = await apiFetch(avatarUrl);
    if (!res.ok) return null;
    return URL.createObjectURL(await res.blob());
  } catch {
    return null;
  }
}

async function errorMessage(res: Response, fallback: string): Promise<string> {
  try {
    const body = (await res.json()) as { error?: { message?: string } };
    return body.error?.message ?? `${fallback} (${res.status})`;
  } catch {
    return `${fallback} (${res.status})`;
  }
}
