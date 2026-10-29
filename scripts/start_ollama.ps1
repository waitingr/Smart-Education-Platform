# 启动本机视觉模型（Qwen3-VL-8B）—— 平台唯一使用的模型
# 用法：  powershell -ExecutionPolicy Bypass -File scripts\start_ollama.ps1
#         powershell -ExecutionPolicy Bypass -File scripts\start_ollama.ps1 -Pull   # 首次：拉取模型（约 5.4 GB）
#
# Ollama 运行时与模型库都在项目内（不依赖 E:\Tools 或 %USERPROFILE%\.ollama）：
#   models\ollama\runtime\ollama.exe   Ollama 本体（含 lib\ollama 后端）
#   models\ollama\store                OLLAMA_MODELS（模型权重 + 清单）
#
# 稳定运行的四个环境变量（8 GB 显存机器的关键，已在脚本里设好）：
#   OLLAMA_KEEP_ALIVE=30m      模型常驻 30 分钟，避免每次都花 ~20 秒冷启动
#   OLLAMA_MAX_LOADED_MODELS=1 只允许驻留一个模型，杜绝在模型之间切换时 OOM
#   OLLAMA_NUM_PARALLEL=1      单并发，避免并行请求把显存撑爆
#   OLLAMA_FLASH_ATTENTION=1   开启 flash attention，省显存、略快
# 模型自身的 num_ctx 已固定为 8192（见 Modelfile.8b），与平台 LLM_CONTEXT_CHARS=6000 字预算配套。
#
# 注意：不要设 OLLAMA_HOME —— 新版 Ollama 会据此找运行时目录，指错就报
#       "could not locate ollama app"。只设 OLLAMA_MODELS 即可。

param([switch]$Pull)

$Root = Split-Path -Parent $PSScriptRoot
$Runtime = Join-Path $Root 'models\ollama\runtime'
$Store = Join-Path $Root 'models\ollama\store'
$OllamaExe = Join-Path $Runtime 'ollama.exe'
$Model = 'qwen3-vl:8b'          # 平台唯一使用的模型：能识图、能读文档、全本地

$env:OLLAMA_HOST = '127.0.0.1:11434'
$env:OLLAMA_MODELS = $Store
$env:OLLAMA_KEEP_ALIVE = '30m'
$env:OLLAMA_MAX_LOADED_MODELS = '1'
$env:OLLAMA_NUM_PARALLEL = '1'
$env:OLLAMA_FLASH_ATTENTION = '1'

if (-not (Test-Path $OllamaExe)) {
    Write-Host "未找到 $OllamaExe" -ForegroundColor Red
    Write-Host '本项目的 Ollama 应位于 models\ollama\runtime\。若目录被删，按 models\README.md 重新放入即可。'
    exit 1
}

if (-not (Test-Path (Join-Path $Store 'blobs'))) {
    Write-Host "警告：模型库 $Store 为空，稍后会提示未找到模型。" -ForegroundColor Yellow
}

function Test-OllamaUp {
    try {
        $null = Invoke-RestMethod 'http://127.0.0.1:11434/api/version' -TimeoutSec 3
        return $true
    } catch {
        return $false
    }
}

if (-not (Test-OllamaUp)) {
    Write-Host '启动 ollama serve ...'
    Start-Process -FilePath $OllamaExe -ArgumentList 'serve' -WorkingDirectory $Runtime -WindowStyle Hidden
    for ($i = 0; $i -lt 20; $i++) {
        Start-Sleep -Milliseconds 500
        if (Test-OllamaUp) { break }
    }
}

if (-not (Test-OllamaUp)) {
    Write-Host 'ollama 未能启动，请检查端口 11434 是否被占用。' -ForegroundColor Red
    exit 1
}

$version = (Invoke-RestMethod 'http://127.0.0.1:11434/api/version').version
Write-Host "ollama 已就绪（$version，keep_alive=30m / 单模型 / 单并发）"
Write-Host "模型库：$Store"

# 平台 .env 里的 VISION_MODEL 若与脚本不一致，以 .env 为准并提示
$envFile = Join-Path $Root 'backend\.env'
if (-not (Test-Path $envFile)) { $envFile = Join-Path $Root '.env' }
if (Test-Path $envFile) {
    $line = Get-Content $envFile -Encoding UTF8 | Where-Object { $_ -match '^\s*VISION_MODEL\s*=' } | Select-Object -First 1
    if ($line) {
        $configured = ($line -split '=', 2)[1].Trim() -replace '\s*#.*$', ''
        if ($configured) { $Model = $configured }
    }
}

$installed = @((Invoke-RestMethod 'http://127.0.0.1:11434/api/tags').models | ForEach-Object { $_.name })

if ($installed -notcontains $Model) {
    if ($Pull) {
        Write-Host "拉取 $Model（约 5.4 GB，首次较慢）..."
        & $OllamaExe pull $Model
        $installed = @((Invoke-RestMethod 'http://127.0.0.1:11434/api/tags').models | ForEach-Object { $_.name })
    }
}

if ($installed -contains $Model) {
    $detail = (Invoke-RestMethod 'http://127.0.0.1:11434/api/tags').models | Where-Object { $_.name -eq $Model } | Select-Object -First 1
    $size = [math]::Round([double]$detail.size / 1GB, 2)
    Write-Host "视觉模型可用：$Model（$size GB）" -ForegroundColor Green
    Write-Host '平台启动后 /api/v1/health/stack 中应为 llm.provider=ollama、llm.model=qwen3-vl:8b、llm.vision=true。'
    if (@($installed).Count -gt 1) {
        Write-Host "提示：本机还有其它模型（$($installed -join ', ')）。8 GB 显存建议只保留 $Model。" -ForegroundColor Yellow
    }
} else {
    Write-Host "未找到 $Model。首次使用请运行：" -ForegroundColor Yellow
    Write-Host '  powershell -ExecutionPolicy Bypass -File scripts\start_ollama.ps1 -Pull'
}