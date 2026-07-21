<#
  Windows 源码调试启动器。

  前置条件：Node.js 20.19+、Python 3.11+、uv 已安装。
  首次执行：
    powershell -ExecutionPolicy Bypass -File .\scripts\debug-windows-source.ps1 -InstallDependencies
  后续执行：
    powershell -ExecutionPolicy Bypass -File .\scripts\debug-windows-source.ps1

  启动后：
  - FastAPI 输出写入 .debug\backend-*.log；
  - Vite 输出写入 .debug\frontend-*.log；
  - Electron 会自动打开 DevTools，可直接查看 Console 和 Network；
  - 关闭 Electron 或按 Ctrl+C 会清理本次启动的完整进程树。
#>

[CmdletBinding()]
param(
  [switch]$InstallDependencies
)

$ErrorActionPreference = 'Stop'
$rootPath = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$backendPath = Join-Path $rootPath 'backend'
$frontendPath = Join-Path $rootPath 'frontend'
$debugPath = Join-Path $rootPath '.debug'

New-Item -ItemType Directory -Force -Path $debugPath | Out-Null

function Assert-Command {
  param([string]$Name, [string]$Hint)
  if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
    throw "缺少 $Name。$Hint"
  }
}

function Assert-PortAvailable {
  param([int]$Port, [string]$Name)
  $owner = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
    Select-Object -First 1
  if ($owner) {
    $process = Get-Process -Id $owner.OwningProcess -ErrorAction SilentlyContinue
    $processName = if ($process) { "$($process.ProcessName) (PID $($process.Id))" } else { "PID $($owner.OwningProcess)" }
    throw "$Name 端口 $Port 已被 $processName 占用。请结束该进程后重试。"
  }
}

function Wait-HttpReady {
  param([string]$Url, [string]$Name)
  for ($attempt = 1; $attempt -le 40; $attempt++) {
    try {
      $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 1
      if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500) {
        Write-Host "$Name 已就绪：$Url" -ForegroundColor Green
        return
      }
    } catch {
      Start-Sleep -Seconds 1
    }
  }
  throw "$Name 启动超时：$Url"
}

function Stop-ProcessTree {
  param([System.Diagnostics.Process]$Process)
  if ($null -eq $Process -or $Process.HasExited) {
    return
  }
  & taskkill /pid $Process.Id /T /F 2>$null | Out-Null
}

Assert-Command -Name 'uv' -Hint '请安装 uv：https://docs.astral.sh/uv/'
Assert-Command -Name 'node' -Hint '请安装 Node.js 20.19 或更高版本。'
Assert-Command -Name 'npm.cmd' -Hint '请确认 Node.js 的 npm 已加入 PATH。'

$nodeMajor = [int]((& node -p "process.versions.node.split('.')[0]") | Select-Object -First 1)
if ($nodeMajor -lt 20) {
  throw "当前 Node.js 为 $nodeMajor，Electron 需要 Node.js 20.19 或更高版本。"
}

if ($InstallDependencies) {
  Write-Host '同步 Python 依赖…' -ForegroundColor Cyan
  Push-Location $backendPath
  try { & uv sync } finally { Pop-Location }

  Write-Host '安装前端依赖…' -ForegroundColor Cyan
  Push-Location $frontendPath
  try { & npm.cmd install } finally { Pop-Location }
}

if (-not (Test-Path (Join-Path $frontendPath 'node_modules'))) {
  throw '前端依赖未安装。请先使用 -InstallDependencies 执行一次。'
}

Assert-PortAvailable -Port 8765 -Name 'FastAPI'
Assert-PortAvailable -Port 5173 -Name 'Vite'

$timestamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$backendOut = Join-Path $debugPath "backend-$timestamp.out.log"
$backendErr = Join-Path $debugPath "backend-$timestamp.err.log"
$frontendOut = Join-Path $debugPath "frontend-$timestamp.out.log"
$frontendErr = Join-Path $debugPath "frontend-$timestamp.err.log"
$backendProcess = $null
$frontendProcess = $null
$backendTail = $null
$frontendTail = $null

try {
  Write-Host '启动 FastAPI…' -ForegroundColor Cyan
  $backendProcess = Start-Process -FilePath 'uv' `
    -ArgumentList 'run', 'python', '-m', 'uvicorn', 'src.server.main:app', '--reload', '--reload-dir', 'src', '--port', '8765' `
    -WorkingDirectory $backendPath -PassThru -RedirectStandardOutput $backendOut -RedirectStandardError $backendErr
  Wait-HttpReady -Url 'http://127.0.0.1:8765/api/health' -Name 'FastAPI'

  Write-Host '启动 Vite…' -ForegroundColor Cyan
  $frontendProcess = Start-Process -FilePath 'npm.cmd' -ArgumentList 'run', 'dev', '--', '--host', '127.0.0.1' `
    -WorkingDirectory $frontendPath -PassThru -RedirectStandardOutput $frontendOut -RedirectStandardError $frontendErr
  Wait-HttpReady -Url 'http://127.0.0.1:5173' -Name 'Vite'

  $backendTail = Start-Job -ScriptBlock { param($Path) Get-Content -LiteralPath $Path -Wait | ForEach-Object { "[backend] $_" } } -ArgumentList $backendErr
  $frontendTail = Start-Job -ScriptBlock { param($Path) Get-Content -LiteralPath $Path -Wait | ForEach-Object { "[vite]    $_" } } -ArgumentList $frontendErr

  Write-Host '编译 Electron 主进程…' -ForegroundColor Cyan
  Push-Location $frontendPath
  try {
    & npm.cmd run electron:compile
    if ($LASTEXITCODE -ne 0) { throw 'Electron 主进程编译失败。' }

    Write-Host '启动 Electron 开发窗口，DevTools 将自动打开。关闭窗口或按 Ctrl+C 结束调试。' -ForegroundColor Green
    $env:NODE_ENV = 'development'
    & .\node_modules\.bin\electron.cmd electron-dist\main.js
  } finally {
    Remove-Item Env:NODE_ENV -ErrorAction SilentlyContinue
    Pop-Location
  }
} finally {
  Stop-ProcessTree -Process $frontendProcess
  Stop-ProcessTree -Process $backendProcess
  if ($backendTail) { Stop-Job -Job $backendTail -ErrorAction SilentlyContinue; Remove-Job -Job $backendTail -Force -ErrorAction SilentlyContinue }
  if ($frontendTail) { Stop-Job -Job $frontendTail -ErrorAction SilentlyContinue; Remove-Job -Job $frontendTail -Force -ErrorAction SilentlyContinue }
  Write-Host "调试日志已保存到：$debugPath" -ForegroundColor Yellow
}
