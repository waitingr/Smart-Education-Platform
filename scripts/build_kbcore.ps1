# 编译 C++ 检索内核（kbcore）
#
# 产物：backend/native/kbcore.dll（Windows）/ libkbcore.so（Linux/macOS）
# 用法：powershell -ExecutionPolicy Bypass -File scripts\build_kbcore.ps1
#
# 为什么用 C ABI + 动态库而不是 pybind11 扩展模块：
#   本机 Python 是 MSVC 编译的，MinGW 编出的 .pyd 加载不了（ABI 不兼容）；
#   而 C ABI 的 DLL 通过 ctypes 调用与编译器无关，也不需要 Visual Studio。
# 编不出来也没关系：kb_service 会自动回退纯 Python 引擎（功能不变，只是慢一些）。

param(
    [string]$Compiler = '',
    [switch]$Debug
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$Root = Split-Path -Parent $PSScriptRoot
$Source = Join-Path $Root 'backend\native\kbcore.cpp'
$OutDir = Join-Path $Root 'backend\native'
$IsWindowsHost = $PSVersionTable.PSVersion.Major -ge 6 -and $IsWindows
if (-not $IsWindowsHost -and $env:OS -ne 'Windows_NT') { $IsWindowsHost = $false } else { $IsWindowsHost = $true }
$Target = if ($IsWindowsHost) { Join-Path $OutDir 'kbcore.dll' } else { Join-Path $OutDir 'libkbcore.so' }

# 1. 找编译器：优先显式指定，其次 PATH 里的 g++/clang++
if (-not $Compiler) {
    foreach ($candidate in 'g++', 'clang++') {
        $found = Get-Command $candidate -ErrorAction SilentlyContinue
        if ($found) { $Compiler = $found.Source; break }
    }
}
if (-not $Compiler -and (Test-Path 'D:\Dev_Environment\MingW-64-14.3.0\bin\g++.exe')) {
    $Compiler = 'D:\Dev_Environment\MingW-64-14.3.0\bin\g++.exe'
}
if (-not $Compiler) {
    Write-Host '未找到 g++ / clang++，跳过编译。' -ForegroundColor Yellow
    Write-Host '平台会自动回退纯 Python 引擎，功能不受影响。'
    exit 1
}

New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

# -static 是关键：不带 MinGW 运行时 DLL，ctypes 才能在任意机器上加载
$Flags = @('-std=c++17', '-shared', '-static', '-static-libgcc', '-static-libstdc++', '-Wall', '-Wextra')
if ($Debug) { $Flags += @('-g', '-O0') } else { $Flags += @('-O3') }

Write-Host "编译器：$Compiler" -ForegroundColor Cyan
Write-Host "输出：$Target" -ForegroundColor Cyan
& $Compiler @Flags -o $Target $Source
if ($LASTEXITCODE -ne 0) {
    Write-Host "编译失败（exit=$LASTEXITCODE），平台仍可用纯 Python 引擎。" -ForegroundColor Yellow
    exit 1
}

$info = Get-Item $Target
Write-Host "编译成功：$([math]::Round($info.Length / 1KB)) KB" -ForegroundColor Green

# 2. 立刻验证能被 Python 加载，避免"编译成功但加载失败"
$Python = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $Python)) { $Python = 'python' }
Push-Location $Root
try {
    # 子进程 stdout 走的是控制台代码页，这里只用 ASCII，避免 GBK 乱码误判
    & $Python -c "import sys; sys.path.insert(0, r'$Root\backend'); import kb_native; print('kbcore load:', 'OK' if kb_native.available() else 'FAILED: ' + kb_native.last_error())"
} finally {
    Pop-Location
}