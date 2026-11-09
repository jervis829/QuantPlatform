# ==============================================================================
# QuantPlatform 本机 Docker 一键部署 + GitHub 最新代码实时同步脚本 (Windows PowerShell)
#
# 用法:
#   powershell -ExecutionPolicy Bypass -File .\scripts\deploy_local_docker.ps1
#   powershell -ExecutionPolicy Bypass -File .\scripts\deploy_local_docker.ps1 -ProdOnly
#   powershell -ExecutionPolicy Bypass -File .\scripts\deploy_local_docker.ps1 -WatchRebuild
# ==============================================================================

param(
    [switch]$ProdOnly,
    [switch]$WatchRebuild
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Error "未检测到 docker 命令，请先安装并启动 Docker Desktop。"
    exit 1
}

docker info *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Error "Docker Desktop 尚未启动就绪，请先打开 Docker Desktop。"
    exit 1
}

function Resolve-BaseImage([string]$ShortName) {
    $candidates = @(
        "public.ecr.aws/docker/library/$ShortName",
        "$ShortName",
        "docker.m.daocloud.io/library/$ShortName",
        "docker.1ms.run/library/$ShortName"
    )
    foreach ($img in $candidates) {
        docker image inspect $img *> $null
        if ($LASTEXITCODE -eq 0) { return $img }
    }
    foreach ($img in $candidates) {
        Write-Host "==> 正在拉取基础镜像: $img ..." -ForegroundColor Cyan
        docker pull $img
        if ($LASTEXITCODE -eq 0) { return $img }
        Write-Host "==> [重试切换] $img 拉取失败，尝试下一个镜像源..." -ForegroundColor Yellow
    }
    throw "无法拉取基础镜像 $ShortName，请检查网络或 Docker Desktop 代理设置。"
}

Write-Host "==> [1/4] 正在从 GitHub 同步最新远端分支信息..." -ForegroundColor Cyan
if ((Get-Command git -ErrorAction SilentlyContinue) -and (Test-Path ".git")) {
    git config remote.origin.fetch "+refs/heads/*:refs/remotes/origin/*"
    git fetch --prune origin "+refs/heads/*:refs/remotes/origin/*"
    if ($LASTEXITCODE -ne 0) {
        git fetch --prune https://github.com/jervis829/QuantPlatform.git "+refs/heads/*:refs/remotes/origin/*"
    }
    $dirty = git status --porcelain --untracked-files=no
    if (-not $dirty) {
        $branchSpec = if ($env:GITHUB_SYNC_BRANCH) { $env:GITHUB_SYNC_BRANCH } else { "auto" }
        if ($branchSpec -eq "auto") {
            $refs = git for-each-ref --sort=-committerdate --format="%(refname:short)" refs/remotes/origin/main refs/remotes/origin/arena refs/remotes/origin/arena/*
            $latestRef = ($refs | Where-Object { $_ -notmatch "/HEAD$" } | Select-Object -First 1)
        } else {
            $latestRef = "origin/$branchSpec"
        }
        if ($latestRef) {
            $localBr = $latestRef -replace "^origin/", ""
            Write-Host "==> 切换到最新远端提交: $latestRef" -ForegroundColor Green
            git checkout -B $localBr $latestRef
        }
    } else {
        Write-Host "==> [提示] 检测到本地未提交修改，保留当前工作区文件不覆盖。" -ForegroundColor Yellow
    }
}

$env:PYTHON_BASE_IMAGE = if ($env:PYTHON_BASE_IMAGE) { $env:PYTHON_BASE_IMAGE } else { Resolve-BaseImage "python:3.11-slim" }
$env:NODE_BASE_IMAGE = if ($env:NODE_BASE_IMAGE) { $env:NODE_BASE_IMAGE } else { Resolve-BaseImage "node:22-alpine" }

$uiPort = if ($env:QP_UI_PORT) { $env:QP_UI_PORT } else { "3000" }
$apiPort = if ($env:QP_API_PORT) { $env:QP_API_PORT } else { "8000" }
$promPort = if ($env:QP_PROM_PORT) { $env:QP_PROM_PORT } else { "9090" }
$syncSec = if ($env:GITHUB_SYNC_INTERVAL_SEC) { [int]$env:GITHUB_SYNC_INTERVAL_SEC } else { 15 }

$composeArgs = @("-f", "docker-compose.yml")
if (-not $ProdOnly) { $composeArgs += @("-f", "docker-compose.sync.yml") }

Write-Host "==> [3/4] 编译并启动 QuantPlatform 容器栈..." -ForegroundColor Cyan
docker compose @composeArgs build
if ($LASTEXITCODE -ne 0) {
    $env:DOCKER_BUILDKIT = "0"
    docker build --pull=false --build-arg "PYTHON_BASE_IMAGE=$($env:PYTHON_BASE_IMAGE)" -t quantplatform-api:local -f Dockerfile.api .
    docker build --pull=false --build-arg "NODE_BASE_IMAGE=$($env:NODE_BASE_IMAGE)" --build-arg "BACKEND_INTERNAL_URL=http://api:8000" -t quantplatform-ui:local -f Dockerfile.ui ./frontend
}
docker compose @composeArgs up -d --no-build --remove-orphans

Write-Host ""
Write-Host "==================================================================" -ForegroundColor Green
Write-Host " QuantPlatform 已在本机 Docker 启动成功！" -ForegroundColor Green
Write-Host "  - 前端工作台 (UI)     : http://localhost:$uiPort"
Write-Host "  - 后端接口文档 (API)  : http://localhost:$apiPort/docs"
Write-Host "  - 监控指标 (Prom)     : http://localhost:$promPort"
Write-Host "  - 首次种子回测进度    : docker compose logs -f seed"
if (-not $ProdOnly) {
    Write-Host "  - GitHub 实时同步日志 : docker compose -f docker-compose.yml -f docker-compose.sync.yml logs -f git-sync"
}
Write-Host "==================================================================" -ForegroundColor Green
