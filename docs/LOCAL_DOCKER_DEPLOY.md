# 本机 Docker 一键部署与 GitHub 实时同步指南

本文档说明如何在您本机（macOS / Windows / Linux）通过 Docker 一键启动完整版 **QuantPlatform**（含 PostgreSQL 16、Redis 7、FastAPI 后端、Next.js 14 工作台、Prometheus 监控及 Delphi II `v4.4–v5.0` 真实币安历史回测种子数据），并与 GitHub 远端最新提交保持 **7×24 小时自动实时同步**。

---

## 一、前置准备

1. 安装并启动 **[Docker Desktop](https://www.docker.com/products/docker-desktop/)**（Linux 可直接使用 Docker Engine + `docker compose` v2 插件）。
2. 安装 **Git**。

---

## 二、30 秒一键启动（含 GitHub 实时同步）

### macOS / Linux / WSL2 / Git Bash

打开终端执行以下 3 行命令：

```bash
git clone https://github.com/jervis829/QuantPlatform.git
cd QuantPlatform
git fetch origin && git checkout arena/be59aaf2-quantplatform
./scripts/deploy_local_docker.sh
```

### Windows PowerShell

打开 PowerShell 执行：

```powershell
git clone https://github.com/jervis829/QuantPlatform.git
cd QuantPlatform
git fetch origin; git checkout arena/be59aaf2-quantplatform
powershell -ExecutionPolicy Bypass -File .\scripts\deploy_local_docker.ps1
```

> 或直接使用原生 Docker Compose 命令启动实时同步栈：
> ```bash
> docker compose -f docker-compose.yml -f docker-compose.sync.yml up -d --build
> ```

---

## 三、启动后访问地址

| 服务组件 | 本机访问地址 | 说明 |
| :--- | :--- | :--- |
| **Web 量化工作台 (`ui`)** | **`http://localhost:3000`** | Next.js 14 交互式界面（策略中心、K 线买点卖点、台账、对账、风控） |
| **REST + WS 接口 (`api`)** | **`http://localhost:8000/docs`** | FastAPI OpenAPI 文档与健康检查 (`/api/v1/health`) |
| **监控面板 (`prometheus`)** | **`http://localhost:9090`** | 系统指标采集 |
| **持久化数据库 (`postgres`)** | Docker 卷 `pg_data` | 容器重启或代码热更新均**不会丢失**您的回测、模拟盘与审计记录 |

> **首次启动提示**：`seed` 容器会在后台自动导入 `Delphi II v4.4 ~ v5.0` 及 `BTC 双均线模板` 并跑完 16 组币安真实 1H 历史回测（约需 1～2 分钟），可通过 `docker compose logs -f seed` 查看实时入库进度。

---

## 四、GitHub 实时自动同步工作原理

启动 `docker-compose.sync.yml` 后，栈内常驻 **`git-sync`** 守护容器（无需保持终端窗口打开）：

1. **每 15 秒轮询 GitHub**（可通过环境变量 `GITHUB_SYNC_INTERVAL_SEC` 调整）。
2. **智能追踪最新分支（`GITHUB_SYNC_BRANCH=auto`）**：
   - 自动对比 GitHub 上 `origin/main` 与所有 `origin/arena/*` 分支的最新提交时间戳，自动切换到最新提交所在分支（无论后续在 Arena 新分支推进开发还是合并回 `main`，本机都会自动跟随最新代码）。
   - 如只想固定跟随 `main`，可在 `.env` 中设置 `GITHUB_SYNC_BRANCH=main`。
3. **前后端秒级无感热更新**：
   - 后端 `quant_platform/` 通过卷挂载配合 `uvicorn --reload`，拉取新代码后 **1 秒内自动热重载**；
   - 前端 `frontend/src/` 配合 Next.js 热更新（独立保留容器内 Linux `node_modules`，兼容 macOS / Windows 宿主机），拉取新代码后 **1～2 秒自动刷新**；
   - 若 `scripts/seed_delphi_strategies.py` 新增策略版本（如后续迭代 `v5.1`），`git-sync` 会自动触发增量入库与回测。
4. **本地未提交修改保护**：
   - 若您在本机修改了代码尚未提交，`git-sync` 默认跳过覆盖并输出告警，防止误删您的本地改动（如需强制以远端为准，可设 `GITHUB_SYNC_FORCE_CLEAN=true`）。
5. **依赖变更自动重编译镜像（可选）**：
   - 若希望连 `pyproject.toml` / `package.json` / `Dockerfile` 变动也全自动重新 `docker build`，可使用 `./scripts/deploy_local_docker.sh --watch-rebuild`。

---

## 五、常用运维命令

```bash
# 查看所有容器运行状态
docker compose -f docker-compose.yml -f docker-compose.sync.yml ps

# 查看 GitHub 实时同步日志
docker compose -f docker-compose.yml -f docker-compose.sync.yml logs -f git-sync

# 查看最近一次同步状态详情
cat .git/quant_sync_status.json

# 停止容器（保留 PostgreSQL 数据库卷）
docker compose -f docker-compose.yml -f docker-compose.sync.yml down
```
