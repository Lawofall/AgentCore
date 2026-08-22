import { describe, expect, it } from "vitest";
import { rewriteRootHtmlUrl } from "../vite-serve-html-entry.mjs";

const WEBAPP = "/index.webapp.html";

describe("rewriteRootHtmlUrl", () => {
  it("maps / and /index.html to the browser entry", () => {
    expect(rewriteRootHtmlUrl("/", WEBAPP)).toBe(WEBAPP);
    expect(rewriteRootHtmlUrl("/index.html", WEBAPP)).toBe(WEBAPP);
    expect(rewriteRootHtmlUrl(undefined, WEBAPP)).toBe(WEBAPP);
  });

  it("keeps the query string", () => {
    expect(rewriteRootHtmlUrl("/?x=1", WEBAPP)).toBe(`${WEBAPP}?x=1`);
    expect(rewriteRootHtmlUrl("/index.html?x=1", WEBAPP)).toBe(`${WEBAPP}?x=1`);
  });

  it("leaves the explicit entry and other paths alone", () => {
    expect(rewriteRootHtmlUrl(WEBAPP, WEBAPP)).toBe(WEBAPP);
    expect(rewriteRootHtmlUrl("/index.web.html", WEBAPP)).toBe("/index.web.html");
    expect(rewriteRootHtmlUrl("/assets/app.js", WEBAPP)).toBe("/assets/app.js");
  });
});
