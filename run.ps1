# ============================================================================
# 灵眸智教 · 一键启动（Windows PowerShell）
# 用法：  .\run.ps1              启动（若未运行则自动拉起 Ollama）
#         .\run.ps1 -Check       只做环境自检，不启动
#         .\run.ps1 -Seed        先灌演示数据再启动
# ============================================================================
param(
    [switch]$Check,
    [switch]$Seed
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Definition
$py = Join-Path $root '.venv\Scripts\python.exe'
$envFile = Join-Path $root 'backend\.env'

function Write-Step($text) { Write-Host "==> $text" -ForegroundColor Cyan }
function Write-Ok($text) { Write-Host "  OK  $text" -ForegroundColor Green }
function Write-Warn2($text) { Write-Host "   !  $text" -ForegroundColor Yellow }

Write-Step '环境检查'

if (-not (Test-Path $py)) {
    Write-Warn2 "未找到虚拟环境：$py"
    Write-Host '     先执行：' -NoNewline
    Write-Host 'python -m venv .venv; .\.venv\Scripts\python.exe -m pip install -r requirements.txt' -ForegroundColor White
    exit 1
}
Write-Ok "Python: $py"

if (-not (Test-Path $envFile)) {
    Write-Warn2 '缺少 backend\.env，将使用默认配置（自动降级 SQLite + 离线检索模式）'
    Write-Host '     可复制 .env.example 为 .env 后按需修改' -ForegroundColor White
} else {
    Write-Ok '配置: backend\.env'
}

# ---- 数据库 ----
& $py -c "import sys; sys.path.insert(0, r'$root\backend'); from config import Config; print(Config.SQLALCHEMY_DATABASE_URI)" 2>$null |
    ForEach-Object { Write-Ok "数据库: $_" }

# ---- Ollama 与视觉模型（运行时与模型库都在项目内） ----
$ollamaRuntime = Join-Path $root 'models\ollama\runtime'
$ollamaExe = Join-Path $ollamaRuntime 'ollama.exe'
$ollamaStore = Join-Path $root 'models\ollama\store'
$env:OLLAMA_HOST = '127.0.0.1:11434'   # 不设 OLLAMA_HOME：新版 Ollama 会据此找运行时，指错会报 could not locate ollama app
if (Test-Path (Join-Path $ollamaStore 'blobs')) { $env:OLLAMA_MODELS = $ollamaStore }
$env:OLLAMA_KEEP_ALIVE = '30m'
$env:OLLAMA_MAX_LOADED_MODELS = '1'
$env:OLLAMA_NUM_PARALLEL = '1'
$env:OLLAMA_FLASH_ATTENTION = '1'

if (-not (Test-Path $ollamaExe)) {
    $ollama = Get-Command ollama -ErrorAction SilentlyContinue
    if ($ollama) { $ollamaExe = $ollama.Source; $ollamaRuntime = Split-Path -Parent $ollamaExe }
}
if (Test-Path $ollamaExe) {
    $running = $false
    try {
        $probe = Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/tags' -TimeoutSec 3
        $running = $true
        $names = ($probe.models | ForEach-Object { $_.name }) -join ', '
        Write-Ok "Ollama 已运行，模型: $names"
    } catch { $running = $false }

    if (-not $running -and -not $Check) {
        Write-Step '启动 Ollama'
        Start-Process -FilePath $ollamaExe -ArgumentList 'serve' -WorkingDirectory $ollamaRuntime -WindowStyle Hidden
        Start-Sleep -Seconds 5
        try {
            Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/tags' -TimeoutSec 5 | Out-Null
            Write-Ok 'Ollama 已就绪'
        } catch { Write-Warn2 'Ollama 启动失败，平台会自动进入离线检索模式' }
    }
    if (Test-Path (Join-Path $ollamaStore 'blobs')) {
        $storeSize = [math]::Round(((Get-ChildItem $ollamaStore -Recurse -File -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum) / 1GB, 1)
        Write-Ok "模型库: models\ollama\store（$storeSize GB）"
    }
} else {
    Write-Warn2 '未找到项目内的 Ollama（models\ollama\runtime），平台走离线检索模式（功能仍完整）'
    Write-Host '     恢复方法见 models\README.md，或安装 Ollama 后 ollama pull qwen3-vl:8b' -ForegroundColor White
}

# ---- 知识库索引 ----
$kbMeta = Join-Path $root 'data\vector_db\kb_index.json'
if (Test-Path $kbMeta) { Write-Ok '知识库索引: data\vector_db' } else { Write-Warn2 '知识库索引不存在，首次启动会自动构建' }

# ---- 检索内核（可选） ----
$kbcore = Join-Path $root 'backend\native\kbcore.dll'
if (Test-Path $kbcore) {
    Write-Ok "检索内核: kbcore（$([math]::Round((Get-Item $kbcore).Length / 1KB)) KB）"
} else {
    Write-Warn2 '检索内核未编译：使用纯 Python 引擎（功能一致，学科级规模下更慢）'
    Write-Host '     需要时可：powershell -ExecutionPolicy Bypass -File scripts\build_kbcore.ps1' -ForegroundColor White
}

if ($Check) {
    Write-Step '自检完成（未启动服务）'
    exit 0
}

if ($Seed) {
    Write-Step '灌入演示数据（教师 + 55 名学生 + 作业 + 课程空间）'
    Push-Location $root
    & $py 'scripts\seed_demo.py'
    & $py 'scripts\seed_lms.py'
    Pop-Location
}

Write-Step '启动后端（Ctrl+C 停止）'
Write-Host '  前端入口：http://127.0.0.1:5000/' -ForegroundColor White
Write-Host '  接口文档：http://127.0.0.1:5000/api/v1/meta' -ForegroundColor White
Write-Host '  健康检查：http://127.0.0.1:5000/api/v1/health' -ForegroundColor White
Push-Location (Join-Path $root 'backend')
try { & $py 'app.py' } finally { Pop-Location }