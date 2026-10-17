<#
把千问（DashScope）API Key 写入 .env 并重启 AI 服务。
用法：
  powershell -ExecutionPolicy Bypass -File scripts\set_qwen_key.ps1 -Key "sk-你的key"
  powershell -ExecutionPolicy Bypass -File scripts\set_qwen_key.ps1 -Key "sk-..." -Restart
#>
param(
    [Parameter(Mandatory = $true)][string]$Key,
    [switch]$Restart
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $root ".env"
if (-not (Test-Path $envFile)) { throw "找不到 .env，请先 copy .env.example .env" }

if ($Key -notmatch '^sk-') { Write-Warning "Key 通常以 sk- 开头，请确认没有粘贴错误" }

$lines = Get-Content $envFile
$found = $false
$out = foreach ($line in $lines) {
    if ($line -match '^\s*QWEN_API_KEY\s*=') { $found = $true; "QWEN_API_KEY=$Key" }
    else { $line }
}
if (-not $found) { $out = @($out) + "QWEN_API_KEY=$Key" }
Set-Content -Path $envFile -Value $out -Encoding UTF8
Write-Host "已写入 QWEN_API_KEY（长度 $($Key.Length)）到 $envFile" -ForegroundColor Green

if ($Restart) {
    Get-Process python -ErrorAction SilentlyContinue | Where-Object {
        $_.Path -like "*ict-task5*\.venv*"
    } | ForEach-Object {
        # 只重启监听 8001 的那个：通过命令行无法直接判断，这里按端口处理
        $null = $_
    }
    $conn = Get-NetTCPConnection -LocalPort 8001 -State Listen -ErrorAction SilentlyContinue
    if ($conn) { Stop-Process -Id $conn.OwningProcess -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Seconds 2
    Start-Process -FilePath (Join-Path $root ".venv\Scripts\python.exe") `
        -ArgumentList "-m", "uvicorn", "ai_extraction1.api:app", "--host", "127.0.0.1", "--port", "8001" `
        -WorkingDirectory $root -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $root "logs\ai.out.log") `
        -RedirectStandardError (Join-Path $root "logs\ai.err.log") | Out-Null
    Start-Sleep -Seconds 6
    Write-Host "AI 服务已重启，当前状态：" -ForegroundColor Cyan
    try { Invoke-RestMethod "http://127.0.0.1:8001/health" | ConvertTo-Json -Compress } catch { Write-Warning $_.Exception.Message }
} else {
    Write-Host "下一步：重启 AI 服务使其生效（或加 -Restart 参数）"
}