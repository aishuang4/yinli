<#
  Windows source-debug launcher.

  Requirements: Node.js 20.19+, Python 3.11+, and uv.
  First run:
    powershell -ExecutionPolicy Bypass -File .\scripts\debug-windows-source.ps1 -InstallDependencies
  Later runs:
    powershell -ExecutionPolicy Bypass -File .\scripts\debug-windows-source.ps1

  Behavior:
  - FastAPI output is written to .debug\backend-*.log.
  - Vite output is written to .debug\frontend-*.log.
  - Electron opens DevTools for Console and Network inspection.
  - Closing Electron or pressing Ctrl+C stops this run's process trees.
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
    throw "Missing command: $Name. $Hint"
  }
}

function Assert-PortAvailable {
  param([int]$Port, [string]$Name)
  $owner = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
    Select-Object -First 1
  if ($owner) {
    $process = Get-Process -Id $owner.OwningProcess -ErrorAction SilentlyContinue
    $processName = if ($process) { "$($process.ProcessName) (PID $($process.Id))" } else { "PID $($owner.OwningProcess)" }
    throw "$Name port $Port is already used by $processName. Stop it and retry."
  }
}

function Wait-HttpReady {
  param([string]$Url, [string]$Name)
  for ($attempt = 1; $attempt -le 40; $attempt++) {
    try {
      $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 1
      if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500) {
        Write-Host "$Name ready: $Url" -ForegroundColor Green
        return
      }
    } catch {
      Start-Sleep -Seconds 1
    }
  }
  throw "$Name startup timed out: $Url"
}

function Stop-ProcessTree {
  param([System.Diagnostics.Process]$Process)
  if ($null -eq $Process -or $Process.HasExited) {
    return
  }
  & taskkill /pid $Process.Id /T /F 2>$null | Out-Null
}

Assert-Command -Name 'uv' -Hint 'Install uv from https://docs.astral.sh/uv/'
Assert-Command -Name 'node' -Hint 'Install Node.js 20.19 or newer.'
Assert-Command -Name 'npm.cmd' -Hint 'Ensure npm is on PATH.'

$nodeVersion = ((& node -p "process.versions.node") | Select-Object -First 1).Trim()
if ($nodeVersion -notmatch '^(\d+)\.(\d+)\.(\d+)$') {
  throw "Unable to parse Node.js version: $nodeVersion"
}
$nodeMajor = [int]$Matches[1]
$nodeMinor = [int]$Matches[2]
if ($nodeMajor -lt 20 -or ($nodeMajor -eq 20 -and $nodeMinor -lt 19)) {
  throw "Node.js $nodeVersion is unsupported. Install Node.js 20.19+, 22.12+, or 24 LTS, then open a new PowerShell window."
}

if ($InstallDependencies) {
  Write-Host 'Syncing Python dependencies...' -ForegroundColor Cyan
  Push-Location $backendPath
  try { & uv sync } finally { Pop-Location }

  Write-Host 'Installing frontend dependencies...' -ForegroundColor Cyan
  Push-Location $frontendPath
  try { & npm.cmd install } finally { Pop-Location }
}

if (-not (Test-Path (Join-Path $frontendPath 'node_modules'))) {
  throw 'Frontend dependencies are missing. Run once with -InstallDependencies.'
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
  Write-Host 'Starting FastAPI...' -ForegroundColor Cyan
  $backendProcess = Start-Process -FilePath 'uv' `
    -ArgumentList 'run', 'python', '-m', 'uvicorn', 'src.server.main:app', '--reload', '--reload-dir', 'src', '--port', '8765' `
    -WorkingDirectory $backendPath -PassThru -WindowStyle Hidden -RedirectStandardOutput $backendOut -RedirectStandardError $backendErr
  Wait-HttpReady -Url 'http://127.0.0.1:8765/api/health' -Name 'FastAPI'

  Write-Host 'Starting Vite...' -ForegroundColor Cyan
  $frontendProcess = Start-Process -FilePath 'npm.cmd' -ArgumentList 'run', 'dev', '--', '--host', '127.0.0.1' `
    -WorkingDirectory $frontendPath -PassThru -WindowStyle Hidden -RedirectStandardOutput $frontendOut -RedirectStandardError $frontendErr
  Wait-HttpReady -Url 'http://127.0.0.1:5173' -Name 'Vite'

  $backendTail = Start-Job -ScriptBlock { param($Path) Get-Content -LiteralPath $Path -Wait | ForEach-Object { "[backend] $_" } } -ArgumentList $backendErr
  $frontendTail = Start-Job -ScriptBlock { param($Path) Get-Content -LiteralPath $Path -Wait | ForEach-Object { "[vite]    $_" } } -ArgumentList $frontendErr

  Write-Host 'Compiling Electron main process...' -ForegroundColor Cyan
  Push-Location $frontendPath
  try {
    # prepare-electron-dist.mjs needs dist for desktop-package assembly even in dev mode.
    if (-not (Test-Path (Join-Path $frontendPath 'dist\index.html'))) {
      Write-Host 'Building frontend once for Electron desktop metadata...' -ForegroundColor Cyan
      & npm.cmd run build
      if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
    }

    & npm.cmd run electron:compile
    if ($LASTEXITCODE -ne 0) { throw 'Electron main-process compilation failed.' }

    Write-Host 'Starting Electron. DevTools opens automatically. Close it or press Ctrl+C to stop.' -ForegroundColor Green
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
  Write-Host "Debug logs saved to: $debugPath" -ForegroundColor Yellow
}
