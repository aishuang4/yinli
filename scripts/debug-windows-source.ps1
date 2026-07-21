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
  [switch]$InstallDependencies,
  [string]$ElectronMirror = 'https://npmmirror.com/mirrors/electron/'
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

function Set-ElectronMirror {
  param([AllowEmptyString()][string]$Mirror)
  if ([string]::IsNullOrWhiteSpace($Mirror)) {
    Remove-Item Env:ELECTRON_MIRROR -ErrorAction SilentlyContinue
    return
  }
  $env:ELECTRON_MIRROR = $Mirror.TrimEnd('/') + '/'
}

function Restore-ElectronMirror {
  param([AllowNull()][string]$Mirror)
  if ($null -eq $Mirror) {
    Remove-Item Env:ELECTRON_MIRROR -ErrorAction SilentlyContinue
    return
  }
  $env:ELECTRON_MIRROR = $Mirror
}

function Assert-ElectronBinary {
  $electronPackagePath = Join-Path $frontendPath 'node_modules\electron'
  $electronInstallerPath = Join-Path $electronPackagePath 'install.js'
  $electronBinaryPath = Join-Path $electronPackagePath 'dist\electron.exe'
  $electronCommandPath = Join-Path $frontendPath 'node_modules\.bin\electron.cmd'

  if (-not (Test-Path -LiteralPath $electronInstallerPath -PathType Leaf)) {
    throw 'Electron npm package is missing. Run once with -InstallDependencies.'
  }

  if (-not (Test-Path -LiteralPath $electronBinaryPath -PathType Leaf)) {
    $previousMirror = [Environment]::GetEnvironmentVariable('ELECTRON_MIRROR', 'Process')
    try {
      Set-ElectronMirror -Mirror $ElectronMirror
      for ($attempt = 1; $attempt -le 3; $attempt++) {
        Write-Host "Downloading Electron binary (attempt $attempt/3)..." -ForegroundColor Cyan
        & node $electronInstallerPath
        if ($LASTEXITCODE -eq 0 -and (Test-Path -LiteralPath $electronBinaryPath -PathType Leaf)) {
          break
        }
        if ($attempt -lt 3) {
          Start-Sleep -Seconds (2 * $attempt)
        }
      }
    } finally {
      Restore-ElectronMirror -Mirror $previousMirror
    }
  }

  if (-not (Test-Path -LiteralPath $electronBinaryPath -PathType Leaf)) {
    throw "Electron binary download failed after 3 attempts. Check HTTPS_PROXY/NODE_EXTRA_CA_CERTS, or pass -ElectronMirror with an accessible mirror."
  }

  if (-not (Test-Path -LiteralPath $electronCommandPath -PathType Leaf)) {
    throw 'Electron command shim is missing. Run once with -InstallDependencies.'
  }
  $electronVersionOutput = & $electronCommandPath --version
  $electronExitCode = $LASTEXITCODE
  $electronVersion = ([string]($electronVersionOutput | Select-Object -First 1)).Trim()
  if ($electronExitCode -ne 0 -or [string]::IsNullOrWhiteSpace($electronVersion)) {
    throw 'Electron binary exists but cannot start. Delete frontend\node_modules\electron\dist and retry.'
  }
  Write-Host "Electron ready: $electronVersion" -ForegroundColor Green
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
$nodeSupported = ($nodeMajor -eq 20 -and $nodeMinor -ge 19) `
  -or ($nodeMajor -eq 22 -and $nodeMinor -ge 12) `
  -or ($nodeMajor -ge 24 -and $nodeMajor % 2 -eq 0)
if (-not $nodeSupported) {
  throw "Node.js $nodeVersion is unsupported. Install Node.js 20.19+, 22.12+, or 24 LTS, then open a new PowerShell window."
}

if ($InstallDependencies) {
  Write-Host 'Syncing Python dependencies...' -ForegroundColor Cyan
  Push-Location $backendPath
  try {
    & uv sync
    if ($LASTEXITCODE -ne 0) { throw 'Python dependency installation failed.' }
  } finally { Pop-Location }

  Write-Host 'Installing frontend dependencies...' -ForegroundColor Cyan
  Push-Location $frontendPath
  $previousMirror = [Environment]::GetEnvironmentVariable('ELECTRON_MIRROR', 'Process')
  try {
    # Electron 会校验包内 checksums.json；镜像仅替换下载地址，不跳过完整性校验。
    Set-ElectronMirror -Mirror $ElectronMirror
    & npm.cmd ci
    if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed.' }
  } finally {
    Restore-ElectronMirror -Mirror $previousMirror
    Pop-Location
  }
}

if (-not (Test-Path (Join-Path $frontendPath 'node_modules'))) {
  throw 'Frontend dependencies are missing. Run once with -InstallDependencies.'
}

# 在启动后端和 Vite 前先修复 Electron，避免两个服务已经运行后才发现桌面壳不可用。
Assert-ElectronBinary

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
