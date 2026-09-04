#!/usr/bin/env bash
# 备份 workspaces/ → 停 api → 迁移（DB + 盘上）→ 起 api → 健康检查。
# 供 deploy-backend.yml SSH 调用；与 /opt/agentcore/finish.sh 同路径约定。
# VPC ACR 的 short-sha tag 可能晚于公网端点同步，拉不到时回退 latest 并本地打 tag。
#
# 盘上工作区备份排在**停 api 之前**：那里失败只是取消这次部署（api 照常服务），放到停机
# 窗口里就成了新的停机源。逃生阀 SKIP_WORKSPACE_SNAPSHOT=1 是应急用，不是默认。
# 保留份数 WORKSPACE_BACKUP_KEEP（默认 2），落点 BACKUP_DIR（默认 $AGENTCORE_HOME/backups）。
#
# 顺序铁律（破坏性迁移）：停旧 api → alembic upgrade → schema gate →
# workspace tree（盘上目录搬迁）→ memory pipeline migrate/contract（自滞后一轮保回滚）
# → project docs（读迁移后的 tree/ 落点）→ 起新 api。
# 禁止在旧容器仍接流量时 DROP COLUMN/TABLE（2026-07-20 单日 582×500 根因）。
#
# 盘上迁移同样必须在窗口内、起 api 之前：resolve_workspace_root 无条件 mkdir，新 api
# 一接流量，第一个打开云文件夹的用户就把迁移目标建成空目录；而搬迁「目标已存在就跳过、
# 绝不合并」，事后补跑会被判 skipped，文件永久留在旧的平铺目录里。
set -euo pipefail

DEPLOY="${AGENTCORE_DEPLOY_DIR:-/opt/agentcore/repo/deploy}"
ENVF="$DEPLOY/config/production.env"
ROOT_ENV="${AGENTCORE_HOME:-/opt/agentcore}/.env"
TAG="${1:?usage: finish-server.sh <short-sha|latest>}"

if [[ ! "$TAG" =~ ^([0-9a-fA-F]{7,40}|latest)$ ]]; then
  echo "ERROR: invalid TAG '$TAG' (expected 7–40 hex chars or 'latest')"
  exit 1
fi

[[ -f "$ENVF" ]] || { echo "ERROR: $ENVF 不存在"; exit 1; }

echo "== [1/13] 切 IMAGE_TAG -> $TAG =="
sed -i "s/^IMAGE_TAG=.*/IMAGE_TAG=$TAG/" "$ENVF"
export IMAGE_TAG="$TAG"
export IMAGE_REGISTRY="$(grep -E '^IMAGE_REGISTRY=' "$ENVF" | head -1 | cut -d= -f2-)"
echo "registry=$IMAGE_REGISTRY tag=$IMAGE_TAG"

echo "== [2/13] 登录 ACR(VPC) =="
ACR_USER="$(grep -E '^ACR_USERNAME=' "$ROOT_ENV" | head -1 | cut -d= -f2-)"
ACR_PASS="$(grep -E '^ACR_PASSWORD=' "$ROOT_ENV" | head -1 | cut -d= -f2-)"
ACR_HOST="$(grep -E '^ACR_REGISTRY=' "$ROOT_ENV" | head -1 | cut -d= -f2-)"
echo "$ACR_PASS" | docker login "$ACR_HOST" -u "$ACR_USER" --password-stdin

IMAGE="${IMAGE_REGISTRY}/api:${IMAGE_TAG}"
echo "== [3/13] 拉 api 镜像 ($IMAGE) =="
# 同机构建路径（remote-build-deploy.mjs 的 buildx --load）镜像已在本机：sha tag 视作
# 不可变，直接复用、省一次 ACR 往返。浮动 latest 不享受此捷径（本机存在 ≠ 最新）。
# 本机缺镜像时照旧 pull + 回退 latest，失败语义不变。
if [[ "$IMAGE_TAG" != "latest" ]] && docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "镜像本机已存在（同机构建），跳过 pull"
elif ! docker pull "$IMAGE" 2>/dev/null; then
  echo "WARN: tag $IMAGE_TAG 在 VPC 不可用，回退 latest 并本地打 tag"
  docker pull "${IMAGE_REGISTRY}/api:latest"
  docker tag "${IMAGE_REGISTRY}/api:latest" "$IMAGE"
fi

# One-shots (probe / tar / alembic / migrate) must not include the sandbox overlay:
# sandboxd is a long-running service. `compose run` through that file is not the
# oneshot api identity. Workspace probe empty-stdout abort still applies.
COMPOSE_BASE=( docker compose -p agentcore -f "$DEPLOY/docker-compose.server.yml" -f "$DEPLOY/docker-compose.app.yml" --env-file "$ENVF" )
COMPOSE=( "${COMPOSE_BASE[@]}" )
# gVisor 默认开（代码/内测默认 true）：除非 env 显式 GVISOR_ENABLED=false，否则叠 sandbox。
# 快照目录若缺 sandbox 则回退仓库 deploy/（remote-build-deploy 已 checkout 的 tree）。
_gvisor_off=0
if grep -Eq '^[[:space:]]*GVISOR_ENABLED[[:space:]]*=[[:space:]]*(false|0|no|False|FALSE)[[:space:]]*$' "$ENVF"; then
  _gvisor_off=1
  echo "gVisor sandbox overlay OFF（GVISOR_ENABLED=false 紧急关闭）"
fi
if [[ "$_gvisor_off" -eq 0 ]]; then
  _sandbox_yml=""
  for _cand in \
    "$DEPLOY/docker-compose.sandbox.yml" \
    "${AGENTCORE_HOME:-/opt/agentcore}/repo/deploy/docker-compose.sandbox.yml"; do
    if [[ -f "$_cand" ]]; then
      _sandbox_yml="$_cand"
      break
    fi
  done
  if [[ -z "$_sandbox_yml" ]]; then
    echo "ERROR: 云执行默认开但找不到 docker-compose.sandbox.yml（或设 GVISOR_ENABLED=false）"
    exit 1
  fi
  _sandbox_entrypoint="$(dirname "$_sandbox_yml")/sandboxd-entrypoint.sh"
  if [[ ! -f "$_sandbox_entrypoint" ]]; then
    echo "ERROR: $_sandbox_yml 需要同目录 sandboxd-entrypoint.sh（或设 GVISOR_ENABLED=false）"
    exit 1
  fi
  # Compose 把 overlay 里的 ./ 卷解析到「第一个 -f」所在目录（=$DEPLOY），
  # 不是 sandbox yml 自己的目录。活栈目录若缺该文件，Docker 会建成同名空目录，
  # 入口变成目录 → sandboxd 空跑秒退。
  _ep_dst="$DEPLOY/sandboxd-entrypoint.sh"
  if [[ -d "$_ep_dst" ]]; then
    echo "WARN: $_ep_dst 是目录（Docker 缺文件时的占位）— 删除后写入入口脚本"
    rm -rf "$_ep_dst"
  fi
  if [[ "$_sandbox_entrypoint" != "$_ep_dst" ]]; then
    cp -f "$_sandbox_entrypoint" "$_ep_dst"
    echo "sandboxd entrypoint -> $_ep_dst"
  fi
  COMPOSE+=(-f "$_sandbox_yml")
  echo "gVisor sandbox overlay: $_sandbox_yml"
fi

echo "== [4/13] 确认基础设施在线 + 等 postgres =="
"${COMPOSE[@]}" up -d postgres redis searxng
for i in $(seq 1 30); do
  "${COMPOSE[@]}" exec -T postgres pg_isready -U agentcore >/dev/null 2>&1 && break
  [[ $i -eq 30 ]] && { echo "ERROR: postgres 未就绪"; exit 1; }
  sleep 2
done
echo "postgres ready"

echo "== [5/13] 备份盘上工作区（workspaces/ -> tar.gz）=="
# [9] 的 workspace tree 搬迁是**单向**的，没有反向脚本：回退只把库和镜像退回旧版，盘停在
# tree/ 新布局，旧代码按平铺路径找不到目录就无条件 mkdir 重建一个空的——用户看到「文件夹
# 空了」，往空目录里写的新文件还会制造二次分叉，事后连人工归位都做不到。pg_dump 只覆盖库，
# 盘上这一半（appdata 卷、容器内 /data/workspaces）必须自己备，而且备不成就不许往下走。
BACKUP_DIR="${BACKUP_DIR:-${AGENTCORE_HOME:-/opt/agentcore}/backups}"
WORKSPACE_BACKUP_KEEP="${WORKSPACE_BACKUP_KEEP:-2}"
if [[ "${SKIP_WORKSPACE_SNAPSHOT:-0}" == "1" ]]; then
  echo "WARN: SKIP_WORKSPACE_SNAPSHOT=1 — 跳过 workspaces/ 快照，单向盘上迁移将无回退素材"
else
  mkdir -p "$BACKUP_DIR"
  # 一次性容器读卷：不依赖 api 容器在不在跑（上次部署失败可能把它停在地上）。
  # --no-deps：备份只用 appdata 卷，不该被 DB/Redis 的状态拖住。
  ws_probe="$("${COMPOSE_BASE[@]}" run --rm --no-deps -T api sh -c \
    'if [ -d /data/workspaces ]; then du -sk /data/workspaces | cut -f1; else echo MISSING; fi' \
    | tr -d '\r' | tail -n1 || true)"
  if [[ "$ws_probe" == "MISSING" ]]; then
    echo "WARN: 盘上还没有 workspaces/（首次部署）— 无云工作区数据可备份"
  elif [[ ! "$ws_probe" =~ ^[0-9]+$ ]]; then
    echo "ERROR: 探不到 workspaces/ 体积（输出：${ws_probe:-<空>}）— 证不明「没有数据会丢」，终止部署"
    exit 1
  else
    # 轮转放在写新档**之前**：归档是用户文件的整份拷贝（不是 §7.7 那种 MB 级、可长期堆着的
    # pg_dump），先降到 KEEP-1 份，峰值占盘就是 KEEP 份而不是 KEEP+1；写档全程盘上仍留着
    # 上一份完整归档，中途失败也不至于两手空空。
    mapfile -t ws_old < <(ls -1 "$BACKUP_DIR"/pre-deploy-*-workspaces.tar.gz 2>/dev/null | sort)
    ws_room=$(( WORKSPACE_BACKUP_KEEP > 0 ? WORKSPACE_BACKUP_KEEP - 1 : 0 ))
    if ((${#ws_old[@]} > ws_room)); then
      for ((i = 0; i < ${#ws_old[@]} - ws_room; i++)); do
        rm -f "${ws_old[i]}" && echo "轮转删除 $(basename "${ws_old[i]}")"
      done
    fi
    # 空间检查在动手之前：写到一半撑爆磁盘会同时毁掉备份和还在服务的 api。压缩率取决于用户
    # 存的是文本还是图片/压缩包，按不可压缩的最坏情况 + 20% 余量要。
    ws_need_kb=$((ws_probe + ws_probe / 5))
    ws_free_kb="$(df -Pk "$BACKUP_DIR" | awk 'NR==2 {print $4}')"
    if ((ws_free_kb < ws_need_kb)); then
      echo "ERROR: 备份盘空间不足：$BACKUP_DIR 可用 $((ws_free_kb / 1024)) MiB，需 ≥ $((ws_need_kb / 1024)) MiB"
      echo "       （workspaces/ $((ws_probe / 1024)) MiB + 20% 余量）。清理或扩容后重试 — 本次部署已取消，api 未受影响。"
      exit 1
    fi
    ws_snapshot="$BACKUP_DIR/pre-deploy-$(date +%Y%m%d-%H%M%S)-$IMAGE_TAG-workspaces.tar.gz"
    ws_rc=0
    "${COMPOSE_BASE[@]}" run --rm --no-deps -T api tar -czf - -C /data workspaces >"$ws_snapshot.partial" || ws_rc=$?
    # tar 退 1 = 「读的过程中有文件被改」——api 还在服务，热备份下属常态，不是失败；2+ 才是
    # 真出错。归档到底完不完整不看 tar 的脸色，由下面的 gzip -t 说了算。
    if ((ws_rc == 1)); then
      echo "WARN: tar 报告备份期间有文件变动（api 仍在服务，属预期）"
    elif ((ws_rc != 0)); then
      rm -f "$ws_snapshot.partial"
      echo "ERROR: workspaces/ 备份失败（tar 退出 $ws_rc）— 单向盘上迁移没有备份不许开跑，终止部署。"
      exit 1
    fi
    # 先写 .partial、校验过才改名：半截文件不许冒充「有备份」（同 backup.sh）。
    if ! gzip -t "$ws_snapshot.partial" 2>/dev/null || [[ ! -s "$ws_snapshot.partial" ]]; then
      rm -f "$ws_snapshot.partial"
      echo "ERROR: workspaces/ 归档损坏或为空 — 终止部署（api 未受影响，盘上数据原样）。"
      exit 1
    fi
    mv "$ws_snapshot.partial" "$ws_snapshot"
    echo "workspace snapshot -> $(basename "$ws_snapshot") ($(du -h "$ws_snapshot" | cut -f1))"
  fi
fi

echo "== [6/13] 停 api（关闭旧代码 + 新 schema 窗口）=="
# 须与 compose stop_grace_period=40s 对齐。裸 stop 默认 10s 会砍断
# 排空 5s + 抢救 20s + 收尾 8s，制造孤儿 lease。
"${COMPOSE[@]}" stop --timeout 40 api sandboxd 2>/dev/null || true

echo "== [7/13] alembic upgrade head =="
"${COMPOSE_BASE[@]}" run --rm api alembic upgrade head

echo "== [8/13] schema gate (live) =="
"${COMPOSE_BASE[@]}" run --rm api python scripts/check_schema_gate.py --live

# 迁移步的退出码闸。这些脚本用非零同时表达两件事：真出错，以及「我很安全地什么都没做」
# （tree 的 2 = 目标目录已存在、已跳过待人工确认；docs 的 3 = 一个工作区目录都没扫到的保险）。
# set -e 一视同仁，于是后者会在 api 已停、compose up 还没跑的时刻中断部署，而且它是稳定
# 复现的——之后每次部署都卡在同一步。所以只放行点名的「无操作」码；其余非零照旧硬停：
# 迁移真出错时新 api 绝不能接流量。
migrate_step() {
  local label="$1" no_op_codes="$2"
  shift 2
  local rc=0
  "$@" || rc=$?
  if [[ "$rc" -eq 0 ]]; then
    return 0
  fi
  if [[ " $no_op_codes " == *" $rc "* ]]; then
    echo "WARN: $label 退出 $rc = 未改动任何数据（请人工确认后处理）— 部署继续"
    return 0
  fi
  echo "ERROR: $label 失败（退出 $rc）— api 保持停机，修复后重跑本脚本"
  return "$rc"
}

# 依赖 [7] 回填的 folders.rel_path；必须早于 [11]（它读迁移后的 tree/ 落点）。
echo "== [9/13] workspace tree 迁移（平铺目录 -> tree/<rel_path>）=="
migrate_step "workspace tree" 2 \
  "${COMPOSE_BASE[@]}" run --rm api python scripts/migrate_workspace_tree.py

echo "== [10/13] memory pipeline migrate/contract (self-lagged) =="
"${COMPOSE_BASE[@]}" run --rm api python scripts/migrate_memory_pipeline.py

echo "== [11/13] project docs 迁移（厚约定文档 -> 记忆条目）=="
migrate_step "project docs" 3 \
  "${COMPOSE_BASE[@]}" run --rm api python scripts/migrate_project_docs.py

echo "== [12/13] 起 api =="
"${COMPOSE[@]}" up -d

echo "== [13/13] 健康检查 /readyz =="
ok=0
for i in $(seq 1 40); do
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:8000/readyz || true)"
  [[ "$code" = "200" ]] && { ok=1; break; }
  sleep 3
done
if [[ "$ok" != "1" ]]; then
  echo "ERROR: /readyz 未健康"
  "${COMPOSE[@]}" logs --tail 80 api
  exit 1
fi
echo "READYZ OK"
echo "--- /version ---"
curl -s http://127.0.0.1:8000/version
echo

# 健康后回收本机历史 api:<sha>（ACR 仍可回拉）。默认保留最近 5 个 tag + 容器在用镜像。
KEEP_API_IMAGES="${KEEP_API_IMAGES:-5}"
echo "== prune old api images (keep ${KEEP_API_IMAGES}) =="
_reg="${IMAGE_REGISTRY:-}"
if [[ -n "$_reg" ]]; then
  mapfile -t _tags < <(docker images "${_reg}/api" --format '{{.CreatedAt}}\t{{.Tag}}' \
    | sort -r | awk -F'\t' 'NR>0 && $2!="<none>" && $2!="latest" {print $2}')
  if ((${#_tags[@]} > KEEP_API_IMAGES)); then
    for _t in "${_tags[@]:KEEP_API_IMAGES}"; do
      docker rmi "${_reg}/api:${_t}" 2>/dev/null || true
    done
  fi
  # latest 浮动标签与当前 sha 共存；清掉已无引用的悬空层
  docker image prune -f >/dev/null 2>&1 || true
fi
# BuildKit 缓存：只靠 until=168h 在频繁部署下清不掉（2026-08 一次手动 prune 清出 16.69G）。
# 先按时间窗丢掉闲置层，再用体积上界卡住剩余缓存。可覆盖 BUILDER_PRUNE_UNTIL / BUILDER_CACHE_MAX。
BUILDER_PRUNE_UNTIL="${BUILDER_PRUNE_UNTIL:-48h}"
BUILDER_CACHE_MAX="${BUILDER_CACHE_MAX:-12gb}"
echo "== prune builder cache (until=${BUILDER_PRUNE_UNTIL}, max=${BUILDER_CACHE_MAX}) =="
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
echo "FINISH DONE ✓"
