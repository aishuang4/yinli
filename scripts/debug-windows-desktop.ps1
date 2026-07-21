<#
  Debug an installed Windows desktop build.

  Usage:
    powershell -ExecutionPolicy Bypass -File .\debug-windows-desktop.ps1
    powershell -ExecutionPolicy Bypass -File .\debug-windows-desktop.ps1 -AppPath "D:\\YINLI\\YINLI.exe"

  The script starts the app with DevTools, tails Electron/Python logs,
  and prints port and stale-process diagnostics after exit.
#>

[CmdletBinding()]
param(
  [string]$AppPath,
  [switch]$KeepLogs
)

$ErrorActionPreference = 'Stop'

function Resolve-DesktopAppPath {
  param([string]$Candidate)

  if ($Candidate) {
    if (-not (Test-Path -LiteralPath $Candidate -PathType Leaf)) {
      throw "Application not found: $Candidate"
    }
    return (Resolve-Path -LiteralPath $Candidate).Path
  }

  throw 'Use -AppPath to specify the full path to the installed application executable.'
}

$resolvedAppPath = Resolve-DesktopAppPath -Candidate $AppPath
$userDataPath = Join-Path $HOME '.agent-workstation'
$logsPath = Join-Path $userDataPath 'logs'
$mainLogPath = Join-Path $logsPath 'electron-main.log'
$pythonLogPath = Join-Path $logsPath 'python-backend.log'
$ownerPath = Join-Path $userDataPath 'backend-process.json'

New-Item -ItemType Directory -Force -Path $logsPath | Out-Null
if (-not $KeepLogs) {
  Remove-Item -LiteralPath $mainLogPath, $pythonLogPath -Force -ErrorAction SilentlyContinue
}
New-Item -ItemType File -Force -Path $mainLogPath, $pythonLogPath | Out-Null

$env:AGENT_WORKSTATION_DESKTOP_DEBUG = '1'
$env:ELECTRON_ENABLE_LOGGING = '1'
$env:ELECTRON_ENABLE_STACK_DUMPING = '1'

Write-Host "Starting: $resolvedAppPath" -ForegroundColor Cyan
Write-Host "Log directory: $logsPath" -ForegroundColor Cyan
Write-Host 'Electron DevTools opens automatically. Check the first Console error and failed Network requests.' -ForegroundColor Yellow

$mainTail = Start-Job -ScriptBlock {
  param($Path)
  Get-Content -LiteralPath $Path -Wait | ForEach-Object { "[electron] $_" }
} -ArgumentList $mainLogPath
$pythonTail = Start-Job -ScriptBlock {
  param($Path)
  Get-Content -LiteralPath $Path -Wait | ForEach-Object { "[python]   $_" }
} -ArgumentList $pythonLogPath

try {
  $process = Start-Process -FilePath $resolvedAppPath -PassThru
  while (-not $process.HasExited) {
    Receive-Job -Job $mainTail, $pythonTail
    Start-Sleep -Milliseconds 250
    $process.Refresh()
  }
  Receive-Job -Job $mainTail, $pythonTail
  Write-Host "Application exited with code: $($process.ExitCode)" -ForegroundColor Yellow
} finally {
  Stop-Job -Job $mainTail, $pythonTail -ErrorAction SilentlyContinue
  Remove-Job -Job $mainTail, $pythonTail -Force -ErrorAction SilentlyContinue
}

Write-Host "`n--- Post-exit diagnostics ---" -ForegroundColor Cyan
if (Test-Path -LiteralPath $ownerPath) {
  Write-Host 'Found backend process record:' -ForegroundColor Yellow
  Get-Content -LiteralPath $ownerPath
} else {
  Write-Host 'No stale backend process record found.'
}

$portOwners = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue
if ($portOwners) {
  Write-Host 'Port 8765 is still listening in these processes:' -ForegroundColor Yellow
  $portOwners | Select-Object LocalAddress, LocalPort, OwningProcess | Format-Table -AutoSize
  $portOwners | ForEach-Object { Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue } |
    Select-Object Id, ProcessName, Path | Format-Table -AutoSize
} else {
  Write-Host 'Port 8765 is not listening.'
}

Write-Host "`nPlease provide:" -ForegroundColor Cyan
Write-Host '1. The first red error in DevTools Console.'
Write-Host "2. $mainLogPath"
Write-Host "3. $pythonLogPath"
