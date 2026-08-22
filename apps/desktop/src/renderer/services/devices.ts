import { api } from "@/services/api";
import type { components } from "@/types/api.generated";

type DevicePlatform = components["schemas"]["DeviceRegistration"]["platform"];

export async function registerDevice(
  token: string,
  platform: DevicePlatform,
): Promise<void> {
  await api.post("/v1/devices", { token, platform });
}

export async function unregisterDevice(token: string): Promise<void> {
  await api.delete(`/v1/devices?token=${encodeURIComponent(token)}`);
}
