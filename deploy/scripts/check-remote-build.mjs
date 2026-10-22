#!/usr/bin/env node
/**
 * 查看生产机 docker buildx 是否在跑（`pnpm deploy:backend` 预构建耗时长时用）。
 *
 *   node deploy/scripts/check-remote-build.mjs
 */
import { loadDeployEnv, sshScript } from "./load-deploy-env.mjs";

loadDeployEnv();
sshScript(`echo "==> docker buildx processes"
ps aux | grep -E 'docker buildx|buildkit' | grep -v grep | head -8 || echo "(none)"
echo "==> builders"
docker buildx ls
echo "==> guest-base / api tags (local)"
docker images --format '{{.Repository}}:{{.Tag}} {{.Size}}' | grep -E 'guest-base|build-cache|/api:' | head -20 || echo "(none)"
echo "==> containers"
docker ps --format '{{.Names}} {{.Status}}' | head -10`);
