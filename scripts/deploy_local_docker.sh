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

echo "==> [1/3] 正在从 GitHub 同步最新远端分支信息..."
if command -v git >/dev/null 2>&1 && [ -d ".git" ]; then
  git fetch --prune origin || true
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

if [ "${MODE}" = "prod" ]; then
  echo "==> [2/3] 启动 QuantPlatform 生产容器栈..."
  docker compose -f docker-compose.yml up -d --build --remove-orphans
else
  echo "==> [2/3] 启动 QuantPlatform 实时热同步容器栈 (含 git-sync 守护容器，每 ${GITHUB_SYNC_INTERVAL_SEC:-15}s 自动同步 GitHub)..."
  docker compose -f docker-compose.yml -f docker-compose.sync.yml up -d --build --remove-orphans
fi

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
  echo "==> [3/3] 宿主机依赖重编译守护模式已开启 (Ctrl+C 停止宿主机监听，容器将继续后台运行)..."
  LAST_REBUILD_SHA="$(git rev-parse HEAD)"
  while true; do
    sleep "${GITHUB_SYNC_INTERVAL_SEC:-15}"
    CUR_SHA="$(git rev-parse HEAD)"
    if [ "${CUR_SHA}" != "${LAST_REBUILD_SHA}" ]; then
      CHANGED="$(git diff --name-only "${LAST_REBUILD_SHA}" "${CUR_SHA}" || true)"
      if echo "${CHANGED}" | grep -qE '^(pyproject\.toml|Dockerfile\.|docker-compose|frontend/package(-lock)?\.json)'; then
        echo "==> 检测到依赖或 Dockerfile 变更 (${LAST_REBUILD_SHA:0:7} -> ${CUR_SHA:0:7})，自动重编译容器镜像..."
        docker compose -f docker-compose.yml -f docker-compose.sync.yml up -d --build --remove-orphans
      fi
      LAST_REBUILD_SHA="${CUR_SHA}"
    fi
  done
fi
