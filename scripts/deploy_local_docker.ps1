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

Write-Host "==> [1/3] 正在从 GitHub 同步最新远端分支信息..." -ForegroundColor Cyan
if ((Get-Command git -ErrorAction SilentlyContinue) -and (Test-Path ".git")) {
    git fetch --prune origin
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

$uiPort = if ($env:QP_UI_PORT) { $env:QP_UI_PORT } else { "3000" }
$apiPort = if ($env:QP_API_PORT) { $env:QP_API_PORT } else { "8000" }
$promPort = if ($env:QP_PROM_PORT) { $env:QP_PROM_PORT } else { "9090" }
$syncSec = if ($env:GITHUB_SYNC_INTERVAL_SEC) { [int]$env:GITHUB_SYNC_INTERVAL_SEC } else { 15 }

if ($ProdOnly) {
    Write-Host "==> [2/3] 启动 QuantPlatform 生产容器栈..." -ForegroundColor Cyan
    docker compose -f docker-compose.yml up -d --build --remove-orphans
} else {
    Write-Host "==> [2/3] 启动 QuantPlatform 实时热同步容器栈 (每 ${syncSec}s 自动同步 GitHub)..." -ForegroundColor Cyan
    docker compose -f docker-compose.yml -f docker-compose.sync.yml up -d --build --remove-orphans
}

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

if ($WatchRebuild -and (-not $ProdOnly)) {
    Write-Host "==> [3/3] 宿主机依赖重编译守护模式已开启 (Ctrl+C 停止宿主机监听)..." -ForegroundColor Cyan
    $lastSha = (git rev-parse HEAD).Trim()
    while ($true) {
        Start-Sleep -Seconds $syncSec
        $curSha = (git rev-parse HEAD).Trim()
        if ($curSha -ne $lastSha) {
            $changed = git diff --name-only $lastSha $curSha
            if ($changed -match "^(pyproject\.toml|Dockerfile\.|docker-compose|frontend/package(-lock)?\.json)") {
                Write-Host "==> 检测到依赖或 Dockerfile 变更，自动重编译容器镜像..." -ForegroundColor Yellow
                docker compose -f docker-compose.yml -f docker-compose.sync.yml up -d --build --remove-orphans
            }
            $lastSha = $curSha
        }
    }
}
