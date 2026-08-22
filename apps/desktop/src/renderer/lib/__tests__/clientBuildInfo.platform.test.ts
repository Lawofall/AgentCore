import { clientHeaders, clientPlatform } from "@/lib/clientBuildInfo";
import {
  getDeviceId,
  resetDeviceIdentityForTests,
} from "@/services/deviceIdentity";
import { afterEach, describe, expect, it, vi } from "vitest";

describe("clientBuildInfo platform", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    resetDeviceIdentityForTests();
  });

  it("sends X-Client-Platform=android on Capacitor", () => {
    vi.stubGlobal("window", {
      __WEB__: true,
      __NATIVE__: true,
      __NATIVE_PLATFORM__: "android",
    });
    expect(clientPlatform()).toBe("android");
    expect(clientHeaders()["X-Client-Platform"]).toBe("android");
  });

  it("sends X-Client-Platform=web in web runtime", () => {
    vi.stubGlobal("window", { __WEB__: true });
    expect(clientPlatform()).toBe("web");
    expect(clientHeaders()["X-Client-Platform"]).toBe("web");
  });

  it("sends X-Client-Platform=desktop outside web runtime", () => {
    vi.stubGlobal("window", { __WEB__: false });
    expect(clientPlatform()).toBe("desktop");
    expect(clientHeaders()["X-Client-Platform"]).toBe("desktop");
  });

  it("omits X-Client-Device until the device id resolves", () => {
    vi.stubGlobal("window", { __WEB__: false });
    expect(clientHeaders()["X-Client-Device"]).toBeUndefined();
  });

  it("sends the resolved device id so local ops pin to this install", async () => {
    vi.stubGlobal("window", {
      __WEB__: false,
      deviceIdentityApi: { getDeviceId: async () => "device-abc" },
    });
    await getDeviceId();
    expect(clientHeaders()["X-Client-Device"]).toBe("device-abc");
  });

  it("never sends a device id from web runtime (not a fulfiller)", async () => {
    vi.stubGlobal("window", {
      __WEB__: false,
      deviceIdentityApi: { getDeviceId: async () => "device-abc" },
    });
    await getDeviceId();
    vi.stubGlobal("window", { __WEB__: true });
    expect(clientHeaders()["X-Client-Device"]).toBeUndefined();
  });
});
