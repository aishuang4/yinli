<#
  调试已安装的 Windows 桌面端。

  用法：
    powershell -ExecutionPolicy Bypass -File .\debug-windows-desktop.ps1
    powershell -ExecutionPolicy Bypass -File .\debug-windows-desktop.ps1 -AppPath "D:\\引力 YINLI\\引力 YINLI.exe"

  脚本会：
  1. 以桌面调试模式启动安装包，并自动打开 Electron DevTools；
  2. 在当前终端实时打印主进程和 Python 后端日志；
  3. 退出后输出 8765 端口和遗留进程记录，便于反馈排障信息。
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
      throw "未找到指定应用：$Candidate"
    }
    return (Resolve-Path -LiteralPath $Candidate).Path
  }

  $defaultPath = Join-Path $env:LOCALAPPDATA 'Programs\引力 YINLI\引力 YINLI.exe'
  if (Test-Path -LiteralPath $defaultPath -PathType Leaf) {
    return $defaultPath
  }

  throw "未找到默认安装包。请使用 -AppPath 指定 引力 YINLI.exe 的完整路径。"
}

$resolvedAppPath = Resolve-DesktopAppPath -Candidate $AppPath
$userDataPath = Join-Path $env:APPDATA 'agent-workstation-desktop'
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

Write-Host "启动：$resolvedAppPath" -ForegroundColor Cyan
Write-Host "日志目录：$logsPath" -ForegroundColor Cyan
Write-Host 'Electron DevTools 将自动打开；请重点查看 Console 的红色错误和 Network 请求。' -ForegroundColor Yellow

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
  Write-Host "应用已退出，退出代码：$($process.ExitCode)" -ForegroundColor Yellow
} finally {
  Stop-Job -Job $mainTail, $pythonTail -ErrorAction SilentlyContinue
  Remove-Job -Job $mainTail, $pythonTail -Force -ErrorAction SilentlyContinue
}

Write-Host "`n--- 退出后诊断 ---" -ForegroundColor Cyan
if (Test-Path -LiteralPath $ownerPath) {
  Write-Host '发现后端进程记录：' -ForegroundColor Yellow
  Get-Content -LiteralPath $ownerPath
} else {
  Write-Host '未发现遗留的后端进程记录。'
}

$portOwners = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue
if ($portOwners) {
  Write-Host '8765 端口仍被以下进程监听：' -ForegroundColor Yellow
  $portOwners | Select-Object LocalAddress, LocalPort, OwningProcess | Format-Table -AutoSize
  $portOwners | ForEach-Object { Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue } |
    Select-Object Id, ProcessName, Path | Format-Table -AutoSize
} else {
  Write-Host '8765 端口当前未被监听。'
}

Write-Host "`n请将以下内容一并反馈：" -ForegroundColor Cyan
Write-Host "1. DevTools Console 的首个红色错误；"
Write-Host "2. $mainLogPath；"
Write-Host "3. $pythonLogPath。"
