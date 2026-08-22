import {
  bearerAuthHeader,
  clearBearerTokens,
  getBearerTokens,
  isBearerAuth,
  sessionCredentials,
  setBearerTokens,
} from "@/lib/sessionAuth";
import { afterEach, describe, expect, it, vi } from "vitest";

describe("sessionAuth", () => {
  afterEach(() => {
    clearBearerTokens();
    vi.unstubAllGlobals();
  });

  it("defaults to cookie credentials outside Capacitor", () => {
    expect(isBearerAuth()).toBe(false);
    expect(sessionCredentials()).toBe("include");
    expect(bearerAuthHeader()).toEqual({});
  });

  it("uses Bearer when marked native", () => {
    vi.stubGlobal("window", { __NATIVE__: true });
    setBearerTokens({ access_token: "a", refresh_token: "r" });
    expect(isBearerAuth()).toBe(true);
    expect(sessionCredentials()).toBe("omit");
    expect(bearerAuthHeader()).toEqual({ Authorization: "Bearer a" });
    expect(getBearerTokens()?.refresh_token).toBe("r");
  });
});
