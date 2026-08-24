#!/usr/bin/env node
/**
 * 手机 Web SPA 已退役。独立 m.example.com 站已下线。
 * 禁止再构建或上传 stub。
 *
 *   pnpm -C apps/mobile deploy:pages
 */
console.error(
  "手机 Web 已退役：独立 m.example.com 站已下线。禁止再部署 stub。",
);
process.exit(1);
