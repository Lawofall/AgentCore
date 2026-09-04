# AgentCore 后端镜像（部署与运维.md §三 / §7.1）。
# 多阶段：builder 用 uv 按 uv.lock 复现依赖到 venv；runtime 只带 venv + 源码，
# 非 root 运行。GIT_SHA / BUILT_AT 构建期注入并经 GET /version 暴露。
#
# 构建示例：
#   生产机（默认清华 PyPI + 阿里云 apt）：
#     docker buildx build -t agentcore-api --build-arg GIT_SHA=... --build-arg BUILT_AT=... apps/server
#   GHA / 境外 / 本地 Windows：
#     docker buildx build ... --build-arg PYPI_INDEX=https://pypi.org/simple/ --build-arg APT_MIRROR= apps/server

# ---- builder：按 uv.lock 解析依赖到 /app/.venv ----
FROM python:3.12-slim AS builder

# PYPI_INDEX：生产机默认清华（大陆快）；GHA/境外传 https://pypi.org/simple/（清华对境外 IP 常 403）。
# 原 ghcr.io/astral-sh/uv 二进制在大陆云拉取超时，故统一 pip 装 uv。
ARG PYPI_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple/
RUN pip install --no-cache-dir -i "${PYPI_INDEX}" uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0 \
    UV_DEFAULT_INDEX=${PYPI_INDEX}

WORKDIR /app

# 先只拷锁文件 + 清单装依赖（缓存层，源码改动不失效）。
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

# 再拷源码并安装项目自身（不含 dev 组：pytest/ruff 不进镜像）。
COPY agentcore ./agentcore
COPY alembic.ini ./
COPY scripts ./scripts
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# ---- runsc：gVisor 沙箱二进制（云端 code_execute 灰度，安全权限与治理.md §五）----
# 官方发布只在 storage.googleapis.com；大陆构建不通时用 RUNSC_URL 指镜像源，
# 或 INSTALL_RUNSC=0 跳过（镜像可用；缺 runsc 时 GVISOR_ENABLED=true 启动期健康探测失败
# 不拒启——打 sandbox.cloud_health_failed warning、执行类整类不装配、能力行如实显示未装配，
# fail-safe）。
# 校验：默认取 ${RUNSC_URL}.sha512；镜像源无校验文件时 RUNSC_SHA512=skip 显式跳过。
FROM python:3.12-slim AS runsc
ARG INSTALL_RUNSC=1
ARG RUNSC_URL=https://storage.googleapis.com/gvisor/releases/release/latest/x86_64/runsc
ARG RUNSC_SHA512=""
COPY scripts/fetch_runsc.py /fetch_runsc.py
RUN mkdir -p /out \
    && if [ "$INSTALL_RUNSC" = "1" ]; then \
         python /fetch_runsc.py /out/runsc && chmod 0755 /out/runsc; \
       fi

# ---- ripgrep：产品 AI grep 内嵌二进制（不依赖 PATH；缺则工具显式失败）----
# 默认 pin 14.1.1 musl 静态；大陆构建可用 RG_URL / RG_SHA256=skip 指镜像源。
FROM python:3.12-slim AS ripgrep
ARG INSTALL_RG=1
ARG RG_VERSION=14.1.1
ARG RG_TARGET=x86_64-unknown-linux-musl
ARG RG_URL=""
ARG RG_SHA256=""
ENV RG_VERSION=${RG_VERSION} \
    RG_TARGET=${RG_TARGET} \
    RG_URL=${RG_URL} \
    RG_SHA256=${RG_SHA256}
COPY scripts/fetch_ripgrep.py /fetch_ripgrep.py
RUN mkdir -p /out \
    && if [ "$INSTALL_RG" = "1" ]; then \
         python /fetch_ripgrep.py /out/rg && chmod 0755 /out/rg; \
       fi

# ---- runtime：精简镜像，只带 venv + 源码 ----
FROM python:3.12-slim AS runtime

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright

# ── 沙箱运行环境（gVisor 云端 code_execute）──
# 沙箱经 ro bind 看到本镜像的 /usr /bin /etc：解释器 = 系统 python3（/usr/local/bin，
# 与 venv 隔离，不污染 uv.lock 管的应用依赖）、node、bash。
# ① 中文字体：文档/图表产物（pptx、matplotlib 图）没有 CJK 字体会全是豆腐块。
# ② nodejs + npm：code_execute 声明支持 javascript；npm 供云沙箱外环验绿 /
#    Vite build（build_app smoke 等）。随基础镜像 Debian 版本，当前 trixie ≈ node 20。
# ⑥ git：云工作区 exec 系统 git——AI 的 git 工具（含账户级 PAT 注入）与用户「从 GitHub
#    克隆」端点都靠它，base slim 不含。缺它两者一起 FileNotFoundError，PAT 设置项形同虚设。
#    HTTPS 走 git 主包自带的 git-remote-http + libcurl（Depends，recommends 关掉也在）。
# APT_MIRROR：生产机默认阿里云（fonts-noto-cjk ~57MB，官方 deb.debian.org 跨境可到几十 KB/s）；
# GHA/境外传空字符串保留官方源。与 PYPI_INDEX 对称。
ARG APT_MIRROR=mirrors.aliyun.com
ARG PYPI_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple/
RUN set -eux; \
    if [ -n "$APT_MIRROR" ]; then \
      if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
        sed -i "s|deb.debian.org|${APT_MIRROR}|g; s|security.debian.org|${APT_MIRROR}|g" \
          /etc/apt/sources.list.d/debian.sources; \
      elif [ -f /etc/apt/sources.list ]; then \
        sed -i "s|deb.debian.org|${APT_MIRROR}|g; s|security.debian.org|${APT_MIRROR}|g" \
          /etc/apt/sources.list; \
      fi; \
    fi; \
    apt-get update; \
    apt-get install -y --no-install-recommends fonts-noto-cjk fontconfig nodejs npm git; \
    rm -rf /var/lib/apt/lists/*

# ③ 文档/数据常用库装进【系统 site-packages】（沙箱 python3 可见）。
#    清单单一事实源：agentcore/tools/sandbox/cloud_python.txt（勿在此手写包名）。
#    装最新版即可——沙箱库不是应用依赖，不进 uv.lock；升级随镜像重建。
COPY agentcore/tools/sandbox/cloud_python.txt /tmp/cloud_python.txt
RUN pip install --no-cache-dir -i "${PYPI_INDEX}" -r /tmp/cloud_python.txt

# ⑤ L3 团队浏览器（内置浏览器与Agent浏览器提案.md · M0，PoC 已验证）：
#    Playwright 装进【系统 site-packages】（沙箱 python3 经 /usr bind 可见，同 ③）；
#    Chromium bundle 落 /opt/ms-playwright（会话 OCI 额外 ro-bind 这一条——产品既有 5 条
#    host bind 覆盖不到 /opt，见 PoC 差异清单）。--with-deps 补齐 Chromium 的 OS 依赖。
#    默认包含；INSTALL_BROWSER=0 可跳过省 ~1.3GB（不装则 GVISOR 浏览器工具不可用，仿 runsc）。
ARG INSTALL_BROWSER=1
RUN set -eux; \
    if [ "$INSTALL_BROWSER" = "1" ]; then \
      pip install --no-cache-dir -i "${PYPI_INDEX}" playwright; \
      playwright install --with-deps chromium; \
      apt-get update; \
      apt-get install -y --no-install-recommends iproute2; \
      rm -rf /var/lib/apt/lists/*; \
    fi
# iproute2：形状 B 的 netns + veth 由独立 sandboxd（uid 0）exec `ip`；
# base slim 不含 `ip`，随浏览器层一并装（仅 INSTALL_BROWSER=1 时需要）。

# ④ runsc 二进制（INSTALL_RUNSC=0 时该层只拷到一个空目录占位）。
COPY --from=runsc /out/ /usr/local/bin/
# ④b ripgrep 二进制（INSTALL_RG=0 时占位空目录；缺 rg 时 grep 工具显式失败）。
COPY --from=ripgrep /out/ /usr/local/bin/

WORKDIR /app

# 非 root 运行 + 预建 /data 并归属 app：命名卷首次挂载会继承该归属，
# 避免非 root 进程写挂载点失败的经典坑（DATA_DIR=/data 写工作区/记忆/快照）。
# 必须建 /home/app：API 以 USER app 跑；HOME 若仍是 /root，
# asyncpg ssl=prefer 会去读 /root/.postgresql/postgresql.key（0600 root）→
# PermissionError → /readyz 误判库挂。私钥必须 app 可读且不过宽（0600；0644
# 会被 OpenSSL/libpq 拒绝）。sandboxd 是独立 uid-0 容器，不跑 postgres 客户端。
RUN groupadd --system app \
    && useradd --system --gid app --create-home --home-dir /home/app app \
    && mkdir -p /data \
    && chown app:app /data /home/app \
    && chmod 0700 /home/app

# After the home exists: USER app keeps this ENV. sandboxd (uid 0) does not
# inherit this image USER at runtime (compose sets user 0:0).
ENV HOME=/home/app \
    USER=app \
    LOGNAME=app

COPY --from=builder --chown=app:app /app /app

# 构建溯源：构建期钉入镜像 ENV，运行时经 GET /version 暴露（部署与运维.md §7.1）。
# 必须放在 apt/pip/Playwright 等重型层之后：这两个值每次构建都变，而 ARG 声明后的
# RUN 会隐式把 ARG 值计入缓存 key——放 runtime 开头会把整段重型层缓存打穿
# （热修被迫重下 Chromium ~110s + apt 字体 ~47s）。放这里只影响元数据层，零成本。
ARG GIT_SHA=unknown
ARG BUILT_AT=unknown
ENV GIT_SHA=${GIT_SHA} \
    BUILT_AT=${BUILT_AT}

USER app

EXPOSE 8000

# 存活探针：/livez 进程在即恒 200（部署与运维.md §7）。用 urllib 免装 curl。
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/livez', timeout=3).status==200 else 1)"]

# 默认启 API（python -m agentcore 从环境读 HOST/PORT/DEBUG；prod DEBUG=false →
# 无 reload；默认 workers=1）。WEB_CONCURRENCY / UVICORN_WORKERS /
# AGENTCORE_API_WORKERS > 1 一律启动拒绝（DEBUG 仅警告）：履约中枢与对话事件流是进程内
# 单例，RATE_LIMIT_BACKEND=redis 只共享限流器、并不解锁（见部署拓扑 §六）。
# 部署脚本以 `alembic upgrade head` 覆盖命令跑迁移。
CMD ["python", "-m", "agentcore"]
