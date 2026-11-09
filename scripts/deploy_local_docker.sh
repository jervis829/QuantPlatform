#!/usr/bin/env bash
# ==============================================================================
# QuantPlatform 本机 Docker 一键部署 + GitHub 最新代码实时同步脚本 (macOS / Linux / WSL2)
#
# 用法:
#   ./scripts/deploy_local_docker.sh                 # 默认启动容器内 24/7 GitHub 实时热同步栈
#   ./scripts/deploy_local_docker.sh --prod-only     # 仅启动生产编译镜像栈（不开启自动拉取）
#   ./scripts/deploy_local_docker.sh --watch-rebuild # 额外在宿主机守护监测依赖变更并自动重编译镜像
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

MODE="sync"
for arg in "$@"; do
  case "$arg" in
    --prod-only)
      MODE="prod"
      ;;
    --watch-rebuild)
      MODE="watch-rebuild"
      ;;
  esac
done

if ! command -v docker >/dev/null 2>&1; then
  echo "[ERROR] 未检测到 docker 命令，请先安装并启动 Docker Desktop 或 Docker Engine。" >&2
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  echo "[ERROR] Docker 守护进程未运行，请先打开 Docker Desktop 并等待左下角引擎就绪。" >&2
  exit 1
fi

# ------------------------------------------------------------------------------
# 自动选择可用镜像源预拉取基础镜像（规避 Docker Hub auth.docker.io TLS EOF）
# ------------------------------------------------------------------------------
resolve_and_pull_base_image() {
  local short_name="$1" # e.g. python:3.11-slim
  local candidates=(
    "public.ecr.aws/docker/library/${short_name}"
    "${short_name}"
    "docker.m.daocloud.io/library/${short_name}"
    "docker.1ms.run/library/${short_name}"
  )

  # 1. 若本地已缓存任一候选镜像，直接使用，零网络等待
  for img in "${candidates[@]}"; do
    if docker image inspect "${img}" >/dev/null 2>&1; then
      echo "${img}"
      return 0
    fi
  done

  # 2. 依次尝试从 AWS Public ECR / Docker Daemon / 国内镜像源拉取
  for img in "${candidates[@]}"; do
    echo "==> 正在拉取基础镜像: ${img} ..." >&2
    if docker pull "${img}" >&2; then
      echo "${img}"
      return 0
    fi
    echo "==> [重试切换] ${img} 拉取失败，自动切换下一个镜像源..." >&2
  done

  echo "[ERROR] 无法拉取基础镜像 ${short_name}，请检查网络连接或 Docker Desktop 代理设置。" >&2
  return 1
}

build_and_start_stack() {
  local compose_files=(-f docker-compose.yml)
  if [ "${MODE}" != "prod" ]; then
    compose_files+=(-f docker-compose.sync.yml)
  fi

  echo "==> [2/4] 检查并准备 Python 与 Node 基础运行镜像..."
  PYTHON_BASE_IMAGE="${PYTHON_BASE_IMAGE:-$(resolve_and_pull_base_image "python:3.11-slim")}"
  NODE_BASE_IMAGE="${NODE_BASE_IMAGE:-$(resolve_and_pull_base_image "node:22-alpine")}"
  export PYTHON_BASE_IMAGE NODE_BASE_IMAGE
  echo "==> 已就绪基础镜像: PYTHON=${PYTHON_BASE_IMAGE}, NODE=${NODE_BASE_IMAGE}"

  echo "==> [3/4] 编译应用镜像 (首次编译需安装依赖，请稍候)..."
  if ! docker compose "${compose_files[@]}" build; then
    echo "==> [自动降级] BuildKit 远端元数据校验受阻，切换本地缓存构建器 (DOCKER_BUILDKIT=0 --pull=false)..."
    DOCKER_BUILDKIT=0 docker build --pull=false \
      --build-arg "PYTHON_BASE_IMAGE=${PYTHON_BASE_IMAGE}" \
      -t quantplatform-api:local \
      -f Dockerfile.api .
    DOCKER_BUILDKIT=0 docker build --pull=false \
      --build-arg "NODE_BASE_IMAGE=${NODE_BASE_IMAGE}" \
      --build-arg "BACKEND_INTERNAL_URL=${DOCKER_BACKEND_URL:-http://api:8000}" \
      -t quantplatform-ui:local \
      -f Dockerfile.ui ./frontend
  fi

  echo "==> [4/4] 启动容器编排栈..."
  docker compose "${compose_files[@]}" up -d --no-build --remove-orphans
}

echo "==> [1/4] 正在从 GitHub 同步最新远端分支信息..."
if command -v git >/dev/null 2>&1 && [ -d ".git" ]; then
  git config remote.origin.fetch "+refs/heads/*:refs/remotes/origin/*" || true
  git fetch --prune origin "+refs/heads/*:refs/remotes/origin/*" || \
    git fetch --prune https://github.com/jervis829/QuantPlatform.git "+refs/heads/*:refs/remotes/origin/*" || true
  if [ -z "$(git status --porcelain --untracked-files=no)" ]; then
    TARGET_BRANCH="${GITHUB_SYNC_BRANCH:-auto}"
    if [ "${TARGET_BRANCH}" = "auto" ]; then
      LATEST_REF="$(git for-each-ref --sort=-committerdate --format='%(refname:short)' refs/remotes/origin/main refs/remotes/origin/arena refs/remotes/origin/arena/* | grep -v '/HEAD$' | head -n 1 || true)"
    else
      LATEST_REF="origin/${TARGET_BRANCH}"
    fi
    if [ -n "${LATEST_REF}" ]; then
      LOCAL_BR="${LATEST_REF#origin/}"
      echo "==> 切换到最新远端提交: ${LATEST_REF}"
      git checkout -B "${LOCAL_BR}" "${LATEST_REF}"
    fi
  else
    echo "==> [提示] 检测到本地存在未提交修改，保留当前工作区文件不覆盖。"
  fi
fi

build_and_start_stack

echo ""
echo "=================================================================="
echo " QuantPlatform 已在本机 Docker 启动成功！"
echo "  - 前端工作台 (UI)     : http://localhost:${QP_UI_PORT:-3000}"
echo "  - 后端接口文档 (API)  : http://localhost:${QP_API_PORT:-8000}/docs"
echo "  - 监控指标 (Prom)     : http://localhost:${QP_PROM_PORT:-9090}"
echo "  - 首次种子回测进度    : docker compose logs -f seed"
if [ "${MODE}" != "prod" ]; then
  echo "  - GitHub 实时同步日志 : docker compose -f docker-compose.yml -f docker-compose.sync.yml logs -f git-sync"
fi
echo "=================================================================="

if [ "${MODE}" = "watch-rebuild" ]; then
  echo "==> [守护] 宿主机依赖重编译监听已开启 (Ctrl+C 停止宿主机监听，容器将继续后台运行)..."
  LAST_REBUILD_SHA="$(git rev-parse HEAD)"
  while true; do
    sleep "${GITHUB_SYNC_INTERVAL_SEC:-15}"
    CUR_SHA="$(git rev-parse HEAD)"
    if [ "${CUR_SHA}" != "${LAST_REBUILD_SHA}" ]; then
      CHANGED="$(git diff --name-only "${LAST_REBUILD_SHA}" "${CUR_SHA}" || true)"
      if echo "${CHANGED}" | grep -qE '^(pyproject\.toml|Dockerfile\.|docker-compose|frontend/package(-lock)?\.json)'; then
        echo "==> 检测到依赖或 Dockerfile 变更 (${LAST_REBUILD_SHA:0:7} -> ${CUR_SHA:0:7})，自动重编译容器镜像..."
        build_and_start_stack
      fi
      LAST_REBUILD_SHA="${CUR_SHA}"
    fi
  done
fi
