# 一键启动公网演示：neo4j + 后端(127.0.0.1:8000) + cloudflared 隧道，并打印公网地址
#   用法： powershell -ExecutionPolicy Bypass -File scripts\public_demo.ps1
#   仅查看当前状态： powershell -ExecutionPolicy Bypass -File scripts\public_demo.ps1 -StatusOnly
param(
    [int]$Port = 8000,
    [string]$Cloudflared = "D:\tools\cloudflared\cloudflared.exe",
    [switch]$SkipNeo4j,
    [switch]$StatusOnly
)

$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$logDir = Join-Path $root "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$tunnelLog = Join-Path $logDir "cloudflared.log"

function Get-TunnelUrl {
    if (-not (Test-Path $tunnelLog)) { return $null }
    $m = Select-String -Path $tunnelLog -Pattern "https://[a-z0-9-]+\.trycloudflare\.com" -AllMatches -ErrorAction SilentlyContinue
    if ($m) { return ($m.Matches.Value | Select-Object -Unique | Select-Object -Last 1) }
    return $null
}
function Test-Port([int]$p) {
    # 用 TCP 连接探测，不依赖 Get-NetTCPConnection（部分环境/精简系统不可用）
    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $client.Connect("127.0.0.1", $p)
        $client.Close()
        return $true
    } catch { return $false }
}
function Test-Proc([string]$name) {
    return [bool](Get-Process -Name $name -ErrorAction SilentlyContinue)
}

if ($StatusOnly) {
    Write-Host "[状态] 后端端口 $Port 监听中: $(Test-Port $Port)"
    Write-Host "[状态] cloudflared 运行中: $(Test-Proc 'cloudflared')"
    Write-Host "[状态] 公网地址: $(Get-TunnelUrl)"
    exit 0
}

Write-Host "==== 公网演示启动 ===="

# 0) 安全配置自检
$envFile = Join-Path $root ".env"
if (Test-Path $envFile) {
    $envText = Get-Content $envFile -Raw
    if ($envText -notmatch "(?m)^ADMIN_TOKEN=\S") { Write-Warning "  .env 里 ADMIN_TOKEN 为空：删除/清空等接口将无法调用" }
    if ($envText -notmatch "(?m)^PUBLIC_MODE=1")  { Write-Warning "  .env 里 PUBLIC_MODE 不是 1：公网模式下建议设为 1" }
    if ($envText -match "(?m)^ALLOWED_ORIGINS=\*"){ Write-Warning "  ALLOWED_ORIGINS=* ：正式提交前请改成前端实际地址" }
} else {
    Write-Warning "  未找到 .env，请先 copy .env.example .env 并填写"
}

# 1) 图数据库
if (-not $SkipNeo4j) {
    Write-Host "[1/3] 启动 Neo4j ..."
    docker start neo4j | Out-Null
}

# 2) 后端（只监听本机，生产不带 --reload）
if (Test-Port $Port) {
    Write-Host "[2/3] 端口 $Port 已在监听，跳过启动后端"
} else {
    Write-Host "[2/3] 启动后端 (127.0.0.1:$Port) ..."
    Start-Process -FilePath (Join-Path $root ".venv\Scripts\python.exe") `
        -ArgumentList "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", "$Port" `
        -WorkingDirectory $root -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $logDir "uvicorn.out.log") `
        -RedirectStandardError  (Join-Path $logDir "uvicorn.err.log") | Out-Null
    for ($i = 1; $i -le 20; $i++) {
        Start-Sleep -Seconds 1
        try { $r = Invoke-RestMethod "http://127.0.0.1:$Port/api/health" -TimeoutSec 3; break } catch { }
    }
    if ($r) { Write-Host "      后端就绪：$($r.status) | 管理口令已配置=$($r.security.admin_token_configured)" }
    else { Write-Warning "      后端 20 秒内未就绪，请看 logs\uvicorn.err.log"; exit 1 }
}

# 3) 隧道（校园网常封 UDP 7844，用 HTTP/2 走 443）
if (Test-Proc "cloudflared") {
    Write-Host "[3/3] cloudflared 已在运行"
} else {
    Write-Host "[3/3] 建立 Cloudflare 隧道 ..."
    if (-not (Test-Path $Cloudflared)) { Write-Warning "  找不到 $Cloudflared，请先下载 cloudflared.exe"; exit 1 }
    Start-Process -FilePath $Cloudflared `
        -ArgumentList "tunnel", "--url", "http://127.0.0.1:$Port", "--protocol", "http2", "--logfile", $tunnelLog, "--loglevel", "info" `
        -WindowStyle Hidden | Out-Null
}

for ($i = 1; $i -le 30; $i++) {
    Start-Sleep -Seconds 2
    $url = Get-TunnelUrl
    if ($url -and (Select-String -Path $tunnelLog -Pattern "Registered tunnel connection" -Quiet -ErrorAction SilentlyContinue)) { break }
}
$url = Get-TunnelUrl
Write-Host ""
if ($url) {
    Write-Host "公网 API 地址： $url" -ForegroundColor Green
    Write-Host "  · 根路径自检：$url/"
    Write-Host "  · 接口文档  ：$url/docs"
    Write-Host "  · 前端 VITE_API_BASE_URL 填这个地址（临时地址，重启会变）"
} else {
    Write-Warning "  未取到公网地址，请查看 logs\cloudflared.log"
}