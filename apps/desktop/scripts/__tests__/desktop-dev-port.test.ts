import { describe, expect, it } from "vitest";
import {
  assertDesktopDevPortFree,
  parseLsofPidOutput,
  parseTasklistCsv,
  parseWindowsNetstat,
} from "../../scripts/desktop-dev-port.mjs";

const NETSTAT = `
  TCP    0.0.0.0:5173           0.0.0.0:0              LISTENING       40132
  TCP    [::1]:5173             [::]:0                 LISTENING       40132
  TCP    0.0.0.0:15173          0.0.0.0:0              LISTENING       9
  TCP    127.0.0.1:5173         127.0.0.1:9999         ESTABLISHED     8
  TCP    127.0.0.1:5174         0.0.0.0:0              侦听       55
  UDP    0.0.0.0:5173           *:*                                    7
`;

describe("desktop-dev-port", () => {
  it("reads listening pids and ignores lookalike ports", () => {
    expect(parseWindowsNetstat(NETSTAT, 5173)).toEqual([40132]);
    expect(parseWindowsNetstat(NETSTAT, 5174)).toEqual([55]);
    expect(parseWindowsNetstat(NETSTAT, 15173)).toEqual([9]);
  });

  it("parses lsof -Fp and tasklist csv", () => {
    expect(parseLsofPidOutput("p40132\np40132\np12\n")).toEqual([40132, 12]);
    expect(parseTasklistCsv('"node.exe","40132","Console","1","1 K"\n')).toBe(
      "node.exe",
    );
    expect(
      parseTasklistCsv(
        "INFO: No tasks are running which match the specified criteria.\n",
      ),
    ).toBeNull();
  });

  it("names the occupant and stays quiet when the port is free or unknown", () => {
    expect(() =>
      assertDesktopDevPortFree(5173, {
        findListeningPids: () => [40132],
        processName: () => "node.exe",
      }),
    ).toThrow(/node\.exe（pid 40132）/);

    expect(() =>
      assertDesktopDevPortFree(5173, {
        findListeningPids: () => [40132, 12],
        processName: (pid) => (pid === 40132 ? "node.exe" : null),
      }),
    ).toThrow(/node\.exe（pid 40132）、pid 12/);

    expect(() =>
      assertDesktopDevPortFree(5173, {
        findListeningPids: () => [],
        processName: () => {
          throw new Error("should not look up a name");
        },
      }),
    ).not.toThrow();

    expect(() =>
      assertDesktopDevPortFree(5173, {
        findListeningPids: () => null,
        processName: () => {
          throw new Error("should not look up a name");
        },
      }),
    ).not.toThrow();
  });
});
