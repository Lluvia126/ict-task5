<#
演示前自检：一条命令检查后端/图谱/安全配置/公网入口/跨域，全部通过再交付。
用法：
  powershell -ExecutionPolicy Bypass -File scripts\public_ready_check.ps1
  powershell -ExecutionPolicy Bypass -File scripts\public_ready_check.ps1 -ApiUrl https://api.你的域名 -FrontendOrigin https://demo.你的域名
#>
param(
    [string]$ApiUrl = "",          # 公网/正式地址（留空则只做本机检查）
    [string]$FrontendOrigin = "",  # 前端地址，用于检查 CORS 与 Content-Disposition 暴露
    [int]$Port = 8000
)

$ErrorActionPreference = "Continue"
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { }
$ok = 0; $warn = 0; $fail = 0
function Line($level, $text) {
    switch ($level) {
        "OK"   { $script:ok++;   Write-Host ("  [OK]   " + $text) -ForegroundColor Green }
        "WARN" { $script:warn++; Write-Host ("  [WARN] " + $text) -ForegroundColor Yellow }
        "FAIL" { $script:fail++; Write-Host ("  [FAIL] " + $text) -ForegroundColor Red }
    }
}

Write-Host "==== 演示前自检 ====" -ForegroundColor Cyan

# ---------- 1. 本机后端 ----------
$local = "http://127.0.0.1:$Port"
$health = $null
try { $health = Invoke-RestMethod "$local/api/health" -TimeoutSec 8 } catch { }
if ($health) { Line OK "后端存活：$local（$($health.service)）" }
else { Line FAIL "本机后端无响应，请先启动（scripts\public_demo.ps1）" }

$listen = netstat -ano | Select-String "LISTENING" | Select-String ":$Port"
if ($listen -match "0\.0\.0\.0:$Port") { Line WARN "后端监听 0.0.0.0:$Port（同网段可绕过隧道直连），应改成 127.0.0.1" }
elseif ($listen -match "127\.0\.0\.1:$Port") { Line OK "后端只监听 127.0.0.1:$Port" }
else { Line FAIL "端口 $Port 没有监听" }

# ---------- 2. 安全配置 ----------
if ($health) {
    $sec = $health.security
    if ($sec.admin_token_configured) { Line OK "管理口令已配置（删除/清空/图谱重建需 X-Admin-Token）" }
    else { Line FAIL "ADMIN_TOKEN 为空：破坏性接口无法调用，公网下也不安全" }
    if ($sec.public_mode) { Line OK "PUBLIC_MODE=1（公网模式）" } else { Line WARN "PUBLIC_MODE 不是 1" }
    if ($sec.allow_anon_upload -or $sec.allow_anon_extract) {
        Line OK ("匿名可用：上传=" + $sec.allow_anon_upload + " 抽取=" + $sec.allow_anon_extract +
                 "，配额 " + $sec.quota_upload_per_ip_per_day + "/IP/天、" +
                 $sec.quota_extract_per_ip_per_day + "/IP/天、全局 " + $sec.quota_extract_global_per_day + "/天")
    } else { Line WARN "上传与抽取都关了：评委无法自助测试任务三" }
}

# ---------- 3. 数据与图谱 ----------
$count = 0
try {
    $items = Invoke-RestMethod "$local/api/extractions?limit=1000" -TimeoutSec 15
    $count = @($items).Count
    if ($count -gt 0) { Line OK "抽取结果入库：$count 条" } else { Line WARN "抽取结果 0 条（正式演示前应导入官方数据）" }
} catch { Line WARN "读取 /api/extractions 失败" }

try {
    $st = Invoke-RestMethod "$local/api/analysis/graph/status" -TimeoutSec 15
    if (-not $st.neo4j.connected) { Line FAIL "Neo4j 未连接：$($st.neo4j.error)" }
    elseif ($st.in_sync) { Line OK ("图谱与抽取数据一致（节点 " + $st.graph.nodes + "，关系 " + $st.graph.relationships + "）") }
    else { Line FAIL ("图谱不一致：缺失 " + @($st.missing_in_graph).Count + " 条、多余 " + @($st.stale_in_graph).Count + " 条，请 POST /api/analysis/graph/sync") }
} catch { Line WARN "读取图谱状态失败" }

try {
    $ai = Invoke-RestMethod "$local/api/ai/health" -TimeoutSec 20
    if ($ai.ok) { Line OK "AI 抽取服务连通（$($ai.status)）" } else { Line FAIL "AI 抽取服务不通：$($ai.error)" }
} catch { Line FAIL "AI 抽取服务不可达（检查 .env 的 AI_SERVICE_URL）" }

# ---------- 4. 隧道 ----------
$cf = Get-Process cloudflared -ErrorAction SilentlyContinue
if ($cf) { Line OK "cloudflared 运行中（PID $($cf.Id -join ',')）" } else { Line FAIL "cloudflared 未运行" }
if (Get-Service cloudflared -ErrorAction SilentlyContinue) { Line OK "cloudflared Windows 服务已安装（开机自启）" }
else { Line WARN "未安装 cloudflared 服务：重启机器后隧道不会自动恢复" }

# ---------- 5. 公网入口 ----------
if ($ApiUrl) {
    $ApiUrl = $ApiUrl.TrimEnd("/")
    try {
        $pub = Invoke-RestMethod "$ApiUrl/api/health" -TimeoutSec 25
        Line OK "公网可访问：$ApiUrl（status=$($pub.status)）"
    } catch { Line FAIL "公网地址不可访问：$ApiUrl（$($_.Exception.Message)）" }

    try {
        Invoke-WebRequest -Uri "$ApiUrl/api/extractions" -Method Delete -TimeoutSec 25 | Out-Null
        Line FAIL "公网 DELETE 未被拦截！请检查 ADMIN_TOKEN / PUBLIC_MODE"
    } catch {
        $code = $_.Exception.Response.StatusCode.value__
        if ($code -eq 401) { Line OK "公网无口令删除被拒绝（401）" }
        else { Line WARN "公网删除返回 $code（期望 401）" }
    }

    if ($FrontendOrigin) {
        $raw = curl.exe -s -D - -o NUL -H "Origin: $FrontendOrigin" --max-time 25 "$ApiUrl/api/health" 2>$null
        $acao = ($raw | Select-String -Pattern "access-control-allow-origin:\s*(.+)" -AllMatches).Matches.Groups[1].Value.Trim()
        $expo = ($raw | Select-String -Pattern "access-control-expose-headers:\s*(.+)" -AllMatches).Matches.Groups[1].Value.Trim()
        if ($acao) { Line OK "CORS 允许前端来源：$acao" } else { Line FAIL "CORS 未放行 $FrontendOrigin（检查 ALLOWED_ORIGINS）" }
        if ($expo -match "Content-Disposition") { Line OK "已暴露 Content-Disposition（前端可读下载文件名）" }
        else { Line WARN "未暴露 Content-Disposition（当前 Expo: $expo）" }
    } else { Line WARN "未提供 -FrontendOrigin，跳过 CORS 检查" }
} else {
    Line WARN "未提供 -ApiUrl，跳过公网检查"
}

Write-Host ""
Write-Host ("==== 结果：通过 $ok，警告 $warn，失败 $fail ====") -ForegroundColor $(if ($fail -eq 0) { "Green" } else { "Red" })
if ($fail -gt 0) { exit 1 } else { exit 0 }