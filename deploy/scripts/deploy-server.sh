#!/usr/bin/env bash
#
# AgentCore 一键部署 / 回退脚本（部署与运维.md §三 CI/CD）。
#
#   git checkout <sha> → pull 镜像 → 起基础设施 → 迁移前 DB 快照 → 迁移前 workspaces/ 快照 →
#   停 api → alembic upgrade head → schema gate → workspace tree 迁移 →
#   memory pipeline migrate (contract self-lags one deploy) → project docs 迁移 →
#   compose up → /readyz → 记 SHA
#
# 用法：
#   deploy-server.sh [<sha>|<tag>|latest]    # 缺省 latest（= origin/<branch> HEAD）
#
# 正向部署传新 SHA；回退传旧 SHA（镜像在 ACR 秒级切换，回退自动跳过正向迁移，
# 库需对齐时从 backups/ 手动恢复——见 §7.7）。
#
# 配置（可经环境或 $AGENTCORE_HOME/.env 覆盖，部署与运维.md §8.2）：
#   AGENTCORE_HOME   部署根目录            （默认 /opt/agentcore）
#   GIT_BRANCH       latest 解析的分支      （默认 master，对齐 ci.yml / 仓库主干）
#   IMAGE_REGISTRY   ACR 仓库（含命名空间） （compose 拉取用）
#   ACR_USERNAME/ACR_PASSWORD   ACR 登录凭据（缺省则跳过 docker login）
#   HEALTH_URL       健康检查地址          （默认 http://127.0.0.1:8000/readyz）
#   SKIP_SNAPSHOT=1  跳过迁移前 DB 快照（应急用）
#   SKIP_WORKSPACE_SNAPSHOT=1  跳过迁移前 workspaces/ 快照（应急用；**独立**于 SKIP_SNAPSHOT
#                    ——盘上迁移单向不可逆，跳过它等于放弃唯一的回退素材，必须单独按下）
#   WORKSPACE_BACKUP_KEEP  workspaces 归档保留份数（默认 2；写新档前先轮转）

set -euo pipefail

# ── 自更新防护：先把自己拷到临时副本再 exec，避免 git checkout 中途改写本脚本
#    导致运行中的 shell 读到半截内容（部署与运维.md §三）。──
if [[ "${_DEPLOY_REEXEC:-}" != "1" ]]; then
  _self_tmp="$(mktemp)"
  cp "$0" "$_self_tmp"
  chmod +x "$_self_tmp"
  export _DEPLOY_REEXEC=1
  exec "$_self_tmp" "$@"
fi
trap 'rm -f "$0"' EXIT  # 此处 $0 是临时副本

# ── 分段计时（量化各阶段耗时）──
SECONDS=0
_stage_last=0
stage() { printf '  [+%4ds | %3ds] %s\n' "$SECONDS" "$((SECONDS - _stage_last))" "$1"; _stage_last=$SECONDS; }
log()   { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
warn()  { printf '\033[33m[warn]\033[0m %s\n' "$*" >&2; }
err()   { printf '\033[31m[error]\033[0m %s\n' "$*" >&2; }

# ── 配置 ──
# 活栈 env：deploy-paths.sh。compose 文件仍用 $REPO_DIR/deploy（本脚本其余逻辑不动）。
_ac_paths="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/deploy-paths.sh"
if [[ ! -f "$_ac_paths" ]]; then
  _ac_paths="${AGENTCORE_HOME:-/opt/agentcore}/repo/deploy/scripts/deploy-paths.sh"
fi
# shellcheck source=deploy-paths.sh
. "$_ac_paths"
unset _ac_paths
BACKUP_DIR="${BACKUP_DIR:-$AGENTCORE_HOME/backups}"
SHA_FILE="${SHA_FILE:-$AGENTCORE_HOME/.last-deployed-sha}"
GIT_BRANCH="${GIT_BRANCH:-master}"
COMPOSE_PROJECT="${COMPOSE_PROJECT:-agentcore}"
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:8000/readyz}"
HEALTH_RETRIES="${HEALTH_RETRIES:-30}"
HEALTH_INTERVAL="${HEALTH_INTERVAL:-3}"
TARGET_REF="${1:-latest}"

COMPOSE_BASE_FILES=(
  -f "$REPO_DIR/deploy/docker-compose.server.yml"
  -f "$REPO_DIR/deploy/docker-compose.app.yml"
)
COMPOSE_FILES=( "${COMPOSE_BASE_FILES[@]}" )
# gVisor 默认开：除非 GVISOR_ENABLED=false，否则叠独立 sandboxd 服务
# （seccomp/apparmor + netns caps + 独立入口 + mem_limit）。
# 不叠层 → 沙箱起不来，启动期健康探测失败不拒启（fail-safe）：打
# sandbox.cloud_health_failed warning、执行类整类不装配、能力行如实显示未装配。
_gvisor_off=0
if [[ -f "$ENV_FILE" ]] && grep -Eq '^[[:space:]]*GVISOR_ENABLED[[:space:]]*=[[:space:]]*(false|0|no|False|FALSE)[[:space:]]*$' "$ENV_FILE"; then
  _gvisor_off=1
fi
if [[ "$_gvisor_off" -eq 0 ]]; then
  _sandbox_yml="$REPO_DIR/deploy/docker-compose.sandbox.yml"
  if [[ -f "$_sandbox_yml" ]]; then
    _sandbox_entrypoint="$(dirname "$_sandbox_yml")/sandboxd-entrypoint.sh"
    if [[ ! -f "$_sandbox_entrypoint" ]]; then
      err "云执行默认开但缺少 $_sandbox_entrypoint（或设 GVISOR_ENABLED=false）"
      exit 1
    fi
    COMPOSE_FILES+=(-f "$_sandbox_yml")
  else
    err "云执行默认开但缺少 $_sandbox_yml（或设 GVISOR_ENABLED=false）"
    exit 1
  fi
fi
dc() { docker compose -p "$COMPOSE_PROJECT" "${COMPOSE_FILES[@]}" --env-file "$ENV_FILE" "$@"; }
# One-shots must not include the sandbox overlay (long-running sandboxd + socket volume).
dc_oneshot() { docker compose -p "$COMPOSE_PROJECT" "${COMPOSE_BASE_FILES[@]}" --env-file "$ENV_FILE" "$@"; }

[[ -f "$ENV_FILE" ]] || { err "env file not found: $ENV_FILE（从 production.env.example 复制并填值）"; exit 1; }

log "AgentCore deploy — ref=$TARGET_REF branch=$GIT_BRANCH home=$AGENTCORE_HOME"
if [[ "$_gvisor_off" -eq 0 ]]; then
  log "gVisor sandbox overlay ON（docker-compose.sandbox.yml；默认）"
else
  log "gVisor sandbox overlay OFF（GVISOR_ENABLED=false）"
fi

# ── 1. 解析目标 SHA（latest=分支 HEAD；否则解析 tag/短 SHA 为具体提交）──
cd "$REPO_DIR"
git fetch --quiet --tags --prune origin
if [[ "$TARGET_REF" == "latest" ]]; then
  TARGET_SHA="$(git rev-parse "origin/$GIT_BRANCH")"
else
  TARGET_SHA="$(git rev-parse "${TARGET_REF}^{commit}")"
fi
SHORT_SHA="$(git rev-parse --short "$TARGET_SHA")"
stage "resolved $TARGET_REF → $SHORT_SHA"

# ── 2. 判定回退（目标非当前部署的后代 → 回退，跳过正向迁移）──
PREV_SHA="$(cat "$SHA_FILE" 2>/dev/null || true)"
IS_ROLLBACK=0
if [[ -n "$PREV_SHA" ]] && ! git merge-base --is-ancestor "$PREV_SHA" "$TARGET_SHA" 2>/dev/null; then
  IS_ROLLBACK=1
  warn "目标 $SHORT_SHA 非已部署 ${PREV_SHA:0:7} 的后代 → 按【回退】处理：跳过正向迁移"
fi

# ── 3. 检出目标代码（detached HEAD；脚本已从临时副本运行，改写本文件无碍）──
git checkout --quiet "$TARGET_SHA"
export IMAGE_TAG="$SHORT_SHA"
stage "checked out $SHORT_SHA"

# ── 4. 登录 ACR 并拉取镜像（无凭据则跳过 login，假设已登录或公共镜像）──
if [[ -n "${ACR_USERNAME:-}" && -n "${ACR_PASSWORD:-}" && -n "${IMAGE_REGISTRY:-}" ]]; then
  printf '%s' "$ACR_PASSWORD" | docker login "${IMAGE_REGISTRY%%/*}" -u "$ACR_USERNAME" --password-stdin >/dev/null
  stage "ACR login"
fi
dc pull --quiet
stage "pulled images (api:$SHORT_SHA)"

# ── 5. 起基础设施并等就绪（迁移前提）──
dc up -d postgres redis searxng
for ((i = 1; i <= 30; i++)); do
  dc exec -T postgres pg_isready -U agentcore >/dev/null 2>&1 && break
  [[ $i -eq 30 ]] && { err "postgres 未就绪，终止部署"; exit 1; }
  sleep 2
done
stage "infra up + postgres ready"

# ── 6. 迁移前 DB 快照（仅正向；失败即终止，避免无快照迁移，见 §7.7）──
if [[ "$IS_ROLLBACK" -eq 0 && "${SKIP_SNAPSHOT:-0}" != "1" ]]; then
  mkdir -p "$BACKUP_DIR"
  snapshot="$BACKUP_DIR/pre-deploy-$(date +%Y%m%d-%H%M%S)-$SHORT_SHA.sql.gz"
  dc exec -T postgres pg_dump -U agentcore agentcore | gzip >"$snapshot"
  stage "db snapshot → $(basename "$snapshot")"
fi

# ── 6b. 迁移前 workspaces/ 快照（仅正向；必须早于停 api）──
# 步 7 的 workspace tree 搬迁是**单向**的，没有反向脚本：回退只把库和镜像退回旧版，盘停在
# tree/ 新布局，旧代码按平铺路径找不到目录就无条件 mkdir 重建一个空的——用户看到「文件夹
# 空了」，往空目录里写的新文件还会制造二次分叉，事后连人工归位都做不到。pg_dump 只覆盖库，
# 盘上这一半（appdata 卷、容器内 /data/workspaces）必须自己备。
# 排在停 api **之前**是刻意的：此时备份失败只是取消这次部署（api 照常服务），而不是把
# 「保护数据的闸」变成新的停机源。
if [[ "$IS_ROLLBACK" -eq 0 && "${SKIP_WORKSPACE_SNAPSHOT:-0}" != "1" ]]; then
  mkdir -p "$BACKUP_DIR"
  # 一次性容器读卷：不依赖 api 容器在不在跑（上次部署失败可能把它停在地上）。
  # --no-deps：备份只用 appdata 卷，不该被 DB/Redis 的状态拖住。
  ws_probe="$(dc_oneshot run --rm --no-deps -T api sh -c \
    'if [ -d /data/workspaces ]; then du -sk /data/workspaces | cut -f1; else echo MISSING; fi' \
    | tr -d '\r' | tail -n1 || true)"
  if [[ "$ws_probe" == "MISSING" ]]; then
    warn "盘上还没有 workspaces/（首次部署）— 无云工作区数据可备份"
  elif [[ ! "$ws_probe" =~ ^[0-9]+$ ]]; then
    err "探不到 workspaces/ 体积（输出：${ws_probe:-<空>}）— 证不明「没有数据会丢」，终止部署"
    exit 1
  else
    # 轮转放在写新档**之前**：归档是用户文件的整份拷贝（不是 §7.7 那种 MB 级、可长期堆着的
    # pg_dump），先降到 KEEP-1 份，峰值占盘就是 KEEP 份而不是 KEEP+1；写档全程盘上仍留着
    # 上一份完整归档，中途失败也不至于两手空空。
    WORKSPACE_BACKUP_KEEP="${WORKSPACE_BACKUP_KEEP:-2}"
    mapfile -t ws_old < <(ls -1 "$BACKUP_DIR"/pre-deploy-*-workspaces.tar.gz 2>/dev/null | sort)
    ws_room=$(( WORKSPACE_BACKUP_KEEP > 0 ? WORKSPACE_BACKUP_KEEP - 1 : 0 ))
    if ((${#ws_old[@]} > ws_room)); then
      for ((i = 0; i < ${#ws_old[@]} - ws_room; i++)); do
        rm -f "${ws_old[i]}" && warn "轮转删除 $(basename "${ws_old[i]}")"
      done
    fi
    # 空间检查在动手之前：写到一半撑爆磁盘会同时毁掉备份和还在服务的 api。压缩率取决于用户
    # 存的是文本还是图片/压缩包，按不可压缩的最坏情况 + 20% 余量要。
    ws_need_kb=$((ws_probe + ws_probe / 5))
    ws_free_kb="$(df -Pk "$BACKUP_DIR" | awk 'NR==2 {print $4}')"
    if ((ws_free_kb < ws_need_kb)); then
      err "备份盘空间不足：$BACKUP_DIR 可用 $((ws_free_kb / 1024)) MiB，需 ≥ $((ws_need_kb / 1024)) MiB"
      err "（workspaces/ $((ws_probe / 1024)) MiB + 20% 余量）。清理或扩容后重试 — 本次部署已取消，api 未受影响。"
      exit 1
    fi
    ws_snapshot="$BACKUP_DIR/pre-deploy-$(date +%Y%m%d-%H%M%S)-$SHORT_SHA-workspaces.tar.gz"
    ws_rc=0
    dc_oneshot run --rm --no-deps -T api tar -czf - -C /data workspaces >"$ws_snapshot.partial" || ws_rc=$?
    # tar 退 1 = 「读的过程中有文件被改」——api 还在服务，热备份下属常态，不是失败；2+ 才是
    # 真出错。归档到底完不完整不看 tar 的脸色，由下面的 gzip -t 说了算。
    if ((ws_rc == 1)); then
      warn "tar 报告备份期间有文件变动（api 仍在服务，属预期）"
    elif ((ws_rc != 0)); then
      rm -f "$ws_snapshot.partial"
      err "workspaces/ 备份失败（tar 退出 $ws_rc）— 单向盘上迁移没有备份不许开跑，终止部署。"
      exit 1
    fi
    # 先写 .partial、校验过才改名：半截文件不许冒充「有备份」（同 backup.sh）。
    if ! gzip -t "$ws_snapshot.partial" 2>/dev/null || [[ ! -s "$ws_snapshot.partial" ]]; then
      rm -f "$ws_snapshot.partial"
      err "workspaces/ 归档损坏或为空 — 终止部署（api 未受影响，盘上数据原样）。"
      exit 1
    fi
    mv "$ws_snapshot.partial" "$ws_snapshot"
    stage "workspace snapshot → $(basename "$ws_snapshot") ($(du -h "$ws_snapshot" | cut -f1))"
  fi
elif [[ "$IS_ROLLBACK" -eq 0 ]]; then
  warn "SKIP_WORKSPACE_SNAPSHOT=1 — 跳过 workspaces/ 快照，单向盘上迁移将无回退素材"
fi

# 迁移步的退出码闸。这些脚本用非零同时表达两件事：真出错，以及「我很安全地什么都没做」
# （tree 的 2 = 目标目录已存在、已跳过待人工确认；docs 的 3 = 一个工作区目录都没扫到的保险）。
# set -e 一视同仁，于是后者会在 api 已停、dc up 还没跑的时刻中断部署，而且它是稳定复现的
# ——之后每次部署都卡在同一步。所以只放行点名的「无操作」码；其余非零照旧硬停：迁移真出错
# 时新 api 绝不能接流量。
migrate_step() {
  local label="$1" no_op_codes="$2"
  shift 2
  local rc=0
  "$@" || rc=$?
  if [[ "$rc" -eq 0 ]]; then
    return 0
  fi
  if [[ " $no_op_codes " == *" $rc "* ]]; then
    warn "$label 退出 $rc = 未改动任何数据（请人工确认后处理）— 部署继续"
    return 0
  fi
  err "$label 失败（退出 $rc）— api 保持停机，修复后重跑本脚本"
  return "$rc"
}

# ── 7. 停 api → 迁移 → schema gate（仅正向）──
# 破坏性迁移期间旧 api 不得继续接流量（2026-07-20 UndefinedColumn/Table 窗口）。
# 盘上迁移同样在这个窗口内：resolve_workspace_root 无条件 mkdir，新 api 一接流量，
# 第一个打开云文件夹的用户就把搬迁目标建成空目录，而搬迁「目标已存在就跳过、绝不合并」
# ——事后补跑会被判 skipped，文件永久停在旧的平铺目录里。
if [[ "$IS_ROLLBACK" -eq 0 ]]; then
  # 须与 compose stop_grace_period=40s 对齐。裸 stop 默认 10s 会砍断
  # 排空 5s + 抢救 20s + 收尾 8s，制造孤儿 lease。
  dc stop --timeout 40 api sandboxd 2>/dev/null || true
  stage "api stopped before migrate"
  dc_oneshot run --rm api alembic upgrade head
  stage "alembic upgrade head"
  dc_oneshot run --rm api python scripts/check_schema_gate.py --live
  stage "schema gate (live)"
  # 依赖上面回填的 folders.rel_path；必须早于 project docs（它读迁移后的 tree/ 落点）。
  migrate_step "workspace tree relocation" 2 \
    dc_oneshot run --rm api python scripts/migrate_workspace_tree.py
  stage "workspace tree relocation"
  # Memory migrate + self-lagged contract (sources cleared on the *next* deploy).
  dc_oneshot run --rm api python scripts/migrate_memory_pipeline.py
  stage "memory pipeline migrate/contract (lagged)"
  migrate_step "project docs migration" 3 \
    dc_oneshot run --rm api python scripts/migrate_project_docs.py
  stage "project docs → memory entries"
else
  warn "回退：跳过 alembic（如 schema 不一致，从 $BACKUP_DIR 手动恢复对齐）"
fi

# ── 8. 重建应用容器（切流量到新镜像）──
dc up -d
stage "compose up"

# ── 9. 健康检查（/readyz 含 DB 探测；失败不记 SHA、退出非零）──
ok=0
for ((i = 1; i <= HEALTH_RETRIES; i++)); do
  if curl -fsS -o /dev/null --max-time 5 "$HEALTH_URL"; then ok=1; break; fi
  sleep "$HEALTH_INTERVAL"
done
if [[ "$ok" -ne 1 ]]; then
  err "健康检查失败（${HEALTH_URL}，约 $((HEALTH_RETRIES * HEALTH_INTERVAL))s）— 未记录 SHA。排查：dc logs api"
  exit 1
fi
stage "healthy"

# ── 10. 记录成功部署的 SHA（仅健康后）──
echo "$TARGET_SHA" >"$SHA_FILE"

# ── 11. 回收本机历史 api:<sha>（健康后；ACR 仍可回拉）。默认保留最近 5 个。──
KEEP_API_IMAGES="${KEEP_API_IMAGES:-5}"
if [[ -n "${IMAGE_REGISTRY:-}" ]]; then
  mapfile -t _old_tags < <(docker images "${IMAGE_REGISTRY}/api" --format '{{.CreatedAt}}\t{{.Tag}}' \
    | sort -r | awk -F'\t' '$2!="<none>" && $2!="latest" {print $2}')
  if ((${#_old_tags[@]} > KEEP_API_IMAGES)); then
    for _t in "${_old_tags[@]:KEEP_API_IMAGES}"; do
      docker rmi "${IMAGE_REGISTRY}/api:${_t}" 2>/dev/null || true
    done
    stage "pruned old api tags (keep ${KEEP_API_IMAGES})"
  fi
  docker image prune -f >/dev/null 2>&1 || true
fi
# BuildKit 缓存：时间窗 + 体积上界（与 finish-server.sh 同口径）。
BUILDER_PRUNE_UNTIL="${BUILDER_PRUNE_UNTIL:-48h}"
BUILDER_CACHE_MAX="${BUILDER_CACHE_MAX:-12gb}"
docker builder prune -af --filter "until=${BUILDER_PRUNE_UNTIL}" >/dev/null 2>&1 || true
_builder_help="$(docker builder prune --help 2>&1 || true)"
if grep -q -- '--max-used-space' <<<"$_builder_help"; then
  docker builder prune -af --max-used-space "${BUILDER_CACHE_MAX}" >/dev/null 2>&1 || true
elif grep -q -- '--keep-storage' <<<"$_builder_help"; then
  docker builder prune -af --keep-storage "${BUILDER_CACHE_MAX}" >/dev/null 2>&1 || true
else
  _buildx_help="$(docker buildx prune --help 2>&1 || true)"
  if grep -q -- '--max-used-space' <<<"$_buildx_help"; then
    docker buildx prune -af --max-used-space "${BUILDER_CACHE_MAX}" >/dev/null 2>&1 || true
  fi
fi

log "部署成功 ✅  $SHORT_SHA  （总耗时 ${SECONDS}s）"
