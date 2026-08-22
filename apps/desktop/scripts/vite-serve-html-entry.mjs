/**
 * Vite `root` is `src/renderer`, which also contains Electron's `index.html`.
 * Production remaps via `deploy-web.mjs` (`index.webapp.html` → `index.html`).
 * Dev/preview must do the same, or `http://localhost:<port>/` boots the desktop
 * shell (title bar + window controls) instead of the intended browser entry.
 */

/**
 * @param {string | undefined} url
 * @param {string} htmlPath e.g. "/index.webapp.html"
 */
export function rewriteRootHtmlUrl(url, htmlPath) {
  const raw = url ?? "/";
  const q = raw.indexOf("?");
  const path = q === -1 ? raw : raw.slice(0, q);
  const search = q === -1 ? "" : raw.slice(q);
  if (path === "/" || path === "/index.html") {
    return htmlPath + search;
  }
  return raw;
}

/**
 * @param {import("vite").ViteDevServer | import("vite").PreviewServer} server
 * @param {string} htmlPath
 */
function attach(server, htmlPath) {
  server.middlewares.use((req, _res, next) => {
    req.url = rewriteRootHtmlUrl(req.url, htmlPath);
    next();
  });
}

/**
 * @param {string} htmlPath e.g. "/index.webapp.html"
 * @returns {import("vite").Plugin}
 */
export function serveHtmlAtRoot(htmlPath) {
  return {
    name: "serve-html-at-root",
    configureServer(server) {
      attach(server, htmlPath);
    },
    configurePreviewServer(server) {
      attach(server, htmlPath);
    },
  };
}
